"""The business definition: cars, price list and policy values, as typed data.

Loaded from ``seed/business.json`` for the demo. Everything the rules and the calculations need
about the business comes from here, never from literals in code.
"""

import json
import re
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from opsinbox.schemas import Extra


class Car(BaseModel):
    """A car of the fleet."""

    code: str
    year: int
    make: str
    model: str
    body: str
    colour: str
    daily_rate: Decimal
    aliases: list[str]

    @property
    def name(self) -> str:
        """Full display name, for example ``1961 Hartwell Kestrel``."""
        return f"{self.year} {self.make} {self.model}"


class ExtraPrice(BaseModel):
    """Price of an optional service."""

    label: str
    price: Decimal
    per_day: bool


class Season(BaseModel):
    """High season as month-day bounds (inclusive) and its price multiplier."""

    start: str
    end: str
    multiplier: Decimal

    def contains(self, day: date) -> bool:
        """Tell whether ``day`` falls in the season."""
        return self.start <= day.strftime("%m-%d") <= self.end


class Business(BaseModel):
    """The company the inbox works for."""

    name: str
    address: str
    email: str
    phone: str
    tax_id: str
    currency: str
    garage_location: str
    tax_rate: Decimal
    deposit_rate: Decimal
    high_season: Season
    minimum_driver_age: int
    required_licence_class: str
    price_tolerance: Decimal
    extras: dict[Extra, ExtraPrice]
    cars: list[Car]

    def car(self, code: str) -> Car | None:
        """Return the car with this code."""
        return next((c for c in self.cars if c.code == code), None)

    def match_car(self, text: str | None) -> Car | None:
        """Resolve the words a customer used to a car of the fleet, or None if unclear.

        Exact alias first (longest wins), then a close spelling. Two cars matching equally well
        is "unclear": the caller sends the case to review rather than guessing.
        """
        if not text:
            return None
        cleaned = _clean(text)
        scored: list[tuple[float, Car]] = []
        for car in self.cars:
            names = [*car.aliases, car.name, f"{car.make} {car.model}"]
            best = 0.0
            for alias in names:
                candidate = _clean(alias)
                if re.search(rf"\b{re.escape(candidate)}\b", cleaned):
                    best = max(best, 1.0 + len(candidate) / 100)
                else:
                    for word in cleaned.split():
                        if len(word) >= 5 and len(candidate.split()) == 1:
                            ratio = SequenceMatcher(None, word, candidate).ratio()
                            if ratio >= 0.84:
                                best = max(best, ratio)
            if best:
                scored.append((best, car))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]


def _clean(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def default_business_path() -> Path:
    """Location of the demo business file in the repository or the container."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "seed" / "business.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("seed/business.json not found")


@lru_cache
def load_business(path: Path | None = None) -> Business:
    """Load and validate the business definition."""
    source = path or default_business_path()
    return Business.model_validate(json.loads(source.read_text(encoding="utf-8")))
