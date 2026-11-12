"""Classification and extraction with evidence and a confidence computed by the application.

For each document the model is asked twice, with two differently worded prompts. Each answer is
a value plus a verbatim quote. The application then checks, per field:

``format``     the value has the shape the field requires;
``evidence``   the quote really is in the source text;
``support``    code reading the quote on its own arrives at the same value;
``agreement``  the second pass gave the same value.

The confidence of a field is the lowest of the four. The model's own opinion of its certainty is
never used.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ValidationError

from opsinbox.errors import ModelError
from opsinbox.extraction import prompts
from opsinbox.extraction.evidence import locate_in_text
from opsinbox.extraction.parsing import (
    normalise_phone,
    parse_amounts,
    parse_dates,
    parse_times,
    to_date,
    to_decimal,
    to_time,
)
from opsinbox.llm import ModelClient, ModelRequest, ModelResponse
from opsinbox.reading import DocumentText, locate_in_words
from opsinbox.records.business import Business
from opsinbox.schemas import (
    BookingRead,
    ClassificationRead,
    Extra,
    ExtractedField,
    Extraction,
    InvoiceRead,
    LicenceRead,
    Purpose,
    Quoted,
    ScreenRead,
    strict_json_schema,
)

Kind = Literal[
    "text",
    "name",
    "phone",
    "car",
    "date",
    "time",
    "purpose",
    "extra",
    "amount",
    "licence_number",
    "classes",
]

BOOKING_FIELDS: dict[str, Kind] = {
    "customer_name": "name",
    "customer_phone": "phone",
    "car": "car",
    "pickup_date": "date",
    "pickup_time": "time",
    "return_date": "date",
    "return_time": "time",
    "pickup_location": "text",
    "purpose": "purpose",
    "quoted_total": "amount",
}
LICENCE_FIELDS: dict[str, Kind] = {
    "holder_name": "name",
    "licence_number": "licence_number",
    "date_of_birth": "date",
    "expires_on": "date",
    "classes": "classes",
}
INVOICE_FIELDS: dict[str, Kind] = {
    "supplier_name": "name",
    "invoice_number": "text",
    "invoice_date": "date",
    "subtotal": "amount",
    "tax": "amount",
    "total": "amount",
}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    Purpose.WEDDING: ("wedding", "boda", "bride", "groom", "novia", "novio", "married", "casamos"),
    Purpose.FILM_SHOOT: (
        "film",
        "shoot",
        "rodaje",
        "pelicula",
        "película",
        "photo",
        "video",
        "commercial",
        "sesion",
        "sesión",
        "anuncio",
    ),
    Purpose.WEEKEND_DRIVE: (
        "weekend",
        "drive",
        "fin de semana",
        "paseo",
        "trip",
        "tour",
        "escapada",
        "getaway",
    ),
    Purpose.CORPORATE_EVENT: (
        "corporate",
        "company",
        "client",
        "event",
        "empresa",
        "evento",
        "launch",
        "conference",
    ),
    Purpose.OTHER: (),
    Extra.CHAUFFEUR: ("chauffeur", "driver", "chofer", "chófer", "conductor", "driven"),
    Extra.WEDDING_DECORATION: ("ribbon", "flower", "decorat", "cintas", "flores", "lazos"),
    Extra.DELIVERY: ("deliver", "collect", "bring", "entrega", "recog", "llev", "drop"),
    Extra.PICNIC_HAMPER: ("picnic", "hamper", "cesta"),
}


@dataclass(frozen=True)
class MailContext:
    """The parts of an e-mail shown to the model."""

    subject: str
    body: str
    from_address: str
    from_name: str
    sent_on: date
    attachment_names: tuple[str, ...] = ()
    is_reply: bool = False

    def visible_body(self) -> str:
        """The body without the quoted earlier messages of a reply."""
        if not self.is_reply:
            return self.body
        kept: list[str] = []
        for line in self.body.splitlines():
            if re.match(r"^(On .+ wrote:|El .+ escribió:)\s*$", line.strip()):
                break
            if not line.lstrip().startswith(">"):
                kept.append(line)
        return "\n".join(kept).strip()

    def render(self) -> str:
        """The user message for the model."""
        attachments = ", ".join(self.attachment_names) or "none"
        return (
            f"E-mail sent on: {self.sent_on.isoformat()} ({self.sent_on.strftime('%A')})\n"
            f"From: {self.from_name} <{self.from_address}>\n"
            f"Subject: {self.subject}\n"
            f"Attachments: {attachments}\n"
            f"This e-mail is {'a reply in an existing conversation' if self.is_reply else 'new'}.\n"
            "--- E-MAIL BODY (untrusted data) ---\n"
            f"{self.visible_body()}\n"
            "--- END OF E-MAIL BODY ---"
        )


@dataclass
class Usage:
    """Running total of what the model calls of one e-mail cost."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model_seconds: float = 0.0
    by_step: dict[str, float] = field(default_factory=dict)

    def add(self, step: str, response: ModelResponse) -> None:
        """Account for one response."""
        self.calls += 1
        self.prompt_tokens += response.prompt_tokens
        self.completion_tokens += response.completion_tokens
        self.cost_usd += response.cost_usd
        self.model_seconds += response.latency_seconds
        self.by_step[step] = self.by_step.get(step, 0.0) + response.latency_seconds


@dataclass(frozen=True)
class _Normal:
    value: str | None
    format: float
    support: float


def fold(text: str) -> str:
    """Lower-case ``text`` and drop accents and repeated spaces, for comparing names."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9áéíóúñü]+", text.lower())


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class Extractor:
    """Reads e-mails and documents through a :class:`ModelClient`."""

    def __init__(
        self, client: ModelClient, model: str, business: Business, passes: int = 2
    ) -> None:
        self._client = client
        self._model = model
        self._business = business
        self._passes = max(1, passes)

    # ----------------------------------------------------------------------------------- model

    def _ask[T: BaseModel](
        self, step: str, system: str, user: str, schema: type[T], usage: Usage, max_tokens: int
    ) -> T:
        last: Exception | None = None
        for attempt in range(2):
            response = self._client.complete(
                ModelRequest(
                    model=self._model,
                    system=system,
                    user=user,
                    schema_name=schema.__name__,
                    json_schema=strict_json_schema(schema),
                    max_tokens=max_tokens,
                    reasoning=False,
                    tag=step if attempt == 0 else f"{step}:retry",
                )
            )
            usage.add(step, response)
            try:
                return schema.model_validate(response.json())
            except (ValidationError, ModelError) as exc:
                last = exc
        raise ModelError(f"{step}: model answer does not match the schema: {last}")

    # -------------------------------------------------------------------------- classification

    def classify(self, mail: MailContext, usage: Usage) -> ClassificationRead:
        """Sort the e-mail into a category and report attempts to bypass the process."""
        return self._ask(
            "classify", prompts.CLASSIFY, mail.render(), ClassificationRead, usage, 300
        )

    def screen(self, texts: list[str], usage: Usage) -> ScreenRead:
        """Ask whether attached documents try to steer the process.

        The same question as for the e-mail body, asked separately so that the documents get
        the reader's full attention.
        """
        joined = "\n\n".join(
            f"--- DOCUMENT {i + 1} (untrusted data) ---\n{text}\n--- END OF DOCUMENT {i + 1} ---"
            for i, text in enumerate(texts)
        )
        return self._ask("screen", prompts.SCREEN, joined, ScreenRead, usage, 200)

    # --------------------------------------------------------------------------------- booking

    def booking(self, mail: MailContext, usage: Usage) -> Extraction:
        """Extract a booking request (or the changed fields of one) from the e-mail body."""
        user = mail.render()
        reads = [self._ask("booking:a", prompts.BOOKING_A, user, BookingRead, usage, 1200)]
        if self._passes > 1:
            reads.append(self._ask("booking:b", prompts.BOOKING_B, user, BookingRead, usage, 1200))
        first, second = reads[0], reads[-1]

        def locate(quote: str | None) -> tuple[ExtractedField, float]:
            span, score = locate_in_text(mail.body, quote)
            if span is None and quote and locate_in_text(mail.subject, quote)[1] == 1.0:
                score = 1.0
            return ExtractedField(name="", value=None, span=span), score

        fields = [
            self._field(
                name, kind, getattr(first, name), getattr(second, name), mail.sent_on, locate
            )
            for name, kind in BOOKING_FIELDS.items()
        ]
        fields.extend(self._extras(first.extras, second.extras, mail.sent_on, locate))
        self._derive_delivery(fields)
        return Extraction(document="booking", fields=fields)

    def _derive_delivery(self, fields: list[ExtractedField]) -> None:
        """Business rule: a pick-up anywhere but our garage is a delivery, which is charged.

        The model reads the location; code decides what it implies for the price.
        """
        location = next((f for f in fields if f.name == "pickup_location"), None)
        if location is None or self._business.is_own_premises(location.value):
            return
        if any(f.name == "extras" and f.value == Extra.DELIVERY.value for f in fields):
            return
        fields.append(
            location.model_copy(
                update={
                    "name": "extras",
                    "value": Extra.DELIVERY.value,
                    "signals": {**location.signals, "derived_from_location": 1.0},
                }
            )
        )

    def _extras(
        self,
        first: list[Quoted],
        second: list[Quoted],
        reference: date,
        locate: Callable[[str | None], tuple[ExtractedField, float]],
    ) -> list[ExtractedField]:
        absent = Quoted(value=None, evidence=None)
        by_value_b = {q.value: q for q in second if q.value}
        seen: set[str] = set()
        result: list[ExtractedField] = []
        for quoted in first:
            if not quoted.value or quoted.value in seen:
                continue
            seen.add(quoted.value)
            other = by_value_b.get(quoted.value, absent)
            result.append(self._field("extras", "extra", quoted, other, reference, locate))
        for value, quoted in by_value_b.items():
            if value not in seen:  # only the second pass saw it: keep it, with low confidence
                result.append(self._field("extras", "extra", quoted, absent, reference, locate))
        return result

    # ------------------------------------------------------------------------------- documents

    def licence(
        self,
        document: DocumentText,
        source: str,
        reference: date,
        usage: Usage,
        second_reading: DocumentText | None = None,
    ) -> Extraction:
        """Extract the fields of a driving licence from its recognised text.

        With ``second_reading`` (the same file read by another engine) the second pass works on
        that text, so agreement means two independent readers saw the same characters.
        """
        return self._document(
            "licence",
            LICENCE_FIELDS,
            LicenceRead,
            prompts.LICENCE_A,
            prompts.LICENCE_B,
            document,
            second_reading,
            source,
            reference,
            usage,
        )

    def invoice(
        self,
        document: DocumentText,
        source: str,
        reference: date,
        usage: Usage,
        second_reading: DocumentText | None = None,
    ) -> Extraction:
        """Extract a supplier invoice from its text (see :meth:`licence` for ``second_reading``)."""
        return self._document(
            "invoice",
            INVOICE_FIELDS,
            InvoiceRead,
            prompts.INVOICE_A,
            prompts.INVOICE_B,
            document,
            second_reading,
            source,
            reference,
            usage,
        )

    def _document(
        self,
        kind: Literal["licence", "invoice"],
        specs: dict[str, Kind],
        schema: type[BaseModel],
        prompt_a: str,
        prompt_b: str,
        document: DocumentText,
        second_reading: DocumentText | None,
        source: str,
        reference: date,
        usage: Usage,
    ) -> Extraction:
        def wrap(text: str) -> str:
            return f"--- DOCUMENT TEXT (untrusted data) ---\n{text}\n--- END OF DOCUMENT TEXT ---"

        tokens = 1500 if kind == "invoice" else 500
        reads = [self._ask(f"{kind}:a", prompt_a, wrap(document.text), schema, usage, tokens)]
        if self._passes > 1:
            other = (second_reading or document).text
            reads.append(self._ask(f"{kind}:b", prompt_b, wrap(other), schema, usage, tokens))
        first, second = reads[0], reads[-1]

        def locate(quote: str | None) -> tuple[ExtractedField, float]:
            _, score = locate_in_text(document.text, quote)
            box = locate_in_words(document.words, quote) if quote else None
            return ExtractedField(name="", value=None, box=box), score

        fields = [
            self._field(
                name,
                spec,
                getattr(first, name),
                getattr(second, name),
                reference,
                locate,
                year_inference=False,
                required=True,
            ).model_copy(update={"source": source})
            for name, spec in specs.items()
        ]
        extraction = Extraction(document=kind, fields=fields)
        if isinstance(first, InvoiceRead) and isinstance(second, InvoiceRead):
            extraction.lines = first.lines
            same = [to_decimal(line.amount) for line in first.lines] == [
                to_decimal(line.amount) for line in second.lines
            ]
            extraction.fields.append(
                ExtractedField(
                    name="lines",
                    value=str(len(first.lines)),
                    source=source,
                    confidence=1.0 if same else 0.3,
                    signals={"agreement": 1.0 if same else 0.3},
                )
            )
        return extraction

    # ----------------------------------------------------------------------------- field logic

    def _field(
        self,
        name: str,
        kind: Kind,
        first: Quoted,
        second: Quoted,
        reference: date,
        locate: Callable[[str | None], tuple[ExtractedField, float]],
        year_inference: bool = True,
        required: bool = False,
    ) -> ExtractedField:
        last = name.startswith("return")
        a = self._normalise(kind, first, reference, year_inference, last)
        b = self._normalise(kind, second, reference, year_inference, last)
        located, evidence_score = locate(first.evidence)
        if a.value is None and first.value is None:
            evidence_score = 1.0  # nothing claimed, nothing to prove

        if a.value == b.value:
            agreement = 1.0
        elif kind == "name" and a.value and b.value:
            # A name is an identifier: one missing letter is a different name.
            agreement = 1.0 if fold(a.value) == fold(b.value) else 0.3
        elif (
            name == "pickup_location"
            and self._business.is_own_premises(a.value)
            and self._business.is_own_premises(b.value)
        ):
            agreement = 1.0  # "your garage", "su garaje", "their garage": the same place
        elif kind == "text" and a.value and b.value:
            left, right = fold(a.value), fold(b.value)
            ratio = SequenceMatcher(None, left, right).ratio()
            if left == right:
                agreement = 1.0
            elif left in right or right in left:
                agreement = 0.9
            else:
                agreement = ratio if ratio >= 0.9 else 0.3
        else:
            agreement = 0.3
        signals = {
            "format": a.format,
            "evidence": round(evidence_score, 3),
            "support": a.support,
            "agreement": round(agreement, 3),
        }
        if required:
            signals["present"] = 0.0 if a.value is None else 1.0
        return ExtractedField(
            name=name,
            value=a.value,
            evidence=first.evidence,
            span=located.span,
            box=located.box,
            confidence=round(min(signals.values()), 3),
            signals=signals,
        )

    def _normalise(
        self,
        kind: Kind,
        quoted: Quoted,
        reference: date,
        year_inference: bool,
        prefer_last: bool = False,
    ) -> _Normal:
        """Bring a model value to its canonical form and check it against its own evidence."""
        raw = quoted.value.strip() if quoted.value else None
        evidence = quoted.evidence or ""
        if raw is None and kind not in ("date", "time"):
            return _Normal(None, 1.0, 1.0)

        if kind == "date":
            model = to_date(raw)
            found = parse_dates(evidence, reference) if evidence else []
            if not year_inference:
                found = [d for d in found if d.year_stated]
            if raw is None and not found:
                return _Normal(None, 1.0, 1.0)
            if len(found) == 1:
                code = found[0]
                weekday = 1.0 if code.weekday_consistent else 0.2
                if model is None:
                    return _Normal(code.value.isoformat(), 1.0, min(0.8, weekday))
                if model == code.value:
                    return _Normal(model.isoformat(), 1.0, weekday)
                return _Normal(code.value.isoformat(), 1.0, 0.2)
            if model is None:
                return _Normal(None, 0.0 if raw else 1.0, 0.2)
            if found:
                # Several dates in the quote (a range): pick-up is the first, return the last.
                expected = found[-1] if prefer_last else found[0]
                if model == expected.value:
                    return _Normal(model.isoformat(), 1.0, 0.9)
                inside = any(d.value == model for d in found)
                return _Normal(model.isoformat(), 1.0, 0.6 if inside else 0.2)
            return _Normal(model.isoformat(), 1.0, 0.4)  # e.g. "next Saturday": the model computed

        if kind == "time":
            model_time = to_time(raw)
            times = parse_times(evidence) if evidence else []
            if raw is None and not times:
                return _Normal(None, 1.0, 1.0)
            if model_time is None:
                return _Normal(times[0] if len(times) == 1 else None, 1.0 if times else 0.0, 0.5)
            if model_time in times:
                if len(times) == 1:
                    return _Normal(model_time, 1.0, 1.0)
                # "from 12:30 to 20:30": pick-up is the first time quoted, return the last.
                expected_time = times[-1] if prefer_last else times[0]
                return _Normal(model_time, 1.0, 0.9 if model_time == expected_time else 0.6)
            return _Normal(model_time, 1.0, 0.2 if times else 0.5)

        assert raw is not None
        if kind == "amount":
            amount = to_decimal(raw)
            if amount is None:
                return _Normal(raw, 0.0, 0.0)
            amounts = parse_amounts(evidence)
            support = 1.0 if amount in amounts else (0.5 if not amounts else 0.2)
            return _Normal(f"{amount:.2f}", 1.0, support)

        if kind == "phone":
            phone = normalise_phone(raw)
            if phone is None:
                return _Normal(raw, 0.0, 0.0)
            digits = re.sub(r"\D", "", evidence)
            return _Normal(phone, 1.0, 1.0 if phone.lstrip("+") in digits else 0.3)

        if kind == "car":
            by_value = self._business.match_car(raw)
            by_evidence = self._business.match_car(evidence)
            if by_value is None:
                return _Normal(None, 0.0, 0.0)  # not a car of ours: the words stay as evidence
            if by_evidence is None:
                return _Normal(by_value.code, 1.0, 0.7)
            return _Normal(by_value.code, 1.0, 1.0 if by_evidence.code == by_value.code else 0.2)

        if kind in ("purpose", "extra"):
            choices = {c.value for c in (Purpose if kind == "purpose" else Extra)}
            if raw not in choices:
                return _Normal(raw, 0.0, 0.0)
            words = _KEYWORDS.get(raw, ())
            hit = not words or any(word in evidence.lower() for word in words)
            return _Normal(raw, 1.0, 1.0 if hit else 0.6)

        if kind == "licence_number":
            number = re.sub(r"\s+", "", raw).upper()
            valid = re.match(self._business.licence_number_pattern, number) is not None
            return _Normal(
                number, 1.0 if valid else 0.0, 1.0 if _alnum(number) in _alnum(evidence) else 0.3
            )

        if kind == "classes":
            # Class codes are single letters; OCR often glues them together ("PT").
            letters = sorted(set(re.sub(r"[^A-Z]", "", raw.upper())))
            classes = " ".join(letters)
            printed = set(re.sub(r"[^A-Z]", "", evidence.upper()))
            inside = bool(letters) and all(letter in printed for letter in letters)
            return _Normal(classes, 1.0, 1.0 if inside else 0.3)

        # text and name
        value = " ".join(raw.split())
        evidence_tokens = set(_tokens(fold(evidence)))
        inside = all(token in evidence_tokens for token in _tokens(fold(value)))
        return _Normal(value, 1.0, 1.0 if inside else 0.5)
