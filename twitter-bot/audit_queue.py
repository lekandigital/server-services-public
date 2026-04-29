#!/usr/bin/env python3
"""
audit_queue.py — Scan queue.json, followed.csv, unfollowed.csv for redundancies.

Reports duplicate usernames, overlap with CSV logs, cross-source pending overlap,
and (optional) CSV file scans using structured parsing from twitter_csv.py.

Usage:
  python3 audit_queue.py                          # audit ./ (cwd should be data dir)
  python3 audit_queue.py --data-dir /home/REDACTED_USER/xb
  python3 audit_queue.py --scan-csvs a.csv b.csv
  python3 audit_queue.py --fix                  # dedupe queue.json + reconcile statuses (backup first)
"""

import argparse
import csv
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime

from twitter_csv import identity_key_set, parse_csv_export_rows
from queue_dedupe import merge_identity_duplicates


def load_queue(data_dir):
    path = os.path.join(data_dir, "queue.json")
    if not os.path.exists(path):
        print(f"  queue.json not found at {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_csv_users(filepath):
    if not os.path.exists(filepath):
        return []
    results = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            u = row.get("username", "").strip().lower()
            if u:
                results.append((u, i))
    return results


def parse_any_csv(filepath):
    """Parse CSV via twitter_csv (same rules as dashboard import)."""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        return parse_csv_export_rows(f.read())


def audit_server_data(data_dir, fix=False):
    """Full audit of queue.json + followed.csv + unfollowed.csv."""
    print(f"\n{'='*60}")
    print(f"  QUEUE AUDIT — {data_dir}")
    print(f"{'='*60}\n")

    queue = load_queue(data_dir)
    print(f"queue.json: {len(queue)} entries")

    statuses = Counter(e.get("status", "unknown") for e in queue)
    for s, c in statuses.most_common():
        print(f"  {s}: {c}")

    q_usernames = [e.get("username", "").lower() for e in queue]
    q_counts = Counter(q_usernames)
    q_dupes = {u: c for u, c in q_counts.items() if c > 1 and u}
    print(f"\nDuplicate usernames in queue.json: {len(q_dupes)}")
    if q_dupes:
        for u, c in sorted(q_dupes.items(), key=lambda x: -x[1])[:25]:
            entries = [e for e in queue if e.get("username", "").lower() == u]
            sources = set(e.get("source_list", "?") for e in entries)
            stats = [e.get("status", "?") for e in entries]
            print(f"  {u}: {c}x — sources={sources} statuses={stats}")

    key_dupes = defaultdict(list)
    for i, e in enumerate(queue):
        for k in identity_key_set(e.get("username"), e.get("user_id")):
            key_dupes[k].append(i)
    merged_key_dupes = {k: ix for k, ix in key_dupes.items() if len(ix) > 1}
    print(f"\nOverlapping identity keys (username/id): {len(merged_key_dupes)} keys")
    if merged_key_dupes:
        for k, ix in sorted(merged_key_dupes.items())[:15]:
            print(f"  {k}: rows {ix}")

    id_entries = [e for e in queue if e.get("username", "").isdigit()]
    print(f"\nEntries using numeric user IDs as username: {len(id_entries)}")
    for e in id_entries[:15]:
        print(f"  id={e['username']} status={e.get('status')} source={e.get('source_list', '?')}")

    f_path = os.path.join(data_dir, "followed.csv")
    followed_rows = load_csv_users(f_path)
    followed_set = set(u for u, _ in followed_rows)
    f_counts = Counter(u for u, _ in followed_rows)
    f_dupes = {u: c for u, c in f_counts.items() if c > 1}
    print(f"\nfollowed.csv: {len(followed_rows)} rows, {len(followed_set)} unique")
    if f_dupes:
        print(f"  Duplicate rows: {len(f_dupes)} users appear more than once")
        for u, c in sorted(f_dupes.items(), key=lambda x: -x[1])[:10]:
            print(f"    {u}: {c}x")

    u_path = os.path.join(data_dir, "unfollowed.csv")
    unf_rows = load_csv_users(u_path)
    unf_set = set(u for u, _ in unf_rows)
    u_counts = Counter(u for u, _ in unf_rows)
    u_dupes = {u: c for u, c in u_counts.items() if c > 1}
    print(f"\nunfollowed.csv: {len(unf_rows)} rows, {len(unf_set)} unique")
    if u_dupes:
        print(f"  Duplicate rows: {len(u_dupes)} users appear more than once")
        for u, c in sorted(u_dupes.items(), key=lambda x: -x[1])[:10]:
            print(f"    {u}: {c}x")

    pending = [e for e in queue if e.get("status") == "pending_follow"]
    pending_also_followed = [e for e in pending if e.get("username", "").lower() in followed_set]
    pending_also_unfollowed = [e for e in pending if e.get("username", "").lower() in unf_set]
    print(f"\npending_follow also in followed.csv: {len(pending_also_followed)}")
    print(f"pending_follow also in unfollowed.csv: {len(pending_also_unfollowed)}")

    sources = defaultdict(lambda: defaultdict(int))
    for e in queue:
        src = e.get("source_list", "unknown")
        status = e.get("status", "unknown")
        sources[src][status] += 1
        sources[src]["_total"] += 1

    print(f"\nSources ({len(sources)}):")
    for src in sorted(sources.keys(), key=lambda s: sources[s].get("pending_follow", 0)):
        info = sources[src]
        total = info["_total"]
        pend = info.get("pending_follow", 0)
        foll = info.get("followed", 0)
        unfoll = info.get("unfollowed", 0)
        skip = info.get("skipped", 0)
        print(f"  {src}: {total} total | {pend} pending | {foll} followed | {unfoll} unfollowed | {skip} skipped")

    pending_by_source = defaultdict(set)
    for e in pending:
        src = e.get("source_list", "unknown")
        pending_by_source[src].add(e.get("username", "").lower())

    src_names = sorted(pending_by_source.keys())
    overlaps_found = False
    for i, s1 in enumerate(src_names):
        for s2 in src_names[i + 1 :]:
            overlap = pending_by_source[s1] & pending_by_source[s2]
            if overlap:
                if not overlaps_found:
                    print(f"\nCross-source overlap (pending entries, by username):")
                    overlaps_found = True
                print(f"  {s1} ∩ {s2}: {len(overlap)} users")
    if not overlaps_found:
        print(f"\nNo cross-source overlap among pending entries (by username).")

    if fix:
        print(f"\n{'='*60}")
        print("  FIXING REDUNDANCIES")
        print(f"{'='*60}")

        qpath = os.path.join(data_dir, "queue.json")
        backup = qpath + f".backup-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(qpath, backup)
        print(f"  Backed up queue.json -> {backup}")

        original_len = len(queue)

        deduped = merge_identity_duplicates(queue)

        removed_dupes = len(queue) - len(deduped)

        corrected_followed = 0
        corrected_unfollowed = 0
        for e in deduped:
            if e.get("status") == "pending_follow":
                ul = e.get("username", "").lower()
                if ul in unf_set:
                    e["status"] = "unfollowed"
                    e["timestamp"] = e.get("timestamp") or datetime.now().isoformat()
                    corrected_unfollowed += 1
                elif ul in followed_set:
                    e["status"] = "followed"
                    e["timestamp"] = e.get("timestamp") or datetime.now().isoformat()
                    corrected_followed += 1

        tmp = qpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(deduped, f, indent=1)
        os.replace(tmp, qpath)

        print(f"  Removed {removed_dupes} duplicate rows ({original_len} -> {len(deduped)})")
        print(f"  Corrected {corrected_followed} pending->followed (already in followed.csv)")
        print(f"  Corrected {corrected_unfollowed} pending->unfollowed (already in unfollowed.csv)")

    print()


def scan_csvs(csv_paths, data_dir=None):
    """Cross-file duplicate detection using identity keys."""
    print(f"\n{'='*60}")
    print("  CSV CROSS-FILE SCAN")
    print(f"{'='*60}\n")

    key_occurrences = defaultdict(list)

    for path in csv_paths:
        if not os.path.exists(path):
            print(f"  SKIP: {path} not found")
            continue

        rows = parse_any_csv(path)
        basename = os.path.basename(path)
        print(f"  {basename}: {len(rows)} parsed rows")

        for row in rows:
            if not row.get("username") and not row.get("user_id"):
                continue
            bucket = row.get("username") or ("i:" + row["user_id"])
            keys = identity_key_set(row.get("username"), row.get("user_id"))
            for k in keys:
                key_occurrences[k].append(
                    {"file": basename, "bucket": bucket, "followers": row.get("followers_count")}
                )

    multi_key = {k: v for k, v in key_occurrences.items() if len(v) > 1}
    print(f"\n  Identity keys appearing more than once (within/across files): {len(multi_key)}")
    shown = 0
    for k in sorted(multi_key.keys()):
        entries = multi_key[k]
        files = set(e["file"] for e in entries)
        if len(files) > 1:
            print(f"    {k}: cross-file ({len(entries)} occurrences)")
            shown += 1
            if shown >= 25:
                break

    if data_dir:
        qpath = os.path.join(data_dir, "queue.json")
        if os.path.exists(qpath):
            queue = load_queue(data_dir)
            pending_keys = set()
            all_keys = set()
            for e in queue:
                ks = identity_key_set(e.get("username"), e.get("user_id"))
                all_keys |= ks
                if e.get("status") == "pending_follow":
                    pending_keys |= ks

            csv_keys = set(key_occurrences.keys())

            print(f"\n  Overlap with queue.json (identity keys):")
            print(f"    CSV keys also in queue (any status): {len(csv_keys & all_keys)}")
            print(f"    CSV keys also pending_follow: {len(csv_keys & pending_keys)}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Audit twitter bot queue for redundancies")
    parser.add_argument("--data-dir", default=".", help="Path to bot data directory")
    parser.add_argument("--scan-csvs", nargs="+", help="Scan local CSV files for duplicates")
    parser.add_argument("--fix", action="store_true", help="Dedupe queue.json and reconcile vs CSVs (backup)")
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir)

    if args.scan_csvs:
        scan_csvs(args.scan_csvs, data_dir=data_dir if os.path.isdir(data_dir) else None)
    else:
        audit_server_data(data_dir, fix=args.fix)


if __name__ == "__main__":
    main()
