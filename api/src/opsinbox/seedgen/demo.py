"""Load the demo business and fill the local mailbox.

    python -m opsinbox.seedgen.demo load                 # world, rules, threshold into PostgreSQL
    python -m opsinbox.seedgen.demo send                 # the demo e-mails into the mail server
    python -m opsinbox.seedgen.demo send --ids m002,m057 # chosen e-mails

The e-mails come from the labelled set, so their model answers are already recorded and the demo
runs without an API key. Both commands are safe to run twice.
"""

import argparse
import json
import smtplib
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from opsinbox.db import get_sessionmaker
from opsinbox.records import repository as repo
from opsinbox.records.business import default_business_path, load_business
from opsinbox.records.models import (
    AppSetting,
    Booking,
    NumberSequence,
    PayableInvoice,
    RuleRecord,
    Supplier,
)
from opsinbox.records.pricing import quote
from opsinbox.rules import load_rules
from opsinbox.seedgen.world import World
from opsinbox.settings import get_settings

#: A mailbox that shows every path: clean bookings (scan, photo, PDF-quality), stops for review,
#: a change and a cancellation in thread, a question, supplier invoices and adversarial mail.
DEMO_IDS = [
    "m001",
    "m005",
    "m010",
    "m070",
    "m079",  # clean bookings: clean file, scan, photo, forwarded
    "m007",
    "m097",
    "m122",
    "m134",
    "m146",  # stops: misread name, vague dates, car taken, price, expired
    "m182",
    "m185",
    "m196",
    "m199",
    "m208",  # changes and cancellations in thread
    "m211",  # a question
    "m229",
    "m244",
    "m251",
    "m254",  # supplier invoices: PDF, scan, wrong totals, duplicate
    "m262",
    "m266",  # not relevant
    "m277",
    "m289",
    "m292",  # adversarial: instruction in the body, bank change, spoofed sender
]
#: Sent live while recording the videos.
LIVE_IDS = {"straight_through": "m008", "adversarial": "m284"}


def seed_dir() -> Path:
    """The ``seed`` folder."""
    return default_business_path().parent


def load() -> None:
    """Load the starting world into the database (idempotent)."""
    business = load_business()
    world = World.model_validate_json((seed_dir() / "dataset" / "world.json").read_text())
    sessions = get_sessionmaker()
    with sessions() as session:
        repo.seed_business(session, business)
        for supplier in world.suppliers:
            session.execute(
                insert(Supplier).values(name=supplier.name, email=supplier.email)
                .on_conflict_do_nothing(index_elements=["name"])
            )  # fmt: skip
        for name, number in world.recorded_invoices:
            session.execute(
                insert(PayableInvoice).values(supplier_name=name, number=number, status="paid")
                .on_conflict_do_nothing()
            )  # fmt: skip
        for item in world.bookings:
            if session.execute(
                select(Booking.id).where(Booking.reference == item.reference)
            ).first():
                continue
            customer = repo.upsert_customer(session, item.customer_email, item.customer_name,
                                            item.customer_phone, item.language)  # fmt: skip
            car = business.car(item.car)
            assert car is not None
            priced = quote(business, car, item.pickup_at, item.return_at, item.extras)
            booking = Booking(
                reference=item.reference, car_code=item.car, customer_id=customer.id,
                pickup_at=item.pickup_at, return_at=item.return_at,
                pickup_location=item.pickup_location, purpose=item.purpose,
                extras=[e.value for e in item.extras], total=priced.total, deposit=priced.deposit,
            )  # fmt: skip
            session.add(booking)
            session.flush()
            repo.link_thread(session, booking.id,
                             [item.request_message_id, item.confirmation_message_id])  # fmt: skip
        last = max(int(b.reference.rsplit("-", 1)[1]) for b in world.bookings)
        session.execute(
            insert(NumberSequence).values(kind="booking", year=2026, last_value=last)
            .on_conflict_do_update(index_elements=["kind", "year"], set_={"last_value": last})
        )  # fmt: skip
        if not session.execute(select(RuleRecord.id).limit(1)).first():
            for position, rule in enumerate(load_rules()):
                session.add(RuleRecord(id=rule.id, position=position, definition=rule.model_dump()))
        threshold_file = seed_dir().parent / "reports" / "threshold.json"
        threshold = (
            json.loads(threshold_file.read_text())["threshold"] if threshold_file.exists() else 0.8
        )
        session.execute(
            insert(AppSetting).values(key="confidence_threshold", value=threshold)
            .on_conflict_do_nothing()
        )  # fmt: skip
        session.commit()
    print(
        f"loaded {len(world.bookings)} bookings, {len(world.suppliers)} suppliers, threshold {threshold}"
    )


def send(ids: list[str], delay: float) -> None:
    """Deliver labelled e-mails to the local mail server, byte for byte."""
    settings = get_settings()
    dataset = seed_dir() / "dataset"
    index = {path.stem: path for path in dataset.glob("*/*.eml")}
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as client:
        for key in ids:
            raw = index[key].read_bytes()
            client.sendmail("demo-sender@example.org", [settings.mail_from], raw)
            print(f"sent {key}")
            time.sleep(delay)


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Load the demo business and fill the mailbox")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("load")
    sender = sub.add_parser("send")
    sender.add_argument("--ids", default=",".join(DEMO_IDS))
    sender.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    if args.command == "load":
        load()
    else:
        send([i for i in args.ids.split(",") if i], args.delay)


if __name__ == "__main__":
    main()
