#!/usr/bin/env python3
"""Re-file queue rows that are stranded in the list they were first seen in.

Why this exists: until the import path learned to claim (dashboard.import_batch_to_queue),
importing a CSV *skipped* anyone already in the queue. One row per person is right, but it
meant the list that actually introduced them never got the row — so most of
`legacy_import_pre_queue` is people who also appear in a named list and are sitting behind
it in the follow order, at the very bottom.

This applies a plan file built off the original export CSVs:

    {"source_list": "legacy_import_pre_queue",
     "new_list": "legacy_overlap_finished_lists",
     "moves": {"u:handle": "target list", "i:1234567": "target list", ...}}

Keys are twitter_csv.identity_key_set keys, so a row matches on its handle or its numeric
id — the legacy rows carry one or the other, never both, which is exactly why the match has
to be done on keys rather than on usernames.

Nothing is created, deleted or duplicated: matched rows change `source_list` and gain a
`claimed_from` breadcrumb. Only `pending_follow` rows move — someone already followed or
unfollowed is history, and re-filing them would restate what each list achieved.

    python3 reattribute_backlog.py plan.json            # dry run, prints what would move
    python3 reattribute_backlog.py plan.json --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime

from queue_store import queue_transaction, read_queue
from twitter_csv import identity_key_set


def plan_moves(queue, moves, source_list):
    """[(row, target)] for every pending row in `source_list` the plan names."""
    out = []
    for entry in queue:
        if entry.get("status") != "pending_follow":
            continue
        if source_list and (entry.get("source_list") or "") != source_list:
            continue
        target = None
        for key in identity_key_set(entry.get("username"), entry.get("user_id")):
            if key in moves:
                target = moves[key]
                break
        if target and target != (entry.get("source_list") or ""):
            out.append((entry, target))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", help="JSON plan file")
    ap.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    ap.add_argument("--data-dir", default=".")
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    moves = plan.get("moves") or {}
    source_list = plan.get("source_list") or ""
    if not moves:
        print("plan has no moves")
        return 1

    if not args.apply:
        pending = plan_moves(read_queue(args.data_dir), moves, source_list)
        dest = Counter(t for _, t in pending)
        print(f"dry run: {len(pending):,} rows would move out of {source_list or 'any list'}")
        for name, count in dest.most_common():
            print(f"  +{count:>6,}  {name}")
        print("\nre-run with --apply to write it")
        return 0

    stamp = datetime.now().isoformat()
    moved = Counter()
    with queue_transaction(args.data_dir) as queue:
        if not queue:
            print("queue.json is empty or missing — nothing done")
            return 1
        for entry, target in plan_moves(queue, moves, source_list):
            entry["claimed_from"] = entry.get("source_list")
            entry["source_list"] = target
            entry["claimed_at"] = stamp
            moved[target] += 1
    print(f"moved {sum(moved.values()):,} rows into {len(moved)} lists")
    for name, count in moved.most_common():
        print(f"  +{count:>6,}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
