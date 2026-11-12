"""Code-side checks for e-mails that try to steer the system or impersonate someone.

These do not depend on the model noticing anything. A hit never rejects an e-mail by itself: it
sends the case to a person, with the reason.
"""

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, str]] = [
    (
        "instruction_to_system",
        r"\b(ignore|disregard|forget|override|olvida|ignora|omite)\b.{0,40}"
        r"\b(rules?|instructions?|polic(y|ies)|checks?|prompt|reglas?|instrucciones|normas)\b",
    ),
    (
        "instruction_to_system",
        r"\b(system prompt|as an ai|ai assistant|language model|you are now)\b",
    ),
    (
        "instruction_to_system",
        r"\b(approve|confirm|process|aprueba|confirma)\b.{0,40}"
        r"\b(automatically|without (review|checking|verification|approval)|"
        r"autom[aá]ticamente|sin (revisi[oó]n|verificaci[oó]n))\b",
    ),
    (
        "instruction_to_system",
        r"\b(skip|bypass|do not|don't|no need to|sin)\b.{0,30}"
        r"\b(review|verification|human|validation|checks?|revisi[oó]n|verificaci[oó]n)\b",
    ),
    (
        "payment_redirection",
        r"\b(new|updated|changed|different|nuev[ao]s?)\b.{0,30}"
        r"\b(bank (details|account)|iban|account number|cuenta bancaria|datos bancarios)\b",
    ),
    (
        "payment_redirection",
        r"\b(refund|reembols\w+|transfer|wire)\b.{0,60}"
        r"\b(iban|account|cuenta|crypto|gift cards?)\b",
    ),
    (
        "pressure",
        r"\b(immediately|right now|within the hour|urgent(ly)?|asap|de inmediato|urgente)\b.{0,80}"
        r"\b(or (we|i) will|legal action|lawyer|cancel everything|demandar|abogado)\b",
    ),
]
_COMPILED = [(name, re.compile(pattern, re.IGNORECASE | re.DOTALL)) for name, pattern in _PATTERNS]


@dataclass(frozen=True)
class Suspicion:
    """One reason to distrust an e-mail."""

    kind: str
    detail: str


def scan_text(text: str) -> list[Suspicion]:
    """Return what in ``text`` looks like an attempt to steer the system or redirect money."""
    hits: list[Suspicion] = []
    seen: set[str] = set()
    for kind, pattern in _COMPILED:
        match = pattern.search(text)
        if match and kind not in seen:
            seen.add(kind)
            hits.append(Suspicion(kind, " ".join(match.group(0).split())[:160]))
    return hits


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def scan_sender(
    from_address: str, from_name: str, reply_to: str, own_domain: str, own_name: str
) -> list[Suspicion]:
    """Return what in the headers looks like impersonation."""
    hits: list[Suspicion] = []
    if reply_to and _domain(reply_to) != _domain(from_address):
        hits.append(
            Suspicion("sender_mismatch", f"replies go to {reply_to}, not to {from_address}")
        )
    claims_us = own_name.split()[0].lower() in from_name.lower() if from_name else False
    if claims_us and _domain(from_address) != own_domain:
        hits.append(
            Suspicion("sender_mismatch", f"display name {from_name!r} but address {from_address}")
        )
    return hits
