"""Google OAuth authentication for gcr-sync.

Handles the complete OAuth2 flow including:
- First-time authorization with browser-based consent
- Token caching to disk
- Automatic token refresh
- Credential validation

Uses the Google Auth library with the installed application flow.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource

from src.config import GoogleConfig
from src.logger import get_logger

logger = get_logger()


def _build_client_config(config: GoogleConfig) -> dict:
    """Build the OAuth client configuration dictionary.

    Constructs the client config from environment variables instead
    of requiring a credentials.json file.

    Args:
        config: Google OAuth configuration.

    Returns:
        Client configuration dictionary for InstalledAppFlow.
    """
    return {
        "installed": {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uris": [config.redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _load_cached_credentials(token_path: Path) -> Optional[Credentials]:
    """Load cached OAuth credentials from disk.

    Args:
        token_path: Path to the token.json file.

    Returns:
        Credentials if found and parseable, None otherwise.
    """
    if not token_path.exists():
        logger.debug("No cached token found at %s", token_path)
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(token_path))
        logger.debug("Loaded cached credentials from %s", token_path)
        return creds
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        logger.warning("Failed to load cached token: %s", exc)
        return None


def _save_credentials(creds: Credentials, token_path: Path) -> None:
    """Save OAuth credentials to disk.

    Args:
        creds: The credentials to save.
        token_path: Path to write the token.json file.
    """
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    logger.debug("Saved credentials to %s", token_path)


def _refresh_credentials(creds: Credentials) -> Optional[Credentials]:
    """Attempt to refresh expired credentials.

    Args:
        creds: Expired credentials with a refresh token.

    Returns:
        Refreshed credentials, or None if refresh failed.
    """
    if not creds.refresh_token:
        logger.warning("No refresh token available")
        return None

    try:
        creds.refresh(Request())
        logger.info("Successfully refreshed OAuth token")
        return creds
    except Exception as exc:
        logger.warning("Token refresh failed: %s", exc)
        return None


def authenticate(config: GoogleConfig) -> Credentials:
    """Perform Google OAuth authentication.

    Attempts to load cached credentials, refresh if expired,
    or initiate a new authorization flow if necessary.

    Args:
        config: Google OAuth configuration.

    Returns:
        Valid Google OAuth credentials.

    Raises:
        SystemExit: If authentication fails completely.
    """
    token_path = config.token_path

    # Try loading cached credentials
    creds = _load_cached_credentials(token_path)

    if creds and creds.valid:
        logger.info("Using cached OAuth credentials")
        return creds

    # Try refreshing expired credentials
    if creds and creds.expired and creds.refresh_token:
        refreshed = _refresh_credentials(creds)
        if refreshed and refreshed.valid:
            _save_credentials(refreshed, token_path)
            return refreshed

    # Need fresh authorization
    logger.info("Starting OAuth authorization flow...")
    logger.info("A browser window will open for Google sign-in.")

    # Allow Google to return slightly different scopes than requested
    # (e.g., replacing coursework.me with student-submissions.me)
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

    try:
        client_config = _build_client_config(config)
        flow = InstalledAppFlow.from_client_config(
            client_config,
            scopes=config.scopes,
        )
        creds = flow.run_local_server(
            port=8080,
            prompt="consent",
            access_type="offline",
        )
    except Exception as exc:
        logger.error("OAuth authorization failed: %s", exc)
        raise SystemExit(f"❌ Authentication failed: {exc}") from exc

    # Save for future use
    _save_credentials(creds, token_path)
    logger.info("OAuth authentication successful")
    return creds


def build_classroom_service(creds: Credentials) -> Resource:
    """Build the Google Classroom API service client.

    Args:
        creds: Valid Google OAuth credentials.

    Returns:
        Google Classroom API service resource.
    """
    service = build("classroom", "v1", credentials=creds)
    logger.debug("Built Classroom API service")
    return service


def build_drive_service(creds: Credentials) -> Resource:
    """Build the Google Drive API service client.

    Args:
        creds: Valid Google OAuth credentials.

    Returns:
        Google Drive API service resource.
    """
    service = build("drive", "v3", credentials=creds)
    logger.debug("Built Drive API service")
    return service
