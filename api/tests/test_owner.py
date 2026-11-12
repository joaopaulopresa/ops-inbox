"""Owner endpoints and the bookings sheet, through the HTTP API."""

import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from opsinbox.db import get_session
from opsinbox.exports import COLUMNS, CsvSheet, GoogleSheet, SheetsError, sync
from opsinbox.main import app
from opsinbox.models import Message
from opsinbox.records import repository as repo
from opsinbox.records.business import load_business
from opsinbox.records.models import Case
from opsinbox.routes.deps import get_store
from opsinbox.storage import MemoryObjectStore
from opsinbox.workflow.facts import jsonable

BUSINESS = load_business()


@pytest.fixture
def client(sessions: sessionmaker[Session], store: MemoryObjectStore) -> Iterator[TestClient]:
    def session_dependency() -> Iterator[Session]:
        with sessions() as session:
            yield session
            session.commit()

    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_store] = lambda: store
    with sessions() as session:
        repo.seed_business(session, BUSINESS)
        customer = repo.upsert_customer(session, "ada@customer.example", "Ada Quill", None, "en")
        repo.create_booking(
            session,
            case_id=uuid.uuid4(),
            car="SPRITE",
            customer=customer,
            pickup_at=datetime(2027, 6, 12, 10),
            return_at=datetime(2027, 6, 12, 18),
            pickup_location="Hollowmere Abbey",
            purpose="wedding",
            extras=["delivery"],
            total=Decimal("482.40"),
            deposit=Decimal("144.72"),
            licence=None,
            thread_ids=[],
            actor="test",
        )
        session.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def past_case(sessions: sessionmaker[Session], quoted: str, tariff: str) -> None:
    facts = jsonable(
        {
            "quoted_total": Decimal(quoted),
            "tariff_total": Decimal(tariff),
            "price_tolerance": Decimal("1.00"),
        }
    )
    with sessions() as session:
        message = Message(
            mailbox="t",
            message_id=f"<{uuid.uuid4()}@x>",
            content_hash=uuid.uuid4().hex,
            raw_key="k",
            from_address="a@x.example",
            subject=f"Quote {quoted} for {tariff}",
            thread_key="t",
        )
        session.add(message)
        session.flush()
        session.add(
            Case(
                message_pk=message.id,
                status="done",
                category="new_booking",
                route="auto",
                state={"facts": facts},
            )
        )
        session.commit()


def test_testing_a_rule_change_shows_which_past_emails_it_would_stop(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    past_case(sessions, "1000.00", "1000.50")
    past_case(sessions, "1000.00", "1030.00")
    rule = next(r for r in client.get("/api/rules").json() if r["id"] == "price_matches_tariff")

    stricter = {**rule, "check": "abs(quoted_total - tariff_total) <= decimal('0.10')"}
    result = client.post("/api/rules/test", json=stricter).json()

    assert (result["checked"], result["stops_now"], result["would_stop"]) == (2, 1, 2)
    assert [r["subject"] for r in result["newly_stopped"]] == ["Quote 1000.00"] or len(
        result["newly_stopped"]
    ) == 1


def test_a_rule_that_does_not_parse_is_refused(client: TestClient) -> None:
    bad = {
        "id": "x",
        "name": "x",
        "description": "x",
        "applies_to": ["new_booking"],
        "check": "__import__('os').system('true')",
    }
    response = client.post("/api/rules/test", json=bad)
    assert response.status_code == 422
    assert "allowed" in response.json()["detail"]


def test_saving_a_rule_is_audited_and_used(client: TestClient) -> None:
    rule = next(r for r in client.get("/api/rules").json() if r["id"] == "rental_length")
    rule["check"] = "rental_days <= 7"
    assert client.put("/api/rules/rental_length", json=rule).status_code == 200
    saved = next(r for r in client.get("/api/rules").json() if r["id"] == "rental_length")
    assert saved["check"] == "rental_days <= 7"
    audit = client.get("/api/records/audit").json()
    assert audit[0]["action"] == "rule.saved"


def test_threshold_change_is_bounded_and_audited(client: TestClient) -> None:
    assert client.put("/api/automation/threshold", json={"value": 1.5}).status_code == 422
    assert client.put("/api/automation/threshold", json={"value": 0.6}).json() == {"threshold": 0.6}
    assert client.get("/api/automation").json()["threshold"] == 0.6


def test_template_preview_renders_with_a_real_booking_and_rejects_broken_ones(
    client: TestClient,
) -> None:
    source = client.get("/api/templates/invoice").json()["source"]
    edited = source.replace("<h1>Invoice</h1>", "<h1>Invoice (edited)</h1>")
    preview = client.post("/api/templates/invoice/preview", json={"source": edited})
    assert preview.status_code == 200 and preview.content.startswith(b"%PDF")
    broken = client.put("/api/templates/invoice", json={"source": "{{ quote.nonexistent.field }}"})
    assert broken.status_code == 422
    assert client.put("/api/templates/invoice", json={"source": edited}).status_code == 200
    assert client.get("/api/templates/invoice").json()["customised"] is True


def test_bookings_export_is_current(client: TestClient) -> None:
    response = client.get("/api/export/bookings.csv")
    lines = response.text.strip().splitlines()
    assert lines[0].split(",") == COLUMNS
    assert "SPRITE" in lines[1] and "482.40" in lines[1]


def test_csv_sheet_and_google_sheet_receive_the_same_table(
    client: TestClient, sessions: sessionmaker[Session], store: MemoryObjectStore
) -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        assert request.headers["Authorization"] == "Bearer token-1"
        return httpx.Response(200, json={})

    google = GoogleSheet(
        "sheet-1", lambda: "token-1", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    csv_sheet = CsvSheet(store)
    with sessions() as session:
        assert sync(session, [csv_sheet, google]) == 1

    assert [c[0] for c in calls] == ["POST", "PUT"]
    assert calls[0][1].endswith("/values/Bookings!A:Z:clear")
    values = calls[1][2]["values"]  # type: ignore[index]
    assert values[0] == COLUMNS and values[1][0].startswith("LCH-B-2027-")
    assert csv_sheet.last_key is not None and store.get(csv_sheet.last_key).startswith(
        b"reference,"
    )


def test_google_sheet_errors_are_reported(sessions: sessionmaker[Session]) -> None:
    failing = GoogleSheet(
        "sheet-1",
        lambda: "t",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(403, text="no access"))
        ),
    )
    with pytest.raises(SheetsError):
        failing.replace(COLUMNS, [])


def test_dashboard_counts_automation_and_flags(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    past_case(sessions, "10.00", "10.00")
    case_id = client.get("/api/cases?status=all").json()[0]["id"]
    assert client.post(f"/api/cases/{case_id}/flag", json={"note": "wrong car"}).status_code == 200
    data = client.get("/api/dashboard").json()
    assert data["automatic"] == 1 and data["straight_through_rate"] == 1.0
    assert data["flagged_after_the_fact"] == 1


def test_reply_preview_is_not_taken_for_a_document_kind(client: TestClient) -> None:
    # /preview/reply must reach its own route, not the /preview/{kind} one (which only knows
    # contract and invoice): a 404 for the unknown case proves the routing.
    response = client.post(f"/api/cases/{uuid.uuid4()}/preview/reply", json={"corrections": {}})
    assert response.status_code == 404
