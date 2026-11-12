"""What the validation step needs to know about existing records, behind an interface.

Two implementations: :class:`WorldRecords` answers from the fixed starting state used by the
labelled set (the evaluation needs no database), and the database one in
:mod:`opsinbox.records.repository` answers from PostgreSQL in the running product.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from opsinbox.seedgen.world import World

#: Time kept free between two hires of the same car, to bring it back and prepare it.
TURNAROUND = timedelta(hours=2)


@dataclass(frozen=True)
class BookingSnapshot:
    """The parts of a booking that validation looks at."""

    reference: str
    car: str
    customer_name: str
    customer_email: str
    pickup_at: datetime
    return_at: datetime
    pickup_location: str
    extras: tuple[str, ...]
    total: Decimal
    status: str
    language: str


@dataclass(frozen=True)
class SupplierSnapshot:
    """A supplier the business already pays."""

    name: str
    email: str


class RecordsView(Protocol):
    """Read-only questions about existing records."""

    def booking(self, reference: str) -> BookingSnapshot | None:
        """Return the booking with this reference."""

    def booking_for_thread(self, message_ids: list[str]) -> BookingSnapshot | None:
        """Return the booking whose conversation contains one of these message ids."""

    def conflicts(
        self, car: str, start: datetime, end: datetime, exclude: str | None = None
    ) -> list[str]:
        """Return references of bookings holding ``car`` during the period (with turnaround)."""

    def duplicate_of(self, customer_email: str, car: str, pickup_day: str) -> str | None:
        """Return a booking of the same customer, car and pick-up day, if any."""

    def suppliers(self) -> list[SupplierSnapshot]:
        """Return the known suppliers."""

    def invoice_recorded(self, supplier_name: str, number: str) -> bool:
        """Tell whether this supplier invoice is already recorded."""


class WorldRecords:
    """Records answered from the labelled set's starting state."""

    def __init__(self, world: World) -> None:
        self._world = world
        self._bookings = {
            b.reference: BookingSnapshot(
                reference=b.reference,
                car=b.car,
                customer_name=b.customer_name,
                customer_email=b.customer_email,
                pickup_at=b.pickup_at,
                return_at=b.return_at,
                pickup_location=b.pickup_location,
                extras=tuple(e.value for e in b.extras),
                total=b.total,
                status="confirmed",
                language=b.language,
            )
            for b in world.bookings
        }
        self._threads: dict[str, str] = {}
        for b in world.bookings:
            self._threads[b.request_message_id] = b.reference
            self._threads[b.confirmation_message_id] = b.reference

    def booking(self, reference: str) -> BookingSnapshot | None:
        """Return the booking with this reference."""
        return self._bookings.get(reference.strip().upper())

    def booking_for_thread(self, message_ids: list[str]) -> BookingSnapshot | None:
        """Return the booking whose conversation contains one of these message ids."""
        for message_id in message_ids:
            reference = self._threads.get(message_id)
            if reference:
                return self._bookings[reference]
        return None

    def conflicts(
        self, car: str, start: datetime, end: datetime, exclude: str | None = None
    ) -> list[str]:
        """Return references of bookings holding ``car`` during the period."""
        return [
            b.reference
            for b in self._bookings.values()
            if b.car == car
            and b.reference != exclude
            and b.status == "confirmed"
            and b.pickup_at < end + TURNAROUND
            and start < b.return_at + TURNAROUND
        ]

    def duplicate_of(self, customer_email: str, car: str, pickup_day: str) -> str | None:
        """Return a booking of the same customer, car and pick-up day, if any."""
        for b in self._bookings.values():
            if (
                b.customer_email.lower() == customer_email.lower()
                and b.car == car
                and b.pickup_at.date().isoformat() == pickup_day
                and b.status == "confirmed"
            ):
                return b.reference
        return None

    def suppliers(self) -> list[SupplierSnapshot]:
        """Return the known suppliers."""
        return [SupplierSnapshot(s.name, s.email) for s in self._world.suppliers]

    def invoice_recorded(self, supplier_name: str, number: str) -> bool:
        """Tell whether this supplier invoice is already recorded."""
        wanted = (supplier_name.casefold(), number.replace(" ", "").upper())
        return any(
            (name.casefold(), recorded.upper()) == wanted
            for name, recorded in self._world.recorded_invoices
        )
