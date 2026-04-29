"""
twitter_csv — Parse XPorter / xExport / generic Twitter export CSVs.

Extracts canonical fields without row-by-row heuristics: column names drive mapping.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

USERNAME_COLUMNS = (
    "username",
    "screen_name",
    "handle",
    "user",
    "twitter",
    "x_handle",
)

USER_ID_COLUMNS = (
    "id",
    "user id",
    "user_id",
    "userid",
)

FOLLOWER_COUNT_COLUMNS = (
    "followers_count",
    "follower count",
    "followers",
)


def _norm_header(h: str) -> str:
    return (h or "").strip().lower()


def _find_column(fieldnames: list[str] | None, candidates: tuple[str, ...]) -> str | None:
    if not fieldnames:
        return None
    lower_map = {_norm_header(f): f for f in fieldnames}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    return None


def _parse_int_maybe(raw: str) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip().strip('"').replace(",", "")
    if not s or not re.match(r"^-?\d+$", s):
        return None
    try:
        v = int(s)
        return v if v >= 0 else None
    except ValueError:
        return None


def parse_csv_export_rows(content: str) -> list[dict[str, Any]]:
    """
    Parse CSV text from XPorter, xExport, or similar exports.

    Each row dict has:
      - username: str (lowercase handle without @, may be empty)
      - user_id: str (numeric snowflake, may be empty)
      - followers_count: int | None
    """
    content = content.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(content))
    fieldnames = reader.fieldnames or []

    user_col = _find_column(fieldnames, USERNAME_COLUMNS)
    uid_col = _find_column(fieldnames, USER_ID_COLUMNS)
    fc_col = _find_column(fieldnames, FOLLOWER_COUNT_COLUMNS)

    # XPorter uses "id" for user snowflake — ok if uid_col is "id"
    # xExport may lack username; profile URL fallback below

    rows_out: list[dict[str, Any]] = []
    for row in reader:
        username = ""
        user_id = ""

        if user_col:
            raw = (row.get(user_col) or "").strip().lstrip("@")
            if raw and raw.lower() != "undefined":
                username = raw.lower()

        if uid_col:
            raw = (row.get(uid_col) or "").strip().strip('"')
            if raw.isdigit():
                user_id = raw

        fc_raw = (row.get(fc_col) or "") if fc_col else ""
        followers_count = _parse_int_maybe(fc_raw)

        if not username:
            for fn in fieldnames:
                ln = fn.lower()
                if "profile" in ln and "url" in ln:
                    url = (row.get(fn) or "").strip()
                    if url and "/undefined" not in url.lower():
                        parts = url.rstrip("/").split("/")
                        if parts:
                            cand = parts[-1].lstrip("@").strip()
                            if cand and not cand.isdigit():
                                username = cand.lower()
                    break

        if not username and user_id:
            username = user_id

        rows_out.append(
            {
                "username": username,
                "user_id": user_id,
                "followers_count": followers_count,
            }
        )

    return rows_out


def identity_key_set(username: str | None, user_id: str | None = None) -> set[str]:
    """Dedupe keys for a queue row or CSV row (username handle and/or snowflake id)."""
    keys: set[str] = set()
    u = (username or "").strip().lower()
    uid = (user_id or "").strip()
    if u:
        keys.add("u:" + u)
        if u.isdigit():
            keys.add("i:" + u)
    if uid.isdigit():
        keys.add("i:" + uid)
    return keys
