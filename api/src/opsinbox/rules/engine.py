"""Business rules as data, and their evaluation against the facts of a case."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from opsinbox.errors import RuleError
from opsinbox.rules.expressions import UNKNOWN, evaluate, parse

Outcome = Literal["passed", "failed", "not_applicable", "error"]


class Rule(BaseModel):
    """One business rule.

    ``kind`` is ``check`` (the expression must be true) or ``required`` (each listed fact must be
    present; a missing one fails as ``missing:<fact>``).
    """

    id: str
    name: str
    description: str
    applies_to: list[str]
    kind: Literal["check", "required"] = "check"
    when: str | None = None
    check: str | None = None
    fields: list[str] = Field(default_factory=list)
    message: str = ""
    enabled: bool = True


class RuleResult(BaseModel):
    """What one rule said about one case."""

    rule_id: str
    name: str
    outcome: Outcome
    #: Identifier used in routing reasons: the rule id, or ``missing:<fact>``.
    reason: str
    message: str = ""


def validate_rule(rule: Rule) -> None:
    """Raise :class:`RuleError` if the rule cannot be evaluated."""
    if rule.kind == "check":
        if not rule.check:
            raise RuleError(f"rule {rule.id}: a check rule needs an expression")
        parse(rule.check)
    elif not rule.fields:
        raise RuleError(f"rule {rule.id}: a required rule needs fields")
    if rule.when:
        parse(rule.when)


def _format(message: str, facts: Mapping[str, Any]) -> str:
    class _Safe(dict[str, Any]):
        def __missing__(self, key: str) -> str:
            return "?"

    flat: dict[str, Any] = {}
    for key, value in facts.items():
        if isinstance(value, Mapping):
            for inner, item in value.items():
                flat[f"{key}_{inner}"] = item
        flat[key] = value
    for key, value in list(flat.items()):
        if isinstance(value, datetime):
            flat[key] = value.strftime("%d %b %Y %H:%M")
        elif isinstance(value, date):
            flat[key] = value.strftime("%d %b %Y")
        elif isinstance(value, Decimal):
            flat[key] = f"{value:,.2f}"
    try:
        return message.format_map(_Safe(flat))
    except (ValueError, IndexError):
        return message


def evaluate_rule(rule: Rule, facts: Mapping[str, Any]) -> list[RuleResult]:
    """Evaluate one rule. A ``required`` rule yields one result per missing fact."""
    if rule.when:
        try:
            condition = evaluate(parse(rule.when), facts)
        except RuleError as exc:
            return [
                RuleResult(
                    rule_id=rule.id,
                    name=rule.name,
                    outcome="error",
                    reason=rule.id,
                    message=str(exc),
                )
            ]
        if condition is UNKNOWN or not condition:
            return [
                RuleResult(
                    rule_id=rule.id, name=rule.name, outcome="not_applicable", reason=rule.id
                )
            ]
    if rule.kind == "required":
        missing = [f for f in rule.fields if facts.get(f) in (None, "", [])]
        if not missing:
            return [RuleResult(rule_id=rule.id, name=rule.name, outcome="passed", reason=rule.id)]
        return [
            RuleResult(
                rule_id=rule.id,
                name=rule.name,
                outcome="failed",
                reason=f"missing:{field}",
                message=f"The e-mail does not say {field.replace('_', ' ')}.",
            )
            for field in missing
        ]
    assert rule.check is not None
    try:
        result = evaluate(parse(rule.check), facts)
    except RuleError as exc:
        return [
            RuleResult(
                rule_id=rule.id, name=rule.name, outcome="error", reason=rule.id, message=str(exc)
            )
        ]
    if result is UNKNOWN:
        return [
            RuleResult(rule_id=rule.id, name=rule.name, outcome="not_applicable", reason=rule.id)
        ]
    if result:
        return [RuleResult(rule_id=rule.id, name=rule.name, outcome="passed", reason=rule.id)]
    return [
        RuleResult(
            rule_id=rule.id,
            name=rule.name,
            outcome="failed",
            reason=rule.id,
            message=_format(rule.message, facts),
        )
    ]


def evaluate_rules(rules: list[Rule], category: str, facts: Mapping[str, Any]) -> list[RuleResult]:
    """Evaluate every enabled rule that applies to ``category``."""
    results: list[RuleResult] = []
    for rule in rules:
        if rule.enabled and (category in rule.applies_to or "*" in rule.applies_to):
            results.extend(evaluate_rule(rule, facts))
    return results


def default_rules_path() -> Path:
    """The rules shipped with the demo business."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "seed" / "rules.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("seed/rules.json not found")


def load_rules(path: Path | None = None) -> list[Rule]:
    """Load and validate rules from a JSON file."""
    raw = json.loads((path or default_rules_path()).read_text(encoding="utf-8"))
    rules = [Rule.model_validate(item) for item in raw]
    for rule in rules:
        validate_rule(rule)
    return rules
