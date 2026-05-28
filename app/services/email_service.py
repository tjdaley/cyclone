"""
app/services/email_service.py - SMTP send + IMAP fetch/move for the intake mailbox.

All email I/O goes through this service (the mail analog of StorageService).
Synchronous on purpose: it is driven by the background poller worker, not the
request path, so plain smtplib / imapclient keeps it simple and robust against
stale connections (each call opens and closes its own connection).

No PII in logs — reference messages by Message-ID / UID, never by address or body.
"""
import email as email_lib
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import formataddr, make_msgid, parseaddr, parsedate_to_datetime
from typing import Optional

from imapclient import IMAPClient
from pydantic import BaseModel, Field

from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)


class InboundEmail(BaseModel):
    """A parsed message fetched from the intake mailbox (transport DTO, not a DB model)."""
    uid: int = Field(..., description="IMAP UID within the selected mailbox")
    message_id: str = Field(..., description="RFC 5322 Message-ID header")
    in_reply_to: Optional[str] = Field(default=None, description="In-Reply-To header, for threading")
    references: Optional[str] = Field(default=None, description="References header, for threading")
    from_address: str = Field(..., description="Sender email address (bare addr-spec, lowercased)")
    from_name: Optional[str] = Field(default=None, description="Sender display name, if present")
    subject: Optional[str] = Field(default=None)
    body_text: str = Field(default="", description="Plain-text body, best-effort")
    date: Optional[str] = Field(default=None, description="ISO timestamp from the Date header")


class EmailService:

    # ── Outbound ──────────────────────────────────────────────────────────

    def send(
        self,
        to_address: str,
        subject: str,
        body_text: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
    ) -> str:
        """
        Send a plain-text email and return its Message-ID.

        Store the returned Message-ID so replies can be threaded. When replying,
        pass the prior message's Message-ID as ``in_reply_to`` (and accumulate
        ``references``) so mail clients thread the conversation.

        :return: The Message-ID assigned to the sent message.
        :rtype: str
        """
        msg = EmailMessage()
        message_id = make_msgid()
        msg["Message-ID"] = message_id
        msg["From"] = formataddr((settings.mail_from_name, settings.mail_from_address))
        msg["To"] = to_address
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = ("%s %s" % (references, in_reply_to)) if references else in_reply_to
        msg.set_content(body_text)

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=context)
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)

        LOGGER.info("email_service.send: message_id=%s in_reply_to=%s", message_id, bool(in_reply_to))
        return message_id

    # ── Inbound ───────────────────────────────────────────────────────────

    def fetch_unseen(self, limit: int = 50) -> list[InboundEmail]:
        """
        Fetch UNSEEN messages from the intake mailbox WITHOUT marking them read
        (BODY.PEEK). The caller marks them \\Seen (or moves them) only after
        successfully processing, so a crash mid-process leaves them re-fetchable.
        """
        results: list[InboundEmail] = []
        with self._imap() as client:
            client.select_folder(settings.imap_mailbox)
            uids = client.search(["UNSEEN"])
            for uid in uids[:limit]:
                fetched = client.fetch([uid], ["BODY.PEEK[]"])
                raw = fetched.get(uid, {}).get(b"BODY[]")
                if not raw:
                    continue
                parsed = self._parse(uid, raw)
                if parsed is not None:
                    results.append(parsed)
        LOGGER.info("email_service.fetch_unseen: count=%s", len(results))
        return results

    def mark_seen(self, uid: int) -> None:
        """Flag a message \\Seen after it has been processed."""
        with self._imap() as client:
            client.select_folder(settings.imap_mailbox)
            client.add_flags([uid], [b"\\Seen"])

    def move_to_spam(self, uid: int) -> None:
        """Move a message out of the inbox into the configured spam folder."""
        with self._imap() as client:
            client.select_folder(settings.imap_mailbox)
            client.move([uid], settings.imap_spam_folder)
        LOGGER.info("email_service.move_to_spam: uid=%s", uid)

    # ── Internals ─────────────────────────────────────────────────────────

    def _imap(self) -> IMAPClient:
        client = IMAPClient(settings.imap_host, port=settings.imap_port, use_uid=True, ssl=True)
        client.login(settings.imap_username, settings.imap_password)
        return client

    @staticmethod
    def _parse(uid: int, raw: bytes) -> Optional[InboundEmail]:
        try:
            msg = email_lib.message_from_bytes(raw, policy=default_policy)
            name, addr = parseaddr(msg.get("From", ""))

            body_text = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and not part.get_filename():
                        body_text = part.get_content()
                        break
            elif msg.get_content_type() == "text/plain":
                body_text = msg.get_content()

            date_hdr = msg.get("Date")
            try:
                date_iso = parsedate_to_datetime(date_hdr).isoformat() if date_hdr else None
            except (TypeError, ValueError):
                date_iso = None

            return InboundEmail(
                uid=uid,
                message_id=(msg.get("Message-ID") or "").strip(),
                in_reply_to=(msg.get("In-Reply-To") or None),
                references=(msg.get("References") or None),
                from_address=addr.lower(),
                from_name=name or None,
                subject=msg.get("Subject"),
                body_text=(body_text or "").strip(),
                date=date_iso,
            )
        except Exception as e:  # noqa: BLE001 — never let one bad message break the batch
            LOGGER.warning("email_service._parse: failed uid=%s err=%s", uid, str(e))
            return None


email_service = EmailService()
