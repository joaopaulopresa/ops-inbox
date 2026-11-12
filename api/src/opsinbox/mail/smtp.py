"""SMTP sender."""

import smtplib
from email.message import EmailMessage

from opsinbox.errors import MailError


class SmtpOutbox:
    """Send messages through an SMTP server."""

    def __init__(self, host: str, port: int, user: str, password: str, starttls: bool) -> None:
        self._host, self._port = host, port
        self._user, self._password, self._starttls = user, password, starttls

    def send(self, message: EmailMessage) -> None:
        """Send ``message``."""
        try:
            with smtplib.SMTP(self._host, self._port, timeout=30) as client:
                if self._starttls:
                    client.starttls()
                if self._user:
                    client.login(self._user, self._password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"SMTP send failed: {exc}") from exc
