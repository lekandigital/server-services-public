#!/usr/bin/env python3
"""Regression test: recording a follow must never rewrite followed.csv.

A list reset (dashboard) deletes rows from followed.csv while the bot is mid-cycle. The
bot holds that file as a dataframe for the whole cycle, so if it wrote the frame back the
deleted rows would reappear and the re-queued handles would be skipped forever as
"already followed" — the reset would silently do nothing. This pins the append.

    python3 test_followed_append.py       (needs pandas; run it on the bot host)
"""

import ast
import csv
import os
import sys
import tempfile
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    print("SKIP: pandas not installed here — run this on the bot host")
    sys.exit(0)

PASSED = []


def check(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
    PASSED.append(label)
    print(f"  ok  {label}")


def load_record_followed():
    """Pull the function out of twitter_bot.py without importing it (selenium, drivers)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "twitter_bot.py")
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "record_followed")
    ns = {"pd": pd, "os": os, "csv": csv, "datetime": datetime}
    exec(ast.get_source_segment(src, fn), ns)
    return ns["record_followed"]


def users(path):
    with open(path) as f:
        return [r["username"] for r in csv.DictReader(f)]


def run():
    record_followed = load_record_followed()
    tmp = tempfile.mkdtemp(prefix="xbot-followed-")
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        with open("followed.csv", "w") as f:
            f.write("username,follow_timestamp\n")
            for u in ("alice", "bob", "carol"):
                f.write(f"{u},2026-09-01 08:00:00\n")

        # the bot loads the file once per cycle and keeps the frame
        df = pd.read_csv("followed.csv", parse_dates=["follow_timestamp"])
        check("bot starts the cycle holding every row", len(df) == 3)

        # mid-cycle, a list reset drops bob from the file on disk
        rows = list(csv.reader(open("followed.csv", newline="")))
        with open("followed.csv", "w", newline="") as f:
            csv.writer(f).writerows([r for r in rows if not (r and r[0] == "bob")])
        check("reset removed the row from disk", users("followed.csv") == ["alice", "carol"])

        # the bot now records a follow off its stale frame
        df = record_followed(df, "dave")
        check("the deleted row stays deleted", "bob" not in users("followed.csv"),
              str(users("followed.csv")))
        check("the new follow is appended", users("followed.csv") == ["alice", "carol", "dave"],
              str(users("followed.csv")))
        check("the in-memory frame still tracks the follow for this cycle",
              list(df["username"])[-1] == "dave" and len(df) == 4, str(list(df["username"])))
        check("the file stays readable as the bot reads it",
              len(pd.read_csv("followed.csv", parse_dates=["follow_timestamp"])) == 3)
        check("only one header row", open("followed.csv").read().count("username,follow") == 1)

        # a second follow in the same cycle must not re-add a header or lose anything
        df = record_followed(df, "erin")
        check("appending twice keeps both", users("followed.csv") == ["alice", "carol", "dave", "erin"],
              str(users("followed.csv")))

        os.remove("followed.csv")
        record_followed(pd.DataFrame(columns=["username", "follow_timestamp"]), "frank")
        check("a missing file is recreated with its header",
              open("followed.csv").readline().strip() == "username,follow_timestamp")
        check("and holds the follow", users("followed.csv") == ["frank"])
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\nFAILED after {len(PASSED)} checks: {e}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed")
