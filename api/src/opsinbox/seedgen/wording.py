"""Wording of the synthetic e-mails by the seed model, verified against the facts.

The seed model receives a plain draft written by code and a list of protected phrases. Its
rewrite is accepted only if every protected phrase survived character for character, in order,
and no other date, time, car or suspicious instruction slipped in. Otherwise the plain draft is
kept. Either way the labels stay right, because the labels never depended on the model.
"""

import re
from dataclasses import dataclass
from datetime import date

from opsinbox.errors import ModelError
from opsinbox.extraction.parsing import parse_dates, parse_times
from opsinbox.extraction.suspicion import scan_text
from opsinbox.llm import ModelClient, ModelRequest
from opsinbox.records.business import Business

_SYSTEM = (
    "You rewrite short e-mails so they sound like a real person wrote them. You keep every fact. "
    "You output only the rewritten e-mail body: no subject line, no notes, no quotation marks "
    "around it, no placeholders in brackets."
)


@dataclass(frozen=True)
class WordingJob:
    """A draft to reword and the constraints the result must satisfy."""

    key: str
    language: str
    style: str
    draft: str
    phrases: tuple[str, ...]
    allowed_dates: frozenset[date]
    allowed_times: frozenset[str]
    allowed_car: str | None
    reference: date
    check_facts: bool = True


def problems(body: str, job: WordingJob, business: Business) -> list[str]:
    """Return why ``body`` cannot replace the draft; empty when it can."""
    found: list[str] = []
    if len(body) < 40 or len(body) > max(2500, 4 * len(job.draft)):
        found.append("length")
    if re.search(r"\[[^\]]{2,40}\]|^subject:", body, re.IGNORECASE | re.MULTILINE):
        found.append("placeholder or subject line")
    position = 0
    for phrase in job.phrases:
        index = body.find(phrase, position)
        if index < 0:
            found.append(f"missing or out of order: {phrase!r}")
            break
        position = index + len(phrase)
    if job.check_facts:
        for parsed in parse_dates(body, job.reference):
            if parsed.value not in job.allowed_dates:
                found.append(f"extra date {parsed.value}")
        for clock in parse_times(body):
            if clock not in job.allowed_times:
                found.append(f"extra time {clock}")
        lowered = body.lower()
        for car in business.cars:
            if car.code != job.allowed_car and re.search(
                rf"\b({re.escape(car.make.lower())}|{re.escape(car.model.lower())})\b", lowered
            ):
                found.append(f"other car {car.code}")
        if scan_text(body):
            found.append("reads like an instruction to the system")
    return found


def reword(client: ModelClient, model: str, job: WordingJob, business: Business) -> tuple[str, str]:
    """Return the body to use and where it came from (``model`` or ``template``)."""
    language = "Spanish" if job.language == "es" else "English"
    protected = "\n".join(f"- {phrase}" for phrase in job.phrases) or "- (none)"
    user = (
        f"Rewrite the e-mail below in {language}. Style: {job.style}.\n"
        "Keep each of these phrases EXACTLY as written, character for character, and in the same "
        f"order:\n{protected}\n"
        "Do not add any other date, time, price, phone number, car or request. "
        "Keep the same sender name at the end.\n\n"
        f"E-mail:\n{job.draft}"
    )
    for attempt in range(3):
        try:
            response = client.complete(
                ModelRequest(
                    model=model, system=_SYSTEM, user=user, temperature=0.8, max_tokens=600,
                    tag=f"seed:{job.key}:{attempt}",
                )
            )  # fmt: skip
        except ModelError:
            break
        body = response.text.strip().strip('"').strip()
        body = re.sub(r"^(here is|here's)[^\n]*:\s*\n", "", body, flags=re.IGNORECASE).strip()
        if not problems(body, job, business):
            return body, "model"
    return job.draft, "template"
