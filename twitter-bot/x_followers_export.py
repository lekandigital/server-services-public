#!/usr/bin/env python3
"""
x_followers_export.py — export this account's followers list and backfill followbacks.

Why this exists
---------------
The bot had no proactive way to learn that someone followed back. `follows_back` was
only ever discovered as a side effect of an unfollow attempt happening to see "Follows
you" on the profile. The bot stops looking the moment it unfollows, a few days in, so
a followback after that was invisible forever — and there is no horizon on it. Someone
can follow back next week or in five years; either way the only place it shows up is
our followers list.

So this job reads that list instead. Once a day it pulls the followers list from X's
REST v1.1 API, intersects it with queue.json, and flags every row it finds — including
rows already marked `unfollowed`, which is the whole point. "They follow us and we
already let them go" is a free follower and, until now, an uncountable one. Because it
asks the question from the followers side rather than per-profile, it costs the same
whether the followback happened yesterday or years ago.

Endpoint choice
---------------
REST v1.1 /followers/list.json, not GraphQL. X's GraphQL `Followers` operation returns
404 and GraphQL `Following` can return HTTP 200 with zero usable rows — both documented
in extensions/xporter/utils/api.js, which is the implementation this ports. v1.1 also
needs no x-client-transaction-id (the extension only signs the reply timelines), which
removes the one genuinely hard part of talking to this API from outside a browser.

Quota
-----
This shares the bot account's rate-limit budget: the cookie file is the same account
Selenium drives. Pacing is therefore the only thing protecting the bot, so it is
header-driven and deliberately slow, and it never rotates the VPN — two actors already
fight over that (proton-vpn-watchdog and twitter_bot.cycle_vpn_for_rate_limit). On an
outage this job checkpoints and exits for the timer to retry.

Usage:
    python3 x_followers_export.py --probe [--dry-run]     one page, settles the unknowns
    python3 x_followers_export.py --run                   what the timer runs
    python3 x_followers_export.py --full                  force a complete re-page
    python3 x_followers_export.py --reconcile-only        no API calls; re-apply state
    python3 x_followers_export.py --dry-run-queue         export, but never write queue.json

Exit codes:
    0 ok      2 partial (rate/runtime budget; resume next run)   3 auth error
    4 endpoint shape change / suspicious result                  5 egress down
    6 configuration error                                        1 unexpected
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import random
import socket
import ssl
import sys
import time
import urllib.parse
from datetime import datetime, timedelta

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

from queue_store import (
    attributed_row,
    identity_index,
    lookup_rows,
    parse_ts,
    queue_transaction,
)

# ============================================================
# CONSTANTS
# ============================================================

COOKIE_FILE = "x_export_cookies.txt"
# The bot's own jar. Opened READ-ONLY, for exactly one value: the twid cookie, which
# names the account whose followers we want. No session is ever built from it and no
# request is ever signed with it — that jar belongs to Selenium.
BOT_COOKIE_FILE = "cacheforlogin.json"
STATE_FILE = "followers_state.json"
CURSOR_FILE = "followers_cursor.json"
STATUS_FILE = "followers_export_status.json"
LEDGER_FILE = "followback_ledger.jsonl"
RUN_LOCK_FILE = "followers_export.lock"
SNAPSHOT_DIR = "followers_snapshots"

FOLLOWERS_URL = "https://x.com/i/api/1.1/followers/list.json"
USER_BY_REST_ID_URL = "https://x.com/i/api/graphql/xf3jd90KKBCUxdlI_tNHZw/UserByRestId"

# X's public web bearer. Same constant as twitter_bot.py:60, resolve_ids.py:36 and the
# extension; it has been stable for years but is overridable for the day it is not.
X_BEARER = os.environ.get(
    "X_BEARER",
    "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
)

USER_BY_REST_ID_FEATURES = {
    "hidden_profile_subscriptions_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

# Pacing. If followers/list.json is the historical 15 requests / 900s, then after page
# one the budget maths below lands on ~64s/page all by itself, saturating the window
# without ever tripping a 429. The floor is what protects us when X reports a budget
# far more generous than we want to actually use.
DELAY_FLOOR_S = 20
DELAY_CEIL_S = 300
DELAY_PAD_S = 2
DELAY_JITTER = 0.20
NO_HEADER_DELAY_S = 60          # when x-rate-limit-* are absent entirely
# Longest we will sit waiting for a window to reset. Comfortably above any real X
# window (15 min), so a nonsense reset header cannot park the run for hours.
MAX_RESET_WAIT_S = 20 * 60
MAX_RATE_SLEEPS = 3             # in-process waits for a reset before giving up
DEFAULT_MAX_RUNTIME_S = 90 * 60
EGRESS_RECHECK_BEFORE_SLEEP_S = 300
REQUEST_TIMEOUT_S = 30

# 200 is the most this endpoint returns per page, verified against it on 2026-09-23
# (a count=200 request came back with 200 rows and a cursor). Halves the request count
# for the same export.
DEFAULT_PAGE_SIZE = 200
KNOWN_PAGE_STOP = 3            # consecutive all-known pages before an incremental stop
FULL_REPAGE_DAYS = 7
MAX_PAGES_INCREMENTAL = 8
CHECKPOINT_MAX_AGE_H = 72
# Below this many pages the ordering checks cannot tell newest-first from any other
# order: the slack they need to tolerate a page boundary is as large as the list itself.
# So a short list never verifies and stays on full re-pages — which costs nothing,
# because a 4-page full export is already cheaper than the incremental stop rule.
MIN_PAGES_TO_VERIFY_ORDERING = 5
COOKIE_EXPIRY_WARN_DAYS = 14
REMOVED_CAP = 5000
SUSPICIOUS_MIN_FOLLOWERS = 500

# Exit codes
EXIT_OK, EXIT_PARTIAL, EXIT_AUTH, EXIT_SHAPE, EXIT_EGRESS, EXIT_CONFIG = 0, 2, 3, 4, 5, 6

# XPorter's localized export header, in its order. Matching it means a snapshot from
# this job drops into the dashboard's CSV importer exactly like a browser export.
CSV_HEADERS = [
    "ID", "Name", "Username", "Bio", "Location", "URL", "Followers", "Following",
    "Posts", "List memberships", "Verified", "Private account", "Date",
    "Profile image URL", "Profile URL",
]
CSV_FIELDS = [
    "id", "name", "username", "bio", "location", "url", "followers_count",
    "following_count", "tweet_count", "listed_count", "verified", "protected",
    "created_at", "profile_image_url", "profile_url",
]


# Cloudflare's challenge page, same markers twitter_bot.x_edge_blocked() looks for. The
# block is per-path on a given Proton exit: GraphQL gets challenged while the REST
# endpoints on the same exit answer 200, so this must never be read as a dead session.
EDGE_BLOCK_MARKERS = ("attention required", "cf-error-details", "cloudflare",
                      "just a moment")


class ExporterError(Exception):
    """kind in {AUTH_ERROR, EDGE_BLOCKED, RATE_LIMITED, SHAPE_CHANGED, NETWORK}."""

    def __init__(self, kind, detail="", body="", meta=None):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.body = body
        self.meta = meta or {}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# CONNECTIVITY
# ============================================================

def egress_ok():
    """True if a TLS handshake actually completes.

    Ported from vpn-portal/proton-vpn-watchdog.py:88. A bare TCP connect is not enough:
    with an expired Proton certificate the node still completes the TCP handshake, then
    drops the stream — which is exactly how twitter_bot's TCP-only internet_egress_ok()
    passes while every HTTPS request on the box is dead.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for host in ("1.1.1.1", "9.9.9.9"):
        try:
            with socket.create_connection((host, 443), timeout=6) as sock:
                with ctx.wrap_socket(sock, server_hostname=host):
                    return True
        except OSError:
            continue
    return False


# ============================================================
# COOKIES
# ============================================================

def load_cookie_file(path):
    """-> (cookies: dict, expiries: dict). Accepts both formats we have on hand.

    A leading '[' or '{' means the JSON array of Chrome cookie objects that
    cacheforlogin.json uses; anything else is treated as a Netscape cookie jar, which
    is what the browser extensions export as .txt.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    cookies, expiries = {}, {}
    stripped = raw.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("cookies", [])
        for c in data:
            name = c.get("name")
            if not name:
                continue
            cookies[name] = c.get("value", "")
            exp = c.get("expirationDate", c.get("expiry"))
            if exp:
                expiries[name] = float(exp)
    else:
        for line in raw.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 7:
                continue
            cookies[fields[5]] = fields[6]
            try:
                exp = float(fields[4])
                if exp > 0:
                    expiries[fields[5]] = exp
            except ValueError:
                pass

    # cf_clearance is bound to the IP and User-Agent that were issued it. This box
    # egresses through ProtonVPN from a different address, so replaying it does not help
    # and a mismatched one can provoke the very challenge it is meant to avoid. Getting
    # past Cloudflare is curl_cffi's impersonation, not this cookie.
    cookies.pop("cf_clearance", None)
    return cookies, expiries


def user_id_from_twid(twid):
    """twid is 'u%3D<numeric id>' — the account's own id, no API call required."""
    if not twid:
        return None
    value = urllib.parse.unquote(twid).strip().strip('"')
    if value.startswith("u="):
        value = value[2:]
    return value if value.isdigit() else None


def build_session(cookies, screen_name=None):
    if cffi_requests is None:
        raise ExporterError("NETWORK", "curl_cffi is not installed")
    session = cffi_requests.Session(impersonate="chrome120")
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".x.com")
    referer = f"https://x.com/{screen_name}/followers" if screen_name else "https://x.com/home"
    session.headers.update({
        "authorization": X_BEARER,
        "x-csrf-token": cookies["ct0"],
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "content-type": "application/json",
        "referer": referer,
        "origin": "https://x.com",
    })
    return session


# ============================================================
# REQUESTS
# ============================================================

def _rate_meta(response, elapsed):
    def as_int(header):
        try:
            return int(response.headers.get(header))
        except (TypeError, ValueError):
            return None

    reset = as_int("x-rate-limit-reset")
    return {
        "status": response.status_code,
        "limit": as_int("x-rate-limit-limit"),
        "remaining": as_int("x-rate-limit-remaining"),
        "reset_at": float(reset) if reset and reset > 0 else None,
        "elapsed": round(elapsed, 2),
        "returned": 0,
    }


def _classify(response, meta):
    if response.status_code == 429:
        raise ExporterError("RATE_LIMITED", "HTTP 429", meta=meta)
    if response.status_code in (401, 403):
        body = _body_snippet(response)
        low = body.lower()
        if any(marker in low for marker in EDGE_BLOCK_MARKERS):
            raise ExporterError(
                "EDGE_BLOCKED", f"HTTP {response.status_code} from Cloudflare",
                body=body, meta=meta)
        raise ExporterError(
            "AUTH_ERROR", f"HTTP {response.status_code}", body=body, meta=meta)
    if response.status_code in (400, 404):
        raise ExporterError(
            "SHAPE_CHANGED", f"HTTP {response.status_code}",
            body=_body_snippet(response), meta=meta)
    if response.status_code != 200:
        raise ExporterError(
            "NETWORK", f"HTTP {response.status_code}",
            body=_body_snippet(response), meta=meta)


def _body_snippet(response):
    try:
        return response.text[:500]
    except Exception:
        return ""


def parse_row(u):
    """One v1.1 user object -> our canonical row. Ports api.js:426-451 field for field."""
    screen_name = u.get("screen_name") or ""
    url = u.get("url") or ""
    entities = u.get("entities") or {}
    for item in ((entities.get("url") or {}).get("urls") or []):
        if item.get("expanded_url"):
            url = item["expanded_url"]
            break
    row = {
        "id": str(u.get("id_str") or u.get("id") or ""),
        "name": u.get("name") or "",
        "username": screen_name,
        "bio": (u.get("description") or "").replace("\n", " "),
        "location": u.get("location") or "",
        "url": url,
        "followers_count": u.get("followers_count") or 0,
        "following_count": u.get("friends_count") or 0,
        "tweet_count": u.get("statuses_count") or 0,
        "listed_count": u.get("listed_count") or 0,
        "verified": bool(u.get("verified") or u.get("is_blue_verified")),
        "protected": bool(u.get("protected")),
        "created_at": u.get("created_at") or "",
        "profile_image_url": (u.get("profile_image_url_https") or "").replace("_normal", "_400x400"),
        "profile_url": f"https://x.com/{screen_name}" if screen_name else "",
    }
    # Perspectival: whether the *authenticated* session follows them — which is only the
    # account we are listing when the two happen to be the same. Running the export from
    # a separate account, which is the point of doing so, makes this the export account's
    # relationships and says nothing at all about the bot's. The caller decides whether
    # to keep it; see self_view in run_export.
    if "following" in u:
        row["x_following"] = bool(u.get("following"))
    return row


def fetch_followers_page(session, user_id, cursor=None, count=DEFAULT_PAGE_SIZE,
                         timeout=REQUEST_TIMEOUT_S):
    """-> (rows, next_cursor, meta). Raises ExporterError."""
    params = {
        "user_id": str(user_id),
        "count": str(count),
        "skip_status": "true",
        "include_user_entities": "true",
    }
    if cursor and cursor not in ("0", "-1"):
        params["cursor"] = cursor

    started = time.time()
    try:
        response = session.get(FOLLOWERS_URL, params=params, timeout=timeout)
    except Exception as e:
        raise ExporterError("NETWORK", str(e)[:200])
    meta = _rate_meta(response, time.time() - started)
    _classify(response, meta)

    try:
        data = response.json()
    except Exception:
        raise ExporterError("SHAPE_CHANGED", "response was not JSON",
                            body=_body_snippet(response), meta=meta)
    if not isinstance(data.get("users"), list):
        raise ExporterError("SHAPE_CHANGED", "no users[] in a 200 response",
                            body=json.dumps(data)[:500], meta=meta)

    rows = [parse_row(u) for u in data["users"] if u.get("id_str") or u.get("id")]
    meta["returned"] = len(rows)
    next_cursor_str = data.get("next_cursor_str") or str(data.get("next_cursor") or 0)
    next_cursor = next_cursor_str if next_cursor_str not in ("0", "-1", "") else None
    return rows, next_cursor, meta


def fetch_account(session, user_id):
    """-> {"screen_name", "followers_count"}. One call; validates the id and gives us
    the follower count that every drift check is measured against."""
    try:
        response = session.get(
            USER_BY_REST_ID_URL,
            params={
                "variables": json.dumps({"userId": str(user_id),
                                         "withSafetyModeUserFields": True}),
                "features": json.dumps(USER_BY_REST_ID_FEATURES),
            },
            timeout=REQUEST_TIMEOUT_S,
        )
    except Exception as e:
        raise ExporterError("NETWORK", str(e)[:200])
    meta = _rate_meta(response, 0)
    _classify(response, meta)

    result = (response.json().get("data") or {}).get("user", {}).get("result") or {}
    if result.get("__typename") == "UserUnavailable":
        raise ExporterError("AUTH_ERROR",
                            f"account unavailable: {result.get('reason', 'unknown')}")
    legacy = result.get("legacy") or {}
    core = result.get("core") or {}
    screen_name = core.get("screen_name") or legacy.get("screen_name")
    if not screen_name:
        raise ExporterError("SHAPE_CHANGED", "UserByRestId returned no screen_name",
                            body=json.dumps(result)[:500])
    return {
        "screen_name": screen_name,
        "followers_count": legacy.get("followers_count"),
    }


# ============================================================
# PACING
# ============================================================

def adaptive_delay(meta):
    """Seconds to wait before the next page.

    With a budget in hand, spread what is left over the rest of the window: that
    saturates the quota without ever reaching zero. Without one, fall back to a flat
    minute, which is what the upstream extension paces this endpoint at.
    """
    remaining, reset_at = meta.get("remaining"), meta.get("reset_at")
    if remaining is not None and reset_at is not None and remaining <= 1:
        # Budget spent: wait out the window. Two things must not happen here, and the
        # per-page pacing below would do both. DELAY_CEIL_S is a cap on how long to
        # *spread* requests, not on how long to wait for a reset — applied to a 15
        # minute window it would cut the wait to 5. And the jitter must only ever push
        # this later: waking early spends a request on a guaranteed 429 and burns one of
        # MAX_RATE_SLEEPS, so three early wake-ups end the run for no reason.
        wait = min(max(0.0, reset_at - time.time()), MAX_RESET_WAIT_S)
        return wait + DELAY_PAD_S + random.uniform(5, 20)

    if remaining is None or reset_at is None:
        base = NO_HEADER_DELAY_S
    else:
        base = max(0.0, reset_at - time.time()) / remaining
    base = min(max(base, DELAY_FLOOR_S), DELAY_CEIL_S) + DELAY_PAD_S
    # Clamp after the jitter, not before: the floor is a rate-limit protection, and
    # jitter shaving 20% off it would quietly make it a 16s floor instead of a 20s one.
    return max(DELAY_FLOOR_S, base * random.uniform(1 - DELAY_JITTER, 1 + DELAY_JITTER))


# ============================================================
# STATE
# ============================================================

def _write_json_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    os.replace(tmp, path)


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"⚠️ could not read {path}: {e}")
        return default


def blank_state():
    return {
        "version": 1,
        "account": {},
        "last_run_at": None,
        "last_success_at": None,
        "last_full_run_at": None,
        "last_mode": None,
        "page_size": DEFAULT_PAGE_SIZE,
        "reported_followers_count": None,
        "ordering": {"verified": False, "verified_at": None, "evidence": {}},
        # When this job first completed. Follows older than this were never observed
        # crossing into "follows us", so their followback timing is an upper bound only.
        "watching_since": None,
        "followers": {},
        "removed": {},
        "stats": {},
    }


def load_state():
    state = _read_json(STATE_FILE, None)
    if not isinstance(state, dict) or "followers" not in state:
        return blank_state()
    base = blank_state()
    base.update(state)
    base.setdefault("ordering", {"verified": False, "verified_at": None, "evidence": {}})
    return base


def days_since(iso):
    parsed = parse_ts(iso)
    return math.inf if parsed is None else (datetime.now() - parsed).total_seconds() / 86400.0


def drift_forces_full(reported, known):
    """True when the known set has fallen too far from what X says we have.

    This is the backstop that assumes nothing about page ordering: if X ever reorders
    the list and incremental runs start missing people, the known count falls behind and
    within a day or two a run promotes itself to full and heals.
    """
    if not reported:
        return False
    missing = reported - known
    return missing > max(50, 0.01 * reported) or missing < -max(200, 0.02 * reported)


def verify_ordering(state, pages_this_run, page_size):
    """Did this run's page composition behave like a newest-first list? -> (ok, evidence)

    Two independent checks against the previous run's recorded first-sighting pages:
      A. newness is front-loaded — every id new this run showed up within the first
         ceil(new/page_size) + 1 pages;
      B. old is back-loaded — at least 90% of the previously-seen ids on this run's last
         page were on one of the previous run's last two pages.
    Both must hold, and the list must be long enough for them to mean anything. Until
    then, incremental mode stays off.
    """
    previous = state.get("followers") or {}
    prev_pages = {fid: info.get("page") for fid, info in previous.items()
                  if info.get("page") is not None}
    evidence = {"runs_compared": 2, "pages_scanned": len(pages_this_run)}
    if not prev_pages or not pages_this_run:
        evidence["reason"] = "no previous page index to compare against"
        return False, evidence
    if len(pages_this_run) < MIN_PAGES_TO_VERIFY_ORDERING:
        evidence["reason"] = (
            f"only {len(pages_this_run)} page(s): too few to distinguish newest-first "
            f"from any other order, and a list this short does not need incremental")
        return False, evidence
    prev_max = max(prev_pages.values())
    if prev_max < MIN_PAGES_TO_VERIFY_ORDERING - 1:
        evidence["reason"] = "the previous run was too short to compare tails against"
        return False, evidence

    new_ids, new_id_pages = [], []
    for page_index, rows in enumerate(pages_this_run):
        for row in rows:
            if row["id"] not in previous:
                new_ids.append(row["id"])
                new_id_pages.append(page_index)

    evidence["new_ids"] = len(new_ids)
    allowed = math.ceil(len(new_ids) / page_size) + 1
    evidence["allowed_page_index"] = allowed
    evidence["max_page_index_of_new_id"] = max(new_id_pages) if new_id_pages else 0
    check_a = not new_id_pages or evidence["max_page_index_of_new_id"] <= allowed

    last_page = pages_this_run[-1]
    tail = {fid for fid, page in prev_pages.items() if page >= prev_max - 1}
    overlapping = [r for r in last_page if r["id"] in tail]
    known_on_last = [r for r in last_page if r["id"] in previous]
    ratio = (len(overlapping) / len(known_on_last)) if known_on_last else 0.0
    evidence["tail_overlap_pct"] = round(ratio * 100, 1)
    check_b = ratio >= 0.9

    evidence["check_a_newness_front_loaded"] = check_a
    evidence["check_b_old_back_loaded"] = check_b
    return (check_a and check_b), evidence


# ============================================================
# SNAPSHOTS
# ============================================================

def snapshot_path(mode, partial=False):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    suffix = ".partial" if partial else ("-full" if mode == "full" else "-delta")
    return os.path.join(SNAPSHOT_DIR, f"followers-{day}{suffix}.csv")


class SnapshotWriter:
    """Streams rows to the partial CSV as they arrive, so a crash or a rate-limit exit
    leaves everything fetched so far on disk — which is also how a resumed run rebuilds
    its seen-id set without checkpointing a 20k-entry id list."""

    def __init__(self, mode, enabled=True):
        self.mode = mode
        self.enabled = enabled
        self.path = snapshot_path(mode, partial=True) if enabled else None
        self.handle = None
        self.writer = None

    def open(self, append=False):
        if not self.enabled:
            return
        exists = append and os.path.exists(self.path)
        self.handle = open(self.path, "a" if append else "w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.handle)
        if not exists:
            self.handle.write("﻿")
            self.writer.writerow(CSV_HEADERS)

    def write(self, rows):
        if not self.enabled:
            return
        for row in rows:
            self.writer.writerow([row.get(f, "") for f in CSV_FIELDS])
        self.handle.flush()

    def close(self, finished):
        if not self.enabled or self.handle is None:
            return None
        self.handle.close()
        self.handle = None
        if not finished:
            return self.path
        final = snapshot_path(self.mode, partial=False)
        os.replace(self.path, final)
        return final


def read_partial_rows(path):
    """Rows a previous attempt already wrote, back in our canonical shape.

    A resumed run has to recover these, not just their ids. They are part of this
    export: the fold needs them, and a resumed *full* run computes "who is gone" as
    everything in the known set that this export did not see — so forgetting the first
    attempt's pages would mark every one of those followers as lost.
    """
    if not path or not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                return []
            for raw in reader:
                if not raw:
                    continue
                row = dict(zip(CSV_FIELDS, raw))
                if row.get("id"):
                    rows.append(row)
    except Exception as e:
        log(f"⚠️ could not re-read {path}: {e}")
    return rows


def read_partial_ids(path):
    return {row["id"] for row in read_partial_rows(path)}


def prune_snapshots():
    """Keep 12 weeks of full exports plus the first of each month forever; deltas for 30
    days; abandoned partials for a week."""
    if not os.path.isdir(SNAPSHOT_DIR):
        return 0
    now = datetime.now()
    monthly_kept = set()
    removed = 0
    for name in sorted(os.listdir(SNAPSHOT_DIR)):
        if not name.startswith("followers-") or not name.endswith(".csv"):
            continue
        try:
            day = datetime.strptime(name[len("followers-"):len("followers-") + 10], "%Y-%m-%d")
        except ValueError:
            continue
        age_days = (now - day).total_seconds() / 86400.0
        keep = True
        if name.endswith(".partial.csv"):
            keep = age_days <= 7
        elif name.endswith("-delta.csv"):
            keep = age_days <= 30
        elif name.endswith("-full.csv"):
            month = day.strftime("%Y-%m")
            if month not in monthly_kept:
                monthly_kept.add(month)       # first full export of the month, kept forever
            else:
                keep = age_days <= 84
        if not keep:
            try:
                os.remove(os.path.join(SNAPSHOT_DIR, name))
                removed += 1
            except OSError:
                pass
    return removed


# ============================================================
# RECONCILE
# ============================================================

def reconcile_queue(followers, mode, settings_days, watching_since=None, dry_run=False,
                    self_view=False):
    """Flag queue rows whose handle or id is in the followers set.

    `followers` is the whole known set, not just this run's pages: an incremental run
    fetches 4 pages but must still be able to flag a row that a full run found weeks
    ago and the queue has since gained.

    The flag goes on *every* row for a handle — that is what stops a list reset in some
    other list from re-following a mutual — while the metrics counter goes on exactly
    one, chosen by attributed_row().
    """
    stats = {
        "matched": 0, "newly_marked": 0, "mutual": 0, "follower_only": 0,
        "renamed": 0, "lost": 0, "state_drift": 0, "measured": 0, "unmeasured": 0,
        "ledger": [],
    }
    now_iso = datetime.now().isoformat()
    follow_clock = read_follow_clock()
    watching_from = parse_ts(watching_since)

    with queue_transaction() as queue:
        if not queue:
            return stats
        index = identity_index(queue)

        for fid, info in followers.items():
            handle = info.get("u") or ""
            rows = lookup_rows(index, queue, username=handle, user_id=fid)
            if not rows:
                continue
            stats["matched"] += 1

            primary = attributed_row(rows)
            status = primary.get("status")
            row_state = "mutual" if status == "followed" else (
                "follower_only" if status in ("unfollowed", "skipped") else "mutual")
            was_flagged = bool(primary.get("follows_back"))

            for entry in rows:
                entry["follows_back"] = True
                entry.setdefault("follows_back_at", now_iso)
                entry["follows_back_state"] = row_state
                entry["follows_back_source"] = "followers_export"
                entry["follows_back_checked_at"] = now_iso
                # The id lets us survive a rename; record it once so a later handle-only
                # lookup still lands, without rewriting `username` (followed.csv joins on
                # the handle, so rewriting it would orphan the follow record).
                if fid and not entry.get("user_id"):
                    entry["user_id"] = fid
            for entry in rows:
                entry.pop("follows_back_attributed", None)
            primary["follows_back_attributed"] = True

            existing = (primary.get("username") or "").strip().lower()
            renamed = bool(handle and existing and existing != handle.lower()
                           and not existing.isdigit())
            if renamed:
                stats["renamed"] += 1

            # Only meaningful when the export ran as the account being listed. Otherwise
            # this is "the export account does not follow them", which is true of almost
            # everyone and would report the whole queue as drifted.
            if self_view and info.get("x_following") is False and status == "followed":
                stats["state_drift"] += 1

            if not was_flagged:
                stats["newly_marked"] += 1
                stats[row_state] += 1
                record = _timing_record(primary, existing, handle, follow_clock,
                                        watching_from, settings_days)
                stats["measured" if record["timing"] == "measured" else "unmeasured"] += 1
                record.update({
                    "ts": now_iso,
                    "user_id": fid,
                    "username": handle,
                    "source_list": primary.get("source_list") or "unknown",
                    "queue_status_at_detection": status,
                    "state": row_state,
                    "detected_by": "followers_export",
                    "renamed_from": existing if renamed else None,
                })
                stats["ledger"].append(record)

        # Only a full run may conclude that someone is gone: an incremental run stops
        # after a few pages, so absence from it proves nothing at all. Even then the flag
        # stays — clearing it would let a list reset re-follow a row we are still
        # recorded as following, the exact bug the flag exists to prevent.
        if mode == "full":
            follower_handles = {(info.get("u") or "").lower()
                                for info in followers.values() if info.get("u")}
            for entry in queue:
                if not entry.get("follows_back"):
                    continue
                if entry.get("follows_back_source") != "followers_export":
                    continue
                row_id = str(entry.get("user_id") or "")
                handle = (entry.get("username") or "").strip().lower()
                still_here = (row_id in followers) or (bool(handle)
                                                       and handle in follower_handles)
                if not still_here and not entry.get("follows_back_lost_at"):
                    entry["follows_back_lost_at"] = now_iso
                    stats["lost"] += 1

        if dry_run:
            # Raising out of the transaction body is the abort: queue_transaction only
            # writes on a clean exit, so everything mutated above dies with the in-memory
            # copy and queue.json is never opened for writing at all.
            raise _DryRunAbort(stats)

    return stats


def _timing_record(primary, existing_handle, handle, follow_clock, watching_from,
                   settings_days):
    """How long the followback took — or an honest admission that we cannot know.

    We only ever observe "they follow us now", so the elapsed time is a measurement only
    if we were already watching when the follow went out. For anyone followed before the
    exporter's first run, the followback could have landed the same day; all we have is
    an upper bound, and averaging those in would invent a trend out of the initial
    backfill. `late` is withheld for the same reason: a fan who followed back on day one
    and was unfollowed anyway is not a late followback, and from here the two look
    identical.
    """
    followed_at = None
    for key in (handle.lower() if handle else "", (existing_handle or "").lower()):
        if key and key in follow_clock:
            followed_at = follow_clock[key]
            break
    if not followed_at and primary.get("status") == "followed":
        followed_at = primary.get("timestamp")

    parsed = parse_ts(followed_at)
    if parsed is None:
        return {"followed_at": None, "timing": "unknown",
                "days_to_followback": None, "days_to_followback_max": None}

    elapsed = round((datetime.now() - parsed).total_seconds() / 86400.0, 1)
    if watching_from is not None and parsed >= watching_from:
        return {"followed_at": followed_at, "timing": "measured",
                "days_to_followback": elapsed,
                "late": bool(elapsed > settings_days)}
    return {"followed_at": followed_at, "timing": "upper_bound",
            "days_to_followback_max": elapsed}


class _DryRunAbort(Exception):
    def __init__(self, stats):
        super().__init__("dry run")
        self.stats = stats


def reconcile(followers, mode, settings_days, watching_since=None, dry_run=False,
              self_view=False):
    try:
        return reconcile_queue(followers, mode, settings_days,
                               watching_since=watching_since, dry_run=dry_run,
                               self_view=self_view)
    except _DryRunAbort as abort:
        return abort.stats


def append_ledger(entries, path=LEDGER_FILE):
    if not entries:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps({k: v for k, v in entry.items() if v is not None}) + "\n")
    return len(entries)


def read_follow_clock(path="followed.csv"):
    """{handle -> follow timestamp} from followed.csv.

    The queue row is the wrong place to ask when we followed someone: once the bot
    unfollows, update_queue_status overwrites `timestamp` with the *unfollow* time. Only
    followed.csv keeps the original follow clock, and it keeps it after an unfollow —
    the unfollow pass appends to unfollowed.csv rather than removing the row here.
    """
    clock = {}
    if not os.path.exists(path):
        return clock
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                handle = (row.get("username") or "").strip().lower()
                stamp = (row.get("follow_timestamp") or "").strip()
                if handle and stamp and handle not in clock:
                    clock[handle] = stamp
    except Exception as e:
        log(f"⚠️ could not read {path}: {e}")
    return clock


def read_settings_days(default=4):
    config = _read_json("bot_config.json", {})
    try:
        return int(config.get("unfollow_after_days", default))
    except (TypeError, ValueError):
        return default


# ============================================================
# STATUS
# ============================================================

def write_status(payload):
    payload["written_at"] = datetime.now().isoformat()
    _write_json_atomic(STATUS_FILE, payload)


# ============================================================
# RUN
# ============================================================

def acquire_run_lock():
    """Non-blocking; a second concurrent run exits rather than double-paging the API."""
    import fcntl
    fd = open(RUN_LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return None
    fd.write(f"{os.getpid()}\n")
    fd.flush()
    return fd


def preflight(args):
    """-> (cookies, expiries, user_id). Raises SystemExit with a config code."""
    if not os.path.exists("queue.json") and not args.probe:
        log("❌ no queue.json in the working directory — this is not the bot's data dir.")
        log(f"   cwd = {os.getcwd()}")
        raise SystemExit(EXIT_CONFIG)

    if not os.path.exists(args.cookies):
        log(f"❌ cookie file not found: {args.cookies}")
        raise SystemExit(EXIT_CONFIG)
    try:
        cookies, expiries = load_cookie_file(args.cookies)
    except Exception as e:
        log(f"❌ could not parse {args.cookies}: {e}")
        raise SystemExit(EXIT_CONFIG)

    missing = [c for c in ("ct0", "auth_token") if not cookies.get(c)]
    if missing:
        log(f"❌ {args.cookies} has no {', '.join(missing)} — not a logged-in session.")
        log("   Not falling back to cacheforlogin.json: that jar belongs to Selenium.")
        raise SystemExit(EXIT_CONFIG)

    # Two separate identities, and conflating them is the one mistake that makes this
    # whole job useless:
    #
    #   session_user_id — who the cookie file authenticates us as. Whose quota we spend.
    #   target_user_id  — whose followers we list. Must be the account that does the
    #                     following, because "did they follow *us* back" is a question
    #                     about the bot account's followers and nobody else's.
    #
    # They are allowed to differ: a public account's followers are readable from any
    # logged-in session, which is the entire point of exporting from a second account.
    session_user_id = user_id_from_twid(cookies.get("twid"))

    target_user_id = (args.target_user_id
                      or load_state().get("account", {}).get("user_id"))
    if not target_user_id and os.path.exists(BOT_COOKIE_FILE):
        try:
            bot_cookies, _ = load_cookie_file(BOT_COOKIE_FILE)
            target_user_id = user_id_from_twid(bot_cookies.get("twid"))
            if target_user_id:
                log(f"target account id read from {BOT_COOKIE_FILE} (twid): {target_user_id}")
        except Exception as e:
            log(f"⚠️ could not read a target id from {BOT_COOKIE_FILE}: {e}")
    target_user_id = target_user_id or session_user_id

    if not target_user_id:
        log("❌ could not determine whose followers to export: no --target-user-id, no "
            "cached id, and no twid cookie in either jar.")
        raise SystemExit(EXIT_CONFIG)

    if session_user_id and str(session_user_id) == str(target_user_id):
        log(f"ℹ️ authenticated as the same account we are exporting ({target_user_id}).")
        log("   No quota isolation: these requests spend the bot account's own budget, so "
            "the pacing below is the only thing protecting it.")
    elif session_user_id:
        log(f"ℹ️ authenticated as {session_user_id}, exporting the followers of "
            f"{target_user_id}.")
        log("   This needs the target account's followers to be publicly visible.")

    return cookies, expiries, str(target_user_id), session_user_id


def cookie_health(expiries):
    exp = expiries.get("auth_token")
    if not exp:
        return {"auth_token_expires_at": None, "days_left": None}
    when = datetime.fromtimestamp(exp)
    days = round((when - datetime.now()).total_seconds() / 86400.0, 1)
    if days < COOKIE_EXPIRY_WARN_DAYS:
        log(f"⚠️ export cookies expire in {days} days ({when:%Y-%m-%d}) — re-export soon")
    return {"auth_token_expires_at": when.isoformat(), "days_left": days}


def run_probe(args):
    cookies, expiries, user_id, session_user_id = preflight(args)
    session = build_session(cookies)
    log(f"exporting followers of {user_id}; authenticated as {session_user_id}")

    try:
        account = fetch_account(session, user_id)
        log(f"account: @{account['screen_name']}  followers_count={account['followers_count']}")
    except ExporterError as e:
        if e.kind == "AUTH_ERROR":
            raise
        account = {"screen_name": None, "followers_count": None}
        log(f"account lookup unavailable ({e.kind}) — continuing; the followers list "
            f"uses a different endpoint")

    session = build_session(cookies, account["screen_name"])
    for count in (args.page_size, 200):
        rows, cursor, meta = fetch_followers_page(session, user_id, count=count)
        log(f"count={count} -> HTTP {meta['status']}, {len(rows)} rows, "
            f"next_cursor={'yes' if cursor else 'no'}, {meta['elapsed']}s")
        log(f"   x-rate-limit: limit={meta['limit']} remaining={meta['remaining']} "
            f"reset_at={datetime.fromtimestamp(meta['reset_at']).isoformat() if meta['reset_at'] else None}")
        log(f"   perspectival 'following' field present: {'x_following' in rows[0] if rows else 'n/a'}")
        for row in rows[:3]:
            log(f"   @{row['username']} ({row['id']}) followers={row['followers_count']} "
                f"x_following={row.get('x_following')}")
        if count == args.page_size:
            log(f"   -> next page would wait {adaptive_delay(meta):.0f}s")
            if 200 <= args.page_size:
                break
            time.sleep(min(10, adaptive_delay(meta)))
    if account["followers_count"]:
        for size in (100, 200):
            pages = math.ceil(account["followers_count"] / size)
            log(f"full export estimate at count={size}: {pages} pages ≈ "
                f"{pages * 64 / 60:.0f} min at 64s/page")
    log("probe complete — nothing was written")
    return EXIT_OK


def run_export(args):
    started = time.time()
    run_id = datetime.now().isoformat()
    cookies, expiries, user_id, session_user_id = preflight(args)
    health = cookie_health(expiries)

    status = {
        "run_id": run_id, "started_at": run_id, "finished_at": None, "mode": None,
        "outcome": "partial", "exit_code": EXIT_PARTIAL, "stop_reason": None,
        "pages": 0, "rows_seen": 0, "new_followers": 0, "gone_followers": 0,
        "followers_total": 0, "reported_followers_count": None, "drift": None,
        "queue_marked": 0, "queue_marked_mutual": 0, "queue_marked_follower_only": 0,
        "rate": {}, "cookie": health, "ordering_verified": False,
        "next_full_due": None, "snapshot": None, "dry_run": bool(args.dry_run),
        "message": "",
    }

    if not egress_ok():
        log("❌ no egress (TLS handshake failed) — exiting for the timer to retry.")
        log("   Not touching the VPN: the watchdog and the bot already contend for it.")
        status.update(outcome="egress_down", exit_code=EXIT_EGRESS,
                      message="no egress at start of run")
        write_status(status)
        return EXIT_EGRESS

    state = load_state()
    session = build_session(cookies)

    # The account lookup is enrichment, not the job: it supplies the screen_name used in
    # the referer and the follower count the drift check measures against. It is also the
    # only GraphQL call here, and GraphQL is exactly what a Cloudflare-blocked exit
    # refuses while REST keeps answering — so letting it abort the run would throw away a
    # perfectly working export. Only a genuine auth failure is fatal.
    cached_account = state.get("account") or {}
    try:
        account = fetch_account(session, user_id)
    except ExporterError as e:
        if e.kind == "AUTH_ERROR":
            return _fail(status, e)
        account = {"screen_name": cached_account.get("screen_name"),
                   "followers_count": state.get("reported_followers_count")}
        log(f"⚠️ account lookup unavailable ({e.kind}: {e.detail}); continuing with "
            f"cached details. The followers list itself uses a different endpoint.")
        if e.kind == "EDGE_BLOCKED":
            log("   This exit is Cloudflare-blocked for GraphQL. Not rotating the VPN; "
                "the drift check just sits this run out.")
        status["account_lookup"] = e.kind
    session = build_session(cookies, account["screen_name"])
    reported = account["followers_count"]
    status["reported_followers_count"] = reported
    self_view = bool(session_user_id and str(session_user_id) == str(user_id))
    status["account"] = {"user_id": user_id, "screen_name": account["screen_name"],
                         "session_user_id": session_user_id,
                         "quota_isolated": not self_view,
                         "self_view": self_view}
    state["account"] = {
        "user_id": user_id,
        "screen_name": account["screen_name"],
        "id_source": "arg" if args.target_user_id else "twid_cookie",
        "session_user_id": session_user_id,
        "quota_isolated": bool(session_user_id and str(session_user_id) != str(user_id)),
    }

    known = state["followers"]
    checkpoint = _read_json(CURSOR_FILE, None)
    if checkpoint and days_since(checkpoint.get("started_at")) * 24 > CHECKPOINT_MAX_AGE_H:
        log(f"checkpoint is older than {CHECKPOINT_MAX_AGE_H}h — discarding, X may have "
            f"invalidated the cursor")
        checkpoint = None

    ordering_ok = bool((state.get("ordering") or {}).get("verified"))
    drift = (reported - len(known)) if reported else None
    forced = drift_forces_full(reported, len(known))
    mode = "full" if (args.full or not known or not ordering_ok
                      or days_since(state.get("last_full_run_at")) >= FULL_REPAGE_DAYS
                      or forced) else "incremental"
    if checkpoint and checkpoint.get("mode"):
        mode = checkpoint["mode"]

    reason = ("--full" if args.full else
              "no state yet" if not known else
              "ordering not yet verified" if not ordering_ok else
              "weekly re-page due" if days_since(state.get("last_full_run_at")) >= FULL_REPAGE_DAYS
              else "count drift" if forced else "incremental")
    page_size = args.page_size or state.get("page_size") or DEFAULT_PAGE_SIZE
    max_pages = args.max_pages or (math.inf if mode == "full" else MAX_PAGES_INCREMENTAL)
    status["mode"] = mode
    status["drift"] = drift
    log(f"mode={mode} ({reason}); known={len(known)} reported={reported} "
        f"page_size={page_size}")

    snapshot = SnapshotWriter(mode, enabled=not args.dry_run)
    cursor = None
    pages_done = 0
    seen_ids = set()
    pages_this_run = []
    if checkpoint:
        cursor = checkpoint.get("cursor")
        pages_done = checkpoint.get("pages_done", 0)
        page_size = checkpoint.get("page_size", page_size)
        recovered = read_partial_rows(checkpoint.get("partial_csv"))
        seen_ids = {row["id"] for row in recovered}
        # Page boundaries are not in the CSV, but every page wrote exactly page_size rows
        # (only the last can be short), so chunking reproduces the original indices —
        # which is what the ordering check compares against.
        pages_this_run = [recovered[i:i + page_size]
                          for i in range(0, len(recovered), page_size)]
        log(f"resuming from checkpoint: page {pages_done}, "
            f"{len(seen_ids)} rows recovered from the partial CSV")
        snapshot.open(append=True)
    else:
        snapshot.open(append=False)

    consecutive_known = 0
    rate_sleeps = 0
    new_ids = []
    last_meta = {}
    stop_reason = None
    deadline = started + args.max_runtime

    try:
        while True:
            if pages_done >= max_pages:
                stop_reason = "max_pages"
                break
            try:
                rows, next_cursor, meta = fetch_followers_page(
                    session, user_id, cursor, page_size)
            except ExporterError as e:
                if e.kind == "RATE_LIMITED":
                    rate_sleeps += 1
                    if rate_sleeps > MAX_RATE_SLEEPS:
                        stop_reason = "rate_budget"
                        break
                    wait = max(60.0, (e.meta.get("reset_at") or 0) - time.time()) \
                        + random.uniform(5, 20)
                    log(f"rate limited ({rate_sleeps}/{MAX_RATE_SLEEPS}); waiting {wait/60:.1f}m")
                    if wait > EGRESS_RECHECK_BEFORE_SLEEP_S and not egress_ok():
                        stop_reason = "egress_down"
                        break
                    _checkpoint(run_id, mode, cursor, pages_done, page_size,
                                "rate_limited", snapshot.path)
                    time.sleep(wait)
                    continue
                raise

            last_meta = meta
            pages_done += 1
            pages_this_run.append(rows)
            fresh = [r for r in rows if r["id"] not in known and r["id"] not in seen_ids]
            consecutive_known = 0 if fresh else consecutive_known + 1
            new_ids.extend(r["id"] for r in fresh)
            seen_ids.update(r["id"] for r in rows)
            snapshot.write(rows)
            log(f"page {pages_done}: {len(rows)} rows, {len(fresh)} new, "
                f"remaining={meta['remaining']}")

            if mode == "incremental" and consecutive_known >= KNOWN_PAGE_STOP:
                stop_reason = "known_depth"
                break
            if not next_cursor:
                stop_reason = "end_of_list"
                break
            cursor = next_cursor

            if time.time() > deadline:
                stop_reason = "runtime_budget"
                break
            delay = adaptive_delay(meta)
            if delay > EGRESS_RECHECK_BEFORE_SLEEP_S and not egress_ok():
                stop_reason = "egress_down"
                break
            _checkpoint(run_id, mode, cursor, pages_done, page_size, "paging", snapshot.path)
            time.sleep(delay)
    except ExporterError as e:
        snapshot.close(finished=False)
        _checkpoint(run_id, mode, cursor, pages_done, page_size, e.kind, snapshot.path)
        status.update(pages=pages_done, rows_seen=len(seen_ids), rate=last_meta)
        return _fail(status, e)

    finished = stop_reason in ("end_of_list", "known_depth")
    final_snapshot = snapshot.close(finished=finished)
    status.update(pages=pages_done, rows_seen=len(seen_ids), stop_reason=stop_reason,
                  rate=last_meta, snapshot=final_snapshot)

    if not finished:
        _checkpoint(run_id, mode, cursor, pages_done, page_size, stop_reason, snapshot.path)
        outcome = "egress_down" if stop_reason == "egress_down" else "partial"
        code = EXIT_EGRESS if stop_reason == "egress_down" else EXIT_PARTIAL
        status.update(outcome=outcome, exit_code=code,
                      message=f"stopped after {pages_done} page(s): {stop_reason}; "
                              f"will resume from the checkpoint")
        log(f"⏸️ {status['message']}")
        write_status(status)
        return code

    if os.path.exists(CURSOR_FILE):
        os.remove(CURSOR_FILE)

    # --- fold this run into the known set ---
    now_iso = datetime.now().isoformat()
    # Snapshot the first-sighting pages before the fold overwrites them: the ordering
    # check compares this run's page composition against the *previous* run's, and
    # folding first would make every id look already-known and the check vacuous.
    prev_followers = ({fid: dict(info) for fid, info in known.items()}
                      if mode == "full" else {})
    for page_index, rows in enumerate(pages_this_run):
        for row in rows:
            entry = known.get(row["id"])
            if entry is None:
                known[row["id"]] = {"u": row["username"], "first_seen": now_iso,
                                    "last_seen": now_iso, "page": page_index}
            else:
                entry["u"] = row["username"] or entry.get("u")
                entry["last_seen"] = now_iso
                entry["page"] = page_index
            if self_view and "x_following" in row:
                known[row["id"]]["x_following"] = row["x_following"]
            else:
                # Not our own view: drop it rather than leave a stale value behind from a
                # run that was authenticated as the listed account.
                known[row["id"]].pop("x_following", None)

    gone = []
    if mode == "full":
        present = {r["id"] for page in pages_this_run for r in page}
        gone = [fid for fid in known if fid not in present]
        for fid in gone:
            info = known.pop(fid)
            info["removed_at"] = now_iso
            state["removed"][fid] = info
        if len(state["removed"]) > REMOVED_CAP:
            ordered = sorted(state["removed"].items(),
                             key=lambda kv: kv[1].get("removed_at") or "")
            state["removed"] = dict(ordered[-REMOVED_CAP:])

        ok, evidence = verify_ordering({"followers": prev_followers},
                                       pages_this_run, page_size)
        previously = state["ordering"].get("verified")
        if not previously:
            state["ordering"]["evidence"] = evidence
            if ok:
                state["ordering"]["verified"] = True
                state["ordering"]["verified_at"] = now_iso
                log("✅ newest-first ordering confirmed — later runs may go incremental")
            else:
                log(f"ordering not confirmed yet; staying on full re-pages. {evidence}")
        state["last_full_run_at"] = now_iso

    watching_since = state.get("watching_since")
    state.update(last_run_at=now_iso, last_success_at=now_iso, last_mode=mode,
                 page_size=page_size, reported_followers_count=reported)
    if not watching_since:
        state["watching_since"] = now_iso
    state["stats"] = {"total": len(known), "new_last_run": len(new_ids),
                      "gone_last_run": len(gone)}
    # --dry-run persists nothing but the status file: a run you can point at production
    # to see what it would do. --dry-run-queue is the softer one — it keeps our own
    # state and snapshots and only leaves queue.json alone.
    if not args.dry_run:
        _write_json_atomic(STATE_FILE, state)

    status.update(followers_total=len(known), new_followers=len(new_ids),
                  gone_followers=len(gone),
                  ordering_verified=bool(state["ordering"].get("verified")))
    if state.get("last_full_run_at"):
        due = parse_ts(state["last_full_run_at"]) + timedelta(days=FULL_REPAGE_DAYS)
        status["next_full_due"] = due.strftime("%Y-%m-%d")

    # --- reconcile ---
    if args.dry_run:
        log(f"dry run: {len(known)} followers known; nothing written but the status file")
        status.update(outcome="ok", exit_code=EXIT_OK,
                      message=f"dry run over {pages_done} page(s)")
        write_status(status)
        return EXIT_OK

    code = _apply_reconcile(status, known, mode, args, watching_since=watching_since,
                            self_view=self_view)
    prune_snapshots()
    write_status(status)
    log(f"done in {(time.time() - started)/60:.1f}m: {status['message']}")
    return code


def _apply_reconcile(status, known, mode, args, watching_since=None, self_view=False):
    settings_days = read_settings_days()
    if len(known) > SUSPICIOUS_MIN_FOLLOWERS:
        probe = reconcile(known, mode, settings_days, watching_since, dry_run=True,
                          self_view=self_view)
        if probe["matched"] == 0:
            status.update(outcome="suspicious", exit_code=EXIT_SHAPE,
                          message=f"0 of {len(known)} followers matched any queue row — "
                                  f"refusing to write queue.json")
            log(f"❌ {status['message']}")
            return EXIT_SHAPE

    stats = reconcile(known, mode, settings_days, watching_since,
                      dry_run=args.dry_run_queue, self_view=self_view)
    if args.dry_run_queue:
        append_ledger(stats["ledger"], "followback_ledger.preview.jsonl")
        log(f"would mark {stats['newly_marked']} row(s): {stats['mutual']} mutual, "
            f"{stats['follower_only']} follower_only (of {stats['matched']} matched)")
        for entry in stats["ledger"][:20]:
            log(f"   @{entry['username']} {entry['state']} "
                f"status={entry['queue_status_at_detection']} "
                f"days={entry['days_to_followback']} late={entry['late']}")
    else:
        append_ledger(stats["ledger"])
        log(f"marked {stats['newly_marked']} new followback(s): {stats['mutual']} mutual, "
            f"{stats['follower_only']} follower_only (of {stats['matched']} matched)")
        if stats["unmeasured"]:
            log(f"{stats['unmeasured']} of them predate this job, so their followback "
                f"timing is an upper bound and is left out of the averages")
        if stats["lost"]:
            log(f"{stats['lost']} previously-flagged follower(s) are gone (flag kept)")
        if stats["state_drift"]:
            log(f"⚠️ {stats['state_drift']} row(s): queue says followed, X says not following")

    status.update(queue_marked=stats["newly_marked"],
                  queue_marked_mutual=stats["mutual"],
                  queue_marked_follower_only=stats["follower_only"],
                  queue_marked_measured=stats["measured"],
                  queue_marked_unmeasured=stats["unmeasured"],
                  outcome="ok", exit_code=EXIT_OK)
    status["message"] = (f"{status['mode']} run, {status['pages']} page(s), "
                         f"{status['followers_total']} followers, "
                         f"{stats['newly_marked']} new followback(s)")
    return EXIT_OK


def _checkpoint(run_id, mode, cursor, pages_done, page_size, reason, partial_csv):
    if cursor is None and reason == "paging":
        return
    _write_json_atomic(CURSOR_FILE, {
        "run_id": run_id, "mode": mode, "cursor": cursor, "pages_done": pages_done,
        "page_size": page_size, "started_at": run_id, "reason": reason,
        "partial_csv": partial_csv,
    })


def _fail(status, error):
    mapping = {"AUTH_ERROR": ("auth_error", EXIT_AUTH),
               "EDGE_BLOCKED": ("edge_blocked", EXIT_PARTIAL),
               "SHAPE_CHANGED": ("shape_change", EXIT_SHAPE),
               "NETWORK": ("partial", EXIT_PARTIAL),
               "RATE_LIMITED": ("partial", EXIT_PARTIAL)}
    outcome, code = mapping.get(error.kind, ("partial", EXIT_PARTIAL))
    message = f"{error.kind}: {error.detail}"
    if error.kind == "AUTH_ERROR":
        message += " — the export session is dead; re-export the cookie file"
    elif error.kind == "EDGE_BLOCKED":
        message += (" — this VPN exit is Cloudflare-blocked for that path. The cookies "
                    "are fine; retry, or move the box to another exit.")
    status.update(outcome=outcome, exit_code=code, message=message)
    if error.body:
        status["body"] = error.body
    write_status(status)
    log(f"❌ {message}")
    if error.body:
        log(f"   body: {error.body[:300]}")
    return code


def run_reconcile_only(args):
    state = load_state()
    known = state.get("followers") or {}
    if not known:
        log("❌ no followers_state.json to reconcile from — run an export first")
        return EXIT_CONFIG
    status = _read_json(STATUS_FILE, {})
    status.setdefault("run_id", datetime.now().isoformat())
    status.update(mode="reconcile_only", started_at=datetime.now().isoformat(),
                  followers_total=len(known), dry_run=bool(args.dry_run_queue),
                  pages=0)
    code = _apply_reconcile(status, known, state.get("last_mode") or "incremental", args,
                            watching_since=state.get("watching_since"))
    status["finished_at"] = datetime.now().isoformat()
    write_status(status)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="normal daily run (default)")
    parser.add_argument("--probe", action="store_true",
                        help="one page, print everything, write nothing")
    parser.add_argument("--full", action="store_true", help="force a complete re-page")
    parser.add_argument("--reconcile-only", action="store_true",
                        help="no API calls; re-apply followers_state.json to queue.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="make the requests and report; persist nothing but the "
                             "status file (implies --dry-run-queue)")
    parser.add_argument("--dry-run-queue", action="store_true",
                        help="keep snapshots and state, but never write queue.json; "
                             "previews the rows it would flag")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-runtime", type=int, default=DEFAULT_MAX_RUNTIME_S)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--target-user-id", default=None,
                        help="whose followers to export; defaults to the bot account "
                             "(cacheforlogin.json's twid), NOT the export session")
    parser.add_argument("--cookies", default=COOKIE_FILE)
    args = parser.parse_args()

    if args.data_dir:
        os.chdir(args.data_dir)
    if args.dry_run:
        args.dry_run_queue = True

    lock = acquire_run_lock()
    if lock is None:
        log("another export is already running — exiting")
        return EXIT_OK
    try:
        if args.probe:
            return run_probe(args)
        if args.reconcile_only:
            return run_reconcile_only(args)
        return run_export(args)
    except SystemExit as e:
        return e.code
    except ExporterError as e:
        return _fail({"run_id": datetime.now().isoformat()}, e)
    except KeyboardInterrupt:
        log("interrupted")
        return EXIT_PARTIAL


if __name__ == "__main__":
    sys.exit(main())
