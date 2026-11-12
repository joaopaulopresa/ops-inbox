"""Render the synthetic driving licence and degrade it into a "scan" or a "phone photo".

The design is invented: a licence of the fictional Republic of Norvalle, with its own layout and
field labels, a drawn silhouette in place of a photograph, and SPECIMEN printed across it. It does
not imitate any real government document.
"""

import io
import random
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

CARD_W, CARD_H = 1012, 638
COUNTRY = "REPUBLIC OF NORVALLE"
_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/dejavu/")

Degradation = Literal["clean", "scan", "photo"]


@dataclass(frozen=True)
class LicenceData:
    """What is printed on a licence: also the ground truth for reading it."""

    holder_name: str
    licence_number: str
    date_of_birth: date
    issued_on: date
    expires_on: date
    classes: tuple[str, ...]


@lru_cache
def _font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    name = (
        "DejaVuSansMono-Bold.ttf" if mono else ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    )
    for directory in _FONT_DIRS:
        try:
            return ImageFont.truetype(directory + name, size)
        except OSError:
            continue
    fallback = ImageFont.load_default(size=size)
    assert isinstance(fallback, ImageFont.FreeTypeFont)
    return fallback


def format_licence_date(value: date) -> str:
    """Dates are printed as ``14 MAR 2031`` on the licence."""
    return value.strftime("%d %b %Y").upper()


def render_licence(data: LicenceData) -> Image.Image:
    """Draw the licence card as a clean RGB image."""
    card = Image.new("RGB", (CARD_W, CARD_H), "#eef3ee")
    draw = ImageDraw.Draw(card)

    # Background guilloche: thin sine lines, purely decorative.
    for index in range(26):
        points = [
            (x, 150 + index * 19 + 9 * np.sin((x + index * 37) / 46.0)) for x in range(0, CARD_W, 6)
        ]
        draw.line(points, fill="#dbe7df", width=2)

    draw.rectangle((0, 0, CARD_W, 112), fill="#1d5c52")
    draw.rectangle((0, 112, CARD_W, 120), fill="#d9a441")
    draw.text((36, 20), COUNTRY, font=_font(34, bold=True), fill="#ffffff")
    draw.text((36, 66), "DRIVING LICENCE", font=_font(26), fill="#d6ebe4")
    draw.ellipse((CARD_W - 112, 18, CARD_W - 36, 94), outline="#d9a441", width=5)
    draw.text((CARD_W - 97, 38), "NV", font=_font(30, bold=True), fill="#d9a441")

    # Silhouette placeholder: no face, no photograph.
    draw.rounded_rectangle(
        (36, 152, 286, 472), radius=12, fill="#cfdcd5", outline="#8fa69b", width=3
    )
    draw.ellipse((116, 196, 206, 286), fill="#9db3a8")
    draw.pieslice((76, 300, 246, 520), 180, 360, fill="#9db3a8")
    draw.text((92, 440), "NO PHOTO", font=_font(20, bold=True), fill="#5d7469")

    label, value = _font(19), _font(31, bold=True)
    rows = [
        ("HOLDER", data.holder_name.upper(), 148),
        ("LICENCE NO.", data.licence_number, 232),
        ("DATE OF BIRTH", format_licence_date(data.date_of_birth), 316),
    ]
    for caption, text, y in rows:
        draw.text((326, y), caption, font=label, fill="#48635a")
        draw.text((326, y + 26), text, font=value, fill="#14241f")
    draw.text((326, 400), "ISSUED", font=label, fill="#48635a")
    draw.text((326, 426), format_licence_date(data.issued_on), font=value, fill="#14241f")
    draw.text((640, 400), "VALID UNTIL", font=label, fill="#48635a")
    draw.text((640, 426), format_licence_date(data.expires_on), font=value, fill="#14241f")
    draw.text((36, 500), "CLASSES", font=label, fill="#48635a")
    draw.text((36, 526), "  ".join(data.classes), font=value, fill="#14241f")
    draw.text(
        (36, 590),
        "Synthetic document for software testing. Not valid for any purpose.",
        font=_font(17),
        fill="#48635a",
    )

    # SPECIMEN watermark across the card.
    mark = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    ImageDraw.Draw(mark).text(
        (CARD_W // 2, CARD_H // 2 + 40),
        "SPECIMEN",
        font=_font(120, bold=True),
        fill=(190, 40, 40, 58),
        anchor="mm",
    )
    mark = mark.rotate(18, resample=Image.Resampling.BICUBIC)
    card = Image.alpha_composite(card.convert("RGBA"), mark).convert("RGB")
    return card


def _noise(image: Image.Image, rng: random.Random, sigma: float) -> Image.Image:
    array = np.asarray(image).astype(np.float32)
    generator = np.random.default_rng(rng.getrandbits(32))
    array += generator.normal(0.0, sigma, array.shape).astype(np.float32)
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))


def _as_scan(card: Image.Image, rng: random.Random) -> Image.Image:
    """A flatbed scan: on a white page, slightly skewed, greyish, noisy."""
    page = Image.new("RGB", (1240, 900), "#f7f7f4")
    page.paste(card, (rng.randint(80, 150), rng.randint(90, 170)))
    page = page.rotate(
        rng.uniform(-3.0, 3.0), resample=Image.Resampling.BICUBIC, fillcolor="#f7f7f4"
    )
    grey = page.convert("L").convert("RGB")
    page = Image.blend(page, grey, rng.uniform(0.55, 1.0))
    page = page.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.1)))
    return _noise(page, rng, rng.uniform(4.0, 9.0))


def _perspective_coefficients(
    source: list[tuple[float, float]], target: list[tuple[float, float]]
) -> list[float]:
    rows: list[list[float]] = []
    for (sx, sy), (tx, ty) in zip(source, target, strict=True):
        rows.append([tx, ty, 1, 0, 0, 0, -sx * tx, -sx * ty])
        rows.append([0, 0, 0, tx, ty, 1, -sy * tx, -sy * ty])
    matrix = np.array(rows, dtype=np.float64)
    vector = np.array(source, dtype=np.float64).reshape(8)
    solved: list[float] = np.linalg.solve(matrix, vector).tolist()
    return solved


def _as_photo(card: Image.Image, rng: random.Random) -> Image.Image:
    """A phone photo: on a table, tilted, in perspective, with a shadow and soft focus."""
    width, height = 1280, 960
    table = Image.new(
        "RGB", (width, height), rng.choice(["#6b4f3a", "#3f4a55", "#8a7f72", "#2f3b33"])
    )
    table = _noise(table, rng, 10.0).filter(ImageFilter.GaussianBlur(1.5))

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer.paste(card, ((width - CARD_W) // 2, (height - CARD_H) // 2))
    layer = layer.rotate(rng.uniform(-9.0, 9.0), resample=Image.Resampling.BICUBIC)

    def jitter() -> float:
        return rng.uniform(0, 70)

    corners = [(0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height))]
    moved = [
        (jitter(), jitter()),
        (width - jitter(), jitter()),
        (width - jitter(), height - jitter()),
        (jitter(), height - jitter()),
    ]
    layer = layer.transform(
        (width, height),
        Image.Transform.PERSPECTIVE,
        _perspective_coefficients(corners, moved),
        resample=Image.Resampling.BICUBIC,
    )
    photo = Image.alpha_composite(table.convert("RGBA"), layer).convert("RGB")

    # A soft shadow falling across the picture.
    xs = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    ys = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    angle = rng.uniform(0, np.pi)
    ramp = xs * np.cos(angle) + ys * np.sin(angle)
    shade = 1.0 - rng.uniform(0.18, 0.42) * np.clip((ramp - rng.uniform(0.2, 0.5)) * 3.0, 0, 1)
    array = np.asarray(photo).astype(np.float32) * shade[:, :, None]
    photo = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
    photo = photo.filter(ImageFilter.GaussianBlur(rng.uniform(0.7, 1.6)))
    return _noise(photo, rng, rng.uniform(3.0, 7.0))


def licence_image(data: LicenceData, degradation: Degradation, seed: int) -> bytes:
    """Return the licence as JPEG bytes (PNG when clean), deterministically for a seed."""
    rng = random.Random(seed)
    card = render_licence(data)
    buffer = io.BytesIO()
    if degradation == "clean":
        card.save(buffer, format="PNG", optimize=True)
    elif degradation == "scan":
        _as_scan(card, rng).save(buffer, format="JPEG", quality=rng.randint(45, 70))
    else:
        _as_photo(card, rng).save(buffer, format="JPEG", quality=rng.randint(40, 65))
    return buffer.getvalue()
