"""IMAP mailbox reader. Works with any IMAP4rev1 server; GreenMail in Docker Compose."""

import contextlib
import imaplib
import re

from opsinbox.errors import MailError
from opsinbox.mail.base import RawMail

_UID = re.compile(rb"UID (\d+)")


class ImapInbox:
    """Read a mailbox incrementally by UID. Read-only: messages are never altered."""

    def __init__(
        self, host: str, port: int, user: str, password: str, mailbox: str, ssl: bool
    ) -> None:
        self.name = f"{user}@{host}/{mailbox}"
        self._host, self._port, self._ssl = host, port, ssl
        self._user, self._password, self._mailbox = user, password, mailbox

    def _connect(self) -> imaplib.IMAP4:
        try:
            cls = imaplib.IMAP4_SSL if self._ssl else imaplib.IMAP4
            client = cls(self._host, self._port)
            client.login(self._user, self._password)
            status, _ = client.select(self._mailbox, readonly=True)
            if status != "OK":
                raise MailError(f"cannot select mailbox {self._mailbox}")
            return client
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailError(f"IMAP connection failed: {exc}") from exc

    def uid_validity(self) -> int:
        """Return UIDVALIDITY of the mailbox."""
        client = self._connect()
        try:
            _, data = client.response("UIDVALIDITY")
            return int(data[0]) if data and data[0] else 0
        finally:
            client.logout()

    def fetch_after(self, last_uid: int) -> list[RawMail]:
        """Return the messages whose UID is greater than ``last_uid``, oldest first."""
        client = self._connect()
        try:
            status, data = client.uid("SEARCH", None, f"UID {last_uid + 1}:*")  # type: ignore[arg-type]
            if status != "OK":
                raise MailError("IMAP search failed")
            # "n:*" always matches the newest message, even when its UID is not above last_uid.
            uids = sorted(u for u in (int(x) for x in (data[0] or b"").split()) if u > last_uid)
            mails: list[RawMail] = []
            for uid in uids:
                status, parts = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if status != "OK":
                    raise MailError(f"IMAP fetch failed for UID {uid}")
                for part in parts:
                    if isinstance(part, tuple) and isinstance(part[1], bytes):
                        mails.append(RawMail(uid=uid, raw=part[1]))
                        break
            return mails
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailError(f"IMAP read failed: {exc}") from exc
        finally:
            with contextlib.suppress(OSError, imaplib.IMAP4.error):
                client.logout()
