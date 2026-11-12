"""Contract and invoice rendering, and reply drafting."""

from datetime import date, datetime

from opsinbox.documents import DocumentContext, ReplyFacts, draft_reply, render_html
from opsinbox.llm import FakeModelClient
from opsinbox.records.business import load_business
from opsinbox.records.pricing import quote
from opsinbox.schemas import Extra

BUSINESS = load_business()


def context() -> DocumentContext:
    car = BUSINESS.car("SPRITE")
    assert car is not None
    pickup, returned = datetime(2027, 6, 12, 10), datetime(2027, 6, 13, 18)
    return DocumentContext(
        business=BUSINESS,
        car=car,
        customer={"name": "Ada <Quill>", "email": "ada@x.example", "phone": None},
        booking={
            "reference": "LCH-B-2027-0001",
            "pickup_at": pickup,
            "return_at": returned,
            "pickup_location": "Hollowmere Abbey",
        },
        quote=quote(BUSINESS, car, pickup, returned, [Extra.DELIVERY]),
        licence=None,
        purpose="wedding",
        issued_on=date(2026, 11, 12),
    )


def test_stylesheet_is_not_escaped_but_customer_text_is() -> None:
    html = render_html("invoice", "LCH-INV-2027-0001", context())
    assert 'content: "Page " counter(page)' in html  # the CSS reaches WeasyPrint intact
    assert "Ada &lt;Quill&gt;" in html  # text from e-mails is escaped
    assert "856.80" in html  # 2 started days x 312.00 + 90.00 delivery = 714.00, plus 20% tax


def test_a_drafted_reply_that_changes_a_number_is_replaced_by_the_template() -> None:
    facts = ReplyFacts(
        language="en",
        customer_name="Ada Quill",
        situation="Confirmed.",
        facts=["Total: 748.80 EUR"],
        required=["748.80"],
    )
    honest = FakeModelClient(lambda r: "Dear Ada,\n\nYour total is 748.80 EUR.\n\nKind regards,")
    inventive = FakeModelClient(lambda r: "Dear Ada,\n\nYour total is 700.00 EUR, a discount.\n")
    assert draft_reply(honest, "m", facts)[1] == "model"
    body, origin = draft_reply(inventive, "m", facts)
    assert origin == "template" and "748.80" in body and "700.00" not in body
