"""
queue_store — locked, atomic access to queue.json.

Every writer of queue.json used to do its own read-modify-write with tmp + os.replace
and no lock, while dashboard.py took an flock on data.lock. That was survivable while
the bot was the only writer mid-cycle, but the followers exporter reads the whole
queue, spends time matching thousands of rows, then writes back — long enough for the
bot to mark someone `followed` in the gap and have that write vanish, turning a
`followed` row back into `pending_follow` and following the same person twice.

The fix is to read *inside* the lock, which a bare "take a lock around the write" cannot
express. `queue_transaction` is that: lock, read, hand you the list, write it back if you
changed it, unlock.

The lock file is the same data.lock dashboard.py has always used, so the dashboard, the
bot and the exporter all serialise against each other.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime

from twitter_csv import identity_key_set

LOCK_FILE = "data.lock"
QUEUE_FILE = "queue.json"

# How long to wait for the lock before giving up and writing anyway. Deliberately not
# infinite: a hung dashboard request holding the lock must never wedge the bot's follow
# loop. An unlocked write is what every one of these callers did unconditionally before
# this module existed, so falling back to it is no worse than the old behaviour — but it
# gets logged, because it means something is holding the lock far too long.
DEFAULT_LOCK_TIMEOUT = 15.0


def _fpath(name, data_dir="."):
    return os.path.join(data_dir, name)


@contextmanager
def data_lock(data_dir=".", timeout=DEFAULT_LOCK_TIMEOUT):
    """Exclusive flock on data.lock. Yields True if taken, False on timeout."""
    fd = None
    taken = False
    try:
        fd = open(_fpath(LOCK_FILE, data_dir), "w")
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                taken = True
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    print(f"⚠️ queue_store: data.lock still held after {timeout:.0f}s; "
                          f"proceeding unlocked")
                    break
                time.sleep(0.2)
        yield taken
    finally:
        if fd is not None:
            try:
                if taken:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fd.close()
            except Exception:
                pass


def read_queue(data_dir="."):
    """queue.json as a list. Missing or unreadable file -> []."""
    path = _fpath(QUEUE_FILE, data_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ queue_store: could not read {path}: {e}")
        return []


def write_queue(queue, data_dir="."):
    """Atomic rewrite. indent=1 matches what every existing writer produces, so a
    queue_store write leaves no spurious whole-file diff behind."""
    path = _fpath(QUEUE_FILE, data_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=1)
    os.replace(tmp, path)


@contextmanager
def queue_transaction(data_dir=".", timeout=DEFAULT_LOCK_TIMEOUT):
    """lock -> read -> yield the queue -> write it back if you mutated it -> unlock.

    Mutation is detected by comparing against the bytes read, so a transaction that
    changes nothing writes nothing and cannot touch the file's mtime. Raising from the
    body aborts the write.

    A missing queue.json yields [] and writes nothing: callers must not create one.
    """
    with data_lock(data_dir, timeout):
        queue = read_queue(data_dir)
        if not queue and not os.path.exists(_fpath(QUEUE_FILE, data_dir)):
            yield queue
            return
        before = json.dumps(queue, indent=1, sort_keys=True)
        yield queue
        if json.dumps(queue, indent=1, sort_keys=True) != before:
            write_queue(queue, data_dir)


def parse_ts(value):
    """Parse a queue timestamp; the bot has written three formats over its life.

    Always returns a naive local datetime: the bot writes naive `datetime.now()`, but a
    row imported from elsewhere can carry an offset, and mixing the two raises.
    """
    if not value:
        return None
    parsed = None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except (TypeError, ValueError):
                continue
    if parsed is not None and parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def row_identity_keys(entry):
    """Identity keys for one queue row, via the same helper the CSV importer uses."""
    return identity_key_set(entry.get("username"), entry.get("user_id"))


def identity_index(queue):
    """{identity key -> [row indexes]}. A handle can sit in several imported lists, so
    every key maps to a list, never a single row."""
    index = {}
    for i, entry in enumerate(queue):
        for key in row_identity_keys(entry):
            index.setdefault(key, []).append(i)
    return index


def lookup_rows(index, queue, username=None, user_id=None):
    """Rows matching this identity, id first.

    Id-first matters for late followbacks specifically: a followback has no deadline, so
    someone who follows back long after we let them go has had months or years to change
    their handle, and a handle-only match would miss exactly the case we are looking for.
    """
    seen = set()
    rows = []
    uid = (user_id or "").strip()
    keys = []
    if uid.isdigit():
        keys.append("i:" + uid)
    u = (username or "").strip().lower().lstrip("@")
    if u:
        keys.append("u:" + u)
    for key in keys:
        for i in index.get(key, ()):
            if i not in seen:
                seen.add(i)
                rows.append(queue[i])
    return rows


def attributed_row(rows):
    """Of several rows for one handle, the one the follow actually happened on.

    Mirrors the intent of twitter_bot._primary_queue_row without importing it —
    twitter_bot imports selenium at module scope, so it is not importable from a
    headless job. Used for metrics only: the flag itself goes on every row, because
    that is what keeps a list reset from re-following a mutual in some other list.
    """
    if not rows:
        return None
    followed = [e for e in rows if e.get("status") == "followed"]
    if followed:
        return followed[0]
    unfollowed = [e for e in rows if e.get("status") == "unfollowed"]
    if unfollowed:
        return min(unfollowed, key=lambda e: parse_ts(e.get("timestamp")) or datetime.max)
    skipped = [e for e in rows
               if e.get("status") == "skipped"
               and not str(e.get("skip_reason", "")).startswith("duplicate_actioned_from:")]
    if skipped:
        return skipped[0]
    return rows[0]
