"""Render synthetic supplier invoices as PDF, and as a degraded "scan" of that PDF."""

import hashlib
import io
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape

from PIL import Image, ImageFilter
from weasyprint import HTML

from opsinbox.pdf import render_page
from opsinbox.seedgen.licence import _noise
from opsinbox.seedgen.world import Supplier


@dataclass(frozen=True)
class InvoiceLine:
    """One line as printed."""

    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal


@dataclass(frozen=True)
class InvoiceData:
    """What is printed on an invoice: also the ground truth for reading it."""

    supplier: Supplier
    number: str
    issued_on: date
    lines: tuple[InvoiceLine, ...]
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    style: int = 0


_STYLES = [
    ("Helvetica, Arial, sans-serif", "#1c3d5a", "left"),
    ("Georgia, 'Times New Roman', serif", "#5a2a1c", "right"),
    ("'Courier New', monospace", "#222222", "left"),
]
_DATE_FORMATS = ["%d %B %Y", "%d/%m/%Y", "%Y-%m-%d"]


def printed_date(data: InvoiceData) -> str:
    """The invoice date as printed on this invoice."""
    return data.issued_on.strftime(_DATE_FORMATS[data.style % len(_DATE_FORMATS)])


def invoice_html(data: InvoiceData, customer_name: str, customer_address: str) -> str:
    """Return the HTML of the invoice."""
    font, colour, align = _STYLES[data.style % len(_STYLES)]
    rows = "".join(
        f"<tr><td>{escape(line.description)}</td><td class=n>{line.quantity}</td>"
        f"<td class=n>{line.unit_price:.2f}</td><td class=n>{line.amount:.2f}</td></tr>"
        for line in data.lines
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 22mm 18mm; }}
body {{ font-family: {font}; font-size: 11pt; color: #1a1a1a; }}
h1 {{ color: {colour}; font-size: 24pt; margin: 0 0 2mm; text-align: {align}; }}
.supplier {{ text-align: {align}; color: #444; margin-bottom: 10mm; }}
.meta td {{ padding: 1mm 6mm 1mm 0; }}
table.lines {{ width: 100%; border-collapse: collapse; margin-top: 8mm; }}
table.lines th {{ text-align: left; border-bottom: 1.5pt solid {colour}; padding: 2mm 1mm; }}
table.lines td {{ border-bottom: 0.5pt solid #bbb; padding: 2mm 1mm; }}
.n {{ text-align: right !important; }}
table.totals {{ margin-left: auto; margin-top: 6mm; }}
table.totals td {{ padding: 1mm 0 1mm 10mm; }}
.due td {{ font-weight: bold; border-top: 1.5pt solid {colour}; }}
.foot {{ margin-top: 14mm; color: #555; font-size: 9pt; }}
</style></head><body>
<h1>INVOICE</h1>
<div class="supplier"><strong>{escape(data.supplier.name)}</strong><br>
{escape(data.supplier.address)}<br>Tax ID {escape(data.supplier.tax_id)}</div>
<table class="meta">
<tr><td>Invoice number</td><td><strong>{escape(data.number)}</strong></td></tr>
<tr><td>Invoice date</td><td>{printed_date(data)}</td></tr>
<tr><td>Bill to</td><td>{escape(customer_name)}<br>{escape(customer_address)}</td></tr>
</table>
<table class="lines"><tr><th>Description</th><th class=n>Qty</th><th class=n>Unit price</th>
<th class=n>Amount</th></tr>{rows}</table>
<table class="totals">
<tr><td>Subtotal</td><td class=n>{data.subtotal:.2f}</td></tr>
<tr><td>Tax 20%</td><td class=n>{data.tax:.2f}</td></tr>
<tr class="due"><td>Total due EUR</td><td class=n>{data.total:.2f}</td></tr>
</table>
<p class="foot">Payment within 30 days. Synthetic document for software testing.</p>
</body></html>"""


def invoice_pdf(data: InvoiceData, customer_name: str, customer_address: str) -> bytes:
    """Render the invoice as a PDF with a text layer."""
    html = invoice_html(data, customer_name, customer_address)
    # A fixed identifier makes the PDF byte-for-byte reproducible (WeasyPrint picks a random one).
    pdf: bytes = HTML(string=html).write_pdf(
        pdf_identifier=hashlib.sha256(html.encode()).digest()[:16]
    )
    return pdf


def scanned_invoice(pdf: bytes, seed: int) -> bytes:
    """Turn the PDF into a JPEG that looks like a flatbed scan (no text layer)."""
    rng = random.Random(seed)
    page = render_page(pdf, 0, scale=1.6)
    page = page.rotate(
        rng.uniform(-1.5, 1.5), resample=Image.Resampling.BICUBIC, fillcolor="#fafaf7"
    )
    page = Image.blend(page, page.convert("L").convert("RGB"), 0.9)
    page = page.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8)))
    page = _noise(page, rng, rng.uniform(3.0, 6.0))
    buffer = io.BytesIO()
    page.save(buffer, format="JPEG", quality=rng.randint(50, 70))
    return buffer.getvalue()
