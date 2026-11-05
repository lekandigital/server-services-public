#!/usr/bin/env python3
"""Regression test for imports claiming people who are already in the queue.

The bug: importing a CSV skipped anyone already queued, so the list that introduced them
never got the row. Most of legacy_import_pre_queue turned out to be people who are also in
a named list, stranded at the bottom of the follow order behind it.

    python3 test_claim_on_import.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard  # noqa: E402

PASSED = []
HERE = os.path.dirname(os.path.abspath(__file__))


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
    PASSED.append(label)
    print(f"  ok  {label}")


def csv_bytes(rows):
    out = "username,id\n" + "".join(f"{u},{i}\n" for u, i in rows)
    return (io.BytesIO(out.encode()), "NEWLIST.csv")


def row_for(queue, username):
    return next((e for e in queue if e.get("username") == username), None)


def run():
    tmp = tempfile.mkdtemp(prefix="xb-claim-")
    prev = dashboard.DATA_DIR
    dashboard.DATA_DIR = tmp
    dashboard.app.config["TESTING"] = True
    try:
        queue = [
            # a big low-priority blob, like the legacy import
            {"username": "alice", "user_id": "101", "source_list": "legacy",
             "status": "pending_follow", "added_at": "2026-01-01T00:00:00"},
            # legacy rows carry a handle OR an id, never both — match must survive that
            {"username": "202", "source_list": "legacy",
             "status": "pending_follow", "added_at": "2026-01-01T00:00:00"},
            {"username": "carol", "user_id": "303", "source_list": "legacy",
             "status": "followed", "added_at": "2026-01-01T00:00:00",
             "timestamp": "2026-02-01T00:00:00"},
            # someone in a list the bot reaches FIRST must not be stolen
            {"username": "dave", "user_id": "404", "source_list": "hot",
             "status": "pending_follow", "added_at": "2026-01-01T00:00:00"},
            {"username": "frank", "user_id": "606", "source_list": "next",
             "status": "pending_follow", "added_at": "2026-01-01T00:00:00"},
        ] + [
            {"username": f"pad{i}", "user_id": str(9000 + i), "source_list": "legacy",
             "status": "pending_follow", "added_at": "2026-01-01T00:00:00"}
            for i in range(40)
        ] + [
            {"username": f"hot{i}", "user_id": str(7000 + i), "source_list": "hot",
             "status": "pending_follow", "added_at": "2026-01-01T00:00:00"}
            for i in range(3)
        ] + [
            # "Up next" — a fresh import slots in behind it, so it also keeps its people
            {"username": f"nxt{i}", "user_id": str(8000 + i), "source_list": "next",
             "status": "pending_follow", "added_at": "2026-01-01T00:00:00"}
            for i in range(3)
        ]
        json.dump(queue, open(os.path.join(tmp, "queue.json"), "w"))
        # the order the bot works them; a fresh list lands third, ahead of "legacy"
        json.dump({"order": ["hot", "next", "legacy"], "anchors": {}},
                  open(os.path.join(tmp, "list_priority.json"), "w"))

        with dashboard.app.test_client() as c:
            data = {"file": csv_bytes([("alice", "101"), ("bob", "202"),
                                       ("carol", "303"), ("dave", "404"),
                                       ("erin", "505"), ("frank", "606")]),
                    "source_names": json.dumps({"NEWLIST.csv": "NEWLIST"})}
            pv = c.post("/api/queue/import", data=data,
                        content_type="multipart/form-data").get_json()
            f0 = pv["files"][0]
            check("preview counts the claimable rows instead of calling them all duplicates",
                  pv["combined_claimable"] == 2 and f0["new"] == 1, json.dumps(f0))
            check("preview names the list they would be claimed from",
                  f0["claimed_from"] == {"legacy": 2}, str(f0["claimed_from"]))

            data = {"file": csv_bytes([("alice", "101"), ("bob", "202"),
                                       ("carol", "303"), ("dave", "404"),
                                       ("erin", "505"), ("frank", "606")]),
                    "source_names": json.dumps({"NEWLIST.csv": "NEWLIST"}),
                    "commit": "true"}
            res = c.post("/api/queue/import", data=data,
                         content_type="multipart/form-data").get_json()
            check("commit reports what it claimed",
                  res["total_added"] == 1 and res["total_claimed"] == 2, json.dumps(res))

        q = json.load(open(os.path.join(tmp, "queue.json")))
        check("no row was duplicated", len(q) == len(queue) + 1, f"{len(q)} vs {len(queue)+1}")
        check("a handle match moves off the low-priority list",
              row_for(q, "alice")["source_list"] == "NEWLIST",
              row_for(q, "alice")["source_list"])
        check("an id-only legacy row matches on its id",
              row_for(q, "202")["source_list"] == "NEWLIST",
              row_for(q, "202")["source_list"])
        check("the move leaves a breadcrumb",
              row_for(q, "alice")["claimed_from"] == "legacy")
        check("someone already followed is left where they are",
              row_for(q, "carol")["source_list"] == "legacy"
              and row_for(q, "carol")["status"] == "followed")
        check("a list the bot reaches sooner keeps its people",
              row_for(q, "dave")["source_list"] == "hot")
        check('...including the "up next" list a fresh import slots in behind',
              row_for(q, "frank")["source_list"] == "next")
        check("genuinely new people are still added",
              row_for(q, "erin")["source_list"] == "NEWLIST"
              and row_for(q, "erin")["status"] == "pending_follow")

        print("\n— backlog script —")
        plan = {"source_list": "legacy", "new_list": "leftovers",
                "moves": {"u:pad1": "hot", "i:9001": "hot",
                          "u:pad2": "leftovers", "i:9002": "leftovers",
                          "u:carol": "hot", "i:303": "hot"}}
        ppath = os.path.join(tmp, "plan.json")
        json.dump(plan, open(ppath, "w"))
        script = os.path.join(HERE, "reattribute_backlog.py")
        dry = subprocess.run([sys.executable, script, ppath, "--data-dir", tmp],
                             capture_output=True, text=True)
        check("dry run reports the moves and writes nothing",
              "2 rows would move" in dry.stdout
              and json.load(open(os.path.join(tmp, "queue.json"))) == q,
              dry.stdout + dry.stderr)
        app = subprocess.run([sys.executable, script, ppath, "--data-dir", tmp, "--apply"],
                             capture_output=True, text=True)
        q2 = json.load(open(os.path.join(tmp, "queue.json")))
        check("--apply moves exactly the planned rows",
              "moved 2 rows" in app.stdout, app.stdout + app.stderr)
        check("the plan can route a row into a brand-new list",
              row_for(q2, "pad2")["source_list"] == "leftovers")
        check("...and into an existing one",
              row_for(q2, "pad1")["source_list"] == "hot")
        check("the backlog pass will not re-file an already-followed row",
              row_for(q2, "carol")["source_list"] == "legacy")
        check("row count is unchanged by the backlog pass", len(q2) == len(q))
    finally:
        dashboard.DATA_DIR = prev
        shutil.rmtree(tmp, ignore_errors=True)


def run_requeue_clears_csvs():
    """Re-queue on upload must also drop the handles from followed.csv / unfollowed.csv.

    The bot filters every pending follow against both files, so before this a
    re-queued handle was reset to pending and then skipped forever as "already followed".
    """
    tmp = tempfile.mkdtemp(prefix="xb-requeue-")
    prev = dashboard.DATA_DIR
    dashboard.DATA_DIR = tmp
    dashboard.app.config["TESTING"] = True
    try:
        queue = [
            {"username": "gone1", "source_list": "old", "status": "unfollowed",
             "added_at": "2026-01-01T00:00:00", "timestamp": "2026-02-01T00:00:00"},
            {"username": "gone2", "source_list": "old", "status": "followed",
             "added_at": "2026-01-01T00:00:00", "timestamp": "2026-02-01T00:00:00",
             "skip_reason": "x", "retry_attempts": 2},
            {"username": "stays", "source_list": "old", "status": "unfollowed",
             "added_at": "2026-01-01T00:00:00", "timestamp": "2026-02-01T00:00:00"},
        ]
        with open(os.path.join(tmp, "queue.json"), "w") as f:
            json.dump(queue, f)
        with open(os.path.join(tmp, "followed.csv"), "w") as f:
            f.write("username,follow_timestamp\ngone1,2026-01-02 00:00:00\n"
                    "gone2,2026-01-02 00:00:00\nstays,2026-01-02 00:00:00\n")
        with open(os.path.join(tmp, "unfollowed.csv"), "w") as f:
            f.write("username,unfollow_timestamp\ngone1,2026-01-06 00:00:00\n"
                    "stays,2026-01-06 00:00:00\n")

        client = dashboard.app.test_client()
        r = client.post("/api/queue/import", content_type="multipart/form-data",
                        data={"file": (io.BytesIO(b"gone1\ngone2\n"), "again.txt"),
                              "commit": "true", "allow_requeue": "true"})
        d = r.get_json()
        check("both rows are re-queued", d.get("requeued") == 2, str(d))
        q2 = json.load(open(os.path.join(tmp, "queue.json")))
        check("re-queued rows are pending under the new list",
              all(row_for(q2, u)["status"] == "pending_follow" and row_for(q2, u)["source_list"] == "again"
                  for u in ("gone1", "gone2")))
        check("stale retry fields are dropped", "retry_attempts" not in row_for(q2, "gone2"))
        followed = dashboard._csv_username_set("followed.csv")
        unfollowed = dashboard._csv_username_set("unfollowed.csv")
        check("re-queued handles leave followed.csv", followed == {"stays"}, str(followed))
        check("re-queued handles leave unfollowed.csv", unfollowed == {"stays"}, str(unfollowed))
        check("the counts are reported", d.get("removed_followed") == 2 and d.get("removed_unfollowed") == 1, str(d))
    finally:
        dashboard.DATA_DIR = prev
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        run()
        run_requeue_clears_csvs()
    except AssertionError as e:
        print(f"\nFAILED after {len(PASSED)} checks: {e}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed")
