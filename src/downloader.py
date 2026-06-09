"""Attachment download manager for gcr-sync.

Handles downloading files from Google Drive and direct URLs.
Supports Google Workspace file exports, retry logic, and
safe filename handling to prevent overwrites.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Optional

import requests
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from src.database import Database
from src.logger import get_logger
from src.models import Attachment, ClassroomItem, DownloadResult, ItemType

logger = get_logger()

# Google Workspace MIME types and their export formats
GOOGLE_EXPORT_MAP: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/pdf",
        ".pdf",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/pdf",
        ".pdf",
    ),
    "application/vnd.google-apps.drawing": (
        "application/pdf",
        ".pdf",
    ),
}

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
DOWNLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB


def _safe_filename(directory: Path, filename: str) -> Path:
    """Generate a safe, non-conflicting filename in the target directory.

    If the file already exists, appends _1, _2, etc. to avoid overwrites.

    Args:
        directory: Target directory.
        filename: Desired filename.

    Returns:
        A Path that does not conflict with existing files.
    """
    # Sanitize filename
    safe = filename.strip()
    for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        safe = safe.replace(char, '_')

    if not safe:
        safe = "untitled"

    target = directory / safe
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _get_item_subfolder(item_type: ItemType) -> str:
    """Map item type to its subfolder name.

    Args:
        item_type: The type of classroom item.

    Returns:
        Subfolder name string.
    """
    mapping = {
        ItemType.COURSEWORK: "Assignments",
        ItemType.MATERIAL: "Materials",
        ItemType.ANNOUNCEMENT: "Announcements",
    }
    return mapping.get(item_type, "Other")


class DownloadManager:
    """Manages file downloads from Google Drive and direct URLs.

    Handles Google Workspace exports, retry logic, duplicate detection,
    and safe file naming.

    Attributes:
        drive_service: Google Drive API service resource.
        db: Database instance for tracking downloads.
        base_dir: Base directory for storing downloaded files.
    """

    def __init__(
        self,
        drive_service: Resource,
        db: Database,
        base_dir: Path,
    ) -> None:
        """Initialize the download manager.

        Args:
            drive_service: Google Drive API service resource.
            db: Database instance for download tracking.
            base_dir: Base subjects directory.
        """
        self.drive_service = drive_service
        self.db = db
        self.base_dir = base_dir

    def _ensure_directories(self, course_name: str) -> dict[str, Path]:
        """Create the directory structure for a course.

        Args:
            course_name: Filesystem-safe course name.

        Returns:
            Dictionary mapping subfolder names to their paths.
        """
        course_dir = self.base_dir / course_name
        subdirs = {}
        for subfolder in ("Assignments", "Materials", "Announcements", "Metadata"):
            path = course_dir / subfolder
            path.mkdir(parents=True, exist_ok=True)
            subdirs[subfolder] = path
        return subdirs

    def download_item_attachments(
        self,
        item: ClassroomItem,
    ) -> list[DownloadResult]:
        """Download all attachments for a classroom item.

        Creates necessary directories and downloads each attachment,
        skipping already-downloaded files.

        Args:
            item: The classroom item whose attachments to download.

        Returns:
            List of DownloadResult objects.
        """
        if not item.attachments:
            return []

        subdirs = self._ensure_directories(item.course_name)
        subfolder = _get_item_subfolder(item.item_type)
        target_dir = subdirs.get(subfolder, subdirs.get("Materials", self.base_dir))

        results: list[DownloadResult] = []

        for attachment in item.attachments:
            # Skip non-downloadable attachments (links, YouTube, forms)
            if not attachment.drive_file_id and not attachment.url:
                continue

            # Skip already downloaded
            if self.db.is_file_downloaded(
                item.item_id, item.course_id, attachment.title
            ):
                logger.debug("Skipping already downloaded: %s", attachment.title)
                continue

            result = self._download_attachment(
                attachment=attachment,
                item=item,
                target_dir=target_dir,
            )
            results.append(result)

            # Record in database
            self.db.record_download(
                item_id=item.item_id,
                course_id=item.course_id,
                filename=attachment.title,
                file_path=result.file_path,
                mime_type=attachment.mime_type,
                success=result.success,
            )

        return results

    def _download_attachment(
        self,
        attachment: Attachment,
        item: ClassroomItem,
        target_dir: Path,
    ) -> DownloadResult:
        """Download a single attachment with retry logic.

        Args:
            attachment: The attachment to download.
            item: Parent classroom item.
            target_dir: Directory to save the file in.

        Returns:
            DownloadResult indicating success or failure.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if attachment.drive_file_id:
                    return self._download_drive_file(
                        attachment, target_dir
                    )
                elif attachment.url:
                    return self._download_url(attachment, target_dir)
                else:
                    return DownloadResult(
                        attachment=attachment,
                        success=False,
                        error="No download source available",
                    )
            except Exception as exc:
                logger.warning(
                    "Download attempt %d/%d failed for '%s': %s",
                    attempt, MAX_RETRIES, attachment.title, exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)

        return DownloadResult(
            attachment=attachment,
            success=False,
            error=f"Failed after {MAX_RETRIES} attempts",
        )

    def _download_drive_file(
        self,
        attachment: Attachment,
        target_dir: Path,
    ) -> DownloadResult:
        """Download a file from Google Drive.

        Handles both regular files and Google Workspace exports.

        Args:
            attachment: The Drive file attachment.
            target_dir: Directory to save the file in.

        Returns:
            DownloadResult indicating success or failure.
        """
        file_id = attachment.drive_file_id

        # Get file metadata to determine type
        try:
            file_meta = self.drive_service.files().get(
                fileId=file_id,
                fields="id,name,mimeType,size",
            ).execute()
        except HttpError as exc:
            return DownloadResult(
                attachment=attachment,
                success=False,
                error=f"Failed to get file metadata: {exc}",
            )

        mime_type = file_meta.get("mimeType", "")
        filename = attachment.title or file_meta.get("name", "untitled")

        # Check if this is a Google Workspace file that needs export
        if mime_type in GOOGLE_EXPORT_MAP:
            export_mime, export_ext = GOOGLE_EXPORT_MAP[mime_type]
            if not filename.lower().endswith(export_ext):
                filename = f"{filename}{export_ext}"

            return self._export_google_file(
                file_id=file_id,
                export_mime=export_mime,
                filename=filename,
                target_dir=target_dir,
                attachment=attachment,
            )

        # Regular file download
        return self._download_regular_file(
            file_id=file_id,
            filename=filename,
            target_dir=target_dir,
            attachment=attachment,
        )

    def _export_google_file(
        self,
        file_id: str,
        export_mime: str,
        filename: str,
        target_dir: Path,
        attachment: Attachment,
    ) -> DownloadResult:
        """Export a Google Workspace file to a standard format.

        Args:
            file_id: Google Drive file ID.
            export_mime: Target MIME type for export.
            filename: Desired filename with extension.
            target_dir: Directory to save the file in.
            attachment: The original attachment object.

        Returns:
            DownloadResult indicating success or failure.
        """
        try:
            request = self.drive_service.files().export_media(
                fileId=file_id,
                mimeType=export_mime,
            )
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)

            done = False
            while not done:
                _, done = downloader.next_chunk()

            file_path = _safe_filename(target_dir, filename)
            file_path.write_bytes(buffer.getvalue())

            logger.info("Exported Google file: %s → %s", attachment.title, file_path.name)
            return DownloadResult(
                attachment=attachment,
                success=True,
                file_path=str(file_path),
            )
        except HttpError as exc:
            return DownloadResult(
                attachment=attachment,
                success=False,
                error=f"Export failed: {exc}",
            )

    def _download_regular_file(
        self,
        file_id: str,
        filename: str,
        target_dir: Path,
        attachment: Attachment,
    ) -> DownloadResult:
        """Download a regular (non-Google Workspace) file from Drive.

        Args:
            file_id: Google Drive file ID.
            filename: Desired filename.
            target_dir: Directory to save the file in.
            attachment: The original attachment object.

        Returns:
            DownloadResult indicating success or failure.
        """
        try:
            request = self.drive_service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(
                buffer, request, chunksize=DOWNLOAD_CHUNK_SIZE
            )

            done = False
            while not done:
                _, done = downloader.next_chunk()

            file_path = _safe_filename(target_dir, filename)
            file_path.write_bytes(buffer.getvalue())

            logger.info("Downloaded: %s → %s", attachment.title, file_path.name)
            return DownloadResult(
                attachment=attachment,
                success=True,
                file_path=str(file_path),
            )
        except HttpError as exc:
            return DownloadResult(
                attachment=attachment,
                success=False,
                error=f"Download failed: {exc}",
            )

    def _download_url(
        self,
        attachment: Attachment,
        target_dir: Path,
    ) -> DownloadResult:
        """Download a file from a direct URL.

        Args:
            attachment: The URL attachment.
            target_dir: Directory to save the file in.

        Returns:
            DownloadResult indicating success or failure.
        """
        try:
            response = requests.get(
                attachment.url,
                timeout=60,
                stream=True,
            )
            response.raise_for_status()

            filename = attachment.title or "download"
            file_path = _safe_filename(target_dir, filename)

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)

            logger.info("Downloaded URL: %s → %s", attachment.title, file_path.name)
            return DownloadResult(
                attachment=attachment,
                success=True,
                file_path=str(file_path),
            )
        except requests.RequestException as exc:
            return DownloadResult(
                attachment=attachment,
                success=False,
                error=f"URL download failed: {exc}",
            )

    def save_metadata(self, item: ClassroomItem) -> None:
        """Save item metadata as a JSON file.

        Args:
            item: The classroom item to save metadata for.
        """
        import json

        subdirs = self._ensure_directories(item.course_name)
        metadata_dir = subdirs["Metadata"]

        safe_title = item.title[:60].strip()
        for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
            safe_title = safe_title.replace(char, '_')

        filename = f"{item.item_type.value}_{item.item_id}_{safe_title}.json"
        file_path = metadata_dir / filename

        if file_path.exists():
            return

        metadata = {
            "item_id": item.item_id,
            "course_id": item.course_id,
            "course_name": item.course_name,
            "item_type": item.item_type.value,
            "title": item.title,
            "description": item.description,
            "due_date": item.due_date_str,
            "creation_time": item.creation_time,
            "update_time": item.update_time,
            "alternate_link": item.alternate_link,
            "attachments": [
                {
                    "title": a.title,
                    "url": a.url,
                    "drive_file_id": a.drive_file_id,
                }
                for a in item.attachments
            ],
        }

        file_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
        logger.debug("Saved metadata: %s", file_path.name)
