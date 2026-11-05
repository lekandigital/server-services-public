#!/usr/bin/env python3
"""Regression test: a follow past unfollow_after_days is unfollowed in either mode.

Normal mode used to unfollow only once the following difference passed 430. After an
unfollow-only run had drained the difference, switching back to normal mode left every
overdue follow in place for days, until following alone pushed the difference back over
430. The bot also ignored the dashboard's "Unfollow After (days)" and used 3.

This drives the real main loop with X stubbed out and checks who it unfollows, in what
order, and when it follows in between: once per 6 unfollows while clearing a backlog
(follows still there a day after they came due), otherwise one unfollow per follow. Holding
follows whenever 2+ were due cut following to ~1/hour for most of a day after every busy
follow day, with nothing wrong.

    python3 test_unfollow_schedule.py       (needs pandas + selenium; run it on the bot host)
"""

import csv
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

try:
    import pandas  # noqa: F401
    import selenium  # noqa: F401
except ImportError:
    print("SKIP: pandas/selenium not installed here — run this on the bot host")
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import twitter_bot as bot  # noqa: E402

PASSED = []


def check(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
    PASSED.append(label)
    print(f"  ok  {label}")


def ago(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.%f")


# handle -> days since we followed them
OLD = [f"old{i}" for i in range(1, 8)]     # backlog: due over a day ago, oldest first
RECENT = [f"due{i}" for i in range(1, 4)]  # came due within the last day
FOLLOWED = {
    "mutual": 6.5,      # profile shows "Follows you"
    **{u: 6 - 0.1 * i for i, u in enumerate(OLD)},
    **{u: 4.5 - 0.1 * i for i, u in enumerate(RECENT)},
    "fresh1": 3.5,      # due under the old hard-coded 3 days, not under the 4-day setting
    "fresh2": 1,
    "keeper": 10,       # whitelisted
    "gone": 7,          # already in unfollowed.csv
}
PENDING = [f"p{i}" for i in range(1, 8)]
DUE = ["U:mutual"] + [f"U:{u}" for u in OLD + RECENT]


class FakeDriver:
    def quit(self):
        pass


def setup_dir(config, followed):
    tmp = tempfile.mkdtemp(prefix="xbot-unfollow-")
    os.chdir(tmp)
    if config is not None:
        with open("bot_config.json", "w") as f:
            json.dump(config, f)
    with open("followed.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["username", "follow_timestamp"])
        for u, days in followed.items():
            w.writerow([u, ago(days)])
    with open("unfollowed.csv", "w", newline="") as f:
        csv.writer(f).writerows([["username", "unfollow_timestamp"], ["gone", ago(1)]])
    with open("whitelist.txt", "w") as f:
        f.write("keeper\n")
    queue = [{"username": u, "status": "followed", "source_list": "list-a", "timestamp": ago(d)}
             for u, d in followed.items()]
    queue += [{"username": u, "status": "pending_follow", "source_list": "list-b",
               "followers_count": i} for i, u in enumerate(PENDING)]
    with open("queue.json", "w") as f:
        json.dump(queue, f)
    return tmp


def run_bot(mode, cycles, config={"unfollow_after_days": 4}, unfollow_allowed=True, followed=FOLLOWED):
    """Run main() for `cycles` loop passes; return the actions in order."""
    cwd = os.getcwd()
    setup_dir(config, followed)
    actions = []
    passes = {"n": 0}

    def session_alive(_driver):
        passes["n"] += 1
        if passes["n"] > cycles:
            raise KeyboardInterrupt
        return True

    def fake_unfollow(_driver, username, via_id=False):
        actions.append(f"U:{username}")
        return "follows_back" if username == "mutual" else True

    def fake_follow(_driver, username, _like_limiter):
        actions.append(f"F:{username}")
        return True

    def can_perform(limiter):
        return unfollow_allowed or limiter.action_type != "unfollow"

    saved = {name: getattr(bot, name) for name in (
        "setup_driver", "is_driver_alive", "load_cookies_from_file", "safe_get",
        "check_session_alive", "ask_cleanup_mode", "unfollow_user", "follow_user",
        "smart_break")}
    saved_can_perform, saved_sleep, saved_argv = bot.RateLimit.can_perform, time.sleep, sys.argv
    try:
        bot.setup_driver = FakeDriver
        bot.is_driver_alive = lambda _d: True
        bot.load_cookies_from_file = lambda *_a, **_k: None
        bot.safe_get = lambda *_a, **_k: True
        bot.check_session_alive = session_alive
        bot.ask_cleanup_mode = lambda: {"enabled": False, "dry_run": False}
        bot.unfollow_user = fake_unfollow
        bot.follow_user = fake_follow
        bot.smart_break = lambda *_a, **_k: None
        bot.RateLimit.can_perform = can_perform
        time.sleep = lambda _s: None
        sys.argv = ["twitter_bot.py", mode]
        bot.main()
    finally:
        for name, fn in saved.items():
            setattr(bot, name, fn)
        bot.RateLimit.can_perform, time.sleep, sys.argv = saved_can_perform, saved_sleep, saved_argv
        result_dir = os.getcwd()
        os.chdir(cwd)
    return actions, result_dir


def csv_users(path):
    with open(path, newline="") as f:
        return [r["username"] for r in csv.DictReader(f)]


def queue_status(result_dir):
    with open(os.path.join(result_dir, "queue.json")) as f:
        return {e["username"]: e for e in json.load(f)}


def test_normal_mode_below_threshold():
    actions, d = run_bot("run", cycles=11)
    unfollows = [a for a in actions if a.startswith("U:")]
    check("normal mode unfollows overdue follows far below the 430 threshold",
          unfollows == DUE, str(actions))
    # A mutual costs no unfollow, so its cycle follows as usual. Then, with a backlog, one
    # follow per 6 unfollows; once at most one backlog follow is left, a follow every cycle
    # again, including while the ones that came due within the day are unfollowed.
    check("oldest first, following once per 6 unfollows while catching up",
          actions == ["U:mutual", "F:p1",
                      "U:old1", "U:old2", "U:old3", "U:old4", "U:old5", "U:old6", "F:p2",
                      "U:old7", "F:p3", "U:due1", "F:p4", "U:due2", "F:p5", "U:due3", "F:p6"],
          str(actions))
    check("nothing younger than unfollow_after_days is touched",
          not {"U:fresh1", "U:fresh2"} & set(actions), str(actions))
    check("whitelisted and already-unfollowed handles are left alone",
          not {"U:keeper", "U:gone"} & set(actions), str(actions))
    unf = csv_users(os.path.join(d, "unfollowed.csv"))
    check("each unfollow is recorded", set(OLD) | {"mutual"} <= set(unf), str(unf))
    q = queue_status(d)
    check("queue rows marked unfollowed", all(q[u]["status"] == "unfollowed" for u in OLD))
    check("a mutual is flagged, not unfollowed", q["mutual"].get("follows_back") and q["mutual"]["status"] == "followed",
          str(q["mutual"]))


def test_due_together_is_not_a_backlog():
    # A busy follow day four days ago: many come due together, none a day overdue yet.
    batch = [f"batch{i}" for i in range(1, 7)]
    followed = {**{u: 4.9 - 0.1 * i for i, u in enumerate(batch)}, "fresh2": 1}
    actions, _ = run_bot("run", cycles=7, followed=followed)
    check("follows that merely came due are unfollowed one per follow",
          actions == [a for u, p in zip(batch, PENDING) for a in (f"U:{u}", f"F:{p}")] + ["F:p7"],
          str(actions))


def test_unfollow_only_mode():
    actions, _ = run_bot("unfollow", cycles=13)
    check("unfollow-only mode unfollows the same overdue set, and never follows",
          actions == DUE, str(actions))


def test_reads_dashboard_setting():
    actions, _ = run_bot("run", cycles=14, config={"unfollow_after_days": 3})
    check("a 3-day setting also unfollows the 3.5-day-old follow", "U:fresh1" in actions, str(actions))
    check("still not the 1-day-old one", "U:fresh2" not in actions, str(actions))
    actions, _ = run_bot("run", cycles=13, config=None)
    check("no bot_config.json falls back to 4 days",
          [a for a in actions if a.startswith("U:")] == DUE, str(actions))


def test_rate_limited_unfollows():
    # The wait branch used to read eligible_unfollows before anything had assigned it.
    actions, _ = run_bot("unfollow", cycles=3, unfollow_allowed=False)
    check("unfollow-only mode survives a rate-limited first pass", actions == [], str(actions))
    actions, _ = run_bot("run", cycles=3, unfollow_allowed=False)
    check("a rate-limited unfollow does not block following",
          actions == ["F:p1", "F:p2", "F:p3"], str(actions))


if __name__ == "__main__":
    try:
        test_normal_mode_below_threshold()
        test_due_together_is_not_a_backlog()
        test_unfollow_only_mode()
        test_reads_dashboard_setting()
        test_rate_limited_unfollows()
    except AssertionError as e:
        print(f"\nFAILED after {len(PASSED)} checks: {e}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed")
