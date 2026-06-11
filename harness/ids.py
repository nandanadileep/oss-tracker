"""Identifiers and time. Everything timestamped goes through here."""

from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timedelta, timezone

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # crockford-ish, lowercase


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def today() -> str:
    return utcnow().strftime("%Y-%m-%d")


def parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age(s: str) -> timedelta:
    return utcnow() - parse_iso(s)


def new_id(prefix: str) -> str:
    """Sortable id: <prefix>_<seconds-base32><random>. ULID-flavoured, stdlib-only."""
    t = int(time.time())
    enc = ""
    while t:
        enc = _ALPHABET[t % 32] + enc
        t //= 32
    rand = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    return f"{prefix}_{enc}{rand}"


def run_id() -> str:
    """Stable per-process run id; honours GitHub Actions' run id when present."""
    gh = os.environ.get("GITHUB_RUN_ID")
    return f"gha_{gh}" if gh else new_id("run")
