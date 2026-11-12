"""Contract and invoice generation, and the drafting of replies.

Templates are HTML (Jinja2, autoescaped) rendered to PDF by WeasyPrint. Every number, amount and
date in a document is computed by code and injected; the model only writes the words of a reply,
from a list of facts code gives it, and the facts are checked to be in the draft afterwards.
"""

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import pypdfium2
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy.orm import Session
from weasyprint import HTML

from opsinbox.extraction import prompts
from opsinbox.llm import ModelClient, ModelRequest
from opsinbox.records.business import Business, Car
from opsinbox.records.models import AppSetting
from opsinbox.records.pricing import Quote


def templates_dir() -> Path:
    """Location of the templates in the repository or the container."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "templates"
        if (candidate / "contract.html").exists():
            return candidate
    raise FileNotFoundError("templates/ not found")


def money(value: Decimal) -> str:
    """Format an amount as ``1,164.00``."""
    return f"{value:,.2f}"


def percent(value: Decimal) -> str:
    """Format a rate as ``20%``."""
    return f"{(value * 100).normalize():f}%"


def when(value: datetime) -> str:
    """Format a rental time as ``Sat 12 Jun 2027, 10:00``."""
    return value.strftime("%a %d %b %Y, %H:%M")


@lru_cache
def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(templates_dir()),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    env.globals.update(money=money, percent=percent)
    return env


@dataclass(frozen=True)
class DocumentContext:
    """Everything a contract or an invoice shows."""

    business: Business
    car: Car
    customer: dict[str, Any]
    booking: dict[str, Any]
    quote: Quote
    licence: dict[str, Any] | None
    purpose: str
    issued_on: date


def render_html(kind: str, number: str, context: DocumentContext, source: str | None = None) -> str:
    """Render a template to HTML. ``source`` replaces the stored template (owner preview)."""
    env = _environment()
    template = env.from_string(source) if source is not None else env.get_template(f"{kind}.html")
    css = (templates_dir() / "_base.css").read_text(encoding="utf-8")
    return template.render(
        css=css,
        number=number,
        business=context.business,
        car=context.car,
        customer=context.customer,
        booking=context.booking,
        quote=context.quote,
        licence=context.licence,
        purpose=context.purpose.replace("_", " "),
        issued_on=context.issued_on.strftime("%d %B %Y"),
        pickup_at=when(context.booking["pickup_at"]),
        return_at=when(context.booking["return_at"]),
    )


def pdf_page_png(pdf: bytes, page: int = 0, scale: float = 1.6) -> bytes:
    """One page of a PDF as a PNG (browsers without a PDF viewer can still show it)."""
    document = pypdfium2.PdfDocument(pdf)
    try:
        if not 0 <= page < len(document):
            raise IndexError(f"page {page} of {len(document)}")
        image = document[page].render(scale=scale).to_pil().convert("RGB")
    finally:
        document.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def template_source(session: Session, kind: str) -> str:
    """The template the owner saved, or the shipped one."""
    row = session.get(AppSetting, f"template:{kind}")
    if row is not None:
        return str(row.value)
    return (templates_dir() / f"{kind}.html").read_text(encoding="utf-8")


def render_pdf(html: str) -> bytes:
    """Render HTML to PDF bytes."""
    identifier = hashlib.sha256(html.encode()).digest()[:16]  # stable document identifier
    pdf: bytes = HTML(string=html, base_url=str(templates_dir())).write_pdf(
        pdf_identifier=identifier
    )
    return pdf


# ----------------------------------------------------------------------------------- replies


@dataclass(frozen=True)
class ReplyFacts:
    """What a reply must say. ``required`` strings must appear verbatim in the draft."""

    language: str
    customer_name: str
    situation: str
    facts: list[str]
    required: list[str]


def fallback_reply(reply: ReplyFacts) -> str:
    """A plain reply built by code, used when the drafted one fails the checks."""
    first = reply.customer_name.split()[0] if reply.customer_name else ""
    if reply.language == "es":
        greeting, closing = f"Hola {first}:", "Un cordial saludo,"
    else:
        greeting, closing = f"Dear {first},", "Kind regards,"
    lines = "\n".join(f"- {fact}" for fact in reply.facts)
    return f"{greeting}\n\n{reply.situation}\n\n{lines}\n\n{closing}\nLarkspur Classic Car Hire"


def draft_problems(body: str, reply: ReplyFacts) -> list[str]:
    """Why a drafted reply cannot be used; empty when it can."""
    found = [f"missing {item!r}" for item in reply.required if item not in body]
    allowed = " ".join(reply.facts)
    for amount in re.findall(r"\d[\d,]*\.\d{2}", body):
        if amount not in allowed:
            found.append(f"amount {amount} not among the facts")
    if len(body) > 2500:
        found.append("too long")
    return found


def draft_reply(client: ModelClient | None, model: str, reply: ReplyFacts) -> tuple[str, str]:
    """Return the reply body and its origin (``model`` or ``template``)."""
    if client is None:
        return fallback_reply(reply), "template"
    language = "Spanish" if reply.language == "es" else "English"
    facts = "\n".join(f"- {fact}" for fact in reply.facts)
    user = (
        f"Write the reply in {language} to {reply.customer_name}.\n"
        f"Situation: {reply.situation}\nFacts to convey, exactly as written:\n{facts}"
    )
    try:
        response = client.complete(
            ModelRequest(
                model=model,
                system=prompts.DRAFT,
                user=user,
                temperature=0.3,
                max_tokens=700,
                reasoning=False,
                tag="draft",
            )
        )
    except Exception:
        return fallback_reply(reply), "template"
    body = response.text.strip()
    if draft_problems(body, reply):
        return fallback_reply(reply), "template"
    return body, "model"
