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
import signal
import subprocess
import sys
import tempfile
import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, Response, jsonify, request, redirect, url_for, send_file, abort
)

from twitter_csv import identity_key_set, parse_csv_export_rows

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
    return parsed, total


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
        argv = ["python3", bot_script, "unfollow" if mode == "unfollow" else "run"]
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


def get_queue_stats(queue=None):
    if queue is None:
        queue = read_queue()
    counts = defaultdict(int)
    oldest_followed = None
    for entry in queue:
        s = entry.get("status", "unknown")
        counts[s] += 1
        if s == "followed":
            ts = entry.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                except ValueError:
                    try:
                        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
                    except ValueError:
                        try:
                            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            dt = None
                if dt and (oldest_followed is None or dt < oldest_followed):
                    oldest_followed = dt
    delta = counts.get("followed", 0)
    settings = read_settings()
    uad = settings.get("unfollow_after_days", 4)
    next_unfollow_secs = None
    if oldest_followed:
        eligible_at = oldest_followed + timedelta(days=uad)
        diff = (eligible_at - datetime.now()).total_seconds()
        next_unfollow_secs = max(0, diff)
    return {
        "pending_follow": counts.get("pending_follow", 0),
        "followed": counts.get("followed", 0),
        "unfollowed": counts.get("unfollowed", 0),
        "skipped": counts.get("skipped", 0),
        "total": len(queue),
        "delta": delta,
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

        imports = safe_read_json(_fpath("imports.json"), default=[])
        results = []
        total_added = 0

        for fd_item in files_data:
            source_name = fd_item["source_name"]
            rows_in = fd_item.get("rows")
            if rows_in is None:
                rows_in = fd_item.get("usernames", [])
            added = 0
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
                        entry.pop("skip_reason", None)
                        if uid:
                            entry["user_id"] = uid
                        if fc is not None:
                            entry["followers_count"] = fc
                        added += 1
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
            })
            results.append({"source_name": source_name, "added": added})
            total_added += added

        qpath = _fpath("queue.json")
        tmp = qpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=1)
        os.replace(tmp, qpath)

        imp_path = _fpath("imports.json")
        with open(imp_path, "w") as f:
            json.dump(imports, f, indent=2)

        return {"total_added": total_added, "per_file": results}
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

    all_files_data = []
    cross_batch_keys = set()
    needs_column = False
    needs_column_info = None

    for f in files:
        fname = f.filename or "upload"
        content = f.read().decode("utf-8", errors="replace")
        source_name = source_map.get(fname, fname.rsplit(".", 1)[0] if "." in fname else fname)

        rows, err, columns = _parse_file_import_rows(fname, content, username_col)

        if columns is not None and not rows:
            needs_column = True
            needs_column_info = {"filename": fname, "columns": columns, "needs_column_selection": True}
            break

        if err:
            return jsonify({"error": f"Error parsing {fname}: {err}"}), 400

        per_user = []
        file_new = 0
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

        import_rows = [pu["row"] for pu in per_user if pu["status"] in ("new", "requeue")]
        preview_usernames = [r["username"] for r in rows]
        all_files_data.append({
            "filename": fname,
            "source_name": source_name,
            "import_rows": import_rows,
            "total_in_file": len(rows),
            "new": file_new,
            "requeued": file_requeued,
            "already_in_queue": file_in_queue,
            "already_followed": file_followed,
            "already_unfollowed": file_unfollowed,
            "duplicate_across": file_cross_dup,
            "preview": [u[:20] for u in preview_usernames[:8]],
        })

    if needs_column:
        return jsonify(needs_column_info), 200

    if not commit:
        combined_new = sum(fd["new"] for fd in all_files_data)
        combined_requeued = sum(fd["requeued"] for fd in all_files_data)
        combined_importable = combined_new + combined_requeued
        return jsonify({
            "files": [{k: v for k, v in fd.items() if k != "import_rows"} for fd in all_files_data],
            "combined_new": combined_new,
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
    sources = defaultdict(lambda: {"total": 0, "pending_follow": 0,
                                    "followed": 0, "unfollowed": 0, "skipped": 0})
    for entry in queue:
        src = entry.get("source_list", "unknown")
        status = entry.get("status", "unknown")
        sources[src]["total"] += 1
        if status in sources[src]:
            sources[src][status] += 1

    result = []
    for name, counts in sources.items():
        done = counts["followed"] + counts["unfollowed"] + counts["skipped"]
        pct = round(done / counts["total"] * 100, 1) if counts["total"] > 0 else 0
        result.append({
            "name": name,
            "total": counts["total"],
            "pending": counts["pending_follow"],
            "followed": counts["followed"],
            "unfollowed": counts["unfollowed"],
            "skipped": counts["skipped"],
            "done": done,
            "pct": pct,
        })
    # Sort to roughly match bot priority: sources with fewer pending rows surface first
    # (see queue_dedupe.entry_rank_tuple — smallest source list first).
    # Completed lists (0 pending) drop to the bottom so active work is visible at top.
    result.sort(key=lambda x: (x["pending"] == 0, x["pending"], x["total"]))
    return jsonify({"sources": result})


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
    writer.writerow(["timestamp", "action", "username", "details"])
    for e in entries:
        details = {k: v for k, v in e.items() if k not in ("timestamp", "action", "username")}
        writer.writerow([e.get("timestamp", ""), e.get("action", ""),
                         e.get("username", ""), json.dumps(details)])
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
"""

_NAV = """
<nav>
  <span class="brand">🤖 Twitter Bot</span>
  <a href="/" id="nav-home">Dashboard</a>
  <a href="/upload" id="nav-upload">Upload</a>
  <a href="/queue" id="nav-queue">Queue</a>
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
<div class="card mt-4" id="sources-card"><h3>List Progress</h3><div id="sources-progress">Loading...</div></div>
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
        <div class="stat"><div class="value text-green">${qs.followed}</div><div class="label">Following (delta)</div></div>
        <div class="stat"><div class="value text-muted">${qs.unfollowed}</div><div class="label">Unfollowed</div></div>
        <div class="stat"><div class="value text-amber">${qs.skipped}</div><div class="label">Skipped</div></div>
      </div>
      <div class="mt-4 text-muted" style="font-size:12px">Next unfollow: <strong>${nxt}</strong> · Delta: <strong>${qs.delta}</strong> · Total: <strong>${qs.total}</strong></div>`;
  }).catch(()=>document.getElementById('status-area').innerHTML='<div class="card">Error loading status</div>');
}
function loadActivity(){
  fetch('/api/activity?per_page=20').then(r=>r.json()).then(d=>{
    if(!d.items||!d.items.length){document.getElementById('activity-feed').innerHTML='<div class="text-muted">No activity yet</div>';return}
    document.getElementById('activity-feed').innerHTML=d.items.map(e=>{
      const cls=e.action==='follow'?'badge-follow':e.action==='unfollow'?'badge-unfollow':e.action==='like'?'badge-like':'badge-skip';
      const userUrl=/^\\d+$/.test(e.username)?'https://x.com/i/user/'+e.username:'https://x.com/'+e.username;
      return `<div class="feed-item"><span class="feed-time">${timeAgo(e.timestamp)}</span><span class="badge ${cls}">${e.action}</span><a href="${userUrl}" target="_blank">@${e.username}</a></div>`;
    }).join('');
  }).catch(()=>document.getElementById('activity-feed').innerHTML='<div class="text-muted">Could not load activity</div>');
}
function loadSources(){
  fetch('/api/queue/sources').then(r=>r.json()).then(d=>{
    if(!d.sources||!d.sources.length){document.getElementById('sources-progress').innerHTML='<div class="text-muted">No lists imported yet</div>';return}
    let html='';
    d.sources.forEach(s=>{
      const name=s.name.replace(/^xExport_/,'@').replace(/_following_count-\d+_\d+$/,'').replace(/_/g,' ');
      const pct=s.pct;
      const isDone=pct>=100;
      const barColor=isDone?'fill-green':pct>0?'fill-blue':'fill-amber';
      const statusText=isDone?'✓ Complete':s.done+'/'+s.total;
      html+=`<div style="margin-bottom:10px">
        <div class="flex justify-between items-center" style="font-size:12px;margin-bottom:3px">
          <span style="font-weight:600;color:${isDone?'var(--green)':'var(--text)'}">${name}</span>
          <span class="text-muted mono">${statusText} (${pct}%)</span>
        </div>
        <div class="progress-bar" style="height:6px"><div class="fill ${barColor}" style="width:${pct}%;transition:width .5s"></div></div>
        <div class="flex gap-4 text-muted" style="font-size:10px;margin-top:2px">
          <span>👥 ${s.total}</span>
          ${s.followed?'<span class="text-green">✓'+s.followed+' followed</span>':''}
          ${s.unfollowed?'<span>↩'+s.unfollowed+' unfollowed</span>':''}
          ${s.skipped?'<span class="text-amber">⏭'+s.skipped+' skipped</span>':''}
          ${s.pending?'<span class="text-blue">⏳'+s.pending+' pending</span>':''}
        </div>
      </div>`;
    });
    document.getElementById('sources-progress').innerHTML=html;
  }).catch(()=>document.getElementById('sources-progress').innerHTML='<div class="text-muted">Could not load source progress</div>');
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
        <span>${f.filename}</span>
        <input type="text" class="source-input" data-idx="${i}" value="${f.source_name}" style="max-width:200px;padding:4px 8px;font-size:12px" placeholder="Source name">
      </div>
      <div class="flex gap-4 mt-2 flex-wrap" style="font-size:12px">
        <span>${f.total_in_file} in file</span>
        <span class="text-green">${f.new} new</span>
        ${rq?'<span class="text-blue">'+rq+' re-queue</span>':''}
        <span class="text-amber">${f.already_in_queue} in queue</span>
        <span class="text-muted">${f.already_followed} followed</span>
        <span class="text-muted">${f.already_unfollowed} unfollowed</span>
        ${f.duplicate_across?'<span class="text-pink">'+f.duplicate_across+' cross-dup</span>':''}
      </div>
      ${f.preview&&f.preview.length?'<div class="text-muted mt-2" style="font-size:11px">Preview: '+f.preview.map(u=>'@'+u).join(', ')+'</div>':''}
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
let histPage=1,histAction='',histSearch='';
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
    let html='<table><thead><tr><th>Time</th><th>Action</th><th>Username</th><th>Details</th></tr></thead><tbody>';
    d.items.forEach(e=>{
      const cls=e.action==='follow'?'badge-follow':e.action==='unfollow'?'badge-unfollow':e.action==='like'?'badge-like':'badge-skip';
      const det=e.reason||e.note||e.source_list||'';
      const userUrl=/^\\d+$/.test(e.username)?'https://x.com/i/user/'+e.username:'https://x.com/'+e.username;
      html+=`<tr><td class="mono">${e.timestamp?e.timestamp.slice(0,19):''}</td><td><span class="badge ${cls}">${e.action}</span></td><td><a href="${userUrl}" target="_blank">@${e.username}</a> <button class="shield-btn" title="Add to whitelist" onclick="addWL('${e.username}')">🛡️</button></td><td class="text-muted">${det}</td></tr>`;
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
      const userUrl=/^\\d+$/.test(e.username)?'https://x.com/i/user/'+e.username:'https://x.com/'+e.username;
      html+=`<tr><td class="checkbox-cell"><input type="checkbox" ${chk} onchange="toggleSel('${e.username}',this.checked)" style="width:auto"></td>
        <td><a href="${userUrl}" target="_blank">@${e.username}</a></td>
        <td><span class="badge ${cls}">${e.status}</span></td>
        <td class="text-muted">${e.source_list||''}</td>
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
      +d.usernames.map(u=>{const userUrl=/^\\d+$/.test(u)?'https://x.com/i/user/'+u:'https://x.com/'+u;return `<tr><td><a href="${userUrl}" target="_blank">@${u}</a></td><td><button class="btn btn-danger" style="font-size:11px;padding:2px 8px" onclick="removeWL('${u}')">Remove</button></td></tr>`}).join('')
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
