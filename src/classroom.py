"""Google Classroom API integration for gcr-sync.

Handles all interactions with the Google Classroom API including:
- Course discovery
- Coursework fetching
- Material fetching
- Announcement fetching
- Attachment extraction
"""

from __future__ import annotations

from typing import Any, Optional

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from src.logger import get_logger
from src.models import (
    Attachment,
    ClassroomItem,
    Course,
    DueDate,
    ItemType,
)

logger = get_logger()


def _paginate(request_fn: Any, collection_key: str) -> list[dict]:
    """Generic paginator for Google Classroom API list endpoints.

    Handles nextPageToken-based pagination automatically.

    Args:
        request_fn: A callable that accepts a pageToken keyword argument
                    and returns an HttpRequest.
        collection_key: The key in the response containing the items list.

    Returns:
        Aggregated list of all items across all pages.
    """
    items: list[dict] = []
    page_token: Optional[str] = None

    while True:
        try:
            response = request_fn(pageToken=page_token).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                logger.debug("Resource not found (404), returning empty list")
                return []
            raise

        page_items = response.get(collection_key, [])
        items.extend(page_items)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return items


def _extract_attachments(raw_materials: list[dict]) -> list[Attachment]:
    """Extract attachment objects from Classroom API material entries.

    Google Classroom uses a 'materials' field containing various types:
    driveFile, youtubeVideo, link, form.

    Args:
        raw_materials: List of material dictionaries from the API.

    Returns:
        List of Attachment objects.
    """
    attachments: list[Attachment] = []

    for material in raw_materials:
        if "driveFile" in material:
            drive_file = material["driveFile"].get("driveFile", {})
            attachments.append(Attachment(
                title=drive_file.get("title", "Untitled"),
                url=drive_file.get("alternateLink", ""),
                drive_file_id=drive_file.get("id", ""),
                mime_type=_classify_google_mime(drive_file.get("title", "")),
                is_google_file=_is_google_workspace_file(
                    drive_file.get("id", ""),
                    drive_file.get("title", ""),
                ),
            ))
        elif "link" in material:
            link = material["link"]
            attachments.append(Attachment(
                title=link.get("title", link.get("url", "Link")),
                url=link.get("url", ""),
            ))
        elif "youtubeVideo" in material:
            video = material["youtubeVideo"]
            attachments.append(Attachment(
                title=video.get("title", "YouTube Video"),
                url=video.get("alternateLink", ""),
            ))
        elif "form" in material:
            form = material["form"]
            attachments.append(Attachment(
                title=form.get("title", "Google Form"),
                url=form.get("formUrl", ""),
            ))

    return attachments


def _classify_google_mime(title: str) -> str:
    """Infer MIME type from file title extension.

    Args:
        title: The file title/name.

    Returns:
        Inferred MIME type string.
    """
    title_lower = title.lower()
    extension_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt": "application/vnd.ms-powerpoint",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".txt": "text/plain",
        ".zip": "application/zip",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    for ext, mime in extension_map.items():
        if title_lower.endswith(ext):
            return mime
    return "application/octet-stream"


def _is_google_workspace_file(file_id: str, title: str) -> bool:
    """Determine if a file is a native Google Workspace document.

    Google Workspace files (Docs, Sheets, Slides) don't have standard
    file extensions and need to be exported rather than downloaded.

    Args:
        file_id: Google Drive file ID.
        title: File title.

    Returns:
        True if the file appears to be a Google Workspace file.
    """
    if not file_id:
        return False
    # Google Workspace files typically don't have extensions
    title_lower = title.lower()
    known_extensions = (
        ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
        ".txt", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".csv",
        ".mp4", ".mp3", ".avi", ".mov", ".py", ".java", ".c", ".cpp",
        ".html", ".css", ".js", ".json", ".xml", ".md",
    )
    for ext in known_extensions:
        if title_lower.endswith(ext):
            return False
    return True


def _parse_due_date(coursework: dict) -> Optional[DueDate]:
    """Parse due date from a coursework API response.

    Args:
        coursework: Raw coursework dictionary from the API.

    Returns:
        DueDate object if a due date exists, None otherwise.
    """
    due_date_raw = coursework.get("dueDate")
    if not due_date_raw:
        return None

    due_time_raw = coursework.get("dueTime", {})

    return DueDate(
        year=due_date_raw.get("year", 2000),
        month=due_date_raw.get("month", 1),
        day=due_date_raw.get("day", 1),
        hours=due_time_raw.get("hours", 23),
        minutes=due_time_raw.get("minutes", 59),
    )


class ClassroomClient:
    """Client for interacting with the Google Classroom API.

    Provides methods to discover courses and fetch all types of
    classroom content (coursework, materials, announcements).

    Attributes:
        service: The Google Classroom API service resource.
    """

    def __init__(self, service: Resource) -> None:
        """Initialize the Classroom client.

        Args:
            service: Google Classroom API service resource.
        """
        self.service = service

    def discover_courses(self) -> list[Course]:
        """Discover all active courses for the authenticated user.

        Returns:
            List of active Course objects.
        """
        logger.info("Discovering active courses...")

        try:
            raw_courses = _paginate(
                lambda pageToken=None: self.service.courses().list(
                    courseStates=["ACTIVE"],
                    pageSize=100,
                    pageToken=pageToken,
                ),
                "courses",
            )
        except HttpError as exc:
            logger.error("Failed to fetch courses: %s", exc)
            raise

        courses = [
            Course(
                course_id=c["id"],
                name=c.get("name", "Unknown Course"),
                section=c.get("section", ""),
                state=c.get("courseState", "ACTIVE"),
            )
            for c in raw_courses
        ]

        logger.info("Discovered %d active course(s): %s",
                     len(courses),
                     ", ".join(c.name for c in courses))
        return courses

    def fetch_coursework(self, course: Course) -> list[ClassroomItem]:
        """Fetch all coursework for a course.

        Args:
            course: The course to fetch coursework for.

        Returns:
            List of ClassroomItem objects representing coursework.
        """
        logger.debug("Fetching coursework for '%s'...", course.name)

        try:
            raw_items = _paginate(
                lambda pageToken=None: self.service.courses().courseWork().list(
                    courseId=course.course_id,
                    pageSize=100,
                    orderBy="updateTime desc",
                    pageToken=pageToken,
                ),
                "courseWork",
            )
        except HttpError as exc:
            if exc.resp.status in (403, 404):
                logger.debug("No coursework access for '%s': %s", course.name, exc.resp.status)
                return []
            logger.error("Failed to fetch coursework for '%s': %s", course.name, exc)
            return []

        items: list[ClassroomItem] = []
        for raw in raw_items:
            raw_materials = raw.get("materials", [])
            attachments = _extract_attachments(raw_materials)
            due_date = _parse_due_date(raw)

            item = ClassroomItem(
                item_id=raw["id"],
                course_id=course.course_id,
                course_name=course.safe_name,
                item_type=ItemType.COURSEWORK,
                title=raw.get("title", "Untitled Assignment"),
                description=raw.get("description", ""),
                due_date=due_date,
                creation_time=raw.get("creationTime", ""),
                update_time=raw.get("updateTime", ""),
                attachments=attachments,
                alternate_link=raw.get("alternateLink", ""),
            )
            items.append(item)

        logger.debug("Found %d coursework item(s) for '%s'", len(items), course.name)
        return items

    def fetch_materials(self, course: Course) -> list[ClassroomItem]:
        """Fetch all course materials for a course.

        Args:
            course: The course to fetch materials for.

        Returns:
            List of ClassroomItem objects representing materials.
        """
        logger.debug("Fetching materials for '%s'...", course.name)

        try:
            raw_items = _paginate(
                lambda pageToken=None: self.service.courses().courseWorkMaterials().list(
                    courseId=course.course_id,
                    pageSize=100,
                    orderBy="updateTime desc",
                    pageToken=pageToken,
                ),
                "courseWorkMaterial",
            )
        except HttpError as exc:
            if exc.resp.status in (403, 404):
                logger.debug("No materials access for '%s': %s", course.name, exc.resp.status)
                return []
            logger.error("Failed to fetch materials for '%s': %s", course.name, exc)
            return []

        items: list[ClassroomItem] = []
        for raw in raw_items:
            raw_materials = raw.get("materials", [])
            attachments = _extract_attachments(raw_materials)

            item = ClassroomItem(
                item_id=raw["id"],
                course_id=course.course_id,
                course_name=course.safe_name,
                item_type=ItemType.MATERIAL,
                title=raw.get("title", "Untitled Material"),
                description=raw.get("description", ""),
                creation_time=raw.get("creationTime", ""),
                update_time=raw.get("updateTime", ""),
                attachments=attachments,
                alternate_link=raw.get("alternateLink", ""),
            )
            items.append(item)

        logger.debug("Found %d material(s) for '%s'", len(items), course.name)
        return items

    def fetch_announcements(self, course: Course) -> list[ClassroomItem]:
        """Fetch all announcements for a course.

        Args:
            course: The course to fetch announcements for.

        Returns:
            List of ClassroomItem objects representing announcements.
        """
        logger.debug("Fetching announcements for '%s'...", course.name)

        try:
            raw_items = _paginate(
                lambda pageToken=None: self.service.courses().announcements().list(
                    courseId=course.course_id,
                    pageSize=100,
                    orderBy="updateTime desc",
                    pageToken=pageToken,
                ),
                "announcements",
            )
        except HttpError as exc:
            if exc.resp.status in (403, 404):
                logger.debug("No announcements access for '%s': %s", course.name, exc.resp.status)
                return []
            logger.error("Failed to fetch announcements for '%s': %s", course.name, exc)
            return []

        items: list[ClassroomItem] = []
        for raw in raw_items:
            raw_materials = raw.get("materials", [])
            attachments = _extract_attachments(raw_materials)

            # Announcements don't have a title field; use text truncation
            text = raw.get("text", "")
            title = text[:80].strip() if text else "Announcement"
            if len(text) > 80:
                title += "..."

            item = ClassroomItem(
                item_id=raw["id"],
                course_id=course.course_id,
                course_name=course.safe_name,
                item_type=ItemType.ANNOUNCEMENT,
                title=title,
                description=text,
                creation_time=raw.get("creationTime", ""),
                update_time=raw.get("updateTime", ""),
                attachments=attachments,
                alternate_link=raw.get("alternateLink", ""),
            )
            items.append(item)

        logger.debug("Found %d announcement(s) for '%s'", len(items), course.name)
        return items
