"""Offline creator domain primitives. No network, storage or publishing side effects."""
from dataclasses import dataclass
from uuid import UUID
from datetime import datetime
import unicodedata

PLATFORMS = frozenset(("youtube", "instagram", "tiktok", "facebook"))

def identifier(value):
    return str(UUID(value))

def folder_name(value):
    if not isinstance(value, str):
        raise ValueError("Folder name must be text")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > 120 or value in (".", "..") or any(
        c in "/\\:" or unicodedata.category(c).startswith("C") for c in value
    ):
        raise ValueError("Invalid folder name")
    return value

@dataclass(frozen=True)
class Media:
    workspace: str
    id: str
    size_bytes: int
    duration_seconds: int
    sha256: str

    def __post_init__(self):
        identifier(self.workspace)
        identifier(self.id)
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ValueError("Invalid size")
        if type(self.duration_seconds) is not int or self.duration_seconds <= 0:
            raise ValueError("Invalid duration")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("Invalid digest")

def prepare_publication(media, workspace, platform, account, title, description, hashtags, scheduled_at=None):
    if identifier(media.workspace) != identifier(workspace):
        raise PermissionError("Media not accessible")
    if platform not in PLATFORMS:
        raise ValueError("Unsupported platform")
    account = identifier(account)
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Invalid title")
    if not isinstance(description, str) or len(description) > 10000:
        raise ValueError("Invalid description")
    if not isinstance(hashtags, (list, tuple)) or len(hashtags) > 30:
        raise ValueError("Invalid hashtags")
    tags = []
    for tag in hashtags:
        if not isinstance(tag, str) or not tag.startswith("#") or not 1 < len(tag) <= 100 or not all(c.isalnum() or c == "_" for c in tag[1:]):
            raise ValueError("Invalid hashtag")
        if tag.casefold() not in {t.casefold() for t in tags}:
            tags.append(tag)
    if scheduled_at is not None and (not isinstance(scheduled_at, datetime) or scheduled_at.utcoffset() is None):
        raise ValueError("Timezone required")
    return dict(workspace=identifier(workspace), media_id=identifier(media.id),
                media_sha256=media.sha256, platform=platform, account_id=account,
                title=title.strip(), description=description, hashtags=tags,
                scheduled_at=scheduled_at.isoformat() if scheduled_at else None,
                status="draft", requires_review=True, external_execution=False,
                platform_validation="pending", account_validation="pending")

def check_upload_quota(used, incoming, limit):
    if any(type(n) is not int or n < 0 for n in (used, incoming, limit)) or incoming == 0:
        raise ValueError("Invalid quota inputs")
    if used + incoming > limit:
        raise ValueError("Storage quota exceeded")
    return used + incoming
