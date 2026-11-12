"""How people write dates, times, cars and requests: the surface forms of the ground truth.

Code chooses the facts and the exact words that state them. The seed model may only reword the
text around those words.
"""

import random
from datetime import date, time

from opsinbox.records.business import Car
from opsinbox.schemas import Extra, Purpose

_EN_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_ES_DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]  # fmt: skip
_ES_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
              "septiembre", "octubre", "noviembre", "diciembre"]  # fmt: skip


def _ordinal(day: int) -> str:
    suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def date_phrase(value: date, language: str, rng: random.Random, wrong_weekday: bool = False) -> str:
    """Write a date the way a customer might, always with the year."""
    weekday = value.weekday()
    if wrong_weekday:
        weekday = (weekday + rng.choice([1, 2, 5, 6])) % 7
    if language == "es":
        month = _ES_MONTHS[value.month - 1]
        forms = [
            f"{value.day} de {month} de {value.year}",
            f"{_ES_DAYS[weekday]} {value.day} de {month} de {value.year}",
            f"{value.day:02d}/{value.month:02d}/{value.year}",
        ]
        weekday_forms = [forms[1]]
    else:
        month = _EN_MONTHS[value.month - 1]
        forms = [
            f"{value.day} {month} {value.year}",
            f"{_EN_DAYS[weekday]} {value.day} {month} {value.year}",
            f"{month} {value.day}, {value.year}",
            f"{value.day:02d}/{value.month:02d}/{value.year}",
            f"{_ordinal(value.day)} {month} {value.year}",
            f"{_EN_DAYS[weekday][:3]} {value.day} {month[:3]} {value.year}",
        ]
        weekday_forms = [forms[1], forms[5]]
    return rng.choice(weekday_forms if wrong_weekday else forms)


def time_phrase(value: time, language: str, rng: random.Random) -> str:
    """Write a clock time the way a customer might."""
    plain = f"{value.hour:02d}:{value.minute:02d}"
    if language == "es":
        return rng.choice([plain, plain, f"{value.hour}h{value.minute:02d}"])
    hour12 = value.hour % 12 or 12
    suffix = "am" if value.hour < 12 else "pm"
    forms = [
        plain,
        plain,
        f"{hour12}:{value.minute:02d} {suffix}",
        f"{hour12}.{value.minute:02d}{suffix}",
    ]
    if value.minute == 0:
        forms.append(f"{hour12} {suffix}")
        forms.append(f"{hour12}{suffix}")
    return rng.choice(forms)


def car_phrase(car: Car, language: str, rng: random.Random) -> str:
    """Name a car the way a customer might."""
    alias = rng.choice(car.aliases)
    if language == "es":
        return rng.choice([f"el {car.make} {car.model}", f"el {car.name}", f"su {alias}"])
    return rng.choice([f"the {car.make} {car.model}", f"the {car.name}", f"your {alias}",
                       f"the {alias}"])  # fmt: skip


PURPOSE_PHRASES: dict[str, dict[Purpose, list[str]]] = {
    "en": {
        Purpose.WEDDING: ["our wedding", "my daughter's wedding", "my sister's wedding day"],
        Purpose.FILM_SHOOT: ["a film shoot", "a commercial shoot", "a period drama shoot"],
        Purpose.WEEKEND_DRIVE: [
            "a weekend drive",
            "a weekend getaway",
            "a weekend trip along the coast",
        ],
        Purpose.CORPORATE_EVENT: [
            "a corporate event",
            "a client event",
            "our company launch event",
        ],
    },
    "es": {
        Purpose.WEDDING: ["nuestra boda", "la boda de mi hija", "la boda de mi hermano"],
        Purpose.FILM_SHOOT: [
            "un rodaje",
            "el rodaje de un anuncio",
            "una sesión de fotos y rodaje",
        ],
        Purpose.WEEKEND_DRIVE: ["una escapada de fin de semana", "un paseo de fin de semana"],
        Purpose.CORPORATE_EVENT: ["un evento de empresa", "un evento con clientes"],
    },
}

EXTRA_PHRASES: dict[str, dict[Extra, list[str]]] = {
    "en": {
        Extra.CHAUFFEUR: ["with a chauffeur", "with one of your chauffeurs driving"],
        Extra.WEDDING_DECORATION: ["with ribbons and flowers on the car", "with wedding ribbons"],
        Extra.DELIVERY: ["delivered to and collected from the venue",
                         "with delivery and collection at the venue"],
        Extra.PICNIC_HAMPER: ["with a picnic hamper", "plus the picnic hamper for two"],
    },
    "es": {
        Extra.CHAUFFEUR: ["con chófer", "con uno de sus conductores"],
        Extra.WEDDING_DECORATION: ["con cintas y flores en el coche", "con lazos de boda"],
        Extra.DELIVERY: ["con entrega y recogida en el lugar del evento"],
        Extra.PICNIC_HAMPER: ["con la cesta de picnic", "con una cesta de picnic para dos"],
    },
}  # fmt: skip

STYLES = [
    "polite and formal",
    "friendly and chatty",
    "short and to the point",
    "a little rambling, with a personal detail or two",
    "hurried, written on a phone, lower-case greeting",
    "businesslike",
]
