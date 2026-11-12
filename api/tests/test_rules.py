"""The rule language, the shipped rules, and gapless numbering."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from opsinbox.errors import RuleError
from opsinbox.records.repository import next_number
from opsinbox.rules import load_rules
from opsinbox.rules.engine import Rule, evaluate_rule, evaluate_rules
from opsinbox.rules.expressions import UNKNOWN, evaluate, parse


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "licence.__class__",
        "[x for x in conflicts]",
        "lambda: 1",
        "len(conflicts, key=1)",
    ],
)
def test_the_rule_language_refuses_anything_outside_its_subset(source: str) -> None:
    with pytest.raises(RuleError):
        parse(source)


def test_unknown_facts_make_a_rule_not_applicable_never_passed() -> None:
    tree = parse("return_at > pickup_at")
    assert evaluate(tree, {"pickup_at": datetime(2027, 6, 1)}) is UNKNOWN
    assert evaluate(parse("present(x) or y > 1"), {"y": 2}) is True
    assert evaluate(parse("a > 1 and b > 1"), {"a": 0}) is False


def test_dates_money_and_functions() -> None:
    facts = {
        "pickup_at": datetime(2027, 6, 12, 10),
        "received_at": datetime(2027, 6, 11, 9),
        "quoted": Decimal("100.00"),
        "tariff": Decimal("100.50"),
        "licence": {"expires_on": date(2027, 6, 12), "date_of_birth": date(2002, 6, 13)},
    }
    assert evaluate(parse("pickup_at >= received_at + hours(24)"), facts) is True
    assert evaluate(parse("abs(quoted - tariff) <= decimal('1.00')"), facts) is True
    assert evaluate(parse("licence.expires_on >= date_of(pickup_at)"), facts) is True
    assert evaluate(parse("years_between(licence.date_of_birth, pickup_at)"), facts) == 24


SHIPPED = load_rules()


def rule(rule_id: str) -> Rule:
    return next(r for r in SHIPPED if r.id == rule_id)


def test_shipped_rules_all_parse() -> None:
    assert len(SHIPPED) >= 25


def test_required_fields_name_each_missing_field() -> None:
    results = evaluate_rule(
        rule("required_booking_fields"), {"customer_name": "Ada", "car": "SPRITE"}
    )
    assert {r.reason for r in results} == {
        "missing:pickup_date",
        "missing:pickup_time",
        "missing:return_date",
        "missing:return_time",
    }


def test_price_rule_applies_only_when_a_price_was_quoted() -> None:
    price = rule("price_matches_tariff")
    base = {"tariff_total": Decimal("374.40"), "price_tolerance": Decimal("1.00")}
    assert evaluate_rule(price, base)[0].outcome == "not_applicable"
    ok = evaluate_rule(price, {**base, "quoted_total": Decimal("374.00")})[0]
    wrong = evaluate_rule(price, {**base, "quoted_total": Decimal("300.00")})[0]
    assert ok.outcome == "passed"
    assert wrong.outcome == "failed" and "300.00" in wrong.message and "374.40" in wrong.message


def test_licence_rules() -> None:
    facts = {
        "pickup_at": datetime(2027, 6, 12, 10),
        "return_at": datetime(2027, 6, 14, 10),
        "licence": {
            "expires_on": date(2027, 6, 13),
            "classes": ["M"],
            "date_of_birth": date(2004, 1, 1),
        },
        "required_licence_class": "P",
        "minimum_driver_age": 25,
    }
    outcomes = {r.rule_id: r.outcome for r in evaluate_rules(SHIPPED, "new_booking", facts)}
    assert outcomes["licence_valid_for_rental"] == "failed"
    assert outcomes["licence_class"] == "failed"
    assert outcomes["driver_age"] == "failed"


def test_rules_for_every_category_include_the_suspicion_checks() -> None:
    results = evaluate_rules(
        SHIPPED,
        "not_relevant",
        {"suspicions": ["x"], "sender_warnings": [], "booking_signals": False},
    )
    assert {r.rule_id: r.outcome for r in results}["suspicious_content"] == "failed"


def test_numbering_is_gapless_and_unique_under_concurrency(sessions: sessionmaker[Session]) -> None:
    def take(_: int) -> int:
        with sessions() as session:
            number = next_number(session, "invoice", 2027)
            session.commit()
            return number

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = sorted(pool.map(take, range(40)))
    assert numbers == list(range(1, 41))


def test_a_rolled_back_number_is_handed_out_again(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        assert next_number(session, "contract", 2027) == 1
        session.rollback()
    with sessions() as session:
        assert next_number(session, "contract", 2027) == 1
