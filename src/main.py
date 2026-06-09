"""Main orchestrator for gcr-sync.

Coordinates the complete synchronization pipeline:
1. Load configuration
2. Authenticate with Google
3. Discover courses
4. Fetch new content (coursework, materials, announcements)
5. Filter duplicates and overdue items
6. Download attachments
7. Save metadata
8. Generate AI summary (optional)
9. Send Telegram notification
10. Update database
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.ai_summary import generate_summary
from src.auth import authenticate, build_classroom_service, build_drive_service
from src.classroom import ClassroomClient
from src.config import AppConfig, load_config
from src.database import Database
from src.downloader import DownloadManager
from src.logger import get_logger, setup_logger
from src.models import ClassroomItem, Course, ItemType, SyncResult
from src.notifier import TelegramNotifier


def _filter_new_items(
    items: list[ClassroomItem],
    db: Database,
    skip_overdue: bool = True,
) -> list[ClassroomItem]:
    """Filter items to only those not yet seen, excluding overdue assignments.

    Args:
        items: All fetched items.
        db: Database for duplicate checking.
        skip_overdue: Whether to skip overdue coursework.

    Returns:
        List of genuinely new items.
    """
    logger = get_logger()
    new_items: list[ClassroomItem] = []

    for item in items:
        # Skip already seen
        if db.is_item_seen(item.item_id, item.course_id):
            continue

        # Skip overdue assignments
        if skip_overdue and item.is_overdue:
            logger.debug(
                "Skipping overdue assignment: '%s' (due: %s)",
                item.title,
                item.due_date_str,
            )
            # Still mark as seen so we don't check again
            db.mark_item_seen(
                item.item_id, item.course_id, item.item_type, item.title
            )
            continue

        new_items.append(item)

    return new_items


def _process_course(
    course: Course,
    client: ClassroomClient,
    downloader: DownloadManager,
    db: Database,
) -> SyncResult:
    """Process a single course: fetch, filter, download, and track.

    Args:
        course: The course to process.
        client: Classroom API client.
        downloader: Download manager.
        db: Database instance.

    Returns:
        SyncResult with all new items found.
    """
    logger = get_logger()
    result = SyncResult(course_name=course.safe_name)

    # Update course in database
    db.upsert_course(
        course_id=course.course_id,
        name=course.name,
        section=course.section,
        state=course.state,
    )

    # ── Coursework ─────────────────────────────────────────────────
    try:
        all_coursework = client.fetch_coursework(course)
        new_coursework = _filter_new_items(all_coursework, db, skip_overdue=True)

        for item in new_coursework:
            logger.info("[%s] New assignment: %s", course.name, item.title)
            downloader.download_item_attachments(item)
            downloader.save_metadata(item)
            db.mark_item_seen(
                item.item_id, item.course_id, item.item_type, item.title
            )

        result.new_coursework = new_coursework
    except Exception as exc:
        logger.error("Error fetching coursework for '%s': %s", course.name, exc)

    # ── Materials ──────────────────────────────────────────────────
    try:
        all_materials = client.fetch_materials(course)
        new_materials = _filter_new_items(all_materials, db, skip_overdue=False)

        for item in new_materials:
            logger.info("[%s] New material: %s", course.name, item.title)
            downloader.download_item_attachments(item)
            downloader.save_metadata(item)
            db.mark_item_seen(
                item.item_id, item.course_id, item.item_type, item.title
            )

        result.new_materials = new_materials
    except Exception as exc:
        logger.error("Error fetching materials for '%s': %s", course.name, exc)

    # ── Announcements ──────────────────────────────────────────────
    try:
        all_announcements = client.fetch_announcements(course)
        new_announcements = _filter_new_items(
            all_announcements, db, skip_overdue=False
        )

        for item in new_announcements:
            logger.info("[%s] New announcement: %s", course.name, item.title)
            downloader.download_item_attachments(item)
            downloader.save_metadata(item)
            db.mark_item_seen(
                item.item_id, item.course_id, item.item_type, item.title
            )

        result.new_announcements = new_announcements
    except Exception as exc:
        logger.error("Error fetching announcements for '%s': %s", course.name, exc)

    if result.has_updates:
        logger.info(
            "[%s] Found %d new item(s)",
            course.name,
            result.total_new_items,
        )
    else:
        logger.info("[%s] No new items", course.name)

    return result


def run_sync(config: AppConfig) -> None:
    """Execute the complete synchronization pipeline.

    Args:
        config: Application configuration.
    """
    logger = get_logger()
    logger.info("=" * 60)
    logger.info("gcr-sync starting...")
    logger.info("=" * 60)

    # Initialize database
    db = Database(config.database_path)
    sync_id = db.start_sync()

    total_new_items = 0
    total_downloads = 0

    try:
        # ── Authentication ─────────────────────────────────────────
        logger.info("Authenticating with Google...")
        creds = authenticate(config.google)

        classroom_service = build_classroom_service(creds)
        drive_service = build_drive_service(creds)

        # ── Course Discovery ───────────────────────────────────────
        client = ClassroomClient(classroom_service)
        courses = client.discover_courses()

        if not courses:
            logger.warning("No active courses found")
            db.complete_sync(sync_id, status="completed", error_message="No active courses")
            return

        # ── Initialize Download Manager ────────────────────────────
        downloader = DownloadManager(
            drive_service=drive_service,
            db=db,
            base_dir=config.subjects_dir,
        )

        # Ensure base directory exists
        config.subjects_dir.mkdir(parents=True, exist_ok=True)

        # ── Process Each Course ────────────────────────────────────
        all_results: list[SyncResult] = []

        for course in courses:
            try:
                result = _process_course(course, client, downloader, db)
                all_results.append(result)
                total_new_items += result.total_new_items
            except Exception as exc:
                logger.error(
                    "Failed to process course '%s': %s", course.name, exc
                )
                # Continue with other courses
                continue

        # ── AI Summary ─────────────────────────────────────────────
        ai_summary = None
        if config.ai.enabled:
            try:
                ai_summary = generate_summary(config.ai, all_results)
                if ai_summary:
                    logger.info("AI Summary: %s", ai_summary)
            except Exception as exc:
                logger.error("AI summary failed (non-blocking): %s", exc)

        # ── Telegram Notification ──────────────────────────────────
        active_results = [r for r in all_results if r.has_updates]

        if active_results:
            try:
                notifier = TelegramNotifier(config.telegram)
                sent = notifier.send_sync_notification(all_results, ai_summary)
                if sent:
                    logger.info("Telegram notification sent")
                else:
                    logger.warning("Telegram notification failed")
            except Exception as exc:
                logger.error("Telegram notification error (non-blocking): %s", exc)
        else:
            logger.info("No new items across all courses — no notification sent")

        # ── Complete Sync ──────────────────────────────────────────
        db.complete_sync(
            sync_id=sync_id,
            courses_synced=len(courses),
            new_items_found=total_new_items,
            downloads_count=total_downloads,
            status="completed",
        )

        logger.info("=" * 60)
        logger.info(
            "Sync complete: %d course(s), %d new item(s)",
            len(courses),
            total_new_items,
        )
        logger.info("=" * 60)

    except SystemExit:
        # Re-raise SystemExit (from auth failures, etc.)
        raise
    except Exception as exc:
        logger.error("Sync failed with error: %s", exc, exc_info=True)
        db.complete_sync(
            sync_id=sync_id,
            courses_synced=0,
            new_items_found=total_new_items,
            status="failed",
            error_message=str(exc),
        )
        raise


def main() -> None:
    """Entry point for gcr-sync."""
    try:
        config = load_config()
        setup_logger(config.log_level)
        run_sync(config)
    except SystemExit as exc:
        sys.exit(exc.code)
    except KeyboardInterrupt:
        print("\n⚠️  Sync interrupted by user")
        sys.exit(130)
    except Exception as exc:
        print(f"❌ Fatal error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
