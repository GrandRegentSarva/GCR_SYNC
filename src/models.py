"""Data models for gcr-sync.

Defines dataclasses representing Google Classroom entities
and internal tracking structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ItemType(str, Enum):
    """Type of classroom item."""

    COURSEWORK = "coursework"
    MATERIAL = "material"
    ANNOUNCEMENT = "announcement"


@dataclass
class Attachment:
    """Represents a file attachment from Google Classroom.

    Attributes:
        title: Display name of the attachment.
        url: Direct URL or Google Drive link.
        drive_file_id: Google Drive file ID, if applicable.
        mime_type: MIME type of the file.
        is_google_file: Whether this is a native Google Workspace file.
    """

    title: str
    url: str = ""
    drive_file_id: str = ""
    mime_type: str = ""
    is_google_file: bool = False


@dataclass
class DueDate:
    """Represents a due date for an assignment.

    Attributes:
        year: Year component.
        month: Month component.
        day: Day component.
        hours: Hour component (24h format).
        minutes: Minute component.
    """

    year: int
    month: int
    day: int
    hours: int = 23
    minutes: int = 59

    def to_datetime(self) -> datetime:
        """Convert to a datetime object."""
        return datetime(self.year, self.month, self.day, self.hours, self.minutes)

    def is_overdue(self) -> bool:
        """Check if this due date has passed."""
        return datetime.now() > self.to_datetime()

    def format_short(self) -> str:
        """Format as a short human-readable string (e.g., '12 Jun')."""
        dt = self.to_datetime()
        return dt.strftime("%-d %b")


@dataclass
class Course:
    """Represents a Google Classroom course.

    Attributes:
        course_id: Unique Google Classroom course ID.
        name: Course display name.
        section: Optional section identifier.
        state: Course state (ACTIVE, ARCHIVED, etc.).
    """

    course_id: str
    name: str
    section: str = ""
    state: str = "ACTIVE"

    @property
    def safe_name(self) -> str:
        """Return a filesystem-safe version of the course name."""
        # Replace problematic characters
        name = self.name.strip()
        for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
            name = name.replace(char, '_')
        return name


@dataclass
class ClassroomItem:
    """Represents a single item from Google Classroom.

    This is the unified model for coursework, materials, and announcements.

    Attributes:
        item_id: Unique Google Classroom item ID.
        course_id: ID of the parent course.
        course_name: Display name of the parent course.
        item_type: Type of item (coursework, material, announcement).
        title: Item title or subject.
        description: Item description or body text.
        due_date: Optional due date (coursework only).
        creation_time: When the item was created.
        update_time: When the item was last updated.
        attachments: List of file attachments.
        alternate_link: URL to the item in Google Classroom.
    """

    item_id: str
    course_id: str
    course_name: str
    item_type: ItemType
    title: str
    description: str = ""
    due_date: Optional[DueDate] = None
    creation_time: str = ""
    update_time: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    alternate_link: str = ""

    @property
    def is_overdue(self) -> bool:
        """Check if this item is an overdue assignment."""
        if self.item_type != ItemType.COURSEWORK:
            return False
        if self.due_date is None:
            return False
        return self.due_date.is_overdue()

    @property
    def due_date_str(self) -> str:
        """Return formatted due date string, or empty if none."""
        if self.due_date is None:
            return ""
        return self.due_date.format_short()


@dataclass
class SyncResult:
    """Result of a single sync operation.

    Attributes:
        course_name: Name of the course.
        new_coursework: Newly discovered coursework items.
        new_materials: Newly discovered material items.
        new_announcements: Newly discovered announcement items.
    """

    course_name: str
    new_coursework: list[ClassroomItem] = field(default_factory=list)
    new_materials: list[ClassroomItem] = field(default_factory=list)
    new_announcements: list[ClassroomItem] = field(default_factory=list)

    @property
    def has_updates(self) -> bool:
        """Check if this result contains any new items."""
        return bool(
            self.new_coursework
            or self.new_materials
            or self.new_announcements
        )

    @property
    def total_new_items(self) -> int:
        """Return total count of new items."""
        return (
            len(self.new_coursework)
            + len(self.new_materials)
            + len(self.new_announcements)
        )


@dataclass
class DownloadResult:
    """Result of a file download attempt.

    Attributes:
        attachment: The attachment that was downloaded.
        success: Whether the download succeeded.
        file_path: Local path where the file was saved.
        error: Error message if download failed.
    """

    attachment: Attachment
    success: bool
    file_path: str = ""
    error: str = ""
