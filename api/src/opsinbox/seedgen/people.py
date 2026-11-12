"""Invented people. Surnames are made up so that no real person is named by accident."""

import random
from dataclasses import dataclass
from datetime import date, timedelta

from opsinbox.seedgen.licence import LicenceData

_FIRST_EN = [
    "Ada",
    "Bram",
    "Cora",
    "Dexter",
    "Elsie",
    "Felix",
    "Greta",
    "Hugo",
    "Imogen",
    "Jasper",
    "Kit",
    "Lena",
    "Milo",
    "Nell",
    "Otis",
    "Posy",
    "Quentin",
    "Rhea",
    "Silas",
    "Tess",
    "Una",
    "Victor",
    "Willa",
    "Zane",
]
_FIRST_ES = [
    "Alba",
    "Bruno",
    "Carmen",
    "Dario",
    "Elena",
    "Fabián",
    "Gloria",
    "Héctor",
    "Inés",
    "Joaquín",
    "Lucía",
    "Mateo",
    "Nuria",
    "Óscar",
    "Pilar",
    "Ramón",
    "Sofía",
    "Tomás",
]
_LAST_EN = [
    "Quillfeather",
    "Marrowby",
    "Thistlewood",
    "Penhallow",
    "Ashgrove",
    "Wintermere",
    "Corbyngton",
    "Larkmoor",
    "Dunsmere",
    "Fennimore",
    "Holloway-Pike",
    "Braithwell",
    "Oakhampstead",
    "Sedgewick-Rowe",
    "Tarnbrook",
    "Yarrowfield",
]
_LAST_ES = [
    "Valdecasas",
    "Montelongo",
    "Rioseco",
    "Arrizabal",
    "Peñalver",
    "Torrealta",
    "Villamediana",
    "Casablanca-Ruiz",
    "Olmedilla",
    "Sotomontes",
    "Fuentelsaz",
    "Zarzalejo",
]
_DOMAINS = [
    "mailbox.example",
    "postbox.example",
    "inboxly.example",
    "correo.example",
    "fastmail.example",
    "webpost.example",
]


@dataclass(frozen=True)
class Person:
    """A synthetic customer."""

    first_name: str
    last_name: str
    email: str
    phone: str
    language: str

    @property
    def name(self) -> str:
        """Full name."""
        return f"{self.first_name} {self.last_name}"


def _ascii(text: str) -> str:
    table = str.maketrans("áéíóúñÁÉÍÓÚÑ", "aeiounAEIOUN")
    return text.translate(table).lower().replace(" ", "").replace("-", "")


def make_person(rng: random.Random, language: str = "en") -> Person:
    """Invent a person who writes in ``language``."""
    first = rng.choice(_FIRST_ES if language == "es" else _FIRST_EN)
    last = rng.choice(_LAST_ES if language == "es" else _LAST_EN)
    separator = rng.choice([".", "_", ""])
    email = f"{_ascii(first)}{separator}{_ascii(last)}{rng.choice(['', str(rng.randint(2, 99))])}"
    phone = f"+70 {rng.randint(200, 899)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}"
    return Person(first, last, f"{email}@{rng.choice(_DOMAINS)}", phone, language)


def make_licence(
    rng: random.Random,
    person: Person,
    today: date,
    *,
    expires_on: date | None = None,
    age: int | None = None,
    classes: tuple[str, ...] | None = None,
) -> LicenceData:
    """Invent a licence for ``person``. Defaults: adult over 25, class P, valid for years."""
    years = age if age is not None else rng.randint(27, 68)
    born = date(today.year - years, rng.randint(1, 12), rng.randint(1, 28))
    issued = today - timedelta(days=rng.randint(200, 3200))
    expiry = expires_on or (today + timedelta(days=rng.randint(500, 3000)))
    letters = "ABCDEFGHJKLMNPRSTUVWXYZ"
    number = f"NV-{rng.randint(100000, 999999)}-{rng.choice(letters)}{rng.choice(letters)}"
    chosen = (
        classes
        if classes is not None
        else tuple(sorted({"P", *rng.sample(["M", "T"], rng.randint(0, 1))}))
    )
    return LicenceData(person.name, number, born, issued, expiry, chosen)
