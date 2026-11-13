"""Evaluate the processing steps on the labelled set.

    python -m opsinbox.evaluation.harness --split tuning --limit 10   # try a few first
    python -m opsinbox.evaluation.harness --split tuning              # all 200 tuning e-mails
    python -m opsinbox.evaluation.harness --split heldout --replay    # the reported numbers

Every model answer is recorded under ``seed/recordings``; ``--replay`` refuses to call the model,
so the reported numbers are reproduced without a key. Predictions are written to
``reports/predictions-<split>[-<reader>].jsonl`` and the metrics to ``reports/evaluation-*.json``.
"""

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from opsinbox.evaluation.compare import booking_errors, invoice_errors, licence_errors
from opsinbox.extraction import Extractor
from opsinbox.llm import ModelClient, OpenRouterClient, RecordingClient
from opsinbox.mail import parse_mail
from opsinbox.reading import CachedReader, Reader, TesseractReader, VisionReader
from opsinbox.records.business import default_business_path, load_business
from opsinbox.records.view import WorldRecords
from opsinbox.rules import load_rules
from opsinbox.seedgen.labels import Label, load_labels
from opsinbox.seedgen.world import World
from opsinbox.settings import get_settings
from opsinbox.workflow.processing import (
    CaseState,
    IncomingAttachment,
    IncomingMail,
    Processor,
    decide,
)

THRESHOLDS = [round(0.05 * i, 2) for i in range(0, 21)]
#: Operating point: the most automation with at most this many erroneous automatic e-mails
#: per 100, chosen on the tuning split only.
TARGET_ERRORS_PER_100 = 1.0
THRESHOLD_MARGIN = 0.1


def _loader(data: bytes) -> Callable[[], bytes]:
    return lambda: data


def mail_from_eml(key: str, raw: bytes) -> IncomingMail:
    """Build the processing input from raw message bytes."""
    parsed = parse_mail(raw)
    attachments = [
        IncomingAttachment(
            id=f"{key}/{index}",
            filename=a.filename,
            content_type=a.content_type,
            load=_loader(a.data),
        )
        for index, a in enumerate(parsed.attachments)
    ]
    assert parsed.sent_at is not None
    return IncomingMail(
        id=key,
        subject=parsed.subject,
        body=parsed.body_text,
        from_address=parsed.from_address,
        from_name=parsed.from_name,
        reply_to=parsed.reply_to,
        sent_at=parsed.sent_at.astimezone(UTC),
        in_reply_to=parsed.in_reply_to,
        references=parsed.references,
        attachments=attachments,
    )


@dataclass
class Scored:
    """One e-mail's prediction next to its label."""

    label: Label
    state: CaseState
    seconds: float
    #: Wrong fields by name, among the fields that gate automation and are labelled.
    errors: dict[str, tuple[Any, Any]]
    scored_fields: dict[str, int]

    def category_ok(self) -> bool:
        """Whether the predicted category is the labelled one."""
        return self.state.category == self.label.category

    def route_at(self, threshold: float) -> str:
        """The route this e-mail takes at ``threshold``."""
        return decide(self.state, threshold)[0]


def score(label: Label, state: CaseState) -> tuple[dict[str, tuple[Any, Any]], dict[str, int]]:
    """Compare extracted values with the ground truth. Returns errors and counted fields."""
    errors: dict[str, tuple[Any, Any]] = {}
    counted: dict[str, int] = {}
    if label.booking is not None and state.booking is not None and state.category == label.category:
        e, c = booking_errors(label, state.booking)
        errors |= e
        counted |= c
    if label.licence is not None and state.licences and state.category == label.category:
        e, c = licence_errors(label.licence, state.licences[0])
        errors |= {f"licence.{k}": v for k, v in e.items()}
        counted |= {f"licence.{k}": v for k, v in c.items()}
    if label.invoice is not None and state.invoices and state.category == label.category:
        e, c = invoice_errors(label.invoice, state.invoices[0])
        errors |= {f"invoice.{k}": v for k, v in e.items()}
        counted |= {f"invoice.{k}": v for k, v in c.items()}
    return errors, counted


def automatic_error(item: Scored, threshold: float) -> bool:
    """An automatic e-mail is an error if it should have stopped, or anything it did is wrong."""
    if item.route_at(threshold) != "auto":
        return False
    if not item.category_ok() and item.label.score_category:
        return True
    if item.label.expected_route == "review":
        return True
    return any(not name.endswith("purpose") for name in item.errors)


def metrics(items: list[Scored], threshold: float) -> dict[str, Any]:
    """Everything the report shows, at one threshold, plus the curve."""
    total = len(items)
    scored_categories = [i for i in items if i.label.score_category]
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scored_categories:
        confusion[item.label.category.value][
            item.state.category.value if item.state.category else "none"
        ] += 1

    per_field: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in items:
        for name in item.scored_fields:
            per_field[name][1] += 1
            per_field[name][0] += name not in item.errors
    by_degradation: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in items:
        for attachment in item.label.attachments:
            for name in item.scored_fields:
                if name.startswith(attachment.kind + "."):
                    by_degradation[f"{attachment.kind}:{attachment.degradation}"][1] += 1
                    by_degradation[f"{attachment.kind}:{attachment.degradation}"][0] += (
                        name not in item.errors
                    )

    def at(t: float) -> dict[str, Any]:
        auto = [i for i in items if i.route_at(t) == "auto"]
        wrong = [i for i in auto if automatic_error(i, t)]
        wrong_fields = sum(
            len([n for n in i.errors if not n.endswith("purpose")])
            for i in auto
            if i.category_ok() and i.label.expected_route == "auto"
        )
        should_auto = [i for i in items if i.label.expected_route == "auto"]
        return {
            "threshold": t,
            "automatic": len(auto),
            "straight_through_rate": round(len(auto) / total, 4) if total else 0.0,
            "straight_through_of_clean": round(
                sum(i.route_at(t) == "auto" for i in should_auto) / len(should_auto), 4
            )
            if should_auto
            else 0.0,
            "erroneous_automatic": len(wrong),
            "errors_per_100_automatic": round(100 * len(wrong) / len(auto), 2) if auto else 0.0,
            "wrong_fields_per_100_automatic": round(100 * wrong_fields / len(auto), 2)
            if auto
            else 0.0,
            "should_have_stopped_but_automatic": sum(
                i.label.expected_route == "review" for i in auto
            ),
        }

    adversarial = [i for i in items if i.label.adversarial]
    caught = [i for i in adversarial if i.route_at(threshold) == "review"]
    reason_hits = [
        i
        for i in items
        if i.label.expected_route == "review"
        and i.route_at(threshold) == "review"
        and i.label.reasons
        and any(r.code in i.label.reasons for r in decide(i.state, threshold)[1])
    ]
    review_ok = [
        i for i in items if i.label.expected_route == "review" and i.route_at(threshold) == "review"
    ]
    with_reasons = len([i for i in review_ok if i.label.reasons])
    costs = [i.state.cost_usd for i in items]
    return {
        "emails": total,
        "threshold": threshold,
        "classification": {
            "scored": len(scored_categories),
            "accuracy": round(
                sum(i.category_ok() for i in scored_categories) / len(scored_categories), 4
            )
            if scored_categories
            else None,
            "confusion": {k: dict(v) for k, v in sorted(confusion.items())},
        },
        "fields": {
            name: {"correct": c, "total": n, "accuracy": round(c / n, 4)}
            for name, (c, n) in sorted(per_field.items())
        },
        "fields_by_attachment_quality": {
            k: {"correct": c, "total": n, "accuracy": round(c / n, 4)}
            for k, (c, n) in sorted(by_degradation.items())
        },
        "routing": {
            "expected_auto": sum(i.label.expected_route == "auto" for i in items),
            "expected_review": sum(i.label.expected_route == "review" for i in items),
            "confusion": dict(
                Counter(f"{i.label.expected_route}->{i.route_at(threshold)}" for i in items)
            ),
            "expected_reason_found": f"{len(reason_hits)}/{with_reasons}",
        },
        "operating_point": at(threshold),
        "curve": [at(t) for t in THRESHOLDS],
        "adversarial": {
            "total": len(adversarial),
            "caught": len(caught),
            "catch_rate": round(len(caught) / len(adversarial), 4) if adversarial else None,
            "missed": [i.label.id for i in adversarial if i not in caught],
        },
        "cost": {
            "total_usd": round(sum(costs), 4),
            "per_email_usd": round(sum(costs) / total, 6) if total else 0.0,
            "model_calls_per_email": round(sum(i.state.model_calls for i in items) / total, 2)
            if total
            else 0.0,
            "model_seconds_per_email": round(sum(i.state.model_seconds for i in items) / total, 2)
            if total
            else 0.0,
            "wall_seconds_per_email": round(sum(i.seconds for i in items) / total, 2)
            if total
            else 0.0,
        },
    }


def choose_threshold(items: list[Scored]) -> float:
    """The lowest threshold from which every higher one stays within the target, plus a margin.

    Confidence values are coarse, so the curve has cliffs; the margin keeps the operating point
    off the edge of one.
    """
    ok = []
    for t in THRESHOLDS:
        auto = [i for i in items if i.route_at(t) == "auto"]
        wrong = sum(automatic_error(i, t) for i in auto)
        ok.append(not auto or 100 * wrong / len(auto) <= TARGET_ERRORS_PER_100)
    for index, t in enumerate(THRESHOLDS):
        if all(ok[index:]):
            return min(1.0, round(t + THRESHOLD_MARGIN, 2))
    return 1.0


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Evaluate on the labelled set")
    parser.add_argument("--split", choices=["tuning", "heldout"], required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="", help="comma-separated e-mail ids")
    parser.add_argument("--replay", action="store_true", help="never call the model")
    parser.add_argument("--reader", choices=["vision", "tesseract"], default="vision")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    settings = get_settings()
    seed = default_business_path().parent
    root = seed.parent
    business = load_business()
    world = World.model_validate_json((seed / "dataset" / "world.json").read_text())
    inner: ModelClient | None = None
    if settings.openrouter_api_key and not args.replay:
        inner = OpenRouterClient(
            settings.openrouter_api_key,
            settings.openrouter_base_url,
            settings.llm_timeout_seconds,
            settings.llm_max_retries,
        )
    client = RecordingClient(inner, seed / "recordings", mode="replay" if args.replay else "auto")
    ocr: Reader = CachedReader(TesseractReader(), "tesseract", seed / "readings")
    reader: Reader = VisionReader(client, settings.llm_model) if args.reader == "vision" else ocr
    processor = Processor(
        Extractor(client, settings.llm_model, business),
        reader,
        ocr,
        business,
        load_rules(),
        WorldRecords(world),
        threshold=args.threshold if args.threshold is not None else 0.8,
    )

    directory = seed / "dataset" / args.split
    labels = load_labels(directory)
    if args.only:
        wanted = set(args.only.split(","))
        labels = [label for label in labels if label.id in wanted]
    if args.limit:
        labels = labels[: args.limit]

    def run(label: Label) -> Scored:
        started = time.monotonic()
        mail = mail_from_eml(label.id, (directory / label.eml).read_bytes())
        state = processor.run(mail)
        errors, counted = score(label, state)
        return Scored(label, state, time.monotonic() - started, errors, counted)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        items = list(pool.map(run, labels))

    threshold = args.threshold
    if threshold is None:
        if args.split == "tuning":
            threshold = choose_threshold(items)
        else:
            chosen = root / "reports" / "threshold.json"  # the committed choice, made on tuning
            threshold = json.loads(chosen.read_text())["threshold"] if chosen.exists() else 0.8
    result = metrics(items, threshold)
    result |= {
        "split": args.split,
        "reader": args.reader,
        "model": settings.llm_model,
        "new_model_calls": client.calls,
        "replayed_calls": client.replays,
    }

    reports = Path(os.environ.get("OPSINBOX_REPORTS_DIR") or root / "reports")
    reports.mkdir(exist_ok=True)
    suffix = args.split + ("" if args.reader == "vision" else f"-{args.reader}")
    partial = bool(args.limit or args.only)
    if not partial:
        with (reports / f"predictions-{suffix}.jsonl").open("w", encoding="utf-8") as out:
            for item in items:
                out.write(
                    json.dumps(
                        {
                            "id": item.label.id,
                            "scenario": item.label.scenario,
                            "expected_route": item.label.expected_route,
                            "expected_category": item.label.category.value,
                            "state": item.state.model_dump(mode="json", exclude={"documents"}),
                            "errors": {k: [str(a), str(b)] for k, (a, b) in item.errors.items()},
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        (reports / f"evaluation-{suffix}.json").write_text(json.dumps(result, indent=1) + "\n")
        if args.split == "tuning" and args.reader == "vision" and args.threshold is None:
            (reports / "threshold.json").write_text(
                json.dumps(
                    {
                        "threshold": threshold,
                        "chosen_on": "tuning",
                        "target_errors_per_100_automatic": TARGET_ERRORS_PER_100,
                    },
                    indent=1,
                )
                + "\n"
            )
    summary = {
        k: result[k]
        for k in (
            "emails",
            "threshold",
            "classification",
            "operating_point",
            "adversarial",
            "cost",
            "routing",
            "new_model_calls",
        )
    }
    summary["classification"] = {
        k: v for k, v in result["classification"].items() if k != "confusion"
    }
    print(json.dumps(summary, indent=1))
    print("fields:", {k: v["accuracy"] for k, v in result["fields"].items()})
    for item in items:
        state = item.state
        mark = "ok" if item.route_at(threshold) == item.label.expected_route else "ROUTE"
        print(
            f"{item.label.id} {item.label.scenario:34s} {item.label.expected_route:6s}->"
            f"{item.route_at(threshold):6s} {mark:5s} cat={state.category}"
            f" reasons={[r.code for r in decide(state, threshold)[1]]} errors={item.errors}"
        )


if __name__ == "__main__":
    main()
