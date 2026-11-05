#!/usr/bin/env python3
"""Regression test: a file that isn't a list of handles must not reach the bot.

On 2026-09-18 a Netscape cookie jar (x.com_cookies.txt) was uploaded as a follow list.
Every cookie line became a "handle", the bot later "unfollowed" them, and the g_state
line (JSON, full of commas) went into unfollowed.csv unquoted. pd.read_csv then rejected
the file and the bot crashed on every start. This pins each link of that chain:

  * the importer refuses a cookie jar outright and drops anything that isn't a handle
  * unfollowed.csv is written through the csv module, so a comma can't split a row
  * the bot's CSV reader skips a damaged line instead of dying on it

    python3 test_bad_rows.py       (the bot half needs pandas; run it on the bot host)
"""

import ast
import csv
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from twitter_csv import is_x_target, looks_like_cookie_jar  # noqa: E402

PASSED = []

# Shaped like the real export, with made-up values.
COOKIE_JAR = "\n".join([
    "# Netscape HTTP Cookie File",
    "# https://curl.haxx.se/rfc/cookie_spec.html",
    ".x.com\tTRUE\t/\tTRUE\t1824283242\tauth_token\t0123456789abcdef0123456789abcdef01234567",
    ".x.com\tTRUE\t/\tTRUE\t1824283243\tct0\tfeedfacefeedface",
    'x.com\tFALSE\t/\tFALSE\t1805275283\tg_state\t{"i_l":0,"i_ll":1789723283831,"i_b":"abc"}',
]) + "\n"
G_STATE = 'x.com\tfalse\t/\tfalse\t1805275283\tg_state\t{"i_l":0,"i_ll":1789723283831,"i_b":"abc"}'


def check(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
    PASSED.append(label)
    print(f"  ok  {label}")


def test_validators():
    for good in ("jack", "Jack_Dorsey", "a", "x" * 15, "12", "900000000000000009"):
        check(f"{good!r} is a target", is_x_target(good))
    for bad in ("", " ", "x" * 16, "has space", "dot.name", "@jack", G_STATE,
                ".x.com\tTRUE\t/\tTRUE\t0\tlang\ten", "<script>", 'a"b'):
        check(f"{bad[:20]!r} is not a target", not is_x_target(bad))
    check("a cookie jar is recognised", looks_like_cookie_jar(COOKIE_JAR))
    check("even without its header comment",
          looks_like_cookie_jar("\n".join(COOKIE_JAR.splitlines()[2:])))
    check("a plain handle list is not a cookie jar", not looks_like_cookie_jar("jack\nbiz\n@ev\n"))
    check("an XPorter CSV is not a cookie jar",
          not looks_like_cookie_jar("id,username,name\n12,jack,Jack\n13,biz,Biz\n"))


def test_import():
    import dashboard
    tmp = tempfile.mkdtemp(prefix="xb-badrows-")
    prev = dashboard.DATA_DIR
    dashboard.DATA_DIR = tmp
    dashboard.app.config["TESTING"] = True
    try:
        with open(os.path.join(tmp, "queue.json"), "w") as f:
            json.dump([], f)
        client = dashboard.app.test_client()

        r = client.post("/api/queue/import", content_type="multipart/form-data",
                        data={"file": (io.BytesIO(COOKIE_JAR.encode()), "x.com_cookies.txt"),
                              "commit": "true"})
        check("a cookie jar upload is refused", r.status_code == 400, str(r.status_code))
        check("and the refusal says why", "cookie" in r.get_json().get("error", ""),
              r.get_json().get("error", ""))
        check("and nothing reached the queue",
              json.load(open(os.path.join(tmp, "queue.json"))) == [])

        mixed = "jack\n@biz\nnot a handle\n" + G_STATE + "\n900000000000000009\n"
        r = client.post("/api/queue/import", content_type="multipart/form-data",
                        data={"file": (io.BytesIO(mixed.encode()), "mixed.txt")})
        d = r.get_json()
        f0 = d["files"][0]
        check("preview counts only real handles", f0["total_in_file"] == 3, str(f0))
        check("preview reports what it dropped", f0["invalid"] == 2, str(f0))

        r = client.post("/api/queue/import", content_type="multipart/form-data",
                        data={"file": (io.BytesIO(mixed.encode()), "mixed.txt"), "commit": "true"})
        names = sorted(e["username"] for e in json.load(open(os.path.join(tmp, "queue.json"))))
        check("commit imports only handles and ids",
              names == ["900000000000000009", "biz", "jack"], str(names))
    finally:
        dashboard.DATA_DIR = prev
        shutil.rmtree(tmp, ignore_errors=True)


def load_bot_functions(*names):
    """Pull functions out of twitter_bot.py without importing it (selenium, drivers)."""
    import pandas as pd
    src = open(os.path.join(HERE, "twitter_bot.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    ns = {"pd": pd, "os": os, "csv": csv, "datetime": datetime, "is_x_target": is_x_target,
          "_CSV_SKIP_WARNED": {}}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in names:
            exec(ast.get_source_segment(src, n), ns)
    return [ns[n] for n in names]


def test_bot_csv():
    try:
        import pandas as pd
    except ImportError:
        print("  SKIP bot CSV checks: pandas not installed here — run on the bot host")
        return
    record_unfollowed, load_action_csv = load_bot_functions("record_unfollowed", "load_action_csv")
    tmp = tempfile.mkdtemp(prefix="xb-badrows-bot-")
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        with open("unfollowed.csv", "w") as f:
            f.write("username,unfollow_timestamp\n")
            f.write("alice,2026-09-01 08:00:00.123456\n")
            f.write("bob,2026-09-01 09:00:00\n")  # str(datetime) drops a zero fraction
            # the exact damage the old bare f-string write produced
            f.write(f"{G_STATE},2026-09-27 05:13:44.598572\n")
        try:
            pd.read_csv("unfollowed.csv")
            old_reader_died = False
        except Exception:
            old_reader_died = True
        check("the fixture reproduces the crash", old_reader_died)

        df = load_action_csv("unfollowed.csv", "unfollow_timestamp")
        check("the new reader survives it", list(df["username"]) == ["alice", "bob"],
              str(list(df["username"])))
        check("both timestamp shapes parse", df["unfollow_timestamp"].notna().all(),
              str(df["unfollow_timestamp"].tolist()))

        record_unfollowed("carol")
        record_unfollowed('comma,"quote"')
        rows = list(csv.reader(open("unfollowed.csv", newline="")))
        check("a comma in a value stays inside one quoted field",
              rows[-1][0] == 'comma,"quote"' and len(rows[-1]) == 2, str(rows[-1]))
        df = load_action_csv("unfollowed.csv", "unfollow_timestamp")
        check("appended handles read back", "carol" in list(df["username"]), str(list(df["username"])))
        check("a non-handle never comes back as a username",
              'comma,"quote"' not in list(df["username"]))

        os.remove("unfollowed.csv")
        record_unfollowed("dave")
        check("a missing file is recreated with its header",
              open("unfollowed.csv").readline().strip() == "username,unfollow_timestamp")
        df = load_action_csv("missing.csv", "follow_timestamp")
        check("a missing file reads as empty", df.empty and list(df.columns) == ["username", "follow_timestamp"])
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_validators()
        test_import()
        test_bot_csv()
    except AssertionError as e:
        print(f"\nFAILED after {len(PASSED)} checks: {e}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed")
