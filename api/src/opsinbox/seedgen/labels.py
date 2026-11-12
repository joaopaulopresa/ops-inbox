"""The ground truth stored next to every synthetic e-mail."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from opsinbox.schemas import Category


class AttachmentLabel(BaseModel):
    """What an attachment is and how it was degraded."""

    filename: str
    kind: Literal["licence", "invoice", "other"]
    degradation: Literal["clean", "scan", "photo", "pdf"]


class Label(BaseModel):
    """Ground truth of one e-mail. Values are in the canonical form the extractor produces."""

    id: str
    split: Literal["tuning", "heldout"] = "tuning"
    scenario: str
    category: Category
    #: False for the few e-mails whose category is a matter of opinion (phishing, impersonation).
    score_category: bool = True
    language: str
    #: ``auto``: nothing is wrong, it may run without a person. ``review``: a person must see it.
    expected_route: Literal["auto", "review"]
    #: Why a person must see it (rule or check identifiers); empty for ``auto``.
    reasons: list[str] = Field(default_factory=list)
    adversarial: bool = False
    booking_reference: str | None = None
    booking: dict[str, str | None] | None = None
    extras: list[str] = Field(default_factory=list)
    licence: dict[str, str] | None = None
    invoice: dict[str, str] | None = None
    attachments: list[AttachmentLabel] = Field(default_factory=list)
    wording: Literal["model", "template"] = "template"
    eml: str = ""


def load_labels(directory: Path) -> list[Label]:
    """Read ``labels.jsonl`` of a split directory."""
    lines = (directory / "labels.jsonl").read_text(encoding="utf-8").splitlines()
    return [Label.model_validate_json(line) for line in lines if line.strip()]
