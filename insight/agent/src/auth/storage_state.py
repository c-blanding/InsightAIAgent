"""Trim Playwright storage state down to one site origin."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


class StorageStateError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_origin(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise StorageStateError("auth_origin", f"Not an http(s) URL: {url}")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    is_default = (parsed.scheme == "http" and port in (None, 80)) or (
        parsed.scheme == "https" and port in (None, 443)
    )
    if is_default:
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


def _domain_matches(cookie_domain: str, host: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    host = host.lower()
    if not domain:
        return False
    return host == domain or host.endswith("." + domain)


def scope_storage_state(state: dict[str, Any], origin: str) -> dict[str, Any]:
    """Keep cookies and localStorage that belong to ``origin``."""
    normalized = normalize_origin(origin)
    host = urlsplit(normalized).hostname or ""
    cookies = []
    for cookie in state.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        if _domain_matches(str(cookie.get("domain") or ""), host):
            cookies.append(cookie)

    origins = []
    for entry in state.get("origins") or []:
        if not isinstance(entry, dict):
            continue
        try:
            entry_origin = normalize_origin(str(entry.get("origin") or ""))
        except StorageStateError:
            continue
        if entry_origin == normalized:
            origins.append({"origin": entry_origin, "localStorage": entry.get("localStorage") or []})

    return {"cookies": cookies, "origins": origins}


def has_session_data(state: dict[str, Any]) -> bool:
    if state.get("cookies"):
        return True
    for entry in state.get("origins") or []:
        if entry.get("localStorage"):
            return True
    return False
