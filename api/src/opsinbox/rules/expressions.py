"""A tiny, safe expression language for business rules.

Rules are stored as text and edited by the owner, so they are never passed to ``eval``. The text
is parsed with :mod:`ast` and only this subset is accepted:

* literals (numbers, strings, ``True``, ``False``, ``None``), lists;
* names of facts, and ``.`` to reach a field of a fact (``licence.expires_on``);
* comparisons (``< <= > >= == != in not in``), ``and``, ``or``, ``not``;
* ``+ - *`` on numbers, dates and money;
* calls to the functions in :data:`FUNCTIONS`.

A fact that is unknown (``None``) makes a comparison unknown, and an unknown result means the rule
cannot be evaluated: it is reported as "not applicable", never as passed.
"""

import ast
import operator
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from opsinbox.errors import RuleError


class Unknown:
    """Marker for a value that cannot be known from the facts."""

    def __repr__(self) -> str:
        return "Unknown"


UNKNOWN = Unknown()


def _days(value: Any) -> Any:
    return timedelta(days=int(value))


def _hours(value: Any) -> Any:
    return timedelta(hours=float(value))


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _years_between(start: Any, end: Any) -> int:
    first = start.date() if isinstance(start, datetime) else start
    last = end.date() if isinstance(end, datetime) else end
    years = last.year - first.year
    if (last.month, last.day) < (first.month, first.day):
        years -= 1
    return years


def _date_of(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "days": _days,
    "hours": _hours,
    "present": _present,
    "abs": abs,
    "len": len,
    "years_between": _years_between,
    "date_of": _date_of,
    "decimal": lambda v: Decimal(str(v)),
    "lower": lambda v: str(v).lower(),
}
#: Functions that receive unknown values instead of short-circuiting to unknown.
_UNKNOWN_AWARE = {"present"}

_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_ARITHMETIC: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
}


def parse(source: str) -> ast.Expression:
    """Parse and validate an expression.

    Raises:
        RuleError: when the text is not a valid rule expression.

    """
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as exc:
        raise RuleError(f"not a valid expression: {exc.msg}") from exc
    for node in ast.walk(tree):
        allowed = (
            ast.Expression,
            ast.Compare,
            ast.BoolOp,
            ast.UnaryOp,
            ast.BinOp,
            ast.Name,
            ast.Attribute,
            ast.Constant,
            ast.Call,
            ast.List,
            ast.Tuple,
            ast.Load,
            ast.And,
            ast.Or,
            ast.Not,
            ast.USub,
            *_COMPARE,
            *_ARITHMETIC,
        )
        if not isinstance(node, allowed):
            raise RuleError(f"{type(node).__name__} is not allowed in a rule")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise RuleError("only these functions are allowed: " + ", ".join(FUNCTIONS))
            if node.keywords:
                raise RuleError("keyword arguments are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise RuleError("private attributes are not allowed")
    return tree


def names(tree: ast.Expression) -> set[str]:
    """Return the fact names an expression reads (``licence.expires_on`` counts as ``licence``)."""
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in FUNCTIONS
        and node.id not in {"True", "False", "None"}
    }


def evaluate(tree: ast.Expression, facts: Mapping[str, Any]) -> Any:
    """Evaluate a parsed expression against ``facts``. Returns :data:`UNKNOWN` when undecidable."""
    return _eval(tree.body, facts)


def _eval(node: ast.expr, facts: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        value = facts.get(node.id, UNKNOWN)
        return UNKNOWN if value is None else value
    if isinstance(node, ast.Attribute):
        base = _eval(node.value, facts)
        if base is UNKNOWN:
            return UNKNOWN
        value = base.get(node.attr) if isinstance(base, Mapping) else getattr(base, node.attr, None)
        return UNKNOWN if value is None else value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(item, facts) for item in node.elts]
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, facts)
        if operand is UNKNOWN:
            return UNKNOWN
        return (not operand) if isinstance(node.op, ast.Not) else -operand
    if isinstance(node, ast.BoolOp):
        values = [_eval(item, facts) for item in node.values]
        if isinstance(node.op, ast.And):
            if any(v is not UNKNOWN and not v for v in values):
                return False
            return UNKNOWN if any(v is UNKNOWN for v in values) else True
        if any(v is not UNKNOWN and v for v in values):
            return True
        return UNKNOWN if any(v is UNKNOWN for v in values) else False
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, facts), _eval(node.right, facts)
        if left is UNKNOWN or right is UNKNOWN:
            return UNKNOWN
        try:
            return _ARITHMETIC[type(node.op)](left, right)
        except TypeError as exc:
            raise RuleError(f"cannot combine {left!r} and {right!r}") from exc
    if isinstance(node, ast.Compare):
        left = _eval(node.left, facts)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, facts)
            if left is UNKNOWN or right is UNKNOWN:
                return UNKNOWN
            try:
                if not _COMPARE[type(op)](left, right):
                    return False
            except TypeError as exc:
                raise RuleError(f"cannot compare {left!r} and {right!r}") from exc
            left = right
        return True
    if isinstance(node, ast.Call):
        assert isinstance(node.func, ast.Name)
        name = node.func.id
        args = [_eval(arg, facts) for arg in node.args]
        if name in _UNKNOWN_AWARE:
            return FUNCTIONS[name](*[None if a is UNKNOWN else a for a in args])
        if any(a is UNKNOWN for a in args):
            return UNKNOWN
        try:
            return FUNCTIONS[name](*args)
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise RuleError(f"{name}() failed: {exc}") from exc
    raise RuleError(f"{type(node).__name__} is not allowed in a rule")
