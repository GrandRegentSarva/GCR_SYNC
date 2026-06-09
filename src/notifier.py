"""Telegram notification system for gcr-sync.

Sends consolidated update messages to a Telegram chat
when new classroom content is discovered.
"""

from __future__ import annotations

from typing import Optional

import requests

from src.config import TelegramConfig
from src.logger import get_logger
from src.models import ClassroomItem, ItemType, SyncResult

logger = get_logger()

# Telegram message length limit
MAX_MESSAGE_LENGTH = 4096


def _build_course_section(result: SyncResult) -> str:
    """Build the notification section for a single course.

    Args:
        result: Sync result for one course.

    Returns:
        Formatted string for the course section.
    """
    lines: list[str] = []
    lines.append(f"[{result.course_name}]")
    lines.append("")

    # Materials
    for item in result.new_materials:
        lines.append("📄 New Material")
        lines.append(item.title)
        if item.attachments:
            for att in item.attachments:
                if att.title and att.title != item.title:
                    lines.append(f"  📎 {att.title}")
        lines.append("")

    # Coursework (assignments)
    for item in result.new_coursework:
        lines.append("📝 New Assignment")
        lines.append(item.title)
        if item.due_date_str:
            lines.append(f"Due: {item.due_date_str}")
        if item.attachments:
            for att in item.attachments:
                lines.append(f"  📎 {att.title}")
        lines.append("")

    # Announcements
    for item in result.new_announcements:
        lines.append("📢 New Announcement")
        lines.append(item.title)
        if item.attachments:
            for att in item.attachments:
                lines.append(f"  📎 {att.title}")
        lines.append("")

    return "\n".join(lines).strip()


def build_notification_message(
    results: list[SyncResult],
    ai_summary: Optional[str] = None,
) -> str:
    """Build the complete Telegram notification message.

    Constructs a consolidated message from all sync results,
    optionally including an AI-generated summary.

    Args:
        results: List of sync results with new items.
        ai_summary: Optional AI-generated summary text.

    Returns:
        Formatted notification message string.
    """
    # Filter to only results with updates
    active_results = [r for r in results if r.has_updates]

    if not active_results:
        return ""

    lines: list[str] = []
    lines.append("🎓 Classroom Update")
    lines.append("")

    for i, result in enumerate(active_results):
        section = _build_course_section(result)
        if section:
            lines.append(section)
            lines.append("")
            lines.append("------------------")
            lines.append("")

    # Add AI summary if available
    if ai_summary:
        lines.append("🤖 Summary")
        lines.append("")
        lines.append(ai_summary)
        lines.append("")

    message = "\n".join(lines).strip()

    # Remove trailing separator if no summary
    if message.endswith("------------------"):
        message = message[:-len("------------------")].strip()

    # Truncate if too long for Telegram
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[: MAX_MESSAGE_LENGTH - 20] + "\n\n... (truncated)"

    return message


class TelegramNotifier:
    """Sends notifications via the Telegram Bot API.

    Attributes:
        config: Telegram bot configuration.
    """

    def __init__(self, config: TelegramConfig) -> None:
        """Initialize the Telegram notifier.

        Args:
            config: Telegram bot configuration.
        """
        self.config = config

    def _send_single_message(self, text: str) -> bool:
        """Send a single text message to the configured Telegram chat.

        Args:
            text: Message text to send (must be <= 4096 chars).

        Returns:
            True if the message was sent successfully.
        """
        try:
            payload = {
                "chat_id": self.config.chat_id,
                "text": text,
            }
            response = requests.post(
                self.config.send_message_url,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()

            result = response.json()
            if result.get("ok"):
                return True
            else:
                logger.error(
                    "Telegram API returned error: %s",
                    result.get("description", "Unknown error"),
                )
                return False

        except requests.Timeout:
            logger.error("Telegram API request timed out")
            return False
        except requests.ConnectionError:
            logger.error("Failed to connect to Telegram API")
            return False
        except requests.RequestException as exc:
            logger.error("Telegram notification failed: %s", exc)
            return False

    def send_message(self, text: str) -> bool:
        """Send a text message, splitting into multiple messages if needed.

        Telegram has a 4096 character limit per message. This method
        splits long messages at section boundaries (------------------).

        Args:
            text: Message text to send.

        Returns:
            True if all message parts were sent successfully.
        """
        if not text.strip():
            logger.debug("Empty message, skipping Telegram send")
            return False

        # If message fits in one send, just send it
        if len(text) <= MAX_MESSAGE_LENGTH:
            success = self._send_single_message(text)
            if success:
                logger.info("Telegram notification sent successfully")
            return success

        # Split at section boundaries for long messages
        sections = text.split("------------------")
        chunks: list[str] = []
        current_chunk = ""

        for section in sections:
            candidate = current_chunk + section
            if current_chunk:
                candidate = current_chunk + "------------------" + section

            if len(candidate) <= MAX_MESSAGE_LENGTH:
                current_chunk = candidate
            else:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = section

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        # Send each chunk
        all_success = True
        for i, chunk in enumerate(chunks):
            if len(chunk) > MAX_MESSAGE_LENGTH:
                chunk = chunk[:MAX_MESSAGE_LENGTH - 20] + "\n\n... (truncated)"
            success = self._send_single_message(chunk)
            if success:
                logger.info("Telegram message part %d/%d sent", i + 1, len(chunks))
            else:
                all_success = False

        return all_success

    def send_sync_notification(
        self,
        results: list[SyncResult],
        ai_summary: Optional[str] = None,
    ) -> bool:
        """Send a consolidated sync notification.

        Only sends if there are actual new items to report.

        Args:
            results: List of sync results.
            ai_summary: Optional AI-generated summary.

        Returns:
            True if notification was sent (or no notification needed).
        """
        message = build_notification_message(results, ai_summary)

        if not message:
            logger.info("No new items to notify about")
            return True

        return self.send_message(message)
