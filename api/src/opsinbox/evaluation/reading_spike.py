"""Reading spike: local OCR against the vision model on ten degraded licences.

    python -m opsinbox.evaluation.reading_spike

Writes ``reports/reading_spike.json``. Model responses are recorded under ``seed/recordings``,
so a second run costs nothing.
"""

import json
import random
import re
import time
from datetime import date

from opsinbox.extraction import Extractor, Usage
from opsinbox.extraction.extractor import fold
from opsinbox.llm import ModelClient, ModelRequest, ModelResponse, OpenRouterClient, RecordingClient
from opsinbox.reading import Reader, TesseractReader, VisionReader
from opsinbox.records.business import default_business_path, load_business
from opsinbox.seedgen.licence import LicenceData, format_licence_date, licence_image
from opsinbox.seedgen.people import make_licence, make_person
from opsinbox.settings import get_settings

TODAY = date(2026, 11, 2)
FIELDS = ("holder_name", "licence_number", "date_of_birth", "expires_on", "classes")
CONFIDENT = 0.8


class _TranscriptionCost:
    """Adds up what the vision transcriptions of this spike cost (recorded cost on replay)."""

    def __init__(self, inner: ModelClient) -> None:
        self._inner = inner
        self.cost = 0.0

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._inner.complete(request)
        if request.tag == "transcribe":
            self.cost += response.cost_usd
        return response


def truth(data: LicenceData) -> dict[str, str]:
    """Ground truth in the canonical form the extractor produces."""
    return {
        "holder_name": data.holder_name,
        "licence_number": data.licence_number,
        "date_of_birth": data.date_of_birth.isoformat(),
        "expires_on": data.expires_on.isoformat(),
        "classes": " ".join(sorted(data.classes)),
    }


def printed(data: LicenceData) -> dict[str, str]:
    """The characters printed on the card for each field."""
    return {
        "holder_name": data.holder_name.upper(),
        "licence_number": data.licence_number,
        "date_of_birth": format_licence_date(data.date_of_birth),
        "expires_on": format_licence_date(data.expires_on),
        "classes": " ".join(data.classes),
    }


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def same(field: str, expected: str, got: str | None) -> bool:
    """Compare a canonical value with the truth (names are case-insensitive)."""
    if got is None:
        return False
    return fold(got) == fold(expected) if field == "holder_name" else got == expected


def main() -> None:
    """Run the spike and write the report."""
    settings = get_settings()
    root = default_business_path().parent.parent
    inner: ModelClient | None = None
    if settings.openrouter_api_key:
        inner = OpenRouterClient(
            settings.openrouter_api_key,
            settings.openrouter_base_url,
            settings.llm_timeout_seconds,
            settings.llm_max_retries,
        )
    recorder = RecordingClient(inner, root / "seed" / "recordings")
    transcription = _TranscriptionCost(recorder)
    client: ModelClient = transcription
    business = load_business()
    extractor = Extractor(client, settings.llm_model, business)
    ocr = TesseractReader()
    vision = VisionReader(client, settings.llm_model)
    # name -> (reader for the first pass, reader for the second pass or None for the same text)
    readers: dict[str, tuple[Reader, Reader | None]] = {
        "tesseract": (ocr, None),
        "vision": (vision, None),
        "vision_checked_by_tesseract": (vision, ocr),
        "tesseract_checked_by_vision": (ocr, vision),
    }

    rng = random.Random(20261102)
    samples = []
    for index in range(10):
        person = make_person(rng, "es" if index % 3 == 2 else "en")
        data = make_licence(rng, person, TODAY)
        kind = "scan" if index % 2 == 0 else "photo"
        samples.append((kind, data, licence_image(data, kind, seed=1000 + index)))  # type: ignore[arg-type]

    report: dict[str, object] = {
        "model": settings.llm_model,
        "samples": len(samples),
        "engines": {},
    }
    for name, (reader, checker) in readers.items():
        usage = Usage()
        stats = {"legible": 0, "correct": 0, "confident": 0, "confident_wrong": 0, "fields": 0}
        by_kind = {"scan": [0, 0], "photo": [0, 0]}
        read_seconds = 0.0
        errors: list[dict[str, str | None]] = []
        for kind, data, image in samples:
            started = time.monotonic()
            before = recorder.calls + recorder.replays
            document = reader.read(image, "image/jpeg")
            read_seconds += time.monotonic() - started
            usage.calls += recorder.calls + recorder.replays - before
            second = checker.read(image, "image/jpeg") if checker else None
            extraction = extractor.licence(document, "licence", TODAY, usage, second)
            expected, on_card = truth(data), printed(data)
            for field in FIELDS:
                got = extraction.get(field)
                assert got is not None
                ok = same(field, expected[field], got.value)
                stats["fields"] += 1
                stats["legible"] += _squash(on_card[field]) in _squash(document.text)
                stats["correct"] += ok
                by_kind[kind][0] += ok
                by_kind[kind][1] += 1
                if got.confidence >= CONFIDENT:
                    stats["confident"] += 1
                    stats["confident_wrong"] += not ok
                if not ok:
                    errors.append(
                        {
                            "kind": kind,
                            "field": field,
                            "expected": expected[field],
                            "got": got.value,
                            "confidence": str(got.confidence),
                        }
                    )
        total = stats["fields"]
        report["engines"][name] = {  # type: ignore[index]
            "fields": total,
            "text_contains_printed_value": round(stats["legible"] / total, 3),
            "field_accuracy": round(stats["correct"] / total, 3),
            "field_accuracy_scan": round(by_kind["scan"][0] / by_kind["scan"][1], 3),
            "field_accuracy_photo": round(by_kind["photo"][0] / by_kind["photo"][1], 3),
            "confident_fields": stats["confident"],
            "confident_and_wrong": stats["confident_wrong"],
            "read_seconds_per_document": round(read_seconds / len(samples), 2),
            "extraction_cost_usd_per_document": round(usage.cost_usd / len(samples), 6),
            "errors": errors,
        }
    vision_cost = transcription.cost
    report["vision_transcription_cost_usd_per_document"] = round(vision_cost / len(samples), 6)
    out = root / "reports" / "reading_spike.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
