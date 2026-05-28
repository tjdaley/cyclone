"""
app/services/telegram_service.py - Telegram Bot API sender for agent escalations.

Used to ping a lead responder's personal chat when the agent escalates. Failures
are logged, never raised — a Telegram outage must not crash the escalation path,
which always also sends email as the primary channel.
"""
import httpx

from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_API_BASE = "https://api.telegram.org"


class TelegramService:

    def send(self, chat_id: str, text: str) -> bool:
        """
        Send a message to a Telegram chat.

        :param chat_id: The responder's Telegram chat ID (staff.telegram_id).
        :param text: Message body (Markdown).
        :return: True on success, False on any failure. Never raises.
        :rtype: bool
        """
        if not settings.telegram_bot_token:
            LOGGER.warning("telegram_service.send: no bot token configured; skipping")
            return False
        if not chat_id:
            LOGGER.warning("telegram_service.send: empty chat_id; skipping")
            return False

        url = "%s/bot%s/sendMessage" % (_API_BASE, settings.telegram_bot_token)
        try:
            resp = httpx.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=10.0,
            )
            if resp.status_code != 200:
                LOGGER.warning("telegram_service.send: non-200 status=%s", resp.status_code)
                return False
            return True
        except httpx.HTTPError as e:
            LOGGER.warning("telegram_service.send: request failed err=%s", str(e))
            return False


telegram_service = TelegramService()
