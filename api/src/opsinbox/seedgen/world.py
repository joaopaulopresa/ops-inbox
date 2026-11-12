"""The world before the test e-mails arrive: existing bookings, suppliers and paid invoices.

Every e-mail of the labelled set is judged against this same starting state, so each can be
evaluated on its own and the results do not depend on processing order.
"""

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from pydantic import BaseModel

from opsinbox.records.business import Business
from opsinbox.records.pricing import quote
from opsinbox.schemas import Extra
from opsinbox.seedgen.people import Person, make_person

#: E-mails of the set are "sent" in the weeks after this day; rentals happen in the 2027 season.
DATASET_START = date(2026, 11, 2)
SEASON_START = date(2027, 3, 6)
SEASON_DAYS = 220


class ExistingBooking(BaseModel):
    """A booking that already exists when the test e-mails arrive."""

    reference: str
    car: str
    customer_name: str
    customer_email: str
    customer_phone: str
    language: str
    pickup_at: datetime
    return_at: datetime
    pickup_location: str
    purpose: str
    extras: list[Extra]
    total: Decimal
    confirmation_message_id: str
    request_message_id: str
    request_subject: str


class Supplier(BaseModel):
    """A company that invoices the business."""

    name: str
    email: str
    address: str
    tax_id: str
    services: list[tuple[str, str]]


class World(BaseModel):
    """Starting state shared by the generator, the demo seed and the evaluation."""

    today: date
    bookings: list[ExistingBooking]
    suppliers: list[Supplier]
    recorded_invoices: list[tuple[str, str]]

    def booking(self, reference: str) -> ExistingBooking | None:
        """Return the existing booking with this reference."""
        return next((b for b in self.bookings if b.reference == reference), None)

    def overlapping(self, car: str, start: datetime, end: datetime) -> list[ExistingBooking]:
        """Return the existing bookings of ``car`` that overlap the period."""
        return [
            b for b in self.bookings if b.car == car and b.pickup_at < end and start < b.return_at
        ]


SUPPLIERS = [
    Supplier(
        name="Coachline Trim & Upholstery",
        email="accounts@coachline-trim.example",
        address="Unit 4, Ropewalk Yard, Port Linden",
        tax_id="NV-TAX-0071156",
        services=[("Re-trim driver seat, leather", "420.00"), ("Carpet set, wool", "310.00"),
                  ("Hood re-proofing", "95.00"), ("Door card repair", "140.00")],
    ),
    Supplier(
        name="Port Linden Tyre Works",
        email="invoices@pl-tyreworks.example",
        address="22 Foundry Street, Port Linden",
        tax_id="NV-TAX-0033907",
        services=[("Crossply tyre 5.90-15", "168.00"), ("Wheel balancing", "18.00"),
                  ("Inner tube", "24.50"), ("Wire wheel truing", "75.00")],
    ),
    Supplier(
        name="Brasswell Insurance Brokers",
        email="billing@brasswell.example",
        address="1 Exchange Court, Marlhaven",
        tax_id="NV-TAX-0019442",
        services=[("Fleet hire policy, monthly premium", "1240.00"),
                  ("Additional driver endorsement", "65.00"), ("Agreed value certificate", "40.00")],
    ),
    Supplier(
        name="Gleamhouse Valeting",
        email="office@gleamhouse.example",
        address="9 Quayside Arches, Port Linden",
        tax_id="NV-TAX-0086620",
        services=[("Full valet, classic", "85.00"), ("Chrome polish", "45.00"),
                  ("Leather feed and clean", "38.00"), ("Engine bay detail", "60.00")],
    ),
    Supplier(
        name="Northquay Parts Supply",
        email="sales@northquay-parts.example",
        address="Dock Road Trading Estate, Northquay",
        tax_id="NV-TAX-0054381",
        services=[("Brake master cylinder kit", "132.40"), ("Spark plug set", "36.80"),
                  ("Fan belt", "14.90"), ("Fuel pump, reconditioned", "189.00"),
                  ("Gasket set, head", "72.60")],
    ),
]  # fmt: skip

VENUES = [
    "Hollowmere Abbey", "the Old Customs House, Port Linden", "Saltmarsh Lighthouse Hotel",
    "Brackenridge Manor", "Studio 9, Quarry Lane", "Finca Los Almendros", "Hotel Miramar Norvalle",
    "St Aldhelm's Church, Marlhaven", "the Glasshouse at Fernhollow", "Villa Rosalinda",
]  # fmt: skip


def random_slot(rng: random.Random, max_days: int = 3) -> tuple[datetime, datetime]:
    """Pick a rental period in the season: whole or half hours, one to ``max_days`` days."""
    start_day = SEASON_START + timedelta(days=rng.randrange(SEASON_DAYS))
    pickup = datetime.combine(
        start_day, time(rng.choice([8, 9, 10, 11, 12, 14]), rng.choice([0, 30]))
    )
    days = rng.choices([0, 1, 2, 3], weights=[4, 3, 2, 1])[0]
    days = min(days, max_days)
    hour = rng.choice([16, 17, 18, 19, 20]) if days == 0 else rng.choice([9, 10, 12, 17, 18])
    returned = datetime.combine(start_day + timedelta(days=days), time(hour, rng.choice([0, 30])))
    return pickup, returned


def build_world(business: Business, seed: int = 4242) -> World:
    """Create the starting state deterministically."""
    rng = random.Random(seed)
    bookings: list[ExistingBooking] = []
    for index in range(1, 37):
        person: Person = make_person(rng, "es" if index % 4 == 0 else "en")
        car = rng.choice(business.cars)
        for _ in range(50):
            pickup, returned = random_slot(rng)
            clash = any(
                b.car == car.code and b.pickup_at < returned + timedelta(hours=4)
                and pickup - timedelta(hours=4) < b.return_at
                for b in bookings
            )  # fmt: skip
            if not clash:
                break
        extras = sorted(rng.sample(list(Extra), rng.choice([0, 0, 1, 1, 2])))
        priced = quote(business, car, pickup, returned, extras)
        reference = f"LCH-B-2026-{index:04d}"
        bookings.append(
            ExistingBooking(
                reference=reference,
                car=car.code,
                customer_name=person.name,
                customer_email=person.email,
                customer_phone=person.phone,
                language=person.language,
                pickup_at=pickup,
                return_at=returned,
                pickup_location=rng.choice([business.garage_location, rng.choice(VENUES)]),
                purpose=rng.choice(["wedding", "weekend_drive", "film_shoot", "corporate_event"]),
                extras=extras,
                total=priced.total,
                confirmation_message_id=f"<confirm-{index:04d}@larkspur.example>",
                request_message_id=f"<req-{index:04d}-{rng.randrange(10**6):06d}@{person.email.split('@')[1]}>",
                request_subject=f"{car.make} {car.model} hire",
            )
        )
    recorded = [
        (SUPPLIERS[0].name, "CT-2026-0398"),
        (SUPPLIERS[1].name, "PLT-11872"),
        (SUPPLIERS[3].name, "GH-0000713"),
        (SUPPLIERS[4].name, "NQ/26/004410"),
    ]
    return World(
        today=DATASET_START, bookings=bookings, suppliers=SUPPLIERS, recorded_invoices=recorded
    )
