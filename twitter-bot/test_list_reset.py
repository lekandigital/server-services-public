#!/usr/bin/env python3
"""Regression test for per-list "Follow again" resets (dashboard.py).

Exercises the real Flask routes against a throwaway data dir, because the parts that
can go wrong are the interactions between queue.json and the two CSVs the bot filters
follows against — not any single function.

    python3 test_list_reset.py
"""

import csv
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard  # noqa: E402

RESET_LOG = "list_resets.json"
PASSED = []


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
    PASSED.append(label)
    print(f"  ok  {label}")


def write_fixture(d):
    """One list that is finished, plus a second list still mid-flight.

    u1 finished (followed then unfollowed)        -> resettable
    u2 skipped with retry bookkeeping             -> resettable
    u3 follows us back, flagged by the bot        -> kept
    u4 followed, already in unfollowed.csv        -> held (pre-flag mutual or cleanup pass)
    u5 unfollowed here, still followed in listB   -> held (another list is working them)

    listC finished unfollowing minutes ago — unfollowing is the last thing the bot does
    to a list, so it is resettable straight away, with no waiting period.
    """
    recent = (datetime.now() - timedelta(minutes=5)).isoformat()
    # a row carrying a UTC offset — imports have produced these, and mixing them with the
    # bot's naive timestamps used to blow up /api/status
    recent_utc = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    queue = [
        {"username": "u1", "source_list": "listA", "status": "unfollowed",
         "timestamp": "2026-09-01T10:00:00", "added_at": "2026-08-01T00:00:00"},
        {"username": "u2", "source_list": "listA", "status": "skipped",
         "skip_reason": "not found/suspended", "timestamp": "2026-09-02T10:00:00",
         "retry_attempts": 2, "retry_not_before": "2026-09-03T00:00:00"},
        {"username": "u3", "source_list": "listA", "status": "followed",
         "follows_back": True, "follows_back_at": "2026-09-04T10:00:00",
         "timestamp": "2026-09-01T09:00:00"},
        {"username": "u4", "source_list": "listA", "status": "followed",
         "timestamp": "2026-08-20T09:00:00"},
        {"username": "u5", "source_list": "listA", "status": "unfollowed",
         "timestamp": "2026-09-05T09:00:00"},
        {"username": "u5", "source_list": "listB", "status": "followed",
         "timestamp": "2026-09-06T09:00:00"},
        {"username": "u6", "source_list": "listB", "status": "pending_follow", "timestamp": ""},
        {"username": "u7", "source_list": "listC", "status": "unfollowed", "timestamp": recent},
        {"username": "u8", "source_list": "listC", "status": "unfollowed",
         "timestamp": "2026-09-01T10:00:00"},
        {"username": "u9", "source_list": "listD", "status": "followed", "timestamp": recent_utc},
        {"username": "u11", "source_list": "listE", "status": "unfollowed",
         "timestamp": "2026-09-02T10:00:00"},
        {"username": "u12", "source_list": "listE", "status": "unfollowed",
         "timestamp": "2026-09-02T11:00:00"},
    ]
    with open(os.path.join(d, "queue.json"), "w") as f:
        json.dump(queue, f, indent=1)
    with open(os.path.join(d, "followed.csv"), "w") as f:
        f.write("username,follow_timestamp\n")
        for u, ts in [("u1", "2026-09-01 08:00:00"), ("u2", "2026-09-02 08:00:00"),
                      ("u3", "2026-09-01 07:00:00"), ("u4", "2026-08-20 07:00:00"),
                      ("u5", "2026-09-05 07:00:00"), ("u7", "2026-09-10 07:00:00"),
                      ("u8", "2026-09-01 07:00:00"), ("u9", "2026-09-10 07:00:00"),
                      ("u11", "2026-09-01 07:00:00"), ("u12", "2026-09-01 07:00:00")]:
            f.write(f"{u},{ts}\n")
    with open(os.path.join(d, "unfollowed.csv"), "w") as f:
        f.write("username,unfollow_timestamp\n")
        for u, ts in [("u1", "2026-09-01 10:00:00"), ("u3", "2026-09-04 10:00:00"),
                      ("u4", "2026-09-04 11:00:00"), ("u5", "2026-09-05 09:00:00"),
                      ("u7", "2026-09-17 10:00:00"), ("u8", "2026-09-01 10:00:00"),
                      ("u9", "2026-09-17 10:00:00"), ("u11", "2026-09-02 10:00:00"),
                      ("u12", "2026-09-02 11:00:00")]:
            f.write(f"{u},{ts}\n")


def csv_users(path):
    with open(path) as f:
        return [r["username"] for r in csv.DictReader(f)]


def run():
    tmp = tempfile.mkdtemp(prefix="xbot-reset-test-")
    try:
        write_fixture(tmp)
        dashboard.DATA_DIR = tmp
        # The real check shells out to pgrep; pin it so the test doesn't depend on
        # whether a bot happens to be running on this machine.
        dashboard.bot_is_running = lambda: (False, None, None)
        c = dashboard.app.test_client()

        sources = {s["name"]: s for s in c.get("/api/queue/sources").get_json()["sources"]}
        a, b = sources["listA"], sources["listB"]
        check("finished list is offered a reset", a["can_reset"] is True, str(a))
        check("mutual counted separately", a["mutuals"] == 1, str(a["mutuals"]))
        check("held rows counted (pre-flag mutual + in-flight elsewhere)",
              a["held"] == 2, str(a["held"]))
        check("resettable excludes mutual and held", a["resettable"] == 2, str(a["resettable"]))
        check("unfinished list is not offered a reset", b["can_reset"] is False, str(b))
        c_list = sources["listC"]
        check("a list that just finished unfollowing is resettable immediately",
              c_list["can_reset"] is True and c_list["resettable"] == 2, str(c_list))
        check("a timezone-aware timestamp doesn't break the status endpoint",
              c.get("/api/status").status_code == 200)
        check("block reason names what is outstanding",
              "still queued to follow" in (b["reset_blocked_reason"] or ""), str(b))

        before = open(os.path.join(tmp, "queue.json")).read()
        prev = c.post("/api/queue/sources/reset", json={"name": "listA", "preview": True}).get_json()
        check("preview reports what would happen",
              (prev["requeued"], prev["kept_mutual"], prev["kept_held"]) == (2, 1, 2), str(prev))
        check("preview changes nothing", open(os.path.join(tmp, "queue.json")).read() == before)

        r = c.post("/api/queue/sources/reset", json={"name": "listB"})
        check("reset of an unfinished list is refused", r.status_code == 409, str(r.get_json()))
        r = c.post("/api/queue/sources/reset", json={"name": "nope"})
        check("unknown list is a 404", r.status_code == 404, str(r.get_json()))
        check("a just-unfollowed list resets with no waiting period",
              c.post("/api/queue/sources/reset", json={"name": "listC"}).get_json().get("ok") is True)

        r = c.post("/api/queue/sources/reset", json={"name": "listA"})
        body = r.get_json()
        check("reset succeeds", r.status_code == 200 and body.get("ok"), str(body))
        check("reset reports 2 re-queued", body["requeued"] == 2, str(body))
        check("reset cleared 2 rows from followed.csv", body["removed_followed"] == 2, str(body))
        check("reset cleared 1 row from unfollowed.csv", body["removed_unfollowed"] == 1, str(body))

        queue = json.load(open(os.path.join(tmp, "queue.json")))
        rows = {(e["username"], e["source_list"]): e for e in queue}
        check("row count unchanged", len(queue) == 12, str(len(queue)))
        for u in ("u1", "u2"):
            e = rows[(u, "listA")]
            check(f"{u} is pending again", e["status"] == "pending_follow", str(e))
            check(f"{u} lost its timestamp", e["timestamp"] == "", str(e))
            check(f"{u} records the reset", e["reset_count"] == 1 and e.get("reset_at"), str(e))
        check("skip reason cleared", "skip_reason" not in rows[("u2", "listA")], str(rows[("u2", "listA")]))
        check("retry bookkeeping cleared",
              not any(k.startswith("retry_") for k in rows[("u2", "listA")]), str(rows[("u2", "listA")]))
        check("added_at preserved", rows[("u1", "listA")]["added_at"] == "2026-08-01T00:00:00")
        check("mutual untouched", rows[("u3", "listA")]["status"] == "followed"
              and rows[("u3", "listA")]["follows_back"] is True, str(rows[("u3", "listA")]))
        check("held rows untouched",
              rows[("u4", "listA")]["status"] == "followed"
              and rows[("u5", "listA")]["status"] == "unfollowed")
        check("other list untouched", rows[("u6", "listB")]["status"] == "pending_follow")

        check("followed.csv keeps only the protected handles",
              csv_users(os.path.join(tmp, "followed.csv")) == ["u3", "u4", "u5", "u9", "u11", "u12"],
              str(csv_users(os.path.join(tmp, "followed.csv"))))
        check("unfollowed.csv keeps only the protected handles",
              csv_users(os.path.join(tmp, "unfollowed.csv")) == ["u3", "u4", "u5", "u9", "u11", "u12"],
              str(csv_users(os.path.join(tmp, "unfollowed.csv"))))
        check("a backup of each CSV was written",
              os.path.exists(os.path.join(tmp, "followed.csv.pre-reset.bak"))
              and os.path.exists(os.path.join(tmp, "unfollowed.csv.pre-reset.bak")))

        log = json.load(open(os.path.join(tmp, RESET_LOG)))
        check("every reset is logged in order",
              [r["name"] for r in log] == ["listC", "listA"], str(log))
        after = {s["name"]: s for s in c.get("/api/queue/sources").get_json()["sources"]}["listA"]
        check("list shows as unfinished again", after["pending"] == 2 and after["pct"] < 100, str(after))
        check("list remembers when it was reset", bool(after["last_reset"]), str(after))

        r = c.post("/api/queue/sources/reset", json={"name": "listA"})
        check("a second reset is refused while the re-queued follows are pending",
              r.status_code == 409, str(r.get_json()))

        # Safe with the bot up: it appends to followed.csv instead of rewriting it, and
        # queue.json writes serialise through queue_store's lock.
        dashboard.bot_is_running = lambda: (True, 4242, "run")
        r = c.post("/api/queue/sources/reset", json={"name": "listD", "preview": True})
        check("preview works while the bot is running", r.status_code == 200, str(r.get_json()))
        r = c.post("/api/queue/sources/reset", json={"name": "listE"})
        check("a reset runs while the bot is running, without forcing",
              r.get_json().get("ok") is True and r.get_json()["requeued"] == 2, str(r.get_json()))
        check("it still cleared the CSV rows under the running bot",
              "u11" not in csv_users(os.path.join(tmp, "followed.csv")), str(csv_users(os.path.join(tmp, "followed.csv"))))
        check("the log records that the bot was up",
              json.load(open(os.path.join(tmp, RESET_LOG)))[-1]["bot_was_running"] is True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\nFAILED after {len(PASSED)} checks: {e}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed")
