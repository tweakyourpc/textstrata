"""Canonical identities for externally acquired sources."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,}$")
_TRACKING = {" si", "si", "feature", "app", "fbclid", "gclid", "ref", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}


def _youtube_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be"


def _video_id(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    candidates: list[str] = []
    if parsed.hostname == "youtu.be":
        candidates.append(parsed.path.strip("/").split("/")[0])
    candidates.append(query.get("v", [""])[0])
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0].lower() in {"shorts", "embed", "live", "v"} and len(parts) > 1:
        candidates.append(parts[1])
    return next((value for value in candidates if _VIDEO_ID.fullmatch(value or "")), None)


def canonical_youtube_identity(value: str) -> str | None:
    """Return a stable video or collection identity, without network access."""
    raw = str(value or "").strip()
    if raw.startswith("@"):
        return "youtube:collection:https://www.youtube.com/" + raw
    parsed = urlparse(raw)
    if not _youtube_host(parsed.hostname or ""):
        return None
    video = _video_id(raw)
    if video:
        return f"youtube:video:{video}"
    query = parse_qs(parsed.query)
    playlist = query.get("list", [""])[0]
    if playlist:
        return f"youtube:collection:https://www.youtube.com/playlist?{urlencode({'list': playlist})}"
    path = "/" + "/".join(part for part in parsed.path.split("/") if part)
    if not path or path == "/":
        return None
    if path.lower().startswith(("/channel/", "/c/", "/user/", "/@")):
        path = path.rstrip("/")
        return f"youtube:collection:https://www.youtube.com{path}"
    # Unknown YouTube collection-like URLs remain distinct, but tracking data does not.
    return f"youtube:collection:https://www.youtube.com{path.rstrip('/')}"


def source_identity(value: str, kind: str | None = None) -> str | None:
    if (kind or "").lower() == "youtube" or canonical_youtube_identity(value):
        return canonical_youtube_identity(value)
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query = [(key, val) for key, values in parse_qs(parsed.query, keep_blank_values=True).items() if key.lower() not in _TRACKING for val in values]
    normalized = urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", "", urlencode(sorted(query)), ""))
    return f"url:{normalized}"


def youtube_source_kind(value: str) -> str | None:
    identity = canonical_youtube_identity(value)
    if not identity:
        return None
    return "video" if identity.startswith("youtube:video:") else "collection"
