"""Save a headed-browser login into the encrypted session store.

The password is typed in the Playwright window. This process never reads it.
Changing AUTH_DATA_KEY does not re-encrypt old rows. Revoke them and save again.

    .\\.venv\\Scripts\\python.exe insight\\agent\\src\\auth\\capture.py generate-key
    .\\.venv\\Scripts\\python.exe insight\\agent\\src\\auth\\capture.py save --profile lumenshop --origin http://localhost:5500 --login-url http://localhost:5500/login.html
    .\\.venv\\Scripts\\python.exe insight\\agent\\src\\auth\\capture.py list
    .\\.venv\\Scripts\\python.exe insight\\agent\\src\\auth\\capture.py revoke --profile lumenshop
"""

from __future__ import annotations

import atexit
import argparse
import asyncio
import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path

_SRC = Path(__file__).parent.parent
if not _SRC.is_absolute():
    raise RuntimeError(f"expected absolute __file__, got {__file__!r}")
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from auth.crypto import generate_data_key
from auth.storage_state import StorageStateError, has_session_data, normalize_origin, scope_storage_state
from auth.store import (
    AuthStoreError,
    list_sessions,
    prepare_scratch_dir,
    revoke_session,
    save_session,
)
from mcp_clients.playwright import PlaywrightMCP, PlaywrightToolError
from runtime_paths import absolute_path


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Save or revoke an encrypted browser session")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("generate-key", help="Print a new AUTH_DATA_KEY")

    save = sub.add_parser("save", help="Sign in in the headed browser, then store the session")
    save.add_argument("--profile", required=True, help="Name used later as auth_profile_id")
    save.add_argument("--origin", required=True, help="Site origin, e.g. http://localhost:5500")
    save.add_argument("--login-url", required=True, help="Page where you will sign in")
    save.add_argument(
        "--ttl-minutes",
        type=int,
        default=None,
        help="How long the saved session stays usable (default: AUTH_SESSION_TTL_MINUTES or 60)",
    )

    sub.add_parser("list", help="List saved sessions (no cookie values)")

    revoke = sub.add_parser("revoke", help="Delete a saved session")
    revoke.add_argument("--profile", required=True)
    return parser


async def _save(args: argparse.Namespace) -> None:
    if not sys.stdin.isatty():
        raise AuthStoreError(
            "auth_capture",
            "save must be run in a terminal so you can confirm the sign-in",
        )
    origin = normalize_origin(args.origin)
    login_url = args.login_url.strip()
    if normalize_origin(login_url) != origin:
        raise AuthStoreError(
            "auth_origin",
            f"Login URL must be on {origin}",
        )
    scratch = prepare_scratch_dir()
    capture_path = scratch / f"insight-auth-capture-{uuid.uuid4().hex}.json"
    atexit.register(_unlink_quiet, capture_path)
    try:
        async with PlaywrightMCP() as playwright:
            await playwright.call_mcp("browser_navigate", {"url": login_url})
            print(
                f"Opened {login_url}.\n"
                "Sign in in the Playwright browser window, then press Enter here.",
                flush=True,
            )
            try:
                input()
            except EOFError as exc:
                raise AuthStoreError(
                    "auth_capture",
                    "Sign-in was not confirmed",
                ) from exc
            await playwright.call_mcp(
                "browser_storage_state",
                {"filename": str(absolute_path(capture_path))},
            )
        if not capture_path.exists():
            raise AuthStoreError(
                "auth_capture",
                "Playwright did not write a storage state file. "
                "Start MCP with --caps=storage, from this repo, so it can write .insight_auth.",
            )
        try:
            raw = json.loads(capture_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise AuthStoreError(
                "auth_capture",
                "Storage state file was not valid JSON",
            ) from None
        if not isinstance(raw, dict):
            raise AuthStoreError(
                "auth_capture",
                "Storage state file was not a JSON object",
            )
    except StorageStateError as exc:
        raise AuthStoreError(exc.code, str(exc)) from exc
    finally:
        _unlink_quiet(capture_path)

    scoped = scope_storage_state(raw, origin)
    if not has_session_data(scoped):
        raise AuthStoreError(
            "auth_empty",
            f"No cookies or localStorage for {origin}. Sign in, then save again.",
        )
    ttl = timedelta(minutes=args.ttl_minutes) if args.ttl_minutes else None
    expires_at = save_session(args.profile, origin, scoped, ttl=ttl)
    print(f"Saved session {args.profile.strip()} for {origin} until {expires_at.isoformat()}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate-key":
            print(generate_data_key())
            print("Add this to .env as AUTH_DATA_KEY. Keep it off the database host.", file=sys.stderr)
            return 0
        if args.command == "list":
            rows = list_sessions()
            if not rows:
                print("No unexpired sessions.")
                return 0
            for row in rows:
                print(f"{row['profile_id']}\t{row['origin']}\texpires {row['expires_at'].isoformat()}")
            return 0
        if args.command == "revoke":
            removed = revoke_session(args.profile)
            print("Revoked." if removed else "No session matched that profile.")
            return 0
        asyncio.run(_save(args))
        return 0
    except (AuthStoreError, PlaywrightToolError, StorageStateError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
