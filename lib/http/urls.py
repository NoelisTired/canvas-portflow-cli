"""URL helpers shared across modules."""

from __future__ import annotations

from urllib.parse import urlparse


def resolve_action(action: str | None, current_url: str) -> str:
    """Resolve a form `action` attribute (absolute / root-relative / empty)."""
    p = urlparse(current_url)
    if not action:
        return current_url
    if action.startswith("http://") or action.startswith("https://"):
        return action
    if action.startswith("/"):
        return f"{p.scheme}://{p.netloc}{action}"
    base = current_url.rsplit("?", 1)[0].rsplit("/", 1)[0]
    return f"{base}/{action}"


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def origin_of(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"
