"""Price calculation. Decimal arithmetic in code: the model never computes money or dates."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from math import ceil

from pydantic import BaseModel

from opsinbox.records.business import Business, Car
from opsinbox.schemas import Extra

CENT = Decimal("0.01")


class QuoteLine(BaseModel):
    """One priced line of a quote."""

    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal


class Quote(BaseModel):
    """The price of a rental, fully itemised."""

    days: int
    high_season: bool
    lines: list[QuoteLine]
    subtotal: Decimal
    tax_rate: Decimal
    tax: Decimal
    total: Decimal
    deposit: Decimal
    balance: Decimal


def money(value: Decimal) -> Decimal:
    """Round to cents, half up."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def rental_days(pickup: datetime, returned: datetime) -> int:
    """Number of charged days: every started 24 hours counts, minimum one."""
    seconds = (returned - pickup).total_seconds()
    if seconds <= 0:
        raise ValueError("return must be after pick-up")
    return max(1, ceil(seconds / 86400))


def quote(
    business: Business, car: Car, pickup: datetime, returned: datetime, extras: list[Extra]
) -> Quote:
    """Price a rental from the price list.

    The season of the pick-up day sets the daily rate for the whole rental.
    """
    days = rental_days(pickup, returned)
    high = business.high_season.contains(pickup.date())
    rate = money(car.daily_rate * (business.high_season.multiplier if high else Decimal(1)))
    season = "high season" if high else "standard season"
    lines = [
        QuoteLine(
            description=f"{car.name}: daily hire, {season}",
            quantity=days,
            unit_price=rate,
            amount=money(rate * days),
        )
    ]
    for extra in sorted(set(extras)):
        price = business.extras[extra]
        quantity = days if price.per_day else 1
        lines.append(
            QuoteLine(
                description=price.label + (" (per day)" if price.per_day else ""),
                quantity=quantity,
                unit_price=price.price,
                amount=money(price.price * quantity),
            )
        )
    subtotal = money(sum((line.amount for line in lines), Decimal(0)))
    tax = money(subtotal * business.tax_rate)
    total = subtotal + tax
    deposit = money(total * business.deposit_rate)
    return Quote(
        days=days,
        high_season=high,
        lines=lines,
        subtotal=subtotal,
        tax_rate=business.tax_rate,
        tax=tax,
        total=total,
        deposit=deposit,
        balance=total - deposit,
    )
