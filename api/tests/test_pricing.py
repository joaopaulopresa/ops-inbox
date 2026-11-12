"""Price list arithmetic and car matching."""

from datetime import datetime
from decimal import Decimal

import pytest

from opsinbox.records.business import load_business
from opsinbox.records.pricing import quote, rental_days
from opsinbox.schemas import Extra

BUSINESS = load_business()


def at(day: str, time: str = "10:00") -> datetime:
    return datetime.fromisoformat(f"{day}T{time}")


@pytest.mark.parametrize(
    ("pickup", "returned", "days"),
    [
        (at("2027-03-06", "10:00"), at("2027-03-06", "18:00"), 1),
        (at("2027-03-06", "10:00"), at("2027-03-07", "10:00"), 1),
        (at("2027-03-06", "10:00"), at("2027-03-07", "10:01"), 2),
        (at("2027-03-06", "10:00"), at("2027-03-09", "09:00"), 3),
    ],
)
def test_every_started_day_is_charged(pickup: datetime, returned: datetime, days: int) -> None:
    assert rental_days(pickup, returned) == days


def test_return_before_pickup_is_refused() -> None:
    with pytest.raises(ValueError):
        rental_days(at("2027-03-06"), at("2027-03-05"))


def test_standard_season_quote_with_extras() -> None:
    car = BUSINESS.car("SPRITE")
    assert car is not None
    result = quote(
        BUSINESS, car, at("2027-03-06"), at("2027-03-08"), [Extra.DELIVERY, Extra.CHAUFFEUR]
    )
    # 2 days x 260 + chauffeur 2 x 180 + delivery 90 = 970; VAT 20% = 194; total 1164
    assert (result.days, result.high_season) == (2, False)
    assert result.subtotal == Decimal("970.00")
    assert result.tax == Decimal("194.00")
    assert result.total == Decimal("1164.00")
    assert result.deposit == Decimal("349.20")
    assert result.balance == Decimal("814.80")
    assert sum(line.amount for line in result.lines) == result.subtotal


def test_high_season_multiplier_applies_from_the_pickup_day() -> None:
    car = BUSINESS.car("KESTREL")
    assert car is not None
    result = quote(BUSINESS, car, at("2027-06-12"), at("2027-06-12", "20:00"), [])
    assert result.high_season and result.lines[0].unit_price == Decimal("384.00")
    assert result.total == Decimal("460.80")


@pytest.mark.parametrize(
    ("words", "code"),
    [
        ("the Kestrel", "KESTREL"),
        ("1965 Aldermoor Sprite", "SPRITE"),
        ("your Hartwell convertible", "KESTREL"),
        ("the Kestral", "KESTREL"),  # misspelt
        ("Marlowe GT", "MARLOWE"),
        ("the Lindqvist 1800", "LINDQVIST"),
        ("a nice old car", None),
        ("", None),
    ],
)
def test_car_matching(words: str, code: str | None) -> None:
    car = BUSINESS.match_car(words)
    assert (car.code if car else None) == code
