#!/usr/bin/env python3
"""Regression test for the list name shown against each row in History.

A row used to say only who was followed and when. The list it came from was in
queue.json all along, and a handle can sit in several imported lists at once, so the
dashboard now names the list it was actioned from and keeps the others behind a "+N".
These checks cover the backfill for rows written before the bot recorded the names,
the actioned-list-first ordering, and the CSV export column.

    python3 test_history_lists.py
"""

import csv
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard  # noqa: E402

PASSED = []


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
    PASSED.append(label)
    print(f"  ok  {label}")


def write(tmp, queue, history):
    with open(os.path.join(tmp, "queue.json"), "w") as f:
        json.dump(queue, f)
    with open(os.path.join(tmp, "history.jsonl"), "w") as f:
        for row in history:
            f.write(json.dumps(row) + "\n")


def by_user(entries):
    return {e["username"]: e for e in entries}


def run():
    tmp = tempfile.mkdtemp()
    prev_dir = dashboard.DATA_DIR
    dashboard.DATA_DIR = tmp
    try:
        print("\n— backfill from the queue —")

        # alice sits in two lists and was followed out of the second one; the retired row
        # is how the bot records the list it did not action her from.
        write(tmp, [
            {"username": "alice", "source_list": "VC Twitter", "status": "skipped",
             "skip_reason": "duplicate_actioned_from:AI Founders"},
            {"username": "alice", "source_list": "AI Founders", "status": "followed",
             "timestamp": "2026-09-20T10:00:00"},
            {"username": "bob", "source_list": "Solo Devs", "status": "followed",
             "timestamp": "2026-09-20T11:00:00"},
            {"username": "dave", "source_list": "", "status": "followed",
             "timestamp": "2026-09-20T13:00:00"},
            {"username": "erin", "source_list": "AI Founders", "claimed_from": "Old Backlog",
             "status": "followed", "timestamp": "2026-09-20T14:00:00"},
        ], [
            {"action": "follow", "username": "alice", "timestamp": "2026-09-20T10:00:00"},
            {"action": "follow", "username": "bob", "timestamp": "2026-09-20T11:00:00"},
            {"action": "follow", "username": "carol", "timestamp": "2026-09-20T12:00:00",
             "source_list": "New Import", "source_lists": ["New Import", "Old Import"]},
            {"action": "follow", "username": "dave", "timestamp": "2026-09-20T13:00:00"},
            {"action": "follow", "username": "erin", "timestamp": "2026-09-20T14:00:00"},
        ])

        rows = by_user(dashboard.read_history()[0])
        check("an old row learns its list from the queue",
              rows["alice"].get("source_list") == "AI Founders",
              rows["alice"].get("source_list"))
        check("the list it was actioned from leads, the other follows",
              rows["alice"].get("source_lists") == ["AI Founders", "VC Twitter"],
              str(rows["alice"].get("source_lists")))
        check("a handle in one list gets no +N to expand",
              rows["bob"].get("source_list") == "Solo Devs"
              and "source_lists" not in rows["bob"])
        check("a row the bot already labelled is left alone",
              rows["carol"].get("source_lists") == ["New Import", "Old Import"])
        check("a handle with no list anywhere stays blank, not guessed",
              not rows["dave"].get("source_list"))
        # One row, two lists: an import claimed this handle away from an earlier list, which
        # is how the live queue records a handle that belonged to more than one.
        check("a claimed row names the list it was claimed away from too",
              rows["erin"].get("source_lists") == ["AI Founders", "Old Backlog"],
              str(rows["erin"].get("source_lists")))

        print("\n— what the page and the export get —")

        client = dashboard.app.test_client()
        api = by_user(client.get("/api/history").get_json()["items"])
        check("the History API carries both names", api["alice"]["source_lists"] ==
              ["AI Founders", "VC Twitter"])
        activity = by_user(client.get("/api/activity").get_json()["items"])
        check("so does the activity feed the home page reads",
              activity["alice"].get("source_lists") == ["AI Founders", "VC Twitter"])

        export = client.get("/api/history/export").data.decode()
        exported = {r["username"]: r for r in csv.DictReader(io.StringIO(export))}
        check("the CSV export has a lists column",
              exported["alice"]["lists"] == "AI Founders; VC Twitter",
              exported["alice"]["lists"])
        check("...and no longer buries the list inside the details blob",
              "source_list" not in exported["alice"]["details"])

        print("\n— a page of history costs one queue read —")

        reads = []
        real_read_queue = dashboard.read_queue
        dashboard.read_queue = lambda: (reads.append(1), real_read_queue())[1]
        try:
            dashboard.read_history(limit=50)
            check("the queue is read once for the page, not once per row", len(reads) == 1,
                  f"{len(reads)} reads")
            reads.clear()
            # Every row already names its list: nothing to look up.
            write(tmp, [], [{"action": "follow", "username": "carol",
                             "timestamp": "2026-09-20T12:00:00", "source_list": "New Import"}])
            dashboard.read_history(limit=50)
            check("and not at all when every row already names its list", not reads)
        finally:
            dashboard.read_queue = real_read_queue
    finally:
        dashboard.DATA_DIR = prev_dir
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\nFAILED after {len(PASSED)} checks: {e}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed")
