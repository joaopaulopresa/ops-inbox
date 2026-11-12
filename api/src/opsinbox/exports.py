"""Keep a spreadsheet of bookings in sync: a CSV in object storage, optionally a Google Sheet.

The sheet is rebuilt from the database each time (the database is the truth), so a sync job that
runs twice, or out of order, ends in the same sheet.
"""

import csv
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsinbox.errors import OpsInboxError
from opsinbox.records.models import Booking, Customer
from opsinbox.storage import ObjectStore

COLUMNS = [
    "reference", "status", "car", "customer", "email", "phone", "pickup", "return",
    "pickup_location", "extras", "total", "deposit",
]  # fmt: skip


def booking_rows(session: Session) -> list[list[str]]:
    """Every booking as a sheet row, ordered by pick-up."""
    rows = session.execute(
        select(Booking, Customer).join(Customer, Customer.id == Booking.customer_id)
        .order_by(Booking.pickup_at)
    ).all()  # fmt: skip
    return [
        [
            b.reference, b.status, b.car_code, c.name, c.email, c.phone or "",
            b.pickup_at.strftime("%Y-%m-%d %H:%M"), b.return_at.strftime("%Y-%m-%d %H:%M"),
            b.pickup_location, " ".join(b.extras), f"{b.total:.2f}", f"{b.deposit:.2f}",
        ]
        for b, c in rows
    ]  # fmt: skip


def to_csv(rows: list[list[str]]) -> bytes:
    """Render rows with the header as UTF-8 CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


class Sheet(Protocol):
    """A destination that takes the whole table."""

    def replace(self, header: list[str], rows: list[list[str]]) -> None:
        """Replace the sheet's contents with ``header`` and ``rows``."""


class CsvSheet:
    """The CSV in object storage.

    Objects are immutable, so each version gets its own key; ``last_key`` names the newest.
    """

    def __init__(self, store: ObjectStore) -> None:
        self._store = store
        self.last_key: str | None = None

    def replace(self, header: list[str], rows: list[list[str]]) -> None:
        """Write a new version of the CSV."""
        data = to_csv(rows)
        key = f"exports/bookings-{hashlib.sha256(data).hexdigest()[:16]}.csv"
        self._store.put(key, data, "text/csv")
        self.last_key = key


class SheetsError(OpsInboxError):
    """Google Sheets refused the update."""


@dataclass
class GoogleSheet:
    """Google Sheets through its REST API (``values.clear`` then ``values.update``).

    ``token`` supplies an OAuth access token for a service account with access to the sheet;
    obtaining it needs the owner's Google credentials and is outside the demo.
    """

    spreadsheet_id: str
    token: Callable[[], str]
    sheet_name: str = "Bookings"
    client: httpx.Client | None = None
    base_url: str = "https://sheets.googleapis.com/v4/spreadsheets"

    def _call(self, method: str, path: str, **kwargs: Any) -> None:
        client = self.client or httpx.Client(timeout=30)
        response = client.request(
            method, f"{self.base_url}/{self.spreadsheet_id}/values/{path}",
            headers={"Authorization": f"Bearer {self.token()}"}, **kwargs,
        )  # fmt: skip
        if response.status_code >= 400:
            raise SheetsError(f"Sheets API {response.status_code}: {response.text[:200]}")

    def replace(self, header: list[str], rows: list[list[str]]) -> None:
        """Clear the sheet and write the table from A1."""
        area = f"{self.sheet_name}!A:Z"
        self._call("POST", f"{area}:clear")
        self._call(
            "PUT", f"{self.sheet_name}!A1", params={"valueInputOption": "RAW"},
            json={"range": f"{self.sheet_name}!A1", "majorDimension": "ROWS",
                  "values": [header, *rows]},
        )  # fmt: skip


def sync(session: Session, sheets: list[Sheet]) -> int:
    """Rebuild every sheet from the bookings. Returns the number of rows written."""
    rows = booking_rows(session)
    for sheet in sheets:
        sheet.replace(COLUMNS, rows)
    return len(rows)
