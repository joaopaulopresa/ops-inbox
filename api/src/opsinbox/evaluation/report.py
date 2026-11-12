"""Write ``reports/evaluation.md`` from the evaluation JSON files.

python -m opsinbox.evaluation.report
"""

import json
from pathlib import Path
from typing import Any

from opsinbox.records.business import default_business_path

FIELD_NAMES = {
    "customer_name": "Customer name",
    "customer_phone": "Phone",
    "car": "Car",
    "pickup_date": "Pick-up date",
    "pickup_time": "Pick-up time",
    "return_date": "Return date",
    "return_time": "Return time",
    "pickup_location": "Pick-up place",
    "purpose": "Purpose",
    "extras": "Extras",
    "quoted_total": "Quoted total",
}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _load(reports: Path, name: str) -> dict[str, Any] | None:
    path = reports / f"evaluation-{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def render(reports: Path) -> str:
    """The report as Markdown."""
    held = _load(reports, "heldout")
    tuning = _load(reports, "tuning")
    ocr = _load(reports, "heldout-tesseract")
    spike = json.loads((reports / "reading_spike.json").read_text())
    if held is None or tuning is None:
        raise SystemExit("run the evaluation first (make evaluate-tuning && make evaluate)")
    op, top = held["operating_point"], tuning["operating_point"]
    lines = [
        "# Ops Inbox: evaluation report",
        "",
        f"Model `{held['model']}`, confidence threshold **{held['threshold']}** (chosen on the tuning "
        "split only). All numbers below come from `make evaluate`, which replays recorded model "
        "answers; no number was tuned on the held-out split.",
        "",
        "## Held-out set (100 e-mails, never used for tuning)",
        "",
        "| Measure | Held-out | Tuning |",
        "|---|---|---|",
        f"| Classification accuracy | {_pct(held['classification']['accuracy'])} "
        f"({held['classification']['scored']} scored) | {_pct(tuning['classification']['accuracy'])} |",
        f"| Handled without a person | {op['automatic']} of {held['emails']} "
        f"({_pct(op['straight_through_rate'])}) | {top['automatic']} of {tuning['emails']} "
        f"({_pct(top['straight_through_rate'])}) |",
        f"| ... of the e-mails that could be | {_pct(op['straight_through_of_clean'])} "
        f"| {_pct(top['straight_through_of_clean'])} |",
        f"| Wrong automatic e-mails | {op['erroneous_automatic']} ({op['errors_per_100_automatic']} per 100 "
        f"automatic) | {top['erroneous_automatic']} ({top['errors_per_100_automatic']} per 100) |",
        f"| Wrong fields in automatic e-mails | {op['wrong_fields_per_100_automatic']} per 100 "
        f"| {top['wrong_fields_per_100_automatic']} per 100 |",
        f"| Adversarial e-mails stopped | {held['adversarial']['caught']} of {held['adversarial']['total']} "
        f"| {tuning['adversarial']['caught']} of {tuning['adversarial']['total']} |",
        f"| Model cost per e-mail | ${held['cost']['per_email_usd']:.4f} | ${tuning['cost']['per_email_usd']:.4f} |",
        f"| Model calls per e-mail | {held['cost']['model_calls_per_email']} | {tuning['cost']['model_calls_per_email']} |",
        f"| Model time per e-mail | {held['cost']['model_seconds_per_email']} s | {tuning['cost']['model_seconds_per_email']} s |",
        "",
        "## Field accuracy on the held-out set",
        "",
        "| Field | Correct | Accuracy |",
        "|---|---|---|",
    ]
    for name, value in held["fields"].items():
        label = name.replace("licence.", "Licence: ").replace("invoice.", "Invoice: ")
        label = FIELD_NAMES.get(label, label.replace("_", " "))
        lines.append(
            f"| {label} | {value['correct']} / {value['total']} | {_pct(value['accuracy'])} |"
        )
    lines += [
        "",
        "## Classification (held-out confusion matrix, rows are the truth)",
        "",
    ]
    categories = sorted(held["classification"]["confusion"])
    lines.append("| | " + " | ".join(categories) + " |")
    lines.append("|---" * (len(categories) + 1) + "|")
    for truth in categories:
        row = held["classification"]["confusion"][truth]
        lines.append(f"| {truth} | " + " | ".join(str(row.get(c, 0)) for c in categories) + " |")
    lines += [
        "",
        "## Straight-through rate against errors (held-out)",
        "",
        "| Threshold | Automatic | Wrong automatic | Per 100 automatic |",
        "|---|---|---|---|",
    ]
    for point in held["curve"][::2]:
        lines.append(
            f"| {point['threshold']} | {point['automatic']} | {point['erroneous_automatic']} "
            f"| {point['errors_per_100_automatic']} |"
        )
    lines += [
        "",
        "## Reading: vision model against local OCR",
        "",
        "Spike on ten degraded licences (`reports/reading_spike.json`):",
        "",
        "| Reader | Field accuracy | Scans | Photos | Confident and wrong |",
        "|---|---|---|---|---|",
    ]
    for name, engine in spike["engines"].items():
        lines.append(
            f"| {name.replace('_', ' ')} | {_pct(engine['field_accuracy'])} "
            f"| {_pct(engine['field_accuracy_scan'])} | {_pct(engine['field_accuracy_photo'])} "
            f"| {engine['confident_and_wrong']} |"
        )
    if ocr is not None:
        lines += [
            "",
            "Held-out attachments, by quality (vision reader against local OCR only):",
            "",
            "| Attachment | Vision | Local OCR |",
            "|---|---|---|",
        ]
        for key, value in held["fields_by_attachment_quality"].items():
            other = ocr["fields_by_attachment_quality"].get(key, {})
            lines.append(f"| {key} | {_pct(value['accuracy'])} | {_pct(other.get('accuracy'))} |")
    lines += [
        "",
        "## What limits these numbers",
        "",
        "- **Phone photos.** The second extraction pass reads local OCR text on purpose, so that a vision "
        "misread shows up as a disagreement. On phone photos local OCR is weak and the readings disagree "
        "often, which stops correct e-mails for review. This is most of the gap between the tuning and "
        "the held-out straight-through rates.",
        "- **A flaw in the generated set.** Clean booking requests got their licence format by position "
        "(clean, scan, photo in turn) and the held-out split takes every third e-mail, so every held-out "
        "clean booking came with a phone photo, while the tuning clean bookings had none. Found after the "
        "held-out run; reported rather than reshuffled.",
        "- **The wrong automatic e-mail.** One held-out customer question was sorted as not relevant "
        "and archived without a person (no adversarial e-mail got through). Every decision is in "
        "`reports/predictions-heldout.jsonl`.",
        "- **Synthetic data.** E-mails were worded by a small model around facts set by code; real mail is "
        "messier. The set has 300 e-mails, so one e-mail moves a held-out percentage by a whole point.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """Write the report next to the JSON files."""
    reports = default_business_path().parent.parent / "reports"
    (reports / "evaluation.md").write_text(render(reports), encoding="utf-8")
    print(reports / "evaluation.md")


if __name__ == "__main__":
    main()
