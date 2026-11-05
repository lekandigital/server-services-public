#!/usr/bin/env python3
"""
dashboard.py — Web Dashboard for twitter_bot.py

Single-file Flask dashboard for monitoring and controlling the Twitter bot.
Run alongside the bot on the same machine.

Usage:
    python3 dashboard.py [--port 8003] [--data-dir .]
"""

import argparse
import csv
import fcntl
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, Response, jsonify, request, redirect, url_for, send_file, abort
)

from twitter_csv import identity_key_set, is_x_target, looks_like_cookie_jar, parse_csv_export_rows
from queue_dedupe import (
    LIST_PRIORITY_FILE,
    ListOrder,
    dedupe_best_pending,
    load_list_priority,
    pending_source_sizes,
    resolve_list_order,
)
from queue_store import parse_ts as _parse_ts

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

DATA_DIR = "."
LOCK_PATH = "data.lock"

# ============================================================
# DATA ACCESS LAYER
# ============================================================

def _fpath(name):
    return os.path.join(DATA_DIR, name)


def _acquire_lock():
    lp = _fpath(LOCK_PATH)
    fd = open(lp, "w")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    except Exception:
        pass


def safe_read_json(path, default=None, retries=2):
    for attempt in range(retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except json.JSONDecodeError:
            if attempt < retries - 1:
                time.sleep(0.5)
                continue
            tmp = path + ".tmp"
            if os.path.exists(tmp):
                try:
                    with open(tmp, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return default
        except Exception:
            return default
    return default


def read_queue():
    return safe_read_json(_fpath("queue.json"), default=[])


def read_daily_counts():
    data = safe_read_json(_fpath("daily_counts.json"), default={})
    today = datetime.now().strftime("%Y-%m-%d")
    if data.get("date") != today:
        return {"date": today, "follows": 0, "unfollows": 0, "likes": 0, "last_updated": ""}
    return data


def _lists_by_username():
    """{username_lower: [source_list, ...]} from queue.json.

    History rows written before the bot recorded list names carry none, and a handle
    can sit in several imported lists, so the queue is the only place that still
    remembers where an old follow came from."""
    mapping = {}
    for entry in read_queue():
        username = (entry.get("username") or "").lower()
        name = entry.get("source_list") or ""
        if not username or not name:
            continue
        names = mapping.setdefault(username, [])
        # The list the handle was actually actioned from leads; the rest are where else it
        # sat — a duplicate row, or the list an import claimed the row away from.
        if name not in names:
            if entry.get("status") in ("followed", "unfollowed"):
                names.insert(0, name)
            else:
                names.append(name)
        claimed = entry.get("claimed_from") or ""
        if claimed and claimed not in names:
            names.append(claimed)
    return mapping


def _attach_lists(entries, mapping=None):
    """Fill in source_list/source_lists on history rows that predate them."""
    if mapping is None:
        if all(e.get("source_list") for e in entries):
            return entries
        mapping = _lists_by_username()
    for e in entries:
        if e.get("source_list"):
            continue
        names = mapping.get((e.get("username") or "").lower())
        if not names:
            continue
        e["source_list"] = names[0]
        if len(names) > 1:
            e["source_lists"] = names
    return entries


def read_history(limit=None, offset=0, action_filter=None, search=None):
    entries = []
    path = _fpath("history.jsonl")
    if not os.path.exists(path):
        return entries, 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
    except Exception:
        return entries, 0
    parsed = []
    for line in all_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if action_filter and entry.get("action") != action_filter:
            continue
        if search and search.lower() not in entry.get("username", "").lower():
            continue
        parsed.append(entry)
    parsed.reverse()
    total = len(parsed)
    if limit is not None:
        parsed = parsed[offset:offset + limit]
    return _attach_lists(parsed), total


def read_whitelist():
    path = _fpath("whitelist.txt")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return [l.strip() for l in f if l.strip()]
    except Exception:
        return []


def write_whitelist(usernames):
    fd = _acquire_lock()
    try:
        path = _fpath("whitelist.txt")
        with open(path, "w") as f:
            for u in usernames:
                f.write(u.strip().lower().lstrip("@") + "\n")
    finally:
        _release_lock(fd)


def read_list_priority():
    """Manual follow order as a list of source_list names (first = worked through first)."""
    data = safe_read_json(_fpath(LIST_PRIORITY_FILE), default=None)
    order = data.get("order") if isinstance(data, dict) else data
    if not isinstance(order, list):
        return []
    seen, clean = set(), []
    for name in order:
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            clean.append(name)
    return clean


def read_list_anchors(order=None):
    """{source_list: "up"|"down"} — the lists the user actually dragged, and which way.

    Separate from the order itself because a drag saves every visible row: without this
    a single drag would make every list on screen look deliberately placed, which is
    what used to leave a freshly imported list nowhere to go but the bottom. A file
    written before anchors existed has none, and its order is honoured all the same.
    """
    data = safe_read_json(_fpath(LIST_PRIORITY_FILE), default=None)
    raw = data.get("anchors") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    known = set(read_list_priority() if order is None else order)
    return {name: direction for name, direction in raw.items()
            if isinstance(name, str) and name in known and direction in ("up", "down")}


def write_list_priority(order, anchors=None):
    """Persist the manual follow order; an empty order removes the file (back to auto)."""
    fd = _acquire_lock()
    try:
        path = _fpath(LIST_PRIORITY_FILE)
        if not order:
            if os.path.exists(path):
                os.remove(path)
            return
        payload = {"order": order, "anchors": dict(anchors or {}),
                   "updated": datetime.now().isoformat()}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    finally:
        _release_lock(fd)


def read_settings():
    data = safe_read_json(_fpath("bot_config.json"), default=None)
    if data is None:
        return {
            "follow_limit": 320, "unfollow_limit": 800, "like_limit": 600,
            "unfollow_after_days": 4, "max_following_delta": 500,
            "session_rotate_hours": 4
        }
    return data


def write_settings(data):
    fd = _acquire_lock()
    try:
        path = _fpath("bot_config.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    finally:
        _release_lock(fd)


def _get_process_command(pid):
    proc_cmdline = f"/proc/{pid}/cmdline"
    if os.path.exists(proc_cmdline):
        try:
            with open(proc_cmdline, "rb") as f:
                parts = [p.decode("utf-8", errors="replace") for p in f.read().split(b"\0") if p]
            if parts:
                return " ".join(parts)
        except Exception:
            pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _infer_bot_mode(command):
    if not command or "twitter_bot.py" not in command:
        return None
    normalized = " ".join(command.split())
    if normalized.endswith(" unfollow") or " twitter_bot.py unfollow" in f" {normalized} ":
        return "unfollow"
    return "run"


def _bot_mode_label(mode):
    return "Unfollow Only" if mode == "unfollow" else "Normal"


def bot_is_running():
    pid_path = _fpath("bot.pid")
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            mode = _infer_bot_mode(_get_process_command(pid))
            if mode:
                return True, pid, mode
        except (ValueError, OSError):
            pass
    try:
        result = subprocess.run(
            ["pgrep", "-af", "twitter_bot.py"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.strip().split(None, 1)
                if not parts:
                    continue
                pid = int(parts[0])
                command = parts[1] if len(parts) > 1 else ""
                mode = _infer_bot_mode(command)
                if mode:
                    return True, pid, mode
    except Exception:
        pass
    return False, None, None


def _start_bot_process(mode="run"):
    running, _, _ = bot_is_running()
    if running:
        return {"ok": False, "error": "Bot is already running"}
    try:
        bot_script = os.path.join(DATA_DIR, "twitter_bot.py")
        log_file = os.path.join(DATA_DIR, "twitter_bot.log")
        pid_file = _fpath("bot.pid")
        # -u (unbuffered) so twitter_bot.log updates live; block-buffering to a
        # file otherwise hides progress/errors for a long time during triage.
        argv = ["python3", "-u", bot_script, "unfollow" if mode == "unfollow" else "run"]
        p = subprocess.Popen(
            argv,
            cwd=DATA_DIR,
            stdout=open(log_file, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        with open(pid_file, "w") as f:
            f.write(str(p.pid))
        return {"ok": True, "pid": p.pid, "mode": mode, "mode_label": _bot_mode_label(mode)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# twitter_bot.py hard-codes this; it stops following at this difference. Unfollowing is by
# age alone, unfollow_after_days from bot_config.json, in both modes.
BOT_FOLLOW_CAP = 455


def bot_following_since():
    """{handle: follow time} for everyone the bot still counts as followed.

    The same set its unfollow pass and threshold work from: in followed.csv, not in
    unfollowed.csv or the whitelist. Counting queue rows marked `followed` instead also counts the
    mutuals it keeps on purpose (they sit in unfollowed.csv so it stops re-checking
    them), and the oldest of those made "Next unfollow" read "Now" forever.
    """
    unfollowed = _csv_username_set("unfollowed.csv") | {u.lower() for u in read_whitelist()}
    since = {}
    path = _fpath("followed.csv")
    if not os.path.exists(path):
        return since
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                u = (row.get("username") or "").strip().lower()
                if not is_x_target(u) or u in unfollowed:
                    continue
                dt = _parse_ts(row.get("follow_timestamp") or "")
                if dt and (u not in since or dt > since[u]):
                    since[u] = dt
    except Exception:
        pass
    return since


def get_queue_stats(queue=None):
    if queue is None:
        queue = read_queue()
    counts = defaultdict(int)
    mutuals = set()
    for entry in queue:
        s = entry.get("status", "unknown")
        counts[s] += 1
        if s == "followed" and entry.get("follows_back"):
            mutuals.add((entry.get("username") or "").lower())
    following = bot_following_since()
    delta = len(following)
    next_unfollow_secs = None
    overdue = 0
    if following:
        after = timedelta(days=read_settings().get("unfollow_after_days", 4))
        eligible_at = min(following.values()) + after
        next_unfollow_secs = max(0, (eligible_at - datetime.now()).total_seconds())
        overdue = sum(1 for since in following.values() if since + after <= datetime.now())
    return {
        "pending_follow": counts.get("pending_follow", 0),
        "followed": counts.get("followed", 0),
        "unfollowed": counts.get("unfollowed", 0),
        "skipped": counts.get("skipped", 0),
        "total": len(queue),
        "delta": delta,
        "follow_cap": BOT_FOLLOW_CAP,
        "overdue_unfollows": overdue,
        "mutuals_kept": len(mutuals),
        "next_unfollow_secs": next_unfollow_secs,
    }


def _load_dedup_sets(queue=None):
    """Load all three dedup sources: queue.json usernames, followed.csv, unfollowed.csv."""
    if queue is None:
        queue = read_queue()
    queue_users = {e["username"].lower() for e in queue}
    queue_active = {e["username"].lower() for e in queue if e.get("status") in ("pending_follow",)}
    queue_completed = {e["username"].lower() for e in queue if e.get("status") in ("followed", "unfollowed", "skipped")}

    followed_users = set()
    fp = _fpath("followed.csv")
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    u = row.get("username", "").strip().lower()
                    if u:
                        followed_users.add(u)
        except Exception:
            pass

    unfollowed_users = set()
    up = _fpath("unfollowed.csv")
    if os.path.exists(up):
        try:
            with open(up, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    u = row.get("username", "").strip().lower()
                    if u:
                        unfollowed_users.add(u)
        except Exception:
            pass

    return queue_users, followed_users, unfollowed_users, queue_active, queue_completed


def follow_rank_map(queue, incoming=None):
    """{source_list: position} in the order the bot will actually work the lists.

    `incoming` is {source_list: row count} for lists being imported right now — they have
    no rows in the queue yet, and without their intended size they would look finished and
    sink to the bottom, which is the opposite of where a fresh list belongs.
    """
    sizes = pending_source_sizes([e for e in queue if e.get("status") == "pending_follow"])
    for name, n in (incoming or {}).items():
        sizes[name] = max(sizes.get(name, 0), int(n))
    priority = load_list_priority(DATA_DIR)
    names = set(sizes) | set(priority)
    return {name: i for i, name in enumerate(resolve_list_order(priority, sizes, names))}


def _claim_target(entry, source_name, ranks):
    """True when `source_name` should take this already-queued row off its current list.

    A person sitting in the queue used to make an import skip them outright, so the list
    that actually introduced them never got the row — most of the pre-queue legacy blob is
    people who are also in a named list, stranded behind it in the follow order. Claiming
    keeps the one-row-per-person rule and simply files them under whichever of the two
    lists the bot reaches first, which is the same rule dedupe has always followed.

    Only pending rows move: someone already followed or unfollowed is history, and
    rewriting their source list would silently restate what each list achieved.
    """
    if entry.get("status") != "pending_follow":
        return False
    current = entry.get("source_list") or ""
    if current == source_name:
        return False
    big = len(ranks) + 1
    return ranks.get(source_name, big) < ranks.get(current, big)


def _queue_keys_for_statuses(queue, statuses):
    keys = set()
    for e in queue:
        if e.get("status") in statuses:
            keys |= identity_key_set(e.get("username"), e.get("user_id"))
    return keys


def get_history_stats():
    path = _fpath("history.jsonl")
    stats = {"total_follows": 0, "total_unfollows": 0, "total_skips": 0,
             "total_likes": 0, "unique": set(), "first": None, "last": None,
             "daily": defaultdict(lambda: {"follows": 0, "unfollows": 0, "likes": 0, "skips": 0})}
    if not os.path.exists(path):
        f_path = _fpath("followed.csv")
        u_path = _fpath("unfollowed.csv")
        fc = uc = 0
        if os.path.exists(f_path):
            with open(f_path) as f:
                fc = max(0, sum(1 for _ in f) - 1)
        if os.path.exists(u_path):
            with open(u_path) as f:
                uc = max(0, sum(1 for _ in f) - 1)
        stats["total_follows"] = fc
        stats["total_unfollows"] = uc
        stats["unique"] = set()
        stats["source"] = "csv"
        return stats
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                a = e.get("action", "")
                ts = e.get("timestamp", "")
                day = ts[:10] if len(ts) >= 10 else ""
                if a == "follow":
                    stats["total_follows"] += 1
                    if day:
                        stats["daily"][day]["follows"] += 1
                elif a == "unfollow":
                    stats["total_unfollows"] += 1
                    if day:
                        stats["daily"][day]["unfollows"] += 1
                elif a == "skip":
                    stats["total_skips"] += 1
                    if day:
                        stats["daily"][day]["skips"] += 1
                elif a == "like":
                    stats["total_likes"] += 1
                    if day:
                        stats["daily"][day]["likes"] += 1
                if e.get("username"):
                    stats["unique"].add(e["username"].lower())
                if ts:
                    if stats["first"] is None or ts < stats["first"]:
                        stats["first"] = ts
                    if stats["last"] is None or ts > stats["last"]:
                        stats["last"] = ts
    except Exception:
        pass
    stats["source"] = "jsonl"
    return stats


def _parse_file_usernames(filename, content, username_col=None):
    """Extract usernames from a single file's content. Returns (list, error, columns_if_needed)."""
    rows, err, cols = _parse_file_import_rows(filename, content, username_col)
    if err:
        return [], err, cols
    usernames = [r["username"] for r in rows]
    return usernames, None, cols


def _parse_file_import_rows(filename, content, username_col=None):
    """Extract import rows: {username, user_id?, followers_count?}. Returns (list, error, columns_if_needed)."""
    content = content.lstrip("\ufeff")
    if filename and filename.lower().endswith(".txt"):
        out = []
        for line in content.splitlines():
            u = line.strip().lstrip("@")
            if u and not u.startswith("#"):
                out.append({"username": u.lower(), "user_id": "", "followers_count": None})
        return out, None, None

    try:
        if username_col:
            reader = csv.DictReader(io.StringIO(content))
            columns = reader.fieldnames or []
            if username_col not in (columns or []):
                return [], None, columns
            out = []
            for row in reader:
                u = (row.get(username_col) or "").strip().lstrip("@")
                if not u:
                    continue
                out.append({"username": u.lower(), "user_id": "", "followers_count": None})
            return out, None, None

        parsed = parse_csv_export_rows(content)
        out = []
        for row in parsed:
            u = (row.get("username") or "").strip().lower()
            uid = (row.get("user_id") or "").strip()
            fc = row.get("followers_count")
            if not u and uid:
                u = uid
            if not u:
                continue
            out.append({"username": u, "user_id": uid, "followers_count": fc})

        if out:
            return out, None, None

        reader = csv.DictReader(io.StringIO(content))
        columns = reader.fieldnames or []
        candidate_cols = ["screen_name", "username", "handle", "user", "twitter", "x_handle",
                          "user id", "user_id", "userid"]
        rows = list(reader)

        col = None
        for c in candidate_cols:
            for fc in columns or []:
                if fc.lower().strip() == c:
                    col = fc
                    break
            if col:
                break

        if col is None:
            return [], None, columns

        out = []
        for row in rows:
            u = row.get(col, "").strip().lstrip("@")
            if u:
                out.append({"username": u.lower(), "user_id": "", "followers_count": None})

        if not out and rows:
            for fallback in ["User ID", "user_id", "userid"]:
                fc_match = next((fc for fc in (columns or []) if fc.lower().strip() == fallback.lower()), None)
                if fc_match and fc_match != col:
                    for row in rows:
                        u = row.get(fc_match, "").strip()
                        if u:
                            out.append({"username": u.lower(), "user_id": "", "followers_count": None})
                    if out:
                        return out, None, None
            return [], None, columns

        return out, None, None
    except Exception as e:
        return [], str(e), None


def import_batch_to_queue(files_data, allow_requeue=False):
    """Import multiple files' worth of rows into the queue atomically.
    files_data: list of {source_name, rows} where each row is {username, user_id?, followers_count?}
    (legacy: {source_name, usernames} with str entries still supported)
    When allow_requeue=True, reset existing followed/unfollowed entries to pending_follow.
    """
    fd = _acquire_lock()
    try:
        queue = read_queue()

        def normalize_row(item):
            if isinstance(item, str):
                ul = item.lower().strip().lstrip("@")
                return ul, "", None
            ul = (item.get("username") or "").lower().strip().lstrip("@")
            uid = (item.get("user_id") or "").strip()
            fc = item.get("followers_count")
            return ul, uid, fc

        key_to_idx = {}
        for i, e in enumerate(queue):
            for k in identity_key_set(e.get("username"), e.get("user_id")):
                key_to_idx.setdefault(k, i)

        # Where each list sits in the follow order, with the lists in this batch counted
        # at the size they are about to be — that is what decides whether an incoming list
        # claims a row already sitting in a later list.
        ranks = follow_rank_map(queue, {fd_i["source_name"]: len(fd_i.get("rows") or
                                                                fd_i.get("usernames") or [])
                                        for fd_i in files_data})

        imports = safe_read_json(_fpath("imports.json"), default=[])
        results = []
        total_added = 0
        total_claimed = 0
        requeued = []

        for fd_item in files_data:
            source_name = fd_item["source_name"]
            rows_in = fd_item.get("rows")
            if rows_in is None:
                rows_in = fd_item.get("usernames", [])
            added = 0
            claimed = 0
            for item in rows_in:
                ul, uid, fc = normalize_row(item)
                if not ul:
                    continue
                rk = identity_key_set(ul, uid)
                idx = None
                for k in rk:
                    if k in key_to_idx:
                        idx = key_to_idx[k]
                        break
                if idx is not None:
                    entry = queue[idx]
                    if allow_requeue and entry.get("status") in ("followed", "unfollowed", "skipped"):
                        entry["status"] = "pending_follow"
                        entry["source_list"] = source_name
                        entry["added_at"] = datetime.now().isoformat()
                        entry["timestamp"] = ""
                        for field in RESET_CLEARED_FIELDS:
                            entry.pop(field, None)
                        requeued.append(entry.get("username") or ul)
                        if uid:
                            entry["user_id"] = uid
                        if fc is not None:
                            entry["followers_count"] = fc
                        added += 1
                    elif _claim_target(entry, source_name, ranks):
                        # Same person, same single row — it just moves to the list that
                        # reaches them first. Nothing is duplicated and nothing is dropped.
                        entry["claimed_from"] = entry.get("source_list")
                        entry["source_list"] = source_name
                        entry["claimed_at"] = datetime.now().isoformat()
                        if uid and not entry.get("user_id"):
                            entry["user_id"] = uid
                        if fc is not None and entry.get("followers_count") is None:
                            entry["followers_count"] = fc
                        claimed += 1
                    continue

                new_e = {
                    "username": ul,
                    "source_list": source_name,
                    "added_at": datetime.now().isoformat(),
                    "status": "pending_follow",
                    "timestamp": "",
                }
                if uid:
                    new_e["user_id"] = uid
                if fc is not None:
                    new_e["followers_count"] = fc
                queue.append(new_e)
                ni = len(queue) - 1
                for k in identity_key_set(ul, uid):
                    key_to_idx[k] = ni
                added += 1

            imports.append({
                "date": datetime.now().isoformat(),
                "source_name": source_name,
                "total_in_file": len(rows_in),
                "added": added,
                "claimed": claimed,
            })
            results.append({"source_name": source_name, "added": added, "claimed": claimed})
            total_added += added
            total_claimed += claimed

        qpath = _fpath("queue.json")
        tmp = qpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=1)
        os.replace(tmp, qpath)

        imp_path = _fpath("imports.json")
        with open(imp_path, "w") as f:
            json.dump(imports, f, indent=2)

        # A re-queue is only real if these go too: the bot filters every pending follow
        # against both files, so a re-queued handle still listed there is skipped forever
        # as "already followed". Same as the per-list reset, and safe with the bot up.
        removed_followed = _clear_csv_usernames("followed.csv", requeued)
        removed_unfollowed = _clear_csv_usernames("unfollowed.csv", requeued)

        return {"total_added": total_added, "total_claimed": total_claimed,
                "per_file": results, "requeued": len(requeued),
                "removed_followed": removed_followed, "removed_unfollowed": removed_unfollowed}
    finally:
        _release_lock(fd)


def modify_queue(action, usernames_list):
    fd = _acquire_lock()
    try:
        queue = read_queue()
        target = set(u.lower() for u in usernames_list)
        if action == "remove":
            queue = [e for e in queue if not (e["username"].lower() in target and e["status"] == "pending_follow")]
        elif action == "skip":
            for e in queue:
                if e["username"].lower() in target and e["status"] == "pending_follow":
                    e["status"] = "skipped"
                    e["timestamp"] = datetime.now().isoformat()
                    e["skip_reason"] = "manual_skip"
        elif action == "move_top":
            top = []
            rest = []
            for e in queue:
                if e["username"].lower() in target and e["status"] == "pending_follow":
                    top.append(e)
                else:
                    rest.append(e)
            first_pending = None
            for i, e in enumerate(rest):
                if e["status"] == "pending_follow":
                    first_pending = i
                    break
            if first_pending is not None:
                queue = rest[:first_pending] + top + rest[first_pending:]
            else:
                queue = rest + top

        qpath = _fpath("queue.json")
        tmp = qpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=1)
        os.replace(tmp, qpath)
        return True
    finally:
        _release_lock(fd)


RESET_LOG_FILE = "list_resets.json"

# Fields the bot writes while working a row; a reset strips them so the row looks
# exactly like a freshly imported one.
RESET_CLEARED_FIELDS = (
    "skip_reason", "retry_reason", "retry_last_reason", "retry_last_timestamp",
    "retry_not_before", "retry_attempts", "retry_batch", "retry_source_csv",
)


def _csv_username_set(filename):
    users = set()
    path = _fpath(filename)
    if not os.path.exists(path):
        return users
    try:
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                u = (row.get("username") or "").strip().lower()
                if u:
                    users.add(u)
    except Exception:
        pass
    return users


def _clear_csv_usernames(filename, usernames):
    """Drop these handles from followed.csv / unfollowed.csv.

    A reset is only real if these rows go too: the bot filters every pending follow
    against both files, so a re-queued handle still listed there would be skipped
    forever. One rolling .pre-reset.bak is kept in case a reset was a mistake.
    """
    path = _fpath(filename)
    if not os.path.exists(path) or not usernames:
        return 0
    targets = {u.lower() for u in usernames}
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
    except Exception:
        return 0
    if not rows:
        return 0
    header = rows[0]
    try:
        col = [h.strip().lower() for h in header].index("username")
    except ValueError:
        return 0  # unrecognised header — leave the file alone rather than mangle it
    kept, removed = [header], 0
    for row in rows[1:]:
        if len(row) > col and row[col].strip().lower() in targets:
            removed += 1
            continue
        kept.append(row)
    if not removed:
        return 0
    try:
        shutil.copyfile(path, path + ".pre-reset.bak")
    except Exception:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(kept)
    os.replace(tmp, path)
    return removed


def _reset_scan(queue):
    """Work out, per source list, what a "follow this list again" reset would touch.

    Returns (summary_by_list, rows_by_list). Two kinds of row are deliberately left
    alone, because re-queueing them would mean re-following someone we never stopped
    following:

      * mutuals — flagged `follows_back` and still followed. We keep them on purpose.
      * fans — flagged `follows_back` but already unfollowed: they followed us back after
        the unfollow pass had let them go. A free follower, and re-following them would
        spend a following slot on someone we already have.
      * held — a handle we are still recorded as following, here or in another list.
        Rows the bot marked `followed` but that already sit in unfollowed.csv land here
        too: that is either a pre-flag mutual or a cleanup pass that unfollowed without
        updating the queue, and nothing in the data says which.
    """
    unfollowed_csv = _csv_username_set("unfollowed.csv")
    # Handles we believe we are currently following — they block a reset anywhere.
    blocked = {(e.get("username") or "").lower()
               for e in queue
               if e.get("follows_back") or e.get("status") == "followed"}

    summary = defaultdict(lambda: {"mutuals": 0, "fans": 0, "held": 0,
                                   "awaiting_unfollow": 0, "resettable": 0,
                                   "pending_raw": 0})
    rows = defaultdict(list)
    for e in queue:
        name = e.get("source_list") or "unknown"
        uname = (e.get("username") or "").lower()
        status = e.get("status")
        bucket = summary[name]
        if e.get("follows_back"):
            # Two different things wear this flag, and collapsing them would lie about
            # the follow graph. A mutual is someone we are still following on purpose.
            # A fan followed us back after the unfollow pass had already let them go —
            # usually weeks later, which the followers export is the only thing that can
            # see. Both stay out of `resettable`: re-following either one is wrong.
            if e.get("follows_back_state") == "follower_only" or status in ("unfollowed", "skipped"):
                bucket["fans"] += 1
            else:
                bucket["mutuals"] += 1
        elif status == "followed":
            if uname in unfollowed_csv:
                bucket["held"] += 1
            else:
                bucket["awaiting_unfollow"] += 1
        elif status == "pending_follow":
            bucket["pending_raw"] += 1
        elif status in ("unfollowed", "skipped"):
            if uname in blocked:
                bucket["held"] += 1
            else:
                bucket["resettable"] += 1
                rows[name].append(e)
    return summary, rows


def _reset_block_reason(info):
    """Why this list can't be re-followed yet, or None when it can."""
    if info["pending_raw"]:
        return f"{info['pending_raw']} handle(s) still queued to follow"
    if info["awaiting_unfollow"]:
        return f"{info['awaiting_unfollow']} handle(s) followed but not unfollowed yet"
    if not info["resettable"]:
        return "nothing to reset"
    return None


def read_reset_log():
    data = safe_read_json(_fpath(RESET_LOG_FILE), default=[])
    return data if isinstance(data, list) else []


def append_reset_log(record):
    log = read_reset_log()
    log.append(record)
    log = log[-100:]
    path = _fpath(RESET_LOG_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)
    os.replace(tmp, path)


# ============================================================
# API ROUTES
# ============================================================

@app.route("/api/status")
def api_status():
    running, pid, mode = bot_is_running()
    dc = read_daily_counts()
    qs = get_queue_stats()
    return jsonify({
        "bot_running": running,
        "bot_pid": pid,
        "bot_mode": mode,
        "bot_mode_label": _bot_mode_label(mode),
        "daily_counts": dc,
        "queue_stats": qs,
        "followers_export": _followers_export_banner(),
        "server_time": datetime.now().isoformat(),
    })


@app.route("/api/queue")
def api_queue():
    status_filter = request.args.get("status")
    search = request.args.get("search", "").lower()
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    queue = read_queue()
    if status_filter:
        queue = [e for e in queue if e.get("status") == status_filter]
    if search:
        queue = [e for e in queue if search in e.get("username", "").lower()]

    total = len(queue)
    start = (page - 1) * per_page
    items = queue[start:start + per_page]
    return jsonify({"items": items, "total": total, "page": page, "per_page": per_page})


@app.route("/api/queue/import", methods=["POST"])
def api_queue_import():
    """Handle multi-file upload. Preview or commit."""
    files = request.files.getlist("file")
    if not files or (len(files) == 1 and files[0].filename == ""):
        return jsonify({"error": "No files uploaded"}), 400

    commit = request.form.get("commit", "false") == "true"
    source_names_raw = request.form.get("source_names", "")
    username_col = request.form.get("username_column")
    allow_requeue = request.form.get("allow_requeue", "false") == "true"

    source_map = {}
    if source_names_raw:
        try:
            source_map = json.loads(source_names_raw)
        except Exception:
            pass

    queue = read_queue()
    queue_users, followed_users, unfollowed_users, queue_active, queue_completed = _load_dedup_sets(queue)
    pending_keys = _queue_keys_for_statuses(queue, ("pending_follow",))
    completed_keys = _queue_keys_for_statuses(queue, ("followed", "unfollowed", "skipped"))
    pending_by_key = {}
    for e in queue:
        if e.get("status") == "pending_follow":
            for k in identity_key_set(e.get("username"), e.get("user_id")):
                pending_by_key.setdefault(k, e)

    all_files_data = []
    cross_batch_keys = set()
    needs_column = False
    needs_column_info = None

    for f in files:
        fname = f.filename or "upload"
        content = f.read().decode("utf-8", errors="replace")
        source_name = source_map.get(fname, fname.rsplit(".", 1)[0] if "." in fname else fname)

        if looks_like_cookie_jar(content):
            return jsonify({"error": f"{fname} is a browser cookie export, not a list of handles. "
                                     "It holds live session tokens, so nothing was imported."}), 400

        rows, err, columns = _parse_file_import_rows(fname, content, username_col)

        if columns is not None and not rows:
            needs_column = True
            needs_column_info = {"filename": fname, "columns": columns, "needs_column_selection": True}
            break

        if err:
            return jsonify({"error": f"Error parsing {fname}: {err}"}), 400

        # Only handles and numeric ids go in. Anything else would be opened as a profile
        # URL, then "unfollowed", and written to the bot's CSVs as a username.
        invalid = [r["username"] for r in rows if not is_x_target(r["username"])]
        rows = [r for r in rows if is_x_target(r["username"])]

        # Judge this file on its own size; the commit path re-ranks the whole batch.
        ranks = follow_rank_map(queue, {source_name: len(rows)})

        per_user = []
        file_new = 0
        file_claimable = 0
        file_in_queue = 0
        file_followed = 0
        file_unfollowed = 0
        file_cross_dup = 0
        file_requeued = 0

        for row in rows:
            ul = row["username"]
            uid = row.get("user_id") or ""
            rk = identity_key_set(ul, uid)

            if rk & cross_batch_keys:
                per_user.append({"username": ul, "status": "duplicate_across_uploads", "row": row})
                file_cross_dup += 1
                continue

            if rk & pending_keys:
                existing = next((pending_by_key[k] for k in rk if k in pending_by_key), None)
                if existing is not None and _claim_target(existing, source_name, ranks):
                    per_user.append({"username": ul, "status": "claimable", "row": row,
                                     "from": existing.get("source_list")})
                    file_claimable += 1
                else:
                    per_user.append({"username": ul, "status": "already_in_queue", "row": row})
                    file_in_queue += 1
            elif rk & completed_keys:
                if allow_requeue:
                    per_user.append({"username": ul, "status": "requeue", "row": row})
                    file_requeued += 1
                    cross_batch_keys |= rk
                else:
                    per_user.append({"username": ul, "status": "already_in_queue", "row": row})
                    file_in_queue += 1
            elif ul in followed_users:
                if allow_requeue:
                    per_user.append({"username": ul, "status": "requeue", "row": row})
                    file_requeued += 1
                    cross_batch_keys |= rk
                else:
                    per_user.append({"username": ul, "status": "already_followed", "row": row})
                    file_followed += 1
            elif ul in unfollowed_users:
                if allow_requeue:
                    per_user.append({"username": ul, "status": "requeue", "row": row})
                    file_requeued += 1
                    cross_batch_keys |= rk
                else:
                    per_user.append({"username": ul, "status": "already_unfollowed", "row": row})
                    file_unfollowed += 1
            else:
                per_user.append({"username": ul, "status": "new", "row": row})
                file_new += 1
                cross_batch_keys |= rk

        # claimable rows have to travel to the commit path too — that is where the row
        # already in the queue is re-filed under this list.
        import_rows = [pu["row"] for pu in per_user
                       if pu["status"] in ("new", "requeue", "claimable")]
        preview_usernames = [r["username"] for r in rows]
        all_files_data.append({
            "filename": fname,
            "source_name": source_name,
            "import_rows": import_rows,
            "total_in_file": len(rows),
            "new": file_new,
            "claimable": file_claimable,
            "claimed_from": dict(Counter(pu["from"] for pu in per_user
                                         if pu["status"] == "claimable").most_common(5)),
            "requeued": file_requeued,
            "already_in_queue": file_in_queue,
            "already_followed": file_followed,
            "already_unfollowed": file_unfollowed,
            "duplicate_across": file_cross_dup,
            "invalid": len(invalid),
            "invalid_sample": [u[:24] for u in invalid[:3]],
            "preview": [u[:20] for u in preview_usernames[:8]],
        })

    if needs_column:
        return jsonify(needs_column_info), 200

    if not commit:
        combined_new = sum(fd["new"] for fd in all_files_data)
        combined_claimable = sum(fd["claimable"] for fd in all_files_data)
        combined_requeued = sum(fd["requeued"] for fd in all_files_data)
        combined_importable = combined_new + combined_requeued + combined_claimable
        return jsonify({
            "files": [{k: v for k, v in fd.items() if k != "import_rows"} for fd in all_files_data],
            "combined_new": combined_new,
            "combined_claimable": combined_claimable,
            "combined_requeued": combined_requeued,
            "combined_importable": combined_importable,
            "combined_total": sum(fd["total_in_file"] for fd in all_files_data),
            "file_count": len(all_files_data),
            "committed": False,
        })

    batch = [{"source_name": fd["source_name"], "rows": fd["import_rows"]} for fd in all_files_data]
    result = import_batch_to_queue(batch, allow_requeue=allow_requeue)
    result["committed"] = True
    return jsonify(result)


@app.route("/api/queue/bulk", methods=["POST"])
def api_queue_bulk():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400
    action = data.get("action")
    usernames = data.get("usernames", [])
    if action not in ("skip", "remove", "move_top"):
        return jsonify({"error": "Invalid action"}), 400
    modify_queue(action, usernames)
    return jsonify({"ok": True, "action": action, "count": len(usernames)})


@app.route("/api/queue/sources")
def api_queue_sources():
    """Per-source-list progress: how complete each imported CSV is."""
    queue = read_queue()
    saved_order = read_list_priority()
    anchors = read_list_anchors(saved_order)
    ranks = ListOrder({name: float(i) for i, name in enumerate(saved_order)}, anchors)
    reset_summary, _reset_rows = _reset_scan(queue)
    last_resets = {}
    for rec in read_reset_log():
        if rec.get("name"):
            last_resets[rec["name"]] = rec.get("timestamp", "")

    sources = defaultdict(lambda: {"total": 0, "pending_follow": 0,
                                    "followed": 0, "unfollowed": 0, "skipped": 0})
    for entry in queue:
        src = entry.get("source_list") or "unknown"  # same bucket the bot uses
        status = entry.get("status", "unknown")
        sources[src]["total"] += 1
        if status in sources[src]:
            sources[src][status] += 1

    # A handle in several lists is only ever followed once — from the soonest-to-follow list.
    # Count it as pending there and as a shadow ("dupes") elsewhere, so the numbers match what
    # the bot will actually do and every list can still reach 100%.
    pending_rows = [e for e in queue if e.get("status") == "pending_follow"]
    ordered, _sizes = dedupe_best_pending(pending_rows, ranks)
    real_pending = Counter((e.get("source_list") or "unknown") for e in ordered)
    active_list = (ordered[0].get("source_list") or "unknown") if ordered else None

    result = []
    for name, counts in sources.items():
        pending = real_pending.get(name, 0)
        dupes = counts["pending_follow"] - pending
        done = counts["followed"] + counts["unfollowed"] + counts["skipped"]
        # dupes are settled elsewhere, so they count toward this list being finished
        pct = round((done + dupes) / counts["total"] * 100, 1) if counts["total"] > 0 else 0
        rinfo = reset_summary[name]
        result.append({
            "name": name,
            "total": counts["total"],
            "pending": pending,
            "dupes": dupes,
            "followed": counts["followed"],
            "unfollowed": counts["unfollowed"],
            "skipped": counts["skipped"],
            "done": done,
            "pct": pct,
            "active": name == active_list,
            # Re-follow ("reset") state — see _reset_scan for what each bucket means
            "mutuals": rinfo["mutuals"],
            "fans": rinfo["fans"],
            "followbacks": rinfo["mutuals"] + rinfo["fans"],
            "held": rinfo["held"],
            "awaiting_unfollow": rinfo["awaiting_unfollow"],
            "resettable": rinfo["resettable"],
            "can_reset": _reset_block_reason(rinfo) is None,
            "reset_blocked_reason": _reset_block_reason(rinfo),
            "last_reset": last_resets.get(name, ""),
        })
    # Show them in the order the bot will actually work them (queue_dedupe.resolve_list_order):
    # the lists the user arranged, with anything imported since slotted in behind "Following
    # now", "Up next" and every list dragged upward — and finished lists at the bottom.
    display = resolve_list_order(ranks, real_pending, [r["name"] for r in result])
    positions = {name: i for i, name in enumerate(display)}
    for r in result:
        r["priority"] = ranks.get(r["name"])
        r["pinned"] = anchors.get(r["name"])
        r["fresh"] = r["name"] not in ranks
    result.sort(key=lambda x: positions.get(x["name"], len(positions)))
    return jsonify({"sources": result, "custom_order": bool(ranks),
                    "active_list": active_list})


FOLLOWERS_STATUS_FILE = "followers_export_status.json"
FOLLOWERS_STATE_FILE = "followers_state.json"
FOLLOWBACK_LEDGER_FILE = "followback_ledger.jsonl"
FOLLOWERS_STALE_HOURS = 36
# Below this many follows a list's conversion rate is noise: 1 followback out of 3 is
# not a 33% list. Shown as "—" instead of a number nobody should act on.
CONVERSION_MIN_FOLLOWED = 25


def _followers_status():
    """The exporter's status file, plus the staleness the file cannot know about itself.

    Staleness is measured from last_success_at, not from the last exit code: "the timer
    was never enabled" and "the unit has been failing for a week" both look like silence
    from in here, and only the age of the last success catches both.
    """
    status = safe_read_json(_fpath(FOLLOWERS_STATUS_FILE), {}) or {}
    last = _parse_ts(status.get("finished_at") or status.get("started_at"))
    state = safe_read_json(_fpath(FOLLOWERS_STATE_FILE), {}) or {}
    success = _parse_ts((state.get("last_success_at") or ""))
    hours = None
    if success:
        hours = round((datetime.now() - success).total_seconds() / 3600.0, 1)
    status["hours_since_success"] = hours
    status["stale"] = (hours is None) or (hours > FOLLOWERS_STALE_HOURS)
    status["never_run"] = success is None
    status["last_seen_at"] = last.isoformat() if last else None
    watching_since = state.get("watching_since")
    cutoff = datetime.now() - timedelta(hours=24)
    status["new_followers_24h"] = sum(
        1 for info in (state.get("followers") or {}).values()
        if info.get("first_seen") and info.get("first_seen") != watching_since
        and (_parse_ts(info.get("first_seen")) or datetime.min) >= cutoff)
    return status


def _read_ledger():
    path = _fpath(FOLLOWBACK_LEDGER_FILE)
    if not os.path.exists(path):
        return []
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return entries


def _followers_export_banner():
    """The small slice of exporter health the home page needs.

    A hard-fail exporter stops silently: nothing restarts it, and the only symptom is
    that followbacks quietly stop being discovered. This banner is the whole
    early-warning system, so it reports "never run" and "not run lately" as loudly as
    an outright error.
    """
    status = _followers_status()
    return {
        "stale": status["stale"],
        "never_run": status["never_run"],
        "outcome": status.get("outcome"),
        "hours_since_success": status["hours_since_success"],
        "message": status.get("message", ""),
    }


@app.route("/api/followers/status")
def api_followers_status():
    return jsonify(_followers_status())


@app.route("/api/followers/conversion")
def api_followers_conversion():
    """Per-list followback conversion — which imported lists are actually worth working.

    `followed` counts the rows where a follow relationship existed, which is not the same
    as the rows the list contains:

      * `followed` and `unfollowed` rows count, and so do `skipped` rows whose reason was
        already_following — we had the relationship either way.
      * shadow rows (skip_reason duplicate_actioned_from:...) do not: the list that
        actioned the handle gets the credit, or every overlapping list would claim it.
      * not-found/suspended skips do not either: we never followed them, and counting
        them as failed follows would understate every list that imported dead handles.
    """
    queue = read_queue()
    ledger = _read_ledger()
    days_cutoff = read_settings().get("unfollow_after_days", 4)

    per_list = defaultdict(lambda: {"followed": 0, "mutuals": 0, "fans": 0,
                                    "followbacks": 0, "still_pending": 0,
                                    "late_followbacks": 0, "unmeasured": 0,
                                    "days": []})
    for entry in queue:
        name = entry.get("source_list") or "unknown"
        bucket = per_list[name]
        status = entry.get("status")
        reason = str(entry.get("skip_reason") or "")
        if status in ("followed", "unfollowed"):
            bucket["followed"] += 1
        elif status == "skipped" and reason == "already_following":
            bucket["followed"] += 1
        elif status == "pending_follow":
            bucket["still_pending"] += 1

        if entry.get("follows_back") and entry.get("follows_back_attributed"):
            bucket["followbacks"] += 1
            if entry.get("follows_back_state") == "follower_only" or \
                    status in ("unfollowed", "skipped"):
                bucket["fans"] += 1
            else:
                bucket["mutuals"] += 1

    for record in ledger:
        bucket = per_list[record.get("source_list") or "unknown"]
        # Only entries whose follow went out while this job was already running carry a
        # real elapsed time. For the rest we know they follow us but not when they
        # started, so they are counted as followbacks and excluded from the timing —
        # otherwise the initial backfill would invent a median measured in years.
        if record.get("timing") == "measured":
            if record.get("late"):
                bucket["late_followbacks"] += 1
            days = record.get("days_to_followback")
            if isinstance(days, (int, float)):
                bucket["days"].append(days)
        else:
            bucket["unmeasured"] += 1

    def median(values):
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return round(ordered[mid], 1)
        return round((ordered[mid - 1] + ordered[mid]) / 2, 1)

    lists = []
    for name, bucket in per_list.items():
        followed = bucket["followed"]
        enough = followed >= CONVERSION_MIN_FOLLOWED
        lists.append({
            "name": name,
            "followed": followed,
            "mutuals": bucket["mutuals"],
            "fans": bucket["fans"],
            "followbacks": bucket["followbacks"],
            "still_pending": bucket["still_pending"],
            "late_followbacks": bucket["late_followbacks"],
            "untimed_followbacks": bucket["unmeasured"],
            "median_days_to_followback": median(bucket["days"]),
            "conversion_pct": (round(bucket["followbacks"] / followed * 100, 1)
                               if enough and followed else None),
            "sample_too_small": not enough,
        })
    lists.sort(key=lambda r: (r["conversion_pct"] is None, -(r["conversion_pct"] or 0),
                              -r["followed"]))

    state = safe_read_json(_fpath(FOLLOWERS_STATE_FILE), {}) or {}
    followers_total = len(state.get("followers") or {})
    watching_since = state.get("watching_since")
    attributed = sum(r["followbacks"] for r in lists)
    total_followed = sum(r["followed"] for r in lists)
    return jsonify({
        "lists": lists,
        "totals": {
            "followed": total_followed,
            "followbacks": attributed,
            "conversion_pct": (round(attributed / total_followed * 100, 1)
                               if total_followed else None),
            "late_followbacks": sum(r["late_followbacks"] for r in lists),
            "untimed_followbacks": sum(r["untimed_followbacks"] for r in lists),
            "followers_total": followers_total,
            "attributed_followers": attributed,
            # Everyone following us that no imported list can take credit for.
            "organic_followers": max(0, followers_total - attributed),
        },
        "unfollow_after_days": days_cutoff,
        "watching_since": watching_since,
        "generated_at": datetime.now().isoformat(),
    })


def _followback_index(queue):
    """(by_id, by_handle) over the queue rows flagged as followbacks.

    The attributed row wins where a handle sits in several lists, so the relationship
    shown matches the list the conversion table credits.
    """
    by_handle, by_id = {}, {}
    for entry in queue:
        if not entry.get("follows_back"):
            continue
        handle = (entry.get("username") or "").strip().lower()
        if entry.get("follows_back_attributed") or handle not in by_handle:
            if handle:
                by_handle[handle] = entry
            if entry.get("user_id"):
                by_id[str(entry["user_id"])] = entry
    return by_id, by_handle


def _relationship(fid, handle, by_id, by_handle):
    """(queue row or None, 'mutual' | 'follower_only' | 'organic')."""
    entry = by_id.get(str(fid)) or by_handle.get(handle)
    if entry is None:
        return None, "organic"
    return entry, entry.get("follows_back_state") or (
        "mutual" if entry.get("status") == "followed" else "follower_only")


@app.route("/api/followers/recent")
def api_followers_recent():
    """Who followed (and unfollowed) most recently.

    `first_seen` is when an export first saw them, not when they actually hit follow, so
    this is only as sharp as the run interval. The one exception is the initial backfill:
    every follower who already existed when the exporter first ran shares that run's
    timestamp, which is exactly `watching_since`. Those are not new followers and are
    excluded — showing 1900 people as having "just followed" would make the panel
    useless on day one.
    """
    try:
        limit = min(500, max(1, int(request.args.get("limit", 40))))
    except ValueError:
        limit = 40

    state = safe_read_json(_fpath(FOLLOWERS_STATE_FILE), {}) or {}
    followers = state.get("followers") or {}
    removed = state.get("removed") or {}
    watching_since = state.get("watching_since")
    by_id, by_handle = _followback_index(read_queue())
    now = datetime.now()

    def build(fid, info, stamp_key):
        handle = (info.get("u") or "").lower()
        entry, row_state = _relationship(fid, handle, by_id, by_handle)
        stamp = info.get(stamp_key)
        parsed = _parse_ts(stamp)
        return {
            "user_id": fid,
            "username": info.get("u") or "",
            "state": row_state,
            "at": stamp,
            "hours_ago": (round((now - parsed).total_seconds() / 3600.0, 1)
                          if parsed else None),
            "source_list": (entry or {}).get("source_list"),
            "queue_status": (entry or {}).get("status"),
        }

    arrivals = [build(fid, info, "first_seen") for fid, info in followers.items()
                if info.get("first_seen") and info.get("first_seen") != watching_since]
    arrivals.sort(key=lambda r: r["at"] or "", reverse=True)

    departures = [build(fid, info, "removed_at") for fid, info in removed.items()
                  if info.get("removed_at")]
    departures.sort(key=lambda r: r["at"] or "", reverse=True)

    def within(rows, hours):
        return sum(1 for r in rows
                   if r["hours_ago"] is not None and r["hours_ago"] <= hours)

    return jsonify({
        "arrivals": arrivals[:limit],
        "departures": departures[:limit],
        "counts": {
            "new_24h": within(arrivals, 24),
            "new_7d": within(arrivals, 24 * 7),
            "lost_24h": within(departures, 24),
            "lost_7d": within(departures, 24 * 7),
            "tracked_since_start": len(arrivals),
        },
        "watching_since": watching_since,
        "backfilled": sum(1 for i in followers.values()
                          if i.get("first_seen") == watching_since),
        "generated_at": now.isoformat(),
    })


@app.route("/api/followers/mutuals")
def api_followers_mutuals():
    """The followers list joined to the queue, filtered by relationship."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(500, max(1, int(request.args.get("per_page", 50))))
    except ValueError:
        page, per_page = 1, 50
    search = (request.args.get("search") or "").strip().lower().lstrip("@")
    wanted = (request.args.get("state") or "").strip()

    state = safe_read_json(_fpath(FOLLOWERS_STATE_FILE), {}) or {}
    followers = state.get("followers") or {}
    by_id, by_handle = _followback_index(read_queue())

    rows = []
    for fid, info in followers.items():
        handle = (info.get("u") or "").lower()
        entry, row_state = _relationship(fid, handle, by_id, by_handle)
        if wanted and row_state != wanted:
            continue
        if search and search not in handle:
            continue
        rows.append({
            "user_id": fid,
            "username": info.get("u") or "",
            "state": row_state,
            "first_seen": info.get("first_seen"),
            "source_list": (entry or {}).get("source_list"),
            "queue_status": (entry or {}).get("status"),
            "follows_back_at": (entry or {}).get("follows_back_at"),
            "lost_at": (entry or {}).get("follows_back_lost_at"),
        })
    rows.sort(key=lambda r: r.get("first_seen") or "", reverse=True)

    total = len(rows)
    start = (page - 1) * per_page
    return jsonify({
        "rows": rows[start:start + per_page],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "counts": {
            "all": len(followers),
            "mutual": sum(1 for r in rows if r["state"] == "mutual"),
            "follower_only": sum(1 for r in rows if r["state"] == "follower_only"),
        },
    })


@app.route("/api/queue/sources/order", methods=["POST"])
def api_queue_sources_order():
    """Set (or clear, with reset=true) the manual order the bot works lists in.

    `moved` names the one row the user actually dragged. That row gets anchored, in the
    direction it travelled, and the anchor is what a later import has to stay behind —
    the rest of the order is just where everything happened to be sitting, and a new
    list is free to slot in among it. `unpin` drops an anchor without moving anything.
    """
    data = request.get_json(silent=True) or {}
    if data.get("reset"):
        write_list_priority([])
        return jsonify({"ok": True, "order": [], "custom_order": False})

    previous = read_list_priority()
    anchors = read_list_anchors(previous)

    unpin = data.get("unpin")
    if isinstance(unpin, str) and unpin:
        anchors.pop(unpin, None)
        write_list_priority(previous, anchors)
        return jsonify({"ok": True, "order": previous, "anchors": anchors,
                        "custom_order": bool(previous)})

    order = data.get("order")
    if not isinstance(order, list) or not all(isinstance(n, str) for n in order):
        return jsonify({"error": "order must be a list of source list names"}), 400
    seen, clean = set(), []
    for name in order:
        if name and name not in seen:
            seen.add(name)
            clean.append(name)

    moved = data.get("moved")
    if isinstance(moved, str) and moved in seen:
        direction = data.get("direction")
        if direction not in ("up", "down"):
            # No direction from the browser: fall back to the saved order. It is the
            # rougher signal — what was on screen is the *resolved* order, not this one —
            # but it only has to answer "sooner or later than before".
            was = previous.index(moved) if moved in previous else None
            direction = "down" if was is not None and clean.index(moved) > was else "up"
        # A list the user had never placed counts as dragged up: they went looking for it
        # and pulled it somewhere on purpose, wherever it had been sitting automatically.
        anchors[moved] = direction
    anchors = {k: v for k, v in anchors.items() if k in seen}

    write_list_priority(clean, anchors)
    return jsonify({"ok": True, "order": clean, "anchors": anchors, "custom_order": True})


@app.route("/api/queue/sources/reset", methods=["POST"])
def api_queue_sources_reset():
    """Re-queue a finished list so the bot follows everyone in it again.

    Every handle this list already followed and unfollowed goes back to
    `pending_follow` and is dropped from followed.csv / unfollowed.csv, which is what
    the bot filters new follows against. Anyone who follows us back — or who we are
    otherwise still recorded as following — is left exactly as-is.

    Can be run with the bot up; the re-queued handles come back into play next cycle.
    """
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    force = bool(data.get("force"))
    preview = bool(data.get("preview"))
    if not isinstance(name, str) or not name:
        return jsonify({"error": "name is required"}), 400

    # Safe to run while the bot is working: queue.json writes serialise through
    # queue_store's data.lock, and the bot appends to followed.csv rather than rewriting
    # it from memory, so the rows this clears stay cleared. The bot still holds its own
    # copy of both files for the rest of the current cycle, so it picks the re-queued
    # handles up on the next pass, not instantly.
    running, pid, _mode = bot_is_running()

    fd = _acquire_lock()
    try:
        queue = read_queue()
        if not any((e.get("source_list") or "unknown") == name for e in queue):
            return jsonify({"error": f"No list named {name}"}), 404

        summary, rows_by_list = _reset_scan(queue)
        info = summary[name]
        targets = rows_by_list.get(name, [])
        blocked_reason = _reset_block_reason(info)
        counts = {
            "requeued": len(targets),
            "kept_mutual": info["mutuals"],
            "kept_held": info["held"],
            "awaiting_unfollow": info["awaiting_unfollow"],
            "still_pending": info["pending_raw"],
        }

        if preview:
            return jsonify({"ok": True, "name": name, "preview": True,
                            "can_reset": blocked_reason is None,
                            "reset_blocked_reason": blocked_reason, **counts})

        if blocked_reason and not (force and targets):
            return jsonify({"error": f"Can't reset {name}: {blocked_reason}.",
                            "reset_blocked_reason": blocked_reason, **counts}), 409

        now = datetime.now().isoformat()
        usernames = []
        for e in targets:
            e["status"] = "pending_follow"
            e["timestamp"] = ""
            for field in RESET_CLEARED_FIELDS:
                e.pop(field, None)
            e["reset_at"] = now
            e["reset_count"] = int(e.get("reset_count") or 0) + 1
            if e.get("username"):
                usernames.append(e["username"])

        qpath = _fpath("queue.json")
        tmp = qpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=1)
        os.replace(tmp, qpath)

        removed_followed = _clear_csv_usernames("followed.csv", usernames)
        removed_unfollowed = _clear_csv_usernames("unfollowed.csv", usernames)

        record = {"name": name, "timestamp": now, "forced": force,
                  "bot_was_running": running, "bot_pid": pid,
                  "removed_followed": removed_followed,
                  "removed_unfollowed": removed_unfollowed, **counts}
        try:
            append_reset_log(record)
        except Exception:
            pass

        return jsonify({"ok": True, **record})
    finally:
        _release_lock(fd)


@app.route("/api/history")
def api_history():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    action_filter = request.args.get("action")
    search = request.args.get("search")
    offset = (page - 1) * per_page
    entries, total = read_history(limit=per_page, offset=offset,
                                  action_filter=action_filter, search=search)
    return jsonify({"items": entries, "total": total, "page": page, "per_page": per_page})


@app.route("/api/activity")
def api_activity():
    """Get recent activity by merging queue.json timestamps with history.jsonl.
    This provides real-time activity data even when history.jsonl is stale,
    since the bot always updates queue.json with timestamps."""
    per_page = int(request.args.get("per_page", 20))
    action_filter = request.args.get("action")
    search = request.args.get("search")

    # Gather activity from queue.json (real-time data)
    queue = read_queue()
    status_to_action = {"followed": "follow", "unfollowed": "unfollow", "skipped": "skip"}
    queue_entries = []
    for entry in queue:
        status = entry.get("status", "")
        ts = entry.get("timestamp", "")
        username = entry.get("username", "")
        if status in status_to_action and ts and username:
            action = status_to_action[status]
            if action_filter and action != action_filter:
                continue
            if search and search.lower() not in username.lower():
                continue
            queue_entries.append({
                "action": action,
                "username": username,
                "timestamp": ts,
                "source_list": entry.get("source_list", ""),
            })

    # Also gather from history.jsonl
    history_entries, _ = read_history(action_filter=action_filter, search=search)

    # Merge and deduplicate: prefer queue.json data (more current), use a set to avoid dupes
    seen = set()
    merged = []
    for e in queue_entries:
        key = (e["username"].lower(), e["timestamp"][:16])  # dedup within same minute
        if key not in seen:
            seen.add(key)
            merged.append(e)
    for e in history_entries:
        key = (e.get("username", "").lower(), e.get("timestamp", "")[:16])
        if key not in seen:
            seen.add(key)
            merged.append(e)

    # Sort by timestamp descending
    merged.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    items = merged[:per_page]
    # A queue row names one list; the handle may sit in more, so fill both in from the
    # same lookup rather than reading the queue again per row.
    mapping = _lists_by_username()
    _attach_lists(items, mapping)
    for e in items:
        names = mapping.get((e.get("username") or "").lower()) or []
        if len(names) > 1:
            e["source_lists"] = names
    return jsonify({"items": items, "total": len(merged)})


@app.route("/api/activity/backfill", methods=["POST"])
def api_activity_backfill():
    """One-time backfill: populate history.jsonl from queue.json entries that
    have timestamps and non-pending statuses. Skips entries already in history."""
    queue = read_queue()
    status_to_action = {"followed": "follow", "unfollowed": "unfollow", "skipped": "skip"}

    # Load existing history timestamps to avoid duplicates
    existing = set()
    path = _fpath("history.jsonl")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        existing.add((e.get("username", "").lower(), e.get("timestamp", "")[:16]))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

    added = 0
    try:
        with open(path, "a", encoding="utf-8") as hf:
            for entry in queue:
                status = entry.get("status", "")
                ts = entry.get("timestamp", "")
                username = entry.get("username", "")
                if status in status_to_action and ts and username:
                    key = (username.lower(), ts[:16])
                    if key not in existing:
                        record = {
                            "action": status_to_action[status],
                            "username": username,
                            "timestamp": ts,
                        }
                        if entry.get("skip_reason"):
                            record["skip_reason"] = entry["skip_reason"]
                        hf.write(json.dumps(record) + "\n")
                        existing.add(key)
                        added += 1
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "added": added})


@app.route("/api/history/stats")
def api_history_stats():
    stats = get_history_stats()
    unique_count = len(stats.pop("unique", set()))
    daily = dict(stats.pop("daily", {}))
    return jsonify({**stats, "unique_count": unique_count, "daily": daily})


@app.route("/api/history/chart-data")
def api_history_chart_data():
    days = int(request.args.get("days", 30))
    stats = get_history_stats()
    daily = stats.get("daily", {})

    end = datetime.now().date()
    start = end - timedelta(days=days - 1)
    labels = []
    follows = []
    unfollows = []
    likes = []
    cum_f = 0
    cum_u = 0
    delta_series = []

    all_days_sorted = sorted(daily.keys())
    pre_f = pre_u = 0
    for d in all_days_sorted:
        ds = daily[d]
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        if dt < start:
            pre_f += ds.get("follows", 0)
            pre_u += ds.get("unfollows", 0)

    cum_f = pre_f
    cum_u = pre_u
    current = start
    while current <= end:
        ds = current.strftime("%Y-%m-%d")
        labels.append(ds)
        d = daily.get(ds, {})
        f = d.get("follows", 0)
        u = d.get("unfollows", 0)
        l = d.get("likes", 0)
        follows.append(f)
        unfollows.append(u)
        likes.append(l)
        cum_f += f
        cum_u += u
        delta_series.append(cum_f - cum_u)
        current += timedelta(days=1)

    return jsonify({
        "labels": labels,
        "follows": follows,
        "unfollows": unfollows,
        "likes": likes,
        "cumulative_follows": [pre_f + sum(follows[:i+1]) for i in range(len(follows))],
        "cumulative_unfollows": [pre_u + sum(unfollows[:i+1]) for i in range(len(unfollows))],
        "delta": delta_series,
    })


@app.route("/api/history/export")
def api_history_export():
    entries, _ = read_history()
    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(["timestamp", "action", "username", "lists", "details"])
    for e in entries:
        details = {k: v for k, v in e.items()
                   if k not in ("timestamp", "action", "username", "source_list", "source_lists")}
        lists = e.get("source_lists") or ([e["source_list"]] if e.get("source_list") else [])
        writer.writerow([e.get("timestamp", ""), e.get("action", ""),
                         e.get("username", ""), "; ".join(lists), json.dumps(details)])
    output = io.BytesIO(si.getvalue().encode("utf-8"))
    return send_file(output, mimetype="text/csv", as_attachment=True,
                     download_name=f"history_{datetime.now().strftime('%Y%m%d')}.csv")


@app.route("/api/logs/stream")
def api_logs_stream():
    log_path = _fpath("twitter_bot.log")

    def generate():
        if not os.path.exists(log_path):
            yield f"data: {json.dumps({'line': '--- Log file does not exist yet ---'})}\n\n"
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                tail = lines[-200:] if len(lines) > 200 else lines
                for line in tail:
                    yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
                while True:
                    line = f.readline()
                    if line:
                        yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
                    else:
                        time.sleep(0.5)
        except GeneratorExit:
            return
        except Exception as e:
            yield f"data: {json.dumps({'line': f'--- Error reading log: {e} ---'})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/logs/download")
def api_logs_download():
    log_path = _fpath("twitter_bot.log")
    if not os.path.exists(log_path):
        return jsonify({"error": "Log file not found"}), 404
    return send_file(log_path, mimetype="text/plain", as_attachment=True,
                     download_name="twitter_bot.log")


@app.route("/api/logs/info")
def api_logs_info():
    log_path = _fpath("twitter_bot.log")
    if not os.path.exists(log_path):
        return jsonify({"exists": False, "size": 0, "lines": 0})
    size = os.path.getsize(log_path)
    return jsonify({"exists": True, "size": size,
                    "size_human": f"{size / 1024 / 1024:.1f} MB"})


@app.route("/api/whitelist")
def api_whitelist_get():
    return jsonify({"usernames": read_whitelist()})


@app.route("/api/whitelist", methods=["POST"])
def api_whitelist_post():
    data = request.get_json()
    action = data.get("action")
    username = data.get("username", "").strip().lower().lstrip("@")
    current = read_whitelist()
    if action == "add" and username:
        if username not in [u.lower() for u in current]:
            current.append(username)
            write_whitelist(current)
    elif action == "remove" and username:
        current = [u for u in current if u.lower() != username.lower()]
        write_whitelist(current)
    elif action == "set":
        write_whitelist(data.get("usernames", []))
    return jsonify({"ok": True, "usernames": read_whitelist()})


@app.route("/api/settings")
def api_settings_get():
    s = read_settings()
    files_info = {}
    for name in ["queue.json", "history.jsonl", "daily_counts.json",
                  "followed.csv", "unfollowed.csv", "whitelist.txt",
                  "twitter_bot.log", "bot_config.json", "imports.json"]:
        p = _fpath(name)
        if os.path.exists(p):
            sz = os.path.getsize(p)
            files_info[name] = f"{sz / 1024:.1f} KB" if sz < 1024 * 1024 else f"{sz / 1024 / 1024:.1f} MB"
        else:
            files_info[name] = "not found"
    return jsonify({"settings": s, "files": files_info})


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    data = request.get_json()
    write_settings(data)
    return jsonify({"ok": True})


@app.route("/api/settings/reset-daily", methods=["POST"])
def api_reset_daily():
    fd = _acquire_lock()
    try:
        data = {"date": datetime.now().strftime("%Y-%m-%d"), "follows": 0,
                "unfollows": 0, "likes": 0,
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        path = _fpath("daily_counts.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    finally:
        _release_lock(fd)
    return jsonify({"ok": True})


@app.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    return jsonify(_start_bot_process("run"))


@app.route("/api/bot/start-unfollow", methods=["POST"])
def api_bot_start_unfollow():
    return jsonify(_start_bot_process("unfollow"))


@app.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    running, pid, _ = bot_is_running()
    if not running:
        return jsonify({"ok": False, "error": "Bot is not running"})
    try:
        os.kill(pid, signal.SIGTERM)
        pid_file = _fpath("bot.pid")
        if os.path.exists(pid_file):
            os.remove(pid_file)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/imports")
def api_imports():
    data = safe_read_json(_fpath("imports.json"), default=[])
    return jsonify({"imports": data})


# ============================================================
# PAGE ROUTES
# ============================================================

@app.route("/")
def page_home():
    return Response(HTML_HOME, mimetype="text/html")

@app.route("/upload")
def page_upload():
    return Response(HTML_UPLOAD, mimetype="text/html")

@app.route("/logs")
def page_logs():
    return Response(HTML_LOGS, mimetype="text/html")

@app.route("/history")
def page_history():
    return Response(HTML_HISTORY, mimetype="text/html")

@app.route("/queue")
def page_queue():
    return Response(HTML_QUEUE, mimetype="text/html")

@app.route("/followers")
def page_followers():
    return Response(HTML_FOLLOWERS, mimetype="text/html")


@app.route("/whitelist")
def page_whitelist():
    return Response(HTML_WHITELIST, mimetype="text/html")

@app.route("/settings")
def page_settings():
    return Response(HTML_SETTINGS, mimetype="text/html")


# ============================================================
# HTML TEMPLATES
# ============================================================

_CSS = """
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;
--green:#3fb950;--blue:#58a6ff;--amber:#d29922;--red:#f85149;--pink:#db61a2;--cyan:#39d2c0}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.container{max-width:1200px;margin:0 auto;padding:16px}
nav{background:var(--card);border-bottom:1px solid var(--border);padding:8px 16px;position:sticky;top:0;z-index:100;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
nav a{color:var(--muted);font-size:13px;padding:6px 12px;border-radius:6px}
nav a:hover,nav a.active{color:var(--text);background:var(--border);text-decoration:none}
nav .brand{color:var(--text);font-weight:600;font-size:15px;margin-right:auto}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}
.grid{display:grid;gap:16px}
.grid-2{grid-template-columns:1fr 1fr}
.grid-3{grid-template-columns:1fr 1fr 1fr}
.grid-4{grid-template-columns:1fr 1fr 1fr 1fr}
@media(max-width:768px){.grid-2,.grid-3,.grid-4{grid-template-columns:1fr}}
.stat{text-align:center}.stat .value{font-size:28px;font-weight:700;font-family:'SF Mono',SFMono-Regular,Consolas,monospace}
.stat .label{font-size:12px;color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
.badge-follow{background:#1a3a2a;color:var(--green)}
.badge-unfollow{background:#3a1a1a;color:var(--red)}
.badge-skip{background:#2a2a2a;color:var(--muted)}
.badge-like{background:#3a1a2a;color:var(--pink)}
.badge-pending{background:#1a2a3a;color:var(--blue)}
.badge-followed{background:#1a3a2a;color:var(--green)}
.progress-bar{background:var(--border);border-radius:4px;height:8px;overflow:hidden;margin:4px 0}
.progress-bar .fill{height:100%;border-radius:4px;transition:width .3s}
.fill-green{background:var(--green)}.fill-red{background:var(--red)}.fill-blue{background:var(--blue)}.fill-amber{background:var(--amber)}.fill-pink{background:var(--pink)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px;position:sticky;top:0;background:var(--card)}
tr:hover{background:#1c2129}
.mono{font-family:'SF Mono',SFMono-Regular,Consolas,monospace;font-size:12px}
button,.btn{background:var(--border);color:var(--text);border:1px solid var(--border);padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px;display:inline-flex;align-items:center;gap:4px}
button:hover,.btn:hover{background:#3d444d}
.btn-primary{background:var(--blue);color:#0d1117;border-color:var(--blue)}
.btn-primary:hover{background:#79c0ff}
.btn-danger{background:var(--red);color:#fff;border-color:var(--red)}
.btn-danger:hover{background:#ff7b72}
.btn-green{background:var(--green);color:#0d1117;border-color:var(--green)}
.btn-amber{background:var(--amber);color:#0d1117;border-color:var(--amber)}
.btn-amber:hover{background:#e3b341}
input,select,textarea{background:var(--bg);color:var(--text);border:1px solid var(--border);padding:8px 12px;border-radius:6px;font-size:13px;width:100%}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--blue)}
.feed-item{padding:8px 0;border-bottom:1px solid var(--border);display:flex;gap:12px;align-items:flex-start}
.feed-item:last-child{border-bottom:none}
.feed-time{color:var(--muted);font-size:11px;min-width:80px;font-family:'SF Mono',SFMono-Regular,Consolas,monospace}
.grid>*{min-width:0}
.user-link{min-width:0;overflow-wrap:anywhere}
.log-line{font-family:'SF Mono',SFMono-Regular,Consolas,monospace;font-size:12px;line-height:1.7;white-space:pre-wrap;word-break:break-all}
.log-container{background:#010409;border:1px solid var(--border);border-radius:8px;padding:12px;height:70vh;overflow-y:auto}
.log-toolbar{display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap;align-items:center}
.drop-zone{border:2px dashed var(--border);border-radius:8px;padding:40px;text-align:center;cursor:pointer;transition:border-color .2s}
.drop-zone:hover,.drop-zone.active{border-color:var(--blue)}
.drop-zone p{color:var(--muted)}
.flex{display:flex}.gap-2{gap:8px}.gap-4{gap:16px}.items-center{align-items:center}
.justify-between{justify-content:space-between}.flex-wrap{flex-wrap:wrap}
.mt-2{margin-top:8px}.mt-4{margin-top:16px}.mb-2{margin-bottom:8px}.mb-4{margin-bottom:16px}
.text-muted{color:var(--muted)}.text-green{color:var(--green)}.text-red{color:var(--red)}
.text-amber{color:var(--amber)}.text-blue{color:var(--blue)}.text-pink{color:var(--pink)}
.pagination{display:flex;gap:4px;align-items:center;justify-content:center;margin-top:16px}
.pagination button{min-width:32px}
.list-chip{display:inline-block;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle;padding:1px 7px;border-radius:10px;font-size:11px;background:rgba(88,166,255,.10);color:var(--blue);border:1px solid rgba(88,166,255,.35)}
.list-more{margin-left:4px;vertical-align:middle;cursor:pointer;padding:1px 6px;border-radius:10px;font-size:11px;background:none;color:var(--muted);border:1px solid var(--border)}
.list-more:hover{color:var(--blue);border-color:var(--blue)}
.list-all{margin-top:4px;display:flex;flex-wrap:wrap;gap:4px}
.shield-btn{background:none;border:none;cursor:pointer;padding:2px;font-size:14px;opacity:.4}
.shield-btn:hover{opacity:1}
.checkbox-cell{width:30px}
.checkbox-cell input{width:auto}
.filter-bar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.filter-btn{padding:4px 10px;font-size:11px;border-radius:12px}
.filter-btn.active{background:var(--blue);color:#0d1117;border-color:var(--blue)}
.chart-container{position:relative;height:300px;margin:16px 0}
@media(max-width:768px){.chart-container{height:200px}}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.status-dot.on{background:var(--green);box-shadow:0 0 6px var(--green)}.status-dot.off{background:var(--red)}
.toast{position:fixed;bottom:20px;right:20px;background:var(--card);border:1px solid var(--border);padding:12px 20px;border-radius:8px;z-index:300;display:none;animation:slideIn .3s}
@keyframes slideIn{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
button:disabled,.btn:disabled{opacity:.35;cursor:not-allowed;pointer-events:none}
.file-card{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:8px}
.file-card .fname{font-weight:600;font-size:13px;margin-bottom:6px}
.banner-warn{background:#2a2310;border:1px solid #d29922;border-radius:8px;padding:12px 16px;color:#d29922;font-size:13px;margin:12px 0}
.src-row{display:flex;align-items:flex-start;gap:8px;padding:6px;margin-bottom:4px;border:1px solid transparent;border-radius:6px;background:var(--bg)}
.src-row:hover{border-color:var(--border)}
.src-pin{background:none;border:none;padding:0 2px 0 0;font-size:9px;line-height:1;cursor:pointer;opacity:.75}
.src-pin:hover{opacity:1}
.src-row.dragging{border-color:var(--blue);background:#161b22;opacity:.9;box-shadow:0 4px 12px rgba(0,0,0,.4)}
.src-grip{cursor:grab;color:var(--muted);font-size:14px;line-height:1.2;padding:2px 4px;touch-action:none;user-select:none;flex:0 0 auto}
.src-grip:active{cursor:grabbing}
.src-row.dragging .src-grip{cursor:grabbing}
.src-rank{flex:0 0 auto;min-width:30px;text-align:right;white-space:nowrap;font-size:11px;color:var(--muted)}
.src-row.active{border-color:var(--green);background:#0f1c14}
.badge-active{background:rgba(63,185,80,.15);color:var(--green);border:1px solid var(--green);font-size:9px;padding:1px 6px;border-radius:10px;white-space:nowrap}
.badge-next{background:rgba(88,166,255,.12);color:var(--blue);border:1px solid var(--blue);font-size:9px;padding:1px 6px;border-radius:10px;white-space:nowrap}
.live-dot{width:6px;height:6px;border-radius:50%;background:var(--green);display:inline-block;margin-right:4px;animation:pulse 1.6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.src-body{flex:1 1 auto;min-width:0}
"""

_NAV = """
<nav>
  <span class="brand">🤖 Twitter Bot</span>
  <a href="/" id="nav-home">Dashboard</a>
  <a href="/upload" id="nav-upload">Upload</a>
  <a href="/queue" id="nav-queue">Queue</a>
  <a href="/followers" id="nav-followers">Followers</a>
  <a href="/history" id="nav-history">History</a>
  <a href="/logs" id="nav-logs">Logs</a>
  <a href="/whitelist" id="nav-whitelist">Whitelist</a>
  <a href="/settings" id="nav-settings">Settings</a>
</nav>
"""

_TOAST = """<div id="toast" class="toast"></div>
<script>
function showToast(msg, dur=3000){const t=document.getElementById('toast');t.textContent=msg;t.style.display='block';setTimeout(()=>t.style.display='none',dur)}
function timeAgo(iso){if(!iso)return'never';const d=new Date(iso),s=Math.floor((Date.now()-d)/1000);if(s<60)return s+'s ago';if(s<3600)return Math.floor(s/60)+'m ago';if(s<86400)return Math.floor(s/3600)+'h ago';return Math.floor(s/86400)+'d ago'}
function setActive(id){document.querySelectorAll('nav a').forEach(a=>a.classList.remove('active'));const el=document.getElementById(id);if(el)el.classList.add('active')}
// Usernames come from uploaded files, so they are data, never markup.
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function jsArg(v){return esc(JSON.stringify(String(v)))}
function xUrl(u){u=String(u);return /^\\d+$/.test(u)?'https://x.com/i/user/'+u:'https://x.com/'+encodeURIComponent(u)}
function userLink(u){return '<a class="user-link" href="'+esc(xUrl(u))+'" target="_blank" rel="noopener">@'+esc(u)+'</a>'}
</script>"""

def _page(title, body_html, nav_id="nav-home"):
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Twitter Bot Dashboard</title>
<style>{_CSS}</style>
</head><body>
{_NAV}
<div class="container">{body_html}</div>
{_TOAST}
<script>setActive('{nav_id}')</script>
</body></html>"""


# --- HOME (unchanged) ---
HTML_HOME = _page("Dashboard", """
<div id="status-area"></div>
<div class="grid grid-2 mt-4">
  <div class="card" id="queue-card"><h3>Queue Summary</h3><div id="queue-summary">Loading...</div></div>
  <div class="card"><h3>Recent Activity</h3><div id="activity-feed">Loading...</div></div>
</div>
<div class="card mt-4" id="sources-card">
  <div class="flex justify-between items-center flex-wrap gap-2">
    <h3 style="margin:0">List Progress &amp; Follow Order</h3>
    <button class="btn" id="order-reset" style="font-size:11px;padding:4px 10px" onclick="resetOrder()">Reset to auto</button>
  </div>
  <div class="text-muted" style="font-size:11px;margin:6px 0 10px">Drag <span class="mono">⠿</span> to choose which list the bot follows through first — a list you drag is pinned <span class="mono">📌</span> there. A newly imported list slots in behind &ldquo;Following now&rdquo;, &ldquo;Up next&rdquo; and anything you pinned higher, smallest first; finished lists sit at the bottom. Anyone in more than one list is followed from the earliest list they appear in. <span id="order-mode"></span></div>
  <div id="sources-progress">Loading...</div>
</div>
<div class="flex gap-2 mt-4 flex-wrap">
  <a href="/upload" class="btn btn-primary">Upload New List</a>
  <a href="/logs" class="btn">View Full Logs</a>
  <a href="/whitelist" class="btn">Edit Whitelist</a>
</div>
<script>
function loadStatus(){
  fetch('/api/status').then(r=>r.json()).then(d=>{
    const dc=d.daily_counts,qs=d.queue_stats;
    const running=d.bot_running;
    const modeLabel=d.bot_mode_label||'Normal';
    const fl=dc.follows||0,ul=dc.unfollows||0,lk=dc.likes||0;
    const fmax=320,umax=800,lmax=600;
    document.getElementById('status-area').innerHTML=`
    <div class="card">
      <h3>Bot Status</h3>
      <div class="flex items-center gap-2 mb-4">
        <span class="status-dot ${running?'on':'off'}"></span>
        <strong>${running?'Running (PID '+d.bot_pid+')':'Stopped'}</strong>
        <span class="badge badge-pending">${modeLabel}</span>
        <span class="text-muted" style="margin-left:auto">Last update: ${timeAgo(dc.last_updated?dc.last_updated.replace(' ','T'):'')}</span>
      </div>
      <div class="grid grid-3">
        <div>
          <div class="flex justify-between text-muted" style="font-size:12px"><span>Follows</span><span>${fl}/${fmax}</span></div>
          <div class="progress-bar"><div class="fill fill-green" style="width:${Math.min(100,fl/fmax*100)}%"></div></div>
        </div>
        <div>
          <div class="flex justify-between text-muted" style="font-size:12px"><span>Unfollows</span><span>${ul}/${umax}</span></div>
          <div class="progress-bar"><div class="fill fill-red" style="width:${Math.min(100,ul/umax*100)}%"></div></div>
        </div>
        <div>
          <div class="flex justify-between text-muted" style="font-size:12px"><span>Likes</span><span>${lk}/${lmax}</span></div>
          <div class="progress-bar"><div class="fill fill-pink" style="width:${Math.min(100,lk/lmax*100)}%"></div></div>
        </div>
      </div>
    </div>`;
    let nxt='N/A';
    if(qs.next_unfollow_secs!==null){
      if(qs.next_unfollow_secs<=0)nxt='Now';
      else{const h=Math.floor(qs.next_unfollow_secs/3600);const m=Math.floor((qs.next_unfollow_secs%3600)/60);nxt=h+'h '+m+'m'}
    }
    document.getElementById('queue-summary').innerHTML=`
      <div class="grid grid-2" style="gap:8px">
        <div class="stat"><div class="value text-blue">${qs.pending_follow}</div><div class="label">Pending Follows</div></div>
        <div class="stat"><div class="value text-green">${qs.delta}</div><div class="label">Following (delta)</div></div>
        <div class="stat"><div class="value text-muted">${qs.unfollowed}</div><div class="label">Unfollowed</div></div>
        <div class="stat"><div class="value text-amber">${qs.skipped}</div><div class="label">Skipped</div></div>
      </div>
      <div class="mt-4 text-muted" style="font-size:12px">Next unfollow: <strong>${nxt}</strong>${qs.overdue_unfollows?' ('+qs.overdue_unfollows+' due)':''} · Delta: <strong>${qs.delta}</strong>/${qs.follow_cap} · Mutuals kept: <strong>${qs.mutuals_kept}</strong> · Total: <strong>${qs.total}</strong></div>`;
  }).catch(()=>document.getElementById('status-area').innerHTML='<div class="card">Error loading status</div>');
}
function loadActivity(){
  fetch('/api/activity?per_page=20').then(r=>r.json()).then(d=>{
    if(!d.items||!d.items.length){document.getElementById('activity-feed').innerHTML='<div class="text-muted">No activity yet</div>';return}
    document.getElementById('activity-feed').innerHTML=d.items.map(e=>{
      const cls=e.action==='follow'?'badge-follow':e.action==='unfollow'?'badge-unfollow':e.action==='like'?'badge-like':'badge-skip';
      return `<div class="feed-item"><span class="feed-time">${timeAgo(e.timestamp)}</span><span class="badge ${cls}">${esc(e.action)}</span>${userLink(e.username)}</div>`;
    }).join('');
  }).catch(()=>document.getElementById('activity-feed').innerHTML='<div class="text-muted">Could not load activity</div>');
}
let dragEl=null,dragFrom=-1,orderDirty=0;
function prettyListName(n){return n.replace(/^xExport_/,'@').replace(/_following_count-\\d+_\\d+$/,'').replace(/_/g,' ')}
function loadSources(){
  if(dragEl||Date.now()<orderDirty)return;  // never yank rows out from under a drag or a just-saved order
  fetch('/api/queue/sources').then(r=>r.json()).then(d=>{
    const box=document.getElementById('sources-progress');
    document.getElementById('order-mode').textContent=d.custom_order?'Using your custom order.':'Currently auto (smallest list first).';
    document.getElementById('order-reset').style.display=d.custom_order?'':'none';
    if(!d.sources||!d.sources.length){box.innerHTML='<div class="text-muted">No lists imported yet</div>';return}
    const activeIdx=d.sources.findIndex(s=>s.active);
    const upNextIdx=d.sources.findIndex((s,i)=>i>activeIdx&&s.pending>0);
    box.innerHTML=d.sources.map((s,i)=>{
      const name=prettyListName(s.name);
      const pct=s.pct;
      const isDone=pct>=100;
      const barColor=isDone?'fill-green':pct>0?'fill-blue':'fill-amber';
      const statusText=isDone?'✓ Complete':s.done+'/'+s.total;
      // exactly one "following now" (the list the next follow comes from) and one "up next"
      const tag=s.active?'<span class="badge-active"><span class="live-dot"></span>Following now</span>'
               :i===upNextIdx?'<span class="badge-next">Up next</span>':'';
      // Offer a re-follow only once the list has actually been worked through; a list
      // still mid-flight would otherwise re-queue people we are still following.
      // Unfollowing is the last thing the bot does to a list, so the button goes live as
      // soon as that finishes. A finished list still owed unfollows says so; one that is
      // visibly mid-follow says nothing.
      const waitNote=s.awaiting_unfollow?'↻ after '+s.awaiting_unfollow+' more unfollow'+(s.awaiting_unfollow===1?'':'s'):'';
      const resetBtn=s.resettable&&s.can_reset
        ? `<button class="btn src-reset" style="font-size:10px;padding:2px 8px;margin-left:auto" title="Put the ${s.resettable} already-unfollowed handle(s) back in the queue">↻ Follow again</button>`
        : (isDone&&s.resettable&&waitNote
            ? `<span class="text-muted src-reset-wait" style="margin-left:auto" title="${s.reset_blocked_reason||''}">${waitNote}</span>`
            : '');
      // A pin is a position the user chose by dragging; everything else is placed for
      // them, which is what lets a newly imported list slot in near the top instead of
      // landing under every list that happened to be on screen during the last drag.
      const pin=s.pinned?`<button class="src-pin" title="You dragged this ${s.pinned==='down'?'down — new lists slot in above it':'up — new lists slot in below it'}. Click to unpin.">📌</button>`:'';
      const rankTitle=s.pinned?'Pinned by you':(d.custom_order?'Placed automatically — drag it to pin it':'Automatic order');
      return `<div class="src-row${s.active?' active':''}" data-name="${encodeURIComponent(s.name)}">
        <div class="src-grip" title="Drag to reorder">⠿</div>
        <div class="src-rank mono" title="${rankTitle}">${pin}<span class="src-n">${i+1}</span></div>
        <div class="src-body">
          <div class="flex justify-between items-center" style="font-size:12px;margin-bottom:3px">
            <span style="font-weight:600;color:${isDone?'var(--green)':'var(--text)'};overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name}</span>
            <span class="flex items-center gap-2" style="flex:0 0 auto">${tag}<span class="text-muted mono">${statusText} (${pct}%)</span></span>
          </div>
          <div class="progress-bar" style="height:6px"><div class="fill ${barColor}" style="width:${pct}%;transition:width .5s"></div></div>
          <div class="flex gap-4 text-muted" style="font-size:10px;margin-top:2px">
            <span>👥 ${s.total}</span>
            ${s.followed?'<span class="text-green">✓'+s.followed+' followed</span>':''}
            ${s.unfollowed?'<span>↩'+s.unfollowed+' unfollowed</span>':''}
            ${s.skipped?'<span class="text-amber">⏭'+s.skipped+' skipped</span>':''}
            ${s.pending?'<span class="text-blue">⏳'+s.pending+' pending</span>':''}
            ${s.dupes?'<span class="text-muted" title="Also in an earlier list — followed from there">🔗'+s.dupes+' in an earlier list</span>':''}
            ${s.mutuals?'<span class="text-green" title="They follow you back — kept, never re-followed">🤝'+s.mutuals+' follow you back</span>':''}
            ${s.last_reset?'<span class="text-muted" title="Last reset '+s.last_reset+'">↻ reset '+timeAgo(s.last_reset)+'</span>':''}
            ${resetBtn}
          </div>
        </div>
      </div>`;
    }).join('');
  }).catch(()=>document.getElementById('sources-progress').innerHTML='<div class="text-muted">Could not load source progress</div>');
}
document.getElementById('sources-progress').addEventListener('click',e=>{
  const row=e.target.closest('.src-row');if(!row)return;
  if(e.target.closest('.src-pin')){unpinList(decodeURIComponent(row.dataset.name));return}
  const b=e.target.closest('.src-reset');if(!b||b.disabled)return;
  resetList(decodeURIComponent(row.dataset.name));
});
function resetList(name){
  postReset({name,preview:true}).then(p=>{
    if(p.error){showToast(p.error);return}
    if(!p.can_reset){showToast("Can't reset yet: "+p.reset_blocked_reason);return}
    const kept=(p.kept_mutual||0)+(p.kept_held||0);
    let msg='Follow "'+prettyListName(name)+'" again?\\n\\n'
      +p.requeued+' handle(s) go back to pending and are cleared from followed.csv / unfollowed.csv,'
      +' so the bot treats them as never attempted.';
    if(kept)msg+='\\n\\nLeft alone: '+p.kept_mutual+' who follow you back'
      +(p.kept_held?', '+p.kept_held+' still counted as followed':'')+'.';
    if(!confirm(msg))return;
    doReset(name,false);
  }).catch(()=>showToast('Could not check that list'));
}
function doReset(name,force){
  postReset({name,force}).then(d=>{
    if(d.needs_force){
      if(confirm(d.error+'\\n\\nReset anyway? Rows the bot already holds in memory may come back.'))doReset(name,true);
      return;
    }
    if(d.error){showToast(d.error);return}
    showToast('Re-queued '+d.requeued+' from this list — bot picks them up next cycle');
    loadSources();loadStatus();
  }).catch(()=>showToast('Could not reset that list'));
}
function postReset(body){
  return fetch('/api/queue/sources/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(r=>r.json());
}
// Pointer-based drag so it works with a mouse and on touch screens alike.
function srcRows(){return [...document.querySelectorAll('#sources-progress .src-row')]}
function renumber(){srcRows().forEach((r,i)=>{const n=r.querySelector('.src-n');if(n)n.textContent=i+1})}
document.getElementById('sources-progress').addEventListener('pointerdown',e=>{
  const grip=e.target.closest('.src-grip');if(!grip)return;
  dragEl=grip.closest('.src-row');if(!dragEl)return;
  dragFrom=srcRows().indexOf(dragEl);  // where it sat before the drag, for "sooner" vs "later"
  dragEl.classList.add('dragging');
  e.currentTarget.setPointerCapture(e.pointerId);  // capture on the container: the row itself moves mid-drag
  e.preventDefault();
});
document.getElementById('sources-progress').addEventListener('pointermove',e=>{
  if(!dragEl)return;
  const box=document.getElementById('sources-progress');
  let after=null;
  for(const r of srcRows()){
    if(r===dragEl)continue;
    const b=r.getBoundingClientRect();
    if(e.clientY>b.top+b.height/2)after=r;
  }
  if(after){if(after.nextSibling!==dragEl)after.after(dragEl)}
  else if(box.firstChild!==dragEl)box.prepend(dragEl);
  renumber();
});
function endDrag(){
  if(!dragEl)return;
  dragEl.classList.remove('dragging');
  const moved=decodeURIComponent(dragEl.dataset.name),to=srcRows().indexOf(dragEl);
  const direction=dragFrom>=0&&to>dragFrom?'down':'up';
  dragEl=null;dragFrom=-1;
  saveOrder(moved,direction);
}
document.getElementById('sources-progress').addEventListener('pointerup',endDrag);
document.getElementById('sources-progress').addEventListener('pointercancel',endDrag);
window.addEventListener('pointerup',endDrag);  // safety net if capture is lost
function saveOrder(moved,direction){
  const order=srcRows().map(r=>decodeURIComponent(r.dataset.name));
  orderDirty=Date.now()+3000;  // let the write land before the next auto-refresh repaints
  fetch('/api/queue/sources/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order,moved,direction})})
    .then(r=>r.json()).then(d=>{
      if(d.error){showToast('Could not save order: '+d.error);return}
      showToast(direction==='down'
        ?'Pinned later — new lists will slot in above it'
        :'Pinned — new lists will slot in below it');
      document.getElementById('order-mode').textContent='Using your custom order.';
      document.getElementById('order-reset').style.display='';
    }).catch(()=>showToast('Could not save order'));
}
function unpinList(name){
  orderDirty=Date.now()+3000;
  fetch('/api/queue/sources/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({unpin:name})})
    .then(r=>r.json()).then(d=>{
      if(d.error){showToast('Could not unpin: '+d.error);return}
      showToast('Unpinned — it keeps its place, but no longer holds new lists back');
      orderDirty=0;loadSources();
    }).catch(()=>showToast('Could not unpin'));
}
function resetOrder(){
  fetch('/api/queue/sources/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reset:true})})
    .then(()=>{orderDirty=0;showToast('Back to automatic order');loadSources()})
    .catch(()=>showToast('Could not reset order'));
}
loadStatus();loadActivity();loadSources();setInterval(loadStatus,10000);setInterval(loadActivity,30000);setInterval(loadSources,30000);
</script>
""", "nav-home")

# --- UPLOAD (multi-file) ---
HTML_UPLOAD = _page("Upload Lists", """
<h2 class="mb-4" style="font-size:18px">Import Target Lists</h2>
<div class="card">
  <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
    <p style="font-size:16px;margin-bottom:8px">Drop CSV or TXT files here</p>
    <p style="font-size:12px">or click to browse — multiple files OK</p>
    <input type="file" id="fileInput" accept=".csv,.txt" multiple style="display:none">
  </div>
  <div id="column-select" class="mt-2" style="display:none"></div>
  <div id="preview-area" class="mt-4" style="display:none"></div>
  <div id="requeue-area" class="mt-2" style="display:none">
    <label style="font-size:13px;cursor:pointer;display:flex;align-items:center;gap:6px">
      <input type="checkbox" id="allowRequeue" style="width:auto" onchange="toggleRequeue()">
      Re-queue previously followed/unfollowed users
    </label>
    <span class="text-muted" style="font-size:11px">Only dedup against the active queue — allow users that were already processed to be followed again</span>
  </div>
  <div class="flex gap-2 mt-4 items-center">
    <button class="btn btn-primary" id="commitBtn" disabled onclick="commitImport()">Confirm Import</button>
    <span id="import-result" class="text-muted" style="font-size:13px"></span>
  </div>
</div>
<div class="card mt-4">
  <h3>Import History</h3>
  <div id="import-history">Loading...</div>
</div>
<script>
let pendingFiles=[];
let previewData=null;
const dz=document.getElementById('dropZone'),fi=document.getElementById('fileInput');
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('active')});
dz.addEventListener('dragleave',()=>dz.classList.remove('active'));
dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('active');if(e.dataTransfer.files.length)handleFiles(e.dataTransfer.files)});
fi.addEventListener('change',e=>{if(e.target.files.length)handleFiles(e.target.files)});

function buildFormData(extra){
  const fd=new FormData();
  pendingFiles.forEach(f=>fd.append('file',f));
  const sourceNames={};
  const inputs=document.querySelectorAll('.source-input');
  if(inputs.length){
    inputs.forEach(inp=>{const idx=parseInt(inp.dataset.idx);if(pendingFiles[idx])sourceNames[pendingFiles[idx].name]=inp.value||pendingFiles[idx].name});
  }else{
    pendingFiles.forEach(f=>{sourceNames[f.name]=f.name.replace(/\\.(csv|txt)$/i,'')});
  }
  fd.append('source_names',JSON.stringify(sourceNames));
  if(document.getElementById('allowRequeue').checked)fd.append('allow_requeue','true');
  const col=document.getElementById('colSelect');
  if(col&&col.value)fd.append('username_column',col.value);
  if(extra)Object.entries(extra).forEach(([k,v])=>fd.append(k,v));
  return fd;
}

function handleFiles(fileList){
  pendingFiles=[...fileList];
  document.getElementById('preview-area').style.display='none';
  document.getElementById('import-result').textContent='';
  document.getElementById('commitBtn').disabled=true;
  const fd=buildFormData();
  fetch('/api/queue/import',{method:'POST',body:fd}).then(r=>{
    if(!r.ok)throw new Error('Server error: '+r.status);
    return r.json();
  }).then(d=>{
    if(d.needs_column_selection){
      document.getElementById('column-select').style.display='block';
      document.getElementById('column-select').innerHTML=`
        <label class="text-muted" style="font-size:12px">File <strong>${d.filename}</strong>: select username column</label>
        <select id="colSelect" style="max-width:200px;margin-top:4px">${d.columns.map(c=>'<option value="'+c+'">'+c+'</option>').join('')}</select>
        <button class="btn mt-2" onclick="retryWithColumn()">Retry</button>`;
      document.getElementById('preview-area').style.display='none';
      return;
    }
    document.getElementById('column-select').style.display='none';
    previewData=d;
    showPreview(d);
  }).catch(err=>{showToast('Upload failed: '+err.message,5000);console.error(err)});
}

function retryWithColumn(){
  const fd=buildFormData();
  fetch('/api/queue/import',{method:'POST',body:fd}).then(r=>{
    if(!r.ok)throw new Error('Server error: '+r.status);
    return r.json();
  }).then(d=>{
    document.getElementById('column-select').style.display='none';
    previewData=d;
    showPreview(d);
  }).catch(err=>{showToast('Upload failed: '+err.message,5000);console.error(err)});
}

function toggleRequeue(){
  if(!pendingFiles.length)return;
  handleFiles(pendingFiles);
}

function showPreview(d){
  const pa=document.getElementById('preview-area');pa.style.display='block';
  const importable=d.combined_importable||d.combined_new;
  const hasFollowedOrUnfollowed=d.files.some(f=>(f.already_followed||0)+(f.already_unfollowed||0)>0);
  document.getElementById('requeue-area').style.display=hasFollowedOrUnfollowed?'block':'none';
  let html=`<div class="grid grid-3 mb-4" style="font-size:13px">
    <div class="stat"><div class="value" style="font-size:22px">${d.file_count}</div><div class="label">Files</div></div>
    <div class="stat"><div class="value" style="font-size:22px">${d.combined_total}</div><div class="label">Total Usernames</div></div>
    <div class="stat"><div class="value text-green" style="font-size:22px">${importable}</div><div class="label">Will Import</div></div>
  </div>`;
  if(importable===0){
    html+=`<div class="banner-warn">All ${d.combined_total} usernames are already in your queue, followed history, or unfollowed history. Enable "Re-queue previously followed/unfollowed users" above to import them again.</div>`;
  }
  d.files.forEach((f,i)=>{
    const rq=f.requeued||0;
    html+=`<div class="file-card">
      <div class="fname flex items-center gap-2 justify-between">
        <span>${esc(f.filename)}</span>
        <input type="text" class="source-input" data-idx="${i}" value="${esc(f.source_name)}" style="max-width:200px;padding:4px 8px;font-size:12px" placeholder="Source name">
      </div>
      <div class="flex gap-4 mt-2 flex-wrap" style="font-size:12px">
        <span>${f.total_in_file} in file</span>
        <span class="text-green">${f.new} new</span>
        ${rq?'<span class="text-blue">'+rq+' re-queue</span>':''}
        <span class="text-amber">${f.already_in_queue} in queue</span>
        <span class="text-muted">${f.already_followed} followed</span>
        <span class="text-muted">${f.already_unfollowed} unfollowed</span>
        ${f.duplicate_across?'<span class="text-pink">'+f.duplicate_across+' cross-dup</span>':''}
        ${f.invalid?'<span class="text-red" title="'+esc((f.invalid_sample||[]).join(' | '))+'">'+f.invalid+' not a handle (dropped)</span>':''}
      </div>
      ${f.preview&&f.preview.length?'<div class="text-muted mt-2" style="font-size:11px">Preview: '+f.preview.map(u=>'@'+esc(u)).join(', ')+'</div>':''}
    </div>`;
  });
  pa.innerHTML=html;
  document.getElementById('commitBtn').disabled=importable===0;
  document.getElementById('commitBtn').textContent='Confirm Import';
  if(importable===0)document.getElementById('import-result').textContent='';
  else document.getElementById('import-result').textContent=importable+' users will be added to queue';
}

function commitImport(){
  if(!pendingFiles.length)return;
  const btn=document.getElementById('commitBtn');
  btn.disabled=true;btn.textContent='Importing...';
  document.getElementById('import-result').textContent='';
  const fd=buildFormData({commit:'true'});
  fetch('/api/queue/import',{method:'POST',body:fd}).then(r=>{
    if(!r.ok)throw new Error('Server error: '+r.status);
    return r.json();
  }).then(d=>{
    document.getElementById('import-result').innerHTML='<span class="text-green">Imported '+d.total_added+' users</span>';
    btn.disabled=true;btn.textContent='Confirm Import';
    loadImportHistory();showToast('Import complete: '+d.total_added+' users added');
  }).catch(err=>{
    btn.disabled=false;btn.textContent='Confirm Import';
    document.getElementById('import-result').innerHTML='<span class="text-red">Error: '+err.message+'</span>';
    showToast('Import failed: '+err.message,5000);console.error(err);
  });
}

function loadImportHistory(){
  fetch('/api/imports').then(r=>r.json()).then(d=>{
    if(!d.imports||!d.imports.length){document.getElementById('import-history').innerHTML='<div class="text-muted">No imports yet</div>';return}
    document.getElementById('import-history').innerHTML='<table><thead><tr><th>Date</th><th>Source</th><th>Added</th><th>Total</th></tr></thead><tbody>'
      +d.imports.slice().reverse().map(i=>`<tr><td class="mono">${i.date?i.date.slice(0,16):''}</td><td>${i.source_name}</td><td class="text-green">${i.added}</td><td>${i.total_in_file}</td></tr>`).join('')
      +'</tbody></table>';
  });
}
loadImportHistory();
</script>
""", "nav-upload")

# --- LOGS (unchanged) ---
HTML_LOGS = _page("Live Logs", """
<div class="flex justify-between items-center mb-2 flex-wrap gap-2">
  <h2 style="font-size:18px">Live Logs</h2>
  <div class="flex gap-2 items-center">
    <span id="logSize" class="text-muted" style="font-size:12px"></span>
    <a href="/api/logs/download" class="btn" style="font-size:12px">Download</a>
    <button class="btn" style="font-size:12px" onclick="clearDisplay()">Clear</button>
  </div>
</div>
<div class="log-toolbar">
  <input type="text" id="logSearch" placeholder="Filter logs..." style="max-width:300px">
  <button class="filter-btn active" data-level="INFO" onclick="toggleLevel(this)">INFO</button>
  <button class="filter-btn active" data-level="WARNING" onclick="toggleLevel(this)">WARN</button>
  <button class="filter-btn active" data-level="ERROR" onclick="toggleLevel(this)">ERROR</button>
  <button class="filter-btn" data-level="DEBUG" onclick="toggleLevel(this)">DEBUG</button>
  <div style="margin-left:auto"><label style="font-size:12px;color:var(--muted)"><input type="checkbox" id="pinBottom" checked style="width:auto"> Pin to bottom</label></div>
</div>
<div class="log-container" id="logContainer"></div>
<script>
const lc=document.getElementById('logContainer');
const searchInput=document.getElementById('logSearch');
const pinCb=document.getElementById('pinBottom');
let allLines=[];
let activeLevels=new Set(['INFO','WARNING','ERROR']);
function colorize(line){
  if(line.includes('\\u2705')||line.includes('[INFO]')&&(line.includes('Followed')||line.includes('confirmed')))return'color:var(--green)';
  if(line.includes('\\u274c')||line.includes('[ERROR]'))return'color:var(--red)';
  if(line.includes('\\u26a0')||line.includes('[WARNING]'))return'color:var(--amber)';
  if(line.includes('\\u23f3'))return'color:var(--muted)';
  if(line.includes('\\ud83c\\udfaf'))return'color:var(--blue)';
  if(line.includes('\\u2764'))return'color:var(--pink)';
  if(line.includes('\\ud83d\\udcca')||line.includes('\\ud83d\\udcc2'))return'color:var(--cyan)';
  if(line.includes('[DEBUG]'))return'color:#555';
  return'';
}
function getLevel(line){
  if(line.includes('[DEBUG]'))return'DEBUG';
  if(line.includes('[WARNING]'))return'WARNING';
  if(line.includes('[ERROR]'))return'ERROR';
  return'INFO';
}
function renderLines(){
  const search=searchInput.value.toLowerCase();
  const html=allLines.filter(l=>{
    const lvl=getLevel(l);
    if(!activeLevels.has(lvl))return false;
    if(search&&!l.toLowerCase().includes(search))return false;
    return true;
  }).map(l=>`<div class="log-line" style="${colorize(l)}">${l.replace(/</g,'&lt;')}</div>`).join('');
  lc.innerHTML=html;
  if(pinCb.checked)lc.scrollTop=lc.scrollHeight;
}
function toggleLevel(btn){
  const lvl=btn.dataset.level;
  if(activeLevels.has(lvl)){activeLevels.delete(lvl);btn.classList.remove('active')}
  else{activeLevels.add(lvl);btn.classList.add('active')}
  renderLines();
}
searchInput.addEventListener('input',renderLines);
lc.addEventListener('scroll',()=>{
  const atBottom=lc.scrollHeight-lc.scrollTop-lc.clientHeight<50;
  if(!atBottom)pinCb.checked=false;
});
function clearDisplay(){allLines=[];lc.innerHTML=''}
const es=new EventSource('/api/logs/stream');
es.onmessage=e=>{
  try{const d=JSON.parse(e.data);allLines.push(d.line);if(allLines.length>5000)allLines=allLines.slice(-3000);renderLines()}catch(err){}
};
es.onerror=()=>{setTimeout(()=>{},5000)};
fetch('/api/logs/info').then(r=>r.json()).then(d=>{
  document.getElementById('logSize').textContent=d.exists?d.size_human:'No log file';
});
</script>
""", "nav-logs")

# --- HISTORY (unchanged) ---
HTML_HISTORY = _page("History & Analytics", """
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<h2 class="mb-4" style="font-size:18px">History & Analytics</h2>
<div class="grid grid-4 mb-4" id="stats-row">
  <div class="card stat"><div class="value" id="s-follows">-</div><div class="label">Total Follows</div></div>
  <div class="card stat"><div class="value" id="s-unfollows">-</div><div class="label">Total Unfollows</div></div>
  <div class="card stat"><div class="value" id="s-unique">-</div><div class="label">Unique Accounts</div></div>
  <div class="card stat"><div class="value" id="s-likes">-</div><div class="label">Total Likes</div></div>
</div>
<div class="grid grid-2 mb-4">
  <div class="card"><h3>Daily Activity (30 days)</h3><div class="chart-container"><canvas id="chartDaily"></canvas></div></div>
  <div class="card"><h3>Following Delta Over Time</h3><div class="chart-container"><canvas id="chartDelta"></canvas></div></div>
</div>
<div class="card">
  <h3>Action History</h3>
  <div class="filter-bar">
    <input type="text" id="histSearch" placeholder="Search username..." style="max-width:200px">
    <button class="filter-btn active" data-action="" onclick="setHistFilter(this,'')">All</button>
    <button class="filter-btn" data-action="follow" onclick="setHistFilter(this,'follow')">Follow</button>
    <button class="filter-btn" data-action="unfollow" onclick="setHistFilter(this,'unfollow')">Unfollow</button>
    <button class="filter-btn" data-action="skip" onclick="setHistFilter(this,'skip')">Skip</button>
    <button class="filter-btn" data-action="like" onclick="setHistFilter(this,'like')">Like</button>
    <a href="/api/history/export" class="btn" style="margin-left:auto;font-size:12px">Export CSV</a>
  </div>
  <div id="histTable">Loading...</div>
  <div class="pagination" id="histPag"></div>
</div>
<script>
let histPage=1,histAction='',histSearch='',listCellSeq=0;
// A handle can sit in several imported lists. Show the one it was actioned from and
// keep the rest a click (or a hover) away rather than widening the row.
function listCell(e){
  const names=(e.source_lists&&e.source_lists.length)?e.source_lists:(e.source_list?[e.source_list]:[]);
  if(!names.length)return '<span class="text-muted">&mdash;</span>';
  const first=`<span class="list-chip" title="${esc(names[0])}">${esc(names[0])}</span>`;
  if(names.length===1)return first;
  const id='lists-'+(listCellSeq++);
  const all=names.map(n=>`<span class="list-chip" title="${esc(n)}">${esc(n)}</span>`).join('')
    +`<button class="list-more" onclick="toggleLists('${id}')">&minus;</button>`;
  return `<span id="${id}-c">${first}<button class="list-more" title="${esc(names.join(', '))}" onclick="toggleLists('${id}')">+${names.length-1}</button></span>`
    +`<div class="list-all" id="${id}" style="display:none">${all}</div>`;
}
function toggleLists(id){
  const el=document.getElementById(id),collapsed=document.getElementById(id+'-c');
  const open=el.style.display!=='none';
  el.style.display=open?'none':'flex';
  collapsed.style.display=open?'':'none';
}
function setHistFilter(btn,action){
  document.querySelectorAll('.filter-bar .filter-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');histAction=action;histPage=1;loadHist();
}
document.getElementById('histSearch').addEventListener('input',e=>{histSearch=e.target.value;histPage=1;loadHist()});
function loadHist(){
  let url='/api/history?page='+histPage+'&per_page=50';
  if(histAction)url+='&action='+histAction;
  if(histSearch)url+='&search='+histSearch;
  fetch(url).then(r=>r.json()).then(d=>{
    if(!d.items.length){document.getElementById('histTable').innerHTML='<div class="text-muted mt-2">No entries</div>';document.getElementById('histPag').innerHTML='';return}
    let html='<table><thead><tr><th>Time</th><th>Action</th><th>Username</th><th>List</th><th>Details</th></tr></thead><tbody>';
    d.items.forEach(e=>{
      const cls=e.action==='follow'?'badge-follow':e.action==='unfollow'?'badge-unfollow':e.action==='like'?'badge-like':'badge-skip';
      const det=e.reason||e.note||e.skip_reason||'';
      html+=`<tr><td class="mono">${e.timestamp?esc(e.timestamp.slice(0,19)):''}</td><td><span class="badge ${cls}">${esc(e.action)}</span></td><td>${userLink(e.username)} <button class="shield-btn" title="Add to whitelist" onclick="addWL(${jsArg(e.username)})">🛡️</button></td><td>${listCell(e)}</td><td class="text-muted">${esc(det)}</td></tr>`;
    });
    html+='</tbody></table>';
    document.getElementById('histTable').innerHTML=html;
    const pages=Math.ceil(d.total/50);
    let phtml='';
    for(let i=1;i<=Math.min(pages,10);i++){phtml+=`<button class="${i===histPage?'btn-primary':''}" onclick="histPage=${i};loadHist()">${i}</button>`}
    if(pages>10)phtml+='<span class="text-muted">...'+pages+'</span>';
    document.getElementById('histPag').innerHTML=phtml;
  });
}
function addWL(u){fetch('/api/whitelist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'add',username:u})}).then(()=>showToast('Added @'+u+' to whitelist'))}
function loadStats(){
  fetch('/api/history/stats').then(r=>r.json()).then(d=>{
    document.getElementById('s-follows').textContent=d.total_follows;
    document.getElementById('s-unfollows').textContent=d.total_unfollows;
    document.getElementById('s-unique').textContent=d.unique_count||'?';
    document.getElementById('s-likes').textContent=d.total_likes;
  });
}
function loadCharts(){
  fetch('/api/history/chart-data?days=30').then(r=>r.json()).then(d=>{
    const labels=d.labels.map(l=>l.slice(5));
    new Chart(document.getElementById('chartDaily'),{type:'bar',data:{labels,datasets:[
      {label:'Follows',data:d.follows,backgroundColor:'rgba(63,185,80,.7)'},
      {label:'Unfollows',data:d.unfollows,backgroundColor:'rgba(248,81,73,.7)'},
      {label:'Likes',data:d.likes,backgroundColor:'rgba(219,97,162,.5)'}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#8b949e'}}},scales:{x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}},y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}}});
    new Chart(document.getElementById('chartDelta'),{type:'line',data:{labels,datasets:[
      {label:'Delta (F-U)',data:d.delta,borderColor:'#58a6ff',backgroundColor:'rgba(88,166,255,.1)',fill:true,tension:.3}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#8b949e'}}},scales:{x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}},y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}}});
  });
}
loadStats();loadHist();loadCharts();
</script>
""", "nav-history")

# --- QUEUE (unchanged) ---
HTML_QUEUE = _page("Queue Manager", """
<h2 class="mb-4" style="font-size:18px">Queue Manager</h2>
<div class="filter-bar">
  <input type="text" id="qSearch" placeholder="Search username..." style="max-width:200px">
  <button class="filter-btn active" data-status="" onclick="setQFilter(this,'')">All</button>
  <button class="filter-btn" data-status="pending_follow" onclick="setQFilter(this,'pending_follow')">Pending</button>
  <button class="filter-btn" data-status="followed" onclick="setQFilter(this,'followed')">Followed</button>
  <button class="filter-btn" data-status="unfollowed" onclick="setQFilter(this,'unfollowed')">Unfollowed</button>
  <button class="filter-btn" data-status="skipped" onclick="setQFilter(this,'skipped')">Skipped</button>
</div>
<div id="bulk-bar" class="flex gap-2 mb-2" style="display:none">
  <span id="sel-count" class="text-muted" style="font-size:12px"></span>
  <button class="btn" style="font-size:12px" onclick="bulkAction('move_top')">Move to Top</button>
  <button class="btn" style="font-size:12px" onclick="bulkAction('skip')">Skip</button>
  <button class="btn btn-danger" style="font-size:12px" onclick="bulkAction('remove')">Remove</button>
</div>
<div id="qTable">Loading...</div>
<div class="pagination" id="qPag"></div>
<script>
let qPage=1,qStatus='',qSearch='',selected=new Set();
function setQFilter(btn,status){
  document.querySelectorAll('.filter-bar .filter-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');qStatus=status;qPage=1;selected.clear();loadQ();
}
document.getElementById('qSearch').addEventListener('input',e=>{qSearch=e.target.value;qPage=1;loadQ()});
function loadQ(){
  let url='/api/queue?page='+qPage+'&per_page=50';
  if(qStatus)url+='&status='+qStatus;
  if(qSearch)url+='&search='+qSearch;
  fetch(url).then(r=>r.json()).then(d=>{
    if(!d.items.length){document.getElementById('qTable').innerHTML='<div class="text-muted mt-2">No entries</div>';document.getElementById('qPag').innerHTML='';return}
    let html='<table><thead><tr><th class="checkbox-cell"><input type="checkbox" onchange="toggleAll(this)" style="width:auto"></th><th>Username</th><th>Status</th><th>Source</th><th>Added</th><th>Timestamp</th></tr></thead><tbody>';
    d.items.forEach(e=>{
      const cls=e.status==='pending_follow'?'badge-pending':e.status==='followed'?'badge-followed':e.status==='unfollowed'?'badge-unfollow':'badge-skip';
      const chk=selected.has(e.username)?'checked':'';
      html+=`<tr><td class="checkbox-cell"><input type="checkbox" ${chk} onchange="toggleSel(${jsArg(e.username)},this.checked)" style="width:auto"></td>
        <td>${userLink(e.username)}</td>
        <td><span class="badge ${cls}">${esc(e.status)}</span></td>
        <td class="text-muted">${esc(e.source_list||'')}</td>
        <td class="mono">${e.added_at?e.added_at.slice(0,10):''}</td>
        <td class="mono">${e.timestamp?e.timestamp.slice(0,19):''}</td></tr>`;
    });
    html+='</tbody></table>';
    document.getElementById('qTable').innerHTML=html;
    updateBulkBar();
    const pages=Math.ceil(d.total/50);
    let phtml='<span class="text-muted" style="font-size:12px">'+d.total+' entries</span> ';
    if(qPage>1)phtml+='<button onclick="qPage--;loadQ()">Prev</button> ';
    phtml+='<span class="text-muted" style="font-size:12px">Page '+qPage+'/'+pages+'</span> ';
    if(qPage<pages)phtml+='<button onclick="qPage++;loadQ()">Next</button>';
    document.getElementById('qPag').innerHTML=phtml;
  });
}
function toggleSel(u,on){if(on)selected.add(u);else selected.delete(u);updateBulkBar()}
function toggleAll(cb){document.querySelectorAll('#qTable tbody input[type=checkbox]').forEach(c=>{c.checked=cb.checked;const u=c.closest('tr').querySelector('a').textContent.replace('@','');if(cb.checked)selected.add(u);else selected.delete(u)});updateBulkBar()}
function updateBulkBar(){const bar=document.getElementById('bulk-bar');if(selected.size>0){bar.style.display='flex';document.getElementById('sel-count').textContent=selected.size+' selected'}else{bar.style.display='none'}}
function bulkAction(action){
  fetch('/api/queue/bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,usernames:[...selected]})})
  .then(r=>r.json()).then(()=>{selected.clear();loadQ();showToast(action+' completed')});
}
loadQ();
</script>
""", "nav-queue")

# --- WHITELIST (unchanged) ---
HTML_WHITELIST = _page("Whitelist Editor", """
<h2 class="mb-4" style="font-size:18px">Whitelist — Never Unfollow</h2>
<div class="card">
  <div class="flex gap-2 mb-4">
    <input type="text" id="wlInput" placeholder="Add username (without @)" style="max-width:300px">
    <button class="btn btn-primary" onclick="addToWL()">Add</button>
  </div>
  <div id="wlList">Loading...</div>
</div>
<script>
function loadWL(){
  fetch('/api/whitelist').then(r=>r.json()).then(d=>{
    if(!d.usernames.length){document.getElementById('wlList').innerHTML='<div class="text-muted">Whitelist is empty</div>';return}
    document.getElementById('wlList').innerHTML='<table><thead><tr><th>Username</th><th style="width:80px"></th></tr></thead><tbody>'
      +d.usernames.map(u=>`<tr><td>${userLink(u)}</td><td><button class="btn btn-danger" style="font-size:11px;padding:2px 8px" onclick="removeWL(${jsArg(u)})">Remove</button></td></tr>`).join('')
      +'</tbody></table>';
  });
}
function addToWL(){
  const u=document.getElementById('wlInput').value.trim().replace(/^@/,'');
  if(!u)return;
  fetch('/api/whitelist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'add',username:u})})
  .then(()=>{document.getElementById('wlInput').value='';loadWL();showToast('Added @'+u)});
}
function removeWL(u){
  fetch('/api/whitelist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'remove',username:u})})
  .then(()=>{loadWL();showToast('Removed @'+u)});
}
document.getElementById('wlInput').addEventListener('keydown',e=>{if(e.key==='Enter')addToWL()});
loadWL();
</script>
""", "nav-whitelist")

# --- FOLLOWERS / FOLLOWBACKS ---
HTML_FOLLOWERS = _page("Followers", """
<h2 class="mb-4" style="font-size:18px">Followbacks</h2>
<div id="fxBanner"></div>
<div class="card">
  <div class="flex justify-between items-center flex-wrap gap-2">
    <h3 style="margin:0">Recently followed you</h3>
    <div class="flex gap-2 items-center flex-wrap">
      <span id="fxRecentCounts" class="text-muted" style="font-size:11px"></span>
      <select id="fxRecentKind" onchange="loadRecent()">
        <option value="arrivals">New followers</option>
        <option value="departures">Unfollowed you</option>
      </select>
    </div>
  </div>
  <div id="fxRecentNote" class="text-muted" style="font-size:11px;margin:6px 0 10px"></div>
  <div id="fxRecent">Loading...</div>
</div>
<div class="grid grid-2 mt-4">
  <div class="card">
    <h3>Export status</h3>
    <div id="fxStatus">Loading...</div>
  </div>
  <div class="card">
    <h3>Where your followers came from</h3>
    <div id="fxTotals">Loading...</div>
  </div>
</div>
<div class="card mt-4">
  <h3>Conversion by list</h3>
  <div class="text-muted" style="font-size:11px;margin:6px 0 10px">
    Of the handles each list actually got a follow onto, how many follow us now.
    The bot stops watching the moment it unfollows, so before this export a followback
    after that was invisible &mdash; and there is no deadline on one: it can arrive next
    week or years later. <b>Late</b> counts the ones we can prove arrived after the
    unfollow. <b>Untimed</b> are followbacks from before this job started watching: we
    know they follow us, but not when they began, so they are left out of the median
    rather than inflating it.
    Lists with fewer than 25 follows show &mdash; rather than a number built on noise.
  </div>
  <div id="fxLists">Loading...</div>
</div>
<div class="card mt-4">
  <div class="flex justify-between items-center flex-wrap gap-2">
    <h3 style="margin:0">Followers</h3>
    <div class="flex gap-2 flex-wrap">
      <select id="fxState" onchange="loadRows(1)">
        <option value="">All</option>
        <option value="mutual">Mutual (we follow them)</option>
        <option value="follower_only">Fans (we don't follow them)</option>
        <option value="organic">Organic (no list)</option>
      </select>
      <input id="fxSearch" placeholder="search handle" style="width:160px">
    </div>
  </div>
  <div id="fxRows" class="mt-2">Loading...</div>
  <div id="fxPager" class="flex gap-2 items-center mt-2"></div>
</div>
<script>
function pct(v){return v===null||v===undefined?'&mdash;':v+'%'}
function num(v){return (v===null||v===undefined)?'&mdash;':v}

function loadStatus(){
  fetch('/api/followers/status').then(r=>r.json()).then(d=>{
    let banner='';
    if(d.never_run){
      banner='<div class="banner-warn">The followers export has never completed. Late followbacks are not being detected. Run it once: <span class="mono">python3 x_followers_export.py --probe</span></div>';
    }else if(d.outcome==='auth_error'){
      banner='<div class="banner-warn" style="background:#2a1215;border-color:#f85149;color:#f85149">Export session is dead &mdash; re-export the cookie file. '+(d.message||'')+'</div>';
    }else if(d.outcome==='edge_blocked'){
      banner='<div class="banner-warn">This VPN exit is Cloudflare-blocked for part of the X API \u2014 the cookies are fine. It retries on the next run; move the box to another exit if it persists.</div>';
    }else if(d.outcome==='shape_change'||d.outcome==='suspicious'){
      banner='<div class="banner-warn" style="background:#2a1215;border-color:#f85149;color:#f85149">The followers endpoint returned something unexpected; nothing was written. '+(d.message||'')+'</div>';
    }else if(d.stale){
      banner='<div class="banner-warn">Last successful export was '+num(d.hours_since_success)+'h ago. Check <span class="mono">systemctl list-timers xb-followers-export.timer</span></div>';
    }else if(!d.ordering_verified){
      banner='<div class="banner-warn">Still doing full re-pages: newest-first page ordering has not been confirmed yet, so incremental runs stay off.</div>';
    }
    document.getElementById('fxBanner').innerHTML=banner;

    const acct=d.account||{};
    const iso=acct.quota_isolated;
    const rows=[
      ['Last run', d.finished_at?timeAgo(d.finished_at):'never'],
      ['Outcome', d.outcome||'&mdash;'],
      ['Mode', (d.mode||'&mdash;')+(d.stop_reason?' ('+d.stop_reason+')':'')],
      ['Pages', num(d.pages)],
      ['Followers known', num(d.followers_total)],
      ['X reports', num(d.reported_followers_count)+(d.drift?' (drift '+d.drift+')':'')],
      ['New last run', num(d.new_followers)],
      ['Flagged last run', num(d.queue_marked)+' ('+num(d.queue_marked_mutual)+' mutual, '+num(d.queue_marked_follower_only)+' fans)'],
      ['Next full re-page', d.next_full_due||'&mdash;'],
      ['Cookies expire', (d.cookie&&d.cookie.days_left!==null&&d.cookie.days_left!==undefined)?(d.cookie.days_left+' days'):'&mdash;'],
      ['Export account', acct.session_user_id?('id '+acct.session_user_id+(iso?' (separate from the follow account)':' &mdash; same as the follow account, so no quota isolation')):'&mdash;'],
    ];
    document.getElementById('fxStatus').innerHTML='<table><tbody>'
      +rows.map(r=>'<tr><td class="text-muted" style="width:45%">'+r[0]+'</td><td>'+r[1]+'</td></tr>').join('')
      +'</tbody></table>';
  });
}

function loadConversion(){
  fetch('/api/followers/conversion').then(r=>r.json()).then(d=>{
    const t=d.totals;
    document.getElementById('fxTotals').innerHTML='<table><tbody>'
      +'<tr><td class="text-muted" style="width:45%">Followers</td><td>'+num(t.followers_total)+'</td></tr>'
      +'<tr><td class="text-muted">From your lists</td><td>'+num(t.attributed_followers)+'</td></tr>'
      +'<tr><td class="text-muted">Organic</td><td>'+num(t.organic_followers)+'</td></tr>'
      +'<tr><td class="text-muted">Follows made</td><td>'+num(t.followed)+'</td></tr>'
      +'<tr><td class="text-muted">Overall conversion</td><td><b>'+pct(t.conversion_pct)+'</b></td></tr>'
      +'<tr><td class="text-muted">Late followbacks (after '+d.unfollow_after_days+'d)</td><td>'+num(t.late_followbacks)+'</td></tr>'
      +'<tr><td class="text-muted">Untimed followbacks</td><td>'+num(t.untimed_followbacks)+'</td></tr>'
      +'<tr><td class="text-muted">Watching since</td><td>'+(d.watching_since?timeAgo(d.watching_since):'not yet')+'</td></tr>'
      +'</tbody></table>';

    if(!d.lists.length){document.getElementById('fxLists').innerHTML='<div class="text-muted">No lists yet</div>';return}
    document.getElementById('fxLists').innerHTML='<table><thead><tr>'
      +'<th>List</th><th>Follows</th><th>Followbacks</th><th>Conversion</th>'
      +'<th>Mutual</th><th>Fans</th><th>Late</th><th>Untimed</th><th>Median days</th>'
      +'<th>Pending</th>'
      +'</tr></thead><tbody>'
      +d.lists.map(l=>'<tr>'
        +'<td class="mono">'+l.name+'</td>'
        +'<td>'+l.followed+'</td>'
        +'<td>'+l.followbacks+'</td>'
        +'<td'+(l.sample_too_small?' class="text-muted" title="fewer than 25 follows"':'><b')+'>'+pct(l.conversion_pct)+(l.sample_too_small?'':'</b>')+'</td>'
        +'<td>'+l.mutuals+'</td>'
        +'<td>'+l.fans+'</td>'
        +'<td>'+l.late_followbacks+'</td>'
        +'<td'+(l.untimed_followbacks?' class="text-muted" title="followed before this job started watching; timing unknowable"':'')+'>'+l.untimed_followbacks+'</td>'
        +'<td>'+num(l.median_days_to_followback)+'</td>'
        +'<td>'+l.still_pending+'</td>'
        +'</tr>').join('')
      +'</tbody></table>';
  });
}

let fxPage=1;
function loadRows(page){
  fxPage=page||fxPage;
  const state=document.getElementById('fxState').value;
  const search=document.getElementById('fxSearch').value.trim();
  fetch('/api/followers/mutuals?page='+fxPage+'&per_page=50&state='+encodeURIComponent(state)+'&search='+encodeURIComponent(search))
  .then(r=>r.json()).then(d=>{
    if(!d.rows.length){document.getElementById('fxRows').innerHTML='<div class="text-muted">Nothing to show. Has the export run?</div>';document.getElementById('fxPager').innerHTML='';return}
    const label={mutual:'mutual',follower_only:'fan',organic:'organic'};
    document.getElementById('fxRows').innerHTML='<table><thead><tr>'
      +'<th>Handle</th><th>Relationship</th><th>List</th><th>Queue status</th><th>First seen</th>'
      +'</tr></thead><tbody>'
      +d.rows.map(r=>'<tr>'
        +'<td>'+userLink(r.username)+'</td>'
        +'<td>'+(label[r.state]||r.state)+(r.lost_at?' <span class="text-muted">(gone)</span>':'')+'</td>'
        +'<td class="mono">'+(r.source_list?esc(r.source_list):'&mdash;')+'</td>'
        +'<td>'+(r.queue_status||'&mdash;')+'</td>'
        +'<td class="text-muted">'+(r.first_seen?timeAgo(r.first_seen):'&mdash;')+'</td>'
        +'</tr>').join('')
      +'</tbody></table>';
    document.getElementById('fxPager').innerHTML=
      '<button class="btn" style="font-size:11px;padding:4px 10px" '+(d.page<=1?'disabled':'')+' onclick="loadRows('+(d.page-1)+')">Prev</button>'
      +'<span class="text-muted" style="font-size:11px">page '+d.page+' of '+d.pages+' &middot; '+d.total+' rows</span>'
      +'<button class="btn" style="font-size:11px;padding:4px 10px" '+(d.page>=d.pages?'disabled':'')+' onclick="loadRows('+(d.page+1)+')">Next</button>';
  });
}

function loadRecent(){
  const kind=document.getElementById('fxRecentKind').value;
  fetch('/api/followers/recent?limit=40').then(r=>r.json()).then(d=>{
    const c=d.counts;
    document.getElementById('fxRecentCounts').innerHTML=
      '+'+c.new_24h+' / 24h &middot; +'+c.new_7d+' / 7d &middot; &minus;'+c.lost_24h+' / 24h';
    document.getElementById('fxRecentNote').innerHTML=
      'Timestamps are when an export first saw them, so this is only as sharp as how often it runs. '
      +'The '+d.backfilled+' followers who already existed when tracking started are not listed here '
      +(d.watching_since?'(tracking since '+timeAgo(d.watching_since)+').':'.');
    const rows=d[kind]||[];
    const verb=kind==='arrivals'?'followed':'unfollowed';
    if(!rows.length){
      document.getElementById('fxRecent').innerHTML=
        '<div class="text-muted">Nobody has '+verb+' you since tracking started.</div>';
      return;
    }
    const label={mutual:'mutual',follower_only:'fan',organic:'organic'};
    document.getElementById('fxRecent').innerHTML='<table><thead><tr>'
      +'<th>Handle</th><th>Relationship</th><th>From list</th><th>'
      +(kind==='arrivals'?'First seen':'Gone since')+'</th>'
      +'</tr></thead><tbody>'
      +rows.map(r=>'<tr>'
        +'<td>'+userLink(r.username)+'</td>'
        +'<td>'+(label[r.state]||r.state)+'</td>'
        +'<td class="mono">'+(r.source_list?esc(r.source_list):'&mdash;')+'</td>'
        +'<td class="text-muted">'+(r.at?timeAgo(r.at):'&mdash;')+'</td>'
        +'</tr>').join('')
      +'</tbody></table>';
  });
}
document.getElementById('fxSearch').addEventListener('keydown',e=>{if(e.key==='Enter')loadRows(1)});
loadStatus();loadConversion();loadRecent();loadRows(1);
setInterval(loadRecent,120000);
</script>
""", "nav-followers")

# --- SETTINGS (unchanged) ---
HTML_SETTINGS = _page("Settings", """
<h2 class="mb-4" style="font-size:18px">Bot Settings</h2>
<div class="grid grid-2">
  <div class="card">
    <h3>Configuration</h3>
    <div id="settingsForm">Loading...</div>
    <button class="btn btn-primary mt-4" onclick="saveSettings()">Save Settings</button>
    <p class="text-muted mt-2" style="font-size:11px">Note: Bot reads config on each cycle. Restart for immediate effect.</p>
  </div>
  <div class="card">
    <h3>Bot Control</h3>
    <div id="botControl">Loading...</div>
    <div class="flex gap-2 mt-4 flex-wrap">
      <button class="btn btn-green" onclick="botCmd('start')">Start Normal</button>
      <button class="btn btn-amber" onclick="botCmd('start-unfollow')">Unfollow Only</button>
      <button class="btn btn-danger" onclick="botCmd('stop')">Stop Bot</button>
      <button class="btn" onclick="botCmd('stop');setTimeout(()=>botCmd('start'),3000)">Restart Bot</button>
    </div>
    <h3 class="mt-4">Maintenance</h3>
    <button class="btn btn-danger mt-2" onclick="resetDaily()">Reset Daily Counts</button>
  </div>
</div>
<div class="card mt-4">
  <h3>Data Files</h3>
  <div id="filesInfo">Loading...</div>
</div>
<script>
const fields=[
  {key:'follow_limit',label:'Daily Follow Limit',type:'number'},
  {key:'unfollow_limit',label:'Daily Unfollow Limit',type:'number'},
  {key:'like_limit',label:'Daily Like Limit',type:'number'},
  {key:'unfollow_after_days',label:'Unfollow After (days)',type:'number'},
  {key:'max_following_delta',label:'Max Following Delta',type:'number'},
  {key:'session_rotate_hours',label:'Session Rotate (hours)',type:'number'},
];
function loadSettings(){
  fetch('/api/settings').then(r=>r.json()).then(d=>{
    const s=d.settings;
    document.getElementById('settingsForm').innerHTML=fields.map(f=>`
      <div class="mb-2"><label class="text-muted" style="font-size:12px">${f.label}</label>
      <input type="${f.type}" id="set-${f.key}" value="${s[f.key]||''}" style="max-width:200px"></div>`).join('');
    document.getElementById('filesInfo').innerHTML='<table><thead><tr><th>File</th><th>Size</th></tr></thead><tbody>'
      +Object.entries(d.files).map(([k,v])=>`<tr><td class="mono">${k}</td><td>${v}</td></tr>`).join('')
      +'</tbody></table>';
  });
  fetch('/api/status').then(r=>r.json()).then(d=>{
    const modeLabel=d.bot_mode_label||'Normal';
    document.getElementById('botControl').innerHTML=`
      <div class="flex items-center gap-2"><span class="status-dot ${d.bot_running?'on':'off'}"></span>
      <strong>${d.bot_running?'Running (PID '+d.bot_pid+')':'Stopped'}</strong>
      <span class="badge badge-pending">${modeLabel}</span></div>`;
  });
}
function saveSettings(){
  const data={};
  fields.forEach(f=>{const v=document.getElementById('set-'+f.key).value;data[f.key]=f.type==='number'?Number(v):v});
  fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
  .then(()=>showToast('Settings saved'));
}
function botCmd(action){
  fetch('/api/bot/'+action,{method:'POST'}).then(r=>r.json()).then(d=>{
    showToast(d.ok?action+' succeeded':d.error||'Failed');
    setTimeout(loadSettings,2000);
  });
}
function resetDaily(){
  if(!confirm('Reset daily counts to zero?'))return;
  fetch('/api/settings/reset-daily',{method:'POST'}).then(()=>showToast('Daily counts reset'));
}
loadSettings();
</script>
""", "nav-settings")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Twitter Bot Web Dashboard")
    parser.add_argument("--port", type=int, default=8003, help="Port (default: 8003)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--data-dir", default=".", help="Bot data directory")
    parser.add_argument("--debug", action="store_true", help="Flask debug mode")
    args = parser.parse_args()

    global DATA_DIR
    DATA_DIR = os.path.abspath(args.data_dir)

    print(f"Twitter Bot Dashboard starting on http://{args.host}:{args.port}")
    print(f"Data directory: {DATA_DIR}")

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
