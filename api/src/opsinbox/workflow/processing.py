"""The steps of processing one e-mail, as plain functions over a serialisable state.

The LangGraph workflow (:mod:`opsinbox.workflow.graph`) runs these steps as nodes with a
persisted checkpoint; the evaluation harness runs exactly the same steps without a database.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from opsinbox.errors import ReadingError
from opsinbox.extraction import Extractor, MailContext, Usage
from opsinbox.reading import DocumentText, Reader, Word, pdf_text_layer
from opsinbox.records.business import Business
from opsinbox.records.view import RecordsView
from opsinbox.rules.engine import Rule, RuleResult, evaluate_rules
from opsinbox.schemas import Category, ClassificationRead, Extraction, ScreenRead
from opsinbox.workflow.facts import build_facts, jsonable

#: Fields that change nothing in the booking, the price or the documents: not gated.
UNGATED_FIELDS = frozenset({"purpose"})
#: Categories that may complete without a person when every check passes.
AUTOMATABLE = frozenset(
    {
        Category.NEW_BOOKING,
        Category.CHANGE,
        Category.CANCELLATION,
        Category.SUPPLIER_INVOICE,
        Category.NOT_RELEVANT,
    }
)


@dataclass(frozen=True)
class IncomingAttachment:
    """An attachment and a way to fetch its bytes."""

    id: str
    filename: str
    content_type: str
    load: Callable[[], bytes]


@dataclass(frozen=True)
class IncomingMail:
    """Everything about an e-mail that processing uses."""

    id: str
    subject: str
    body: str
    from_address: str
    from_name: str
    reply_to: str
    sent_at: datetime
    in_reply_to: str
    references: list[str]
    attachments: list[IncomingAttachment] = field(default_factory=list)

    def context(self, business: Business) -> MailContext:
        """The view of the e-mail given to the model."""
        local = business.local(self.sent_at)
        return MailContext(
            subject=self.subject,
            body=self.body,
            from_address=self.from_address,
            from_name=self.from_name,
            sent_on=local.date(),
            attachment_names=tuple(a.filename for a in self.attachments),
            is_reply=bool(self.in_reply_to or self.references),
        )

    @property
    def thread_ids(self) -> list[str]:
        """Message ids of the conversation this e-mail belongs to."""
        return [*self.references, *([self.in_reply_to] if self.in_reply_to else [])]


class DocumentRead(BaseModel):
    """The text read from one attachment."""

    attachment_id: str
    filename: str
    kind: Literal["licence", "invoice", "other"]
    engine: str
    text: str
    words: list[dict[str, Any]] = Field(default_factory=list)
    #: The same file read by an independent engine, for the second extraction pass.
    check_text: str | None = None

    def as_document(self) -> DocumentText:
        """Back to the reader's dataclass."""
        return DocumentText(self.text, [Word(**w) for w in self.words], self.engine)

    def check_document(self) -> DocumentText | None:
        """The independent reading, if there is one."""
        return DocumentText(self.check_text, engine="check") if self.check_text else None


class Reason(BaseModel):
    """Why a case stopped for review, in plain language."""

    code: str
    message: str


class CaseState(BaseModel):
    """The state of one e-mail's processing. JSON-serialisable."""

    mail_id: str
    category: Category | None = None
    language: str = "en"
    classification: ClassificationRead | None = None
    documents: list[DocumentRead] = Field(default_factory=list)
    booking: Extraction | None = None
    licences: list[Extraction] = Field(default_factory=list)
    invoices: list[Extraction] = Field(default_factory=list)
    document_screen: ScreenRead | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    rules: list[RuleResult] = Field(default_factory=list)
    route: Literal["auto", "review"] | None = None
    reasons: list[Reason] = Field(default_factory=list)
    cost_usd: float = 0.0
    model_calls: int = 0
    model_seconds: float = 0.0


def _account(state: CaseState, usage: Usage) -> None:
    state.cost_usd += usage.cost_usd
    state.model_calls += usage.calls
    state.model_seconds += usage.model_seconds


def gated_fields(state: CaseState) -> list[tuple[str, float]]:
    """(name, confidence) of every extracted field that gates automation."""
    found: list[tuple[str, float]] = []
    extractions: list[tuple[str, Extraction]] = []
    if state.category in (Category.NEW_BOOKING, Category.CHANGE) and state.booking:
        extractions.append(("", state.booking))
    if state.category == Category.NEW_BOOKING:
        extractions += [("licence.", e) for e in state.licences[:1]]
    if state.category == Category.SUPPLIER_INVOICE:
        extractions += [("invoice.", e) for e in state.invoices[:1]]
    for prefix, extraction in extractions:
        for item in extraction.fields:
            if item.name in UNGATED_FIELDS:
                continue
            if state.category == Category.CHANGE and item.value is None:
                continue
            found.append((prefix + item.name, item.confidence))
    return found


def decide(state: CaseState, threshold: float) -> tuple[Literal["auto", "review"], list[Reason]]:
    """Route a case: automatic only if every rule passed and every field clears the threshold.

    A pure function of the state, so the evaluation can replay other thresholds.
    """
    reasons: list[Reason] = []
    if state.category is None:
        return "review", [Reason(code="unclassified", message="The e-mail could not be sorted.")]
    for result in state.rules:
        if result.outcome == "failed":
            reasons.append(Reason(code=result.reason, message=result.message or result.name))
        elif result.outcome == "error":
            reasons.append(Reason(code=result.reason, message=f"Rule error: {result.message}"))
    for name, confidence in gated_fields(state):
        if confidence < threshold:
            reasons.append(
                Reason(
                    code=f"low_confidence:{name}",
                    message=f"Not sure about {name.replace('_', ' ')} ({confidence:.0%}).",
                )
            )
    if state.category not in AUTOMATABLE and not reasons:
        reasons.append(
            Reason(code="category", message="This kind of e-mail is handled by a person.")
        )
    return ("review" if reasons else "auto"), reasons


class Processor:
    """Runs the steps with injected dependencies."""

    def __init__(
        self,
        extractor: Extractor | None,
        reader: Reader,
        box_reader: Reader | None,
        business: Business,
        rules: list[Rule],
        records: RecordsView,
        threshold: float,
        cross_check: bool = True,
    ) -> None:
        self.extractor = extractor
        self.reader = reader
        self.box_reader = box_reader
        self.business = business
        self.rules = rules
        self.records = records
        self.threshold = threshold
        self.cross_check = cross_check

    # Each step takes the state and returns it updated; steps are safe to run again.

    def classify(self, state: CaseState, mail: IncomingMail) -> CaseState:
        """Sort the e-mail."""
        usage = Usage()
        assert self.extractor is not None, "classification needs a model"
        read = self.extractor.classify(mail.context(self.business), usage)
        state.classification = read
        state.category = read.category
        state.language = read.language if read.language != "other" else "en"
        _account(state, usage)
        return state

    def read_attachments(self, state: CaseState, mail: IncomingMail) -> CaseState:
        """Turn the attachments that matter for the category into text."""
        documents: list[DocumentRead] = []
        wanted: Literal["licence", "invoice"] | None = None
        if state.category == Category.NEW_BOOKING:
            wanted = "licence"
        elif state.category == Category.SUPPLIER_INVOICE:
            wanted = "invoice"
        for attachment in mail.attachments:
            readable = (
                attachment.content_type.startswith("image/")
                or attachment.content_type == "application/pdf"
            )
            if wanted is None or not readable:
                continue
            data = attachment.load()
            try:
                documents.append(self._read_one(attachment, data, wanted))
            except ReadingError:
                documents.append(
                    DocumentRead(
                        attachment_id=attachment.id,
                        filename=attachment.filename,
                        kind=wanted,
                        engine="unreadable",
                        text="",
                    )
                )
        state.documents = documents
        return state

    def _read_one(
        self, attachment: IncomingAttachment, data: bytes, wanted: Literal["licence", "invoice"]
    ) -> DocumentRead:
        """Read one file: a PDF's own text layer when it has one, otherwise the reader.

        Local OCR always supplies word boxes (for highlighting) and, when enabled, the
        independent reading used by the second extraction pass.
        """
        check: str | None = None
        layer = pdf_text_layer(data) if attachment.content_type == "application/pdf" else None
        if layer is not None:
            boxes = (
                self.box_reader.read(data, attachment.content_type).words if self.box_reader else []
            )
            text, words, engine = layer, boxes, "pdf-text"
        else:
            read = self.reader.read(data, attachment.content_type)
            words, text, engine = read.words, read.text, read.engine
            if not words and self.box_reader is not None:
                independent = self.box_reader.read(data, attachment.content_type)
                words = independent.words
                check = independent.text if self.cross_check else None
        return DocumentRead(
            attachment_id=attachment.id,
            filename=attachment.filename,
            kind=wanted,
            engine=engine,
            text=text,
            words=[w.__dict__ for w in words],
            check_text=check,
        )

    def extract(self, state: CaseState, mail: IncomingMail) -> CaseState:
        """Read the structured fields of the e-mail and its documents."""
        usage = Usage()
        assert self.extractor is not None, "extraction needs a model"
        context = mail.context(self.business)
        state.booking, state.licences, state.invoices = None, [], []
        if state.category in (Category.NEW_BOOKING, Category.CHANGE):
            state.booking = self.extractor.booking(context, usage)
        for document in state.documents:
            if document.engine == "unreadable":
                empty = Extraction(document=document.kind, fields=[])
                (state.licences if document.kind == "licence" else state.invoices).append(empty)
                continue
            if document.kind == "licence":
                state.licences.append(
                    self.extractor.licence(
                        document.as_document(),
                        document.attachment_id,
                        context.sent_on,
                        usage,
                        document.check_document(),
                    )
                )
            elif document.kind == "invoice":
                state.invoices.append(
                    self.extractor.invoice(
                        document.as_document(),
                        document.attachment_id,
                        context.sent_on,
                        usage,
                        document.check_document(),
                    )
                )
        texts = [d.text for d in state.documents if d.text.strip()]
        if texts:
            state.document_screen = self.extractor.screen(texts, usage)
        _account(state, usage)
        return state

    def validate(self, state: CaseState, mail: IncomingMail) -> CaseState:
        """Compute the facts and run the business rules."""
        assert state.category is not None
        facts = build_facts(
            category=state.category,
            mail=mail.context(self.business),
            message_ids=mail.thread_ids,
            reply_to=mail.reply_to,
            received_at=self.business.local(mail.sent_at),
            classification=state.classification,
            document_screen=state.document_screen,
            booking=state.booking,
            licences=state.licences,
            invoices=state.invoices,
            document_texts=[d.text for d in state.documents],
            business=self.business,
            records=self.records,
        )
        state.rules = evaluate_rules(self.rules, state.category.value, facts)
        state.facts = jsonable(facts)
        return state

    def decide(self, state: CaseState) -> CaseState:
        """Route the case."""
        state.route, state.reasons = decide(state, self.threshold)
        return state

    def run(self, mail: IncomingMail) -> CaseState:
        """All steps in order (used by the evaluation)."""
        state = CaseState(mail_id=mail.id)
        for step in (self.classify, self.read_attachments, self.extract, self.validate):
            state = step(state, mail)
        return self.decide(state)
