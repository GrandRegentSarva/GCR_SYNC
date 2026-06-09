"""AI summary generation using Groq for gcr-sync.

Generates concise digests of newly discovered classroom content
using the Groq API. Only processes metadata — never reads
downloaded files or attachment contents.
"""

from __future__ import annotations

from typing import Optional

from src.config import AIConfig
from src.logger import get_logger
from src.models import SyncResult

logger = get_logger()

SYSTEM_PROMPT = """You are a concise academic assistant. You will receive metadata about newly discovered items from Google Classroom courses. Generate a brief, plain-text summary of the updates.

Rules:
- Maximum 100 words
- Plain text only, no markdown formatting
- No bullet points or lists
- Write in complete sentences
- Be factual and concise
- Do not hallucinate or add information not provided
- Do not reference file contents — you only see metadata
- Mention course names, item types, titles, and due dates
- Use natural language"""


def _build_metadata_prompt(results: list[SyncResult]) -> str:
    """Build the metadata prompt from sync results.

    Constructs a structured text representation of all new items
    for the AI to summarize. Only includes metadata, never file contents.

    Args:
        results: List of sync results with new items.

    Returns:
        Formatted metadata string for the AI prompt.
    """
    lines: list[str] = []

    for result in results:
        if not result.has_updates:
            continue

        for item in result.new_coursework:
            lines.append(f"{result.course_name}:")
            lines.append(f"  - New Assignment: {item.title}")
            if item.due_date_str:
                lines.append(f"    Due: {item.due_date_str}")
            lines.append("")

        for item in result.new_materials:
            lines.append(f"{result.course_name}:")
            lines.append(f"  - New Material: {item.title}")
            lines.append("")

        for item in result.new_announcements:
            lines.append(f"{result.course_name}:")
            lines.append(f"  - Announcement: {item.title}")
            lines.append("")

    return "\n".join(lines).strip()


def generate_summary(
    config: AIConfig,
    results: list[SyncResult],
) -> Optional[str]:
    """Generate an AI summary of new classroom content.

    Uses the Groq API to create a concise digest. If AI is disabled
    or the API call fails, returns None without blocking the sync.

    Args:
        config: AI configuration with API key and model.
        results: List of sync results to summarize.

    Returns:
        Summary string, or None if unavailable.
    """
    if not config.enabled:
        logger.debug("AI summary is disabled")
        return None

    if not config.api_key:
        logger.warning("AI summary enabled but no API key configured")
        return None

    # Filter to results with updates
    active_results = [r for r in results if r.has_updates]
    if not active_results:
        logger.debug("No new items to summarize")
        return None

    metadata_prompt = _build_metadata_prompt(active_results)
    if not metadata_prompt:
        return None

    logger.info("Generating AI summary via Groq (%s)...", config.model)

    try:
        from groq import Groq

        client = Groq(api_key=config.api_key)

        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": f"Summarize these new Google Classroom updates:\n\n{metadata_prompt}",
                },
            ],
            model=config.model,
            temperature=0.3,
            max_tokens=200,
        )

        summary = chat_completion.choices[0].message.content
        if summary:
            summary = summary.strip()
            logger.info("AI summary generated (%d chars)", len(summary))
            return summary

        logger.warning("AI returned empty summary")
        return None

    except ImportError:
        logger.error("Groq package not installed. Run: pip install groq")
        return None
    except Exception as exc:
        logger.error("AI summary generation failed: %s", exc)
        return None
