"""A scripted model and reader for workflow tests: no network, deterministic answers."""

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from opsinbox.errors import ModelError
from opsinbox.llm import ModelRequest, ModelResponse
from opsinbox.reading import DocumentText


def quoted(value: str | None, evidence: str | None = None) -> dict[str, str | None]:
    return {"value": value, "evidence": evidence if evidence is not None else value}


@dataclass
class Script:
    """What the fake model answers for one e-mail, found by a marker in its subject."""

    category: str = "new_booking"
    booking: dict[str, Any] = field(default_factory=dict)
    licence: dict[str, Any] = field(default_factory=dict)
    bypass: bool = False


NULL = {"value": None, "evidence": None}
BOOKING_KEYS = (
    "customer_name",
    "customer_phone",
    "car",
    "pickup_date",
    "pickup_time",
    "return_date",
    "return_time",
    "pickup_location",
    "purpose",
    "quoted_total",
)
LICENCE_KEYS = ("holder_name", "licence_number", "date_of_birth", "expires_on", "classes")


class ScriptedModel:
    """Answers by schema name, using the script whose marker appears in the request."""

    def __init__(self, scripts: dict[str, Script]) -> None:
        self.scripts = scripts
        self.calls: list[str] = []
        self.fail_on: set[str] = set()
        self._lock = threading.Lock()

    def _script(self, request: ModelRequest) -> Script:
        for marker, script in self.scripts.items():
            if marker in request.user:
                return script
        return next(iter(self.scripts.values()))

    def complete(self, request: ModelRequest) -> ModelResponse:
        step = request.tag.split(":")[0] if request.tag else "text"
        with self._lock:
            self.calls.append(request.tag or request.schema_name or "text")
            if step in self.fail_on:
                self.fail_on.discard(step)
                raise ModelError(f"scripted failure in {step}")
        script = self._script(request)
        name = request.schema_name
        if name == "ClassificationRead":
            answer: dict[str, Any] = {
                "category": script.category,
                "language": "en",
                "booking_reference": None,
                "asks_to_bypass_process": script.bypass,
                "bypass_evidence": None,
            }
        elif name == "BookingRead":
            answer = {k: script.booking.get(k, NULL) for k in BOOKING_KEYS}
            answer["extras"] = script.booking.get("extras", [])
        elif name == "LicenceRead":
            answer = {k: script.licence.get(k, NULL) for k in LICENCE_KEYS}
        elif name == "ScreenRead":
            answer = {"asks_to_bypass_process": False, "bypass_evidence": None}
        else:  # drafting: repeat the facts so the checks pass
            facts = re.findall(r"^- (.*)$", request.user, re.MULTILINE)
            return ModelResponse(
                text="Dear customer,\n\n" + "\n".join(facts) + "\n\nKind regards,\nLarkspur"
            )
        return ModelResponse(text=json.dumps(answer))


class FixedReader:
    """Reads every attachment as the same text."""

    def __init__(self, text: str) -> None:
        self.text = text

    def read(self, data: bytes, content_type: str) -> DocumentText:
        return DocumentText(self.text, [], engine="fixed")


LICENCE_TEXT = """REPUBLIC OF NORVALLE
DRIVING LICENCE
HOLDER
ADA QUILL
LICENCE NO.
NV-482913-KD
DATE OF BIRTH
14 MAR 1988
VALID UNTIL
{expires}
CLASSES
M P"""


def licence_script(expires_iso: str, expires_printed: str) -> dict[str, Any]:
    return {
        "holder_name": quoted("ADA QUILL"),
        "licence_number": quoted("NV-482913-KD"),
        "date_of_birth": quoted("1988-03-14", "14 MAR 1988"),
        "expires_on": quoted(expires_iso, expires_printed),
        "classes": quoted("M P"),
    }


BOOKING_BODY = (
    "Hello,\n\nI would like to hire the Aldermoor Sprite for our wedding. We would pick it up on "
    "{pickup} at 10:00 and bring it back on {pickup} at 18:00. We will collect it from your "
    "garage. My licence is attached.\n\nThanks,\nAda Quill"
)


def booking_script(pickup_text: str, pickup_iso: str) -> dict[str, Any]:
    return {
        "customer_name": quoted("Ada Quill"),
        "car": quoted("Aldermoor Sprite"),
        "pickup_date": quoted(pickup_iso, f"pick it up on {pickup_text}"),
        "pickup_time": quoted("10:00", f"{pickup_text} at 10:00"),
        "return_date": quoted(pickup_iso, f"bring it back on {pickup_text}"),
        "return_time": quoted("18:00", f"{pickup_text} at 18:00"),
        "pickup_location": quoted("your garage"),
        "purpose": quoted("wedding", "our wedding"),
        "quoted_total": NULL,
    }
