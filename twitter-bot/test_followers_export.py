#!/usr/bin/env python3
"""Regression tests for the followers exporter and followback reconciler.

No network. The pager is driven through the real run_export() with a stubbed page
fetcher, because the parts that can go wrong are the interactions — stop rules against
saved state, reconcile against queue.json rows the bot wrote — not any single function.

    python3 test_followers_export.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import queue_store  # noqa: E402
import x_followers_export as fx  # noqa: E402

PASSED = []


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
    PASSED.append(label)
    print(f"  ok  {label}")


class Args:
    """Stands in for argparse's namespace."""

    def __init__(self, **kw):
        self.run = True
        self.probe = False
        self.full = False
        self.reconcile_only = False
        self.dry_run = False
        self.dry_run_queue = False
        self.page_size = 10
        self.max_pages = 0
        self.max_runtime = 600
        self.data_dir = None
        self.target_user_id = None
        self.cookies = fx.COOKIE_FILE
        self.__dict__.update(kw)


NETSCAPE = """# Netscape HTTP Cookie File
# This is a generated file! Do not edit.

.x.com\tTRUE\t/\tFALSE\t1824283242\tauth_token\tAUTHVALUE
.x.com\tTRUE\t/\tFALSE\t1824283243\tct0\tCT0VALUE
.x.com\tTRUE\t/\tFALSE\t1821298483\ttwid\tu%3D1000000000000000001
.x.com\tTRUE\t/\tFALSE\t1821298479\tcf_clearance\tSHOULDBEDROPPED
x.com\tTRUE\t/\tFALSE\t0\tlang\ten
"""


def user(i, handle=None):
    return {
        "id_str": str(1000 + i),
        "screen_name": handle or f"user{i}",
        "name": f"User {i}",
        "description": "line one\nline two",
        "followers_count": 10 + i,
        "friends_count": 5,
        "statuses_count": 3,
        "listed_count": 0,
        "protected": False,
        "created_at": "Tue Aug 13 16:41:22 +0000 2013",
        "profile_image_url_https": "https://pbs.twimg.com/x_normal.jpg",
    }


def setup_dir(queue=None, cookies=NETSCAPE):
    d = tempfile.mkdtemp(prefix="fxtest-")
    os.chdir(d)
    with open(fx.COOKIE_FILE, "w") as f:
        f.write(cookies)
    with open("queue.json", "w") as f:
        json.dump(queue if queue is not None else [], f, indent=1)
    with open("bot_config.json", "w") as f:
        json.dump({"unfollow_after_days": 4}, f)
    return d


def stub_network(pages, monkey):
    """pages: list of (rows, next_cursor). Returns a call log."""
    calls = []

    def fake_fetch(session, user_id, cursor=None, count=fx.DEFAULT_PAGE_SIZE, timeout=30):
        calls.append({"cursor": cursor, "count": count})
        index = len(calls) - 1
        if index >= len(pages):
            return [], None, {"status": 200, "limit": 15, "remaining": 14,
                              "reset_at": None, "elapsed": 0.1, "returned": 0}
        raw, next_cursor = pages[index]
        rows = [fx.parse_row(u) for u in raw]
        meta = {"status": 200, "limit": 15, "remaining": 14, "reset_at": None,
                "elapsed": 0.1, "returned": len(rows)}
        return rows, next_cursor, meta

    monkey["fetch"] = fx.fetch_followers_page
    monkey["account"] = fx.fetch_account
    monkey["session"] = fx.build_session
    monkey["egress"] = fx.egress_ok
    monkey["sleep"] = fx.time.sleep

    fx.fetch_followers_page = fake_fetch
    fx.fetch_account = lambda s, uid: {"screen_name": "botaccount",
                                       "followers_count": monkey.get("reported", 0)}
    fx.build_session = lambda cookies, screen_name=None: object()
    fx.egress_ok = lambda: True
    fx.time.sleep = lambda s: None
    return calls


def restore(monkey):
    fx.fetch_followers_page = monkey["fetch"]
    fx.fetch_account = monkey["account"]
    fx.build_session = monkey["session"]
    fx.egress_ok = monkey["egress"]
    fx.time.sleep = monkey["sleep"]


# ============================================================

def test_cookies():
    print("\ncookie handling")
    d = setup_dir()
    cookies, expiries = fx.load_cookie_file(fx.COOKIE_FILE)
    check("Netscape jar parses", cookies.get("auth_token") == "AUTHVALUE")
    check("ct0 read", cookies.get("ct0") == "CT0VALUE")
    check("cf_clearance is dropped", "cf_clearance" not in cookies,
          "it is bound to the issuing IP/UA and can provoke a challenge from another")
    check("zero-expiry cookies carry no expiry", "lang" not in expiries)
    check("auth_token expiry is kept", expiries.get("auth_token") == 1824283242.0)
    check("twid decodes to the account id",
          fx.user_id_from_twid(cookies["twid"]) == "1000000000000000001")

    as_json = json.dumps([{"name": "ct0", "value": "J", "expirationDate": 123.0},
                          {"name": "cf_clearance", "value": "x"}])
    with open("alt.json", "w") as f:
        f.write(as_json)
    cookies, expiries = fx.load_cookie_file("alt.json")
    check("Chrome JSON array parses too", cookies == {"ct0": "J"})
    check("expiry read from JSON", expiries == {"ct0": 123.0})


def test_preflight_identity():
    print("\nsession identity vs target account")
    d = setup_dir()
    # A bot jar naming a DIFFERENT account than the export session.
    with open(fx.BOT_COOKIE_FILE, "w") as f:
        json.dump([{"name": "twid", "value": "u%3D5555555555"},
                   {"name": "ct0", "value": "c"}, {"name": "auth_token", "value": "a"}], f)
    cookies, expiries, target, session_id = fx.preflight(Args())
    check("target comes from the bot's jar, not the export session", target == "5555555555",
          f"got {target}")
    check("session id comes from the export cookies",
          session_id == "1000000000000000001", f"got {session_id}")

    os.remove(fx.BOT_COOKIE_FILE)
    cookies, expiries, target, session_id = fx.preflight(Args())
    check("with no bot jar it falls back to the session's own account",
          target == session_id == "1000000000000000001")

    cookies, expiries, target, session_id = fx.preflight(Args(target_user_id="42"))
    check("--target-user-id wins", target == "42")

    with open(fx.COOKIE_FILE, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
    try:
        fx.preflight(Args())
        check("a jar with no ct0/auth_token is refused", False)
    except SystemExit as e:
        check("a jar with no ct0/auth_token is refused", e.code == fx.EXIT_CONFIG)


def test_parse_row():
    print("\nrow parsing")
    raw = user(1)
    raw["entities"] = {"url": {"urls": [{"expanded_url": "https://real.example"}]}}
    raw["following"] = True
    row = fx.parse_row(raw)
    check("newlines are stripped from the bio", "\n" not in row["bio"])
    check("expanded url preferred over t.co", row["url"] == "https://real.example")
    check("avatar upgraded to 400x400", row["profile_image_url"].endswith("_400x400.jpg"))
    check("profile url built from the handle", row["profile_url"] == "https://x.com/user1")
    check("perspectival following captured", row["x_following"] is True)
    check("absent following is not invented", "x_following" not in fx.parse_row(user(2)))


def test_adaptive_delay():
    print("\npacing")
    now = fx.time.time()
    check("no headers -> flat fallback",
          abs(fx.adaptive_delay({"remaining": None, "reset_at": None})
              - fx.NO_HEADER_DELAY_S) < fx.NO_HEADER_DELAY_S * fx.DELAY_JITTER + 3)
    d = fx.adaptive_delay({"remaining": 14, "reset_at": now + 900})
    check("15/900s budget lands near a minute per page", 50 < d < 85, f"{d:.0f}s")
    d = fx.adaptive_delay({"remaining": 400, "reset_at": now + 900})
    check("a generous budget is still floored", d >= fx.DELAY_FLOOR_S, f"{d:.0f}s")
    # Waking before the window resets spends a request on a certain 429, so this wait
    # must never come in under the reset — not even at the bottom of the jitter range.
    for _ in range(200):
        d = fx.adaptive_delay({"remaining": 0, "reset_at": now + 300})
        if d < 300:
            break
    check("an exhausted budget never wakes before the reset", d >= 300, f"{d:.0f}s")
    for _ in range(200):
        d = fx.adaptive_delay({"remaining": 1, "reset_at": now + 900})
        if d < 900:
            break
    check("a full 15-minute window is waited out, not truncated to the page ceiling",
          d >= 900, f"{d:.0f}s — DELAY_CEIL_S must not apply to a reset wait")
    d = fx.adaptive_delay({"remaining": 0, "reset_at": now + 99999})
    check("a nonsense reset header cannot park the run for hours",
          d <= fx.MAX_RESET_WAIT_S + 25, f"{d:.0f}s")
    d = fx.adaptive_delay({"remaining": 8, "reset_at": now + 99999})
    check("per-page spread is still capped", d <= fx.DELAY_CEIL_S * (1 + fx.DELAY_JITTER) + 3,
          f"{d:.0f}s")


def test_incremental_stop():
    print("\nincremental paging")
    monkey = {"reported": 40}
    setup_dir()
    # Pretend a previous full run verified ordering and saw users 1..40.
    state = fx.blank_state()
    now = datetime.now().isoformat()
    for i in range(1, 41):
        state["followers"][str(1000 + i)] = {"u": f"user{i}", "first_seen": now,
                                             "last_seen": now, "page": (i - 1) // 10}
    state["ordering"] = {"verified": True, "verified_at": now, "evidence": {}}
    state["last_full_run_at"] = now
    state["reported_followers_count"] = 40
    fx._write_json_atomic(fx.STATE_FILE, state)

    pages = [([user(i) for i in range(1, 11)], "c1"),
             ([user(i) for i in range(11, 21)], "c2"),
             ([user(i) for i in range(21, 31)], "c3"),
             ([user(i) for i in range(31, 41)], "c4"),
             ([user(i) for i in range(41, 51)], None)]
    calls = stub_network(pages, monkey)
    try:
        code = fx.run_export(Args(page_size=10))
    finally:
        restore(monkey)
    status = json.load(open(fx.STATUS_FILE))
    check("an all-known run exits ok", code == fx.EXIT_OK, str(code))
    check("it went incremental", status["mode"] == "incremental")
    check("it stopped on known depth", status["stop_reason"] == "known_depth",
          str(status["stop_reason"]))
    check(f"it stopped after {fx.KNOWN_PAGE_STOP} pages, not all 5",
          len(calls) == fx.KNOWN_PAGE_STOP, f"made {len(calls)} calls")
    check("the first page omits the cursor", calls[0]["cursor"] is None)
    check("later pages carry the cursor", calls[1]["cursor"] == "c1")

    # A new follower at the top resets the counter, so paging continues past it.
    setup_dir()
    fx._write_json_atomic(fx.STATE_FILE, state)
    pages = [([user(99)] + [user(i) for i in range(1, 10)], "c1"),
             ([user(i) for i in range(11, 21)], "c2"),
             ([user(i) for i in range(21, 31)], "c3"),
             ([user(i) for i in range(31, 41)], None)]
    calls = stub_network(pages, monkey)
    try:
        fx.run_export(Args(page_size=10))
    finally:
        restore(monkey)
    check("a new follower on page 1 does not stop the run early", len(calls) == 4,
          f"made {len(calls)} calls")
    state2 = json.load(open(fx.STATE_FILE))
    check("the new follower is recorded", "1099" in state2["followers"])
    check("new count reported", json.load(open(fx.STATUS_FILE))["new_followers"] == 1)


def test_ordering_verification():
    print("\nordering verification")
    now = datetime.now().isoformat()
    # 60 followers over 6 pages of 10 — long enough for the checks to mean something.
    prev = {str(1000 + i): {"u": f"user{i}", "page": (i - 1) // 10, "first_seen": now}
            for i in range(1, 61)}
    # One new follower at the very top shifts everyone down by one slot.
    good = [[fx.parse_row(u) for u in [user(99)] + [user(i) for i in range(1, 10)]]]
    good += [[fx.parse_row(u) for u in [user(i) for i in range(s, s + 10)]]
             for s in range(10, 60, 10)]
    ok, evidence = fx.verify_ordering({"followers": prev}, good, 10)
    check("a newest-first run verifies", ok, json.dumps(evidence))

    shuffled = list(reversed(good))
    ok, evidence = fx.verify_ordering({"followers": prev}, shuffled, 10)
    check("a reordered run does NOT verify", not ok, json.dumps(evidence))
    check("the failure is evidenced", "check_a_newness_front_loaded" in evidence)
    check("and it is check A that catches it",
          evidence["check_a_newness_front_loaded"] is False)

    ok, evidence = fx.verify_ordering({"followers": {}}, good, 10)
    check("with no previous run it cannot verify", not ok)
    check("and says why", "reason" in evidence)

    ok, evidence = fx.verify_ordering({"followers": prev}, good[:3], 10)
    check("a short list refuses to verify rather than guessing", not ok,
          json.dumps(evidence))
    check("and says the list is too short", "too few" in evidence.get("reason", ""))


def test_first_run_is_full_and_unverified():
    print("\nfirst run")
    monkey = {"reported": 20}
    setup_dir()
    pages = [([user(i) for i in range(1, 11)], "c1"),
             ([user(i) for i in range(11, 21)], None)]
    calls = stub_network(pages, monkey)
    try:
        code = fx.run_export(Args(page_size=10))
    finally:
        restore(monkey)
    state = json.load(open(fx.STATE_FILE))
    status = json.load(open(fx.STATUS_FILE))
    check("first run is full", status["mode"] == "full")
    check("it pages to the end", len(calls) == 2)
    check("ordering is NOT verified off one run",
          state["ordering"]["verified"] is False,
          "incremental must stay off until two runs agree")
    check("the status says so", status["ordering_verified"] is False)
    check("all followers recorded", len(state["followers"]) == 20)
    check("a full snapshot was written", status["snapshot"].endswith("-full.csv"))
    with open(status["snapshot"], encoding="utf-8-sig") as f:
        head = f.readline().strip()
    check("snapshot header matches XPorter's", head.split(",")[:3] == ["ID", "Name", "Username"])
    check("no checkpoint is left behind", not os.path.exists(fx.CURSOR_FILE))
    check("the first successful run stamps watching_since", state.get("watching_since"),
          "everything followed before this instant can only ever have an upper bound")


def test_partial_and_resume():
    print("\npartial run and resume")
    monkey = {"reported": 30}
    setup_dir()
    pages = [([user(i) for i in range(1, 11)], "c1"),
             ([user(i) for i in range(11, 21)], "c2"),
             ([user(i) for i in range(21, 31)], None)]
    calls = stub_network(pages, monkey)
    try:
        code = fx.run_export(Args(page_size=10, max_pages=1))
    finally:
        restore(monkey)
    check("a truncated run reports partial", code == fx.EXIT_PARTIAL, str(code))
    check("a checkpoint is written", os.path.exists(fx.CURSOR_FILE))
    cp = json.load(open(fx.CURSOR_FILE))
    check("the checkpoint holds the next cursor", cp["cursor"] == "c1")
    check("the partial CSV is kept, not renamed", cp["partial_csv"].endswith(".partial.csv"))
    check("state is NOT advanced by a partial run", not os.path.exists(fx.STATE_FILE),
          "a partial set would look like mass unfollows to the next full run")

    ids = fx.read_partial_ids(cp["partial_csv"])
    check("the partial CSV can rebuild the seen set", len(ids) == 10, str(len(ids)))

    calls = stub_network(pages[1:], monkey)
    try:
        code = fx.run_export(Args(page_size=10))
    finally:
        restore(monkey)
    check("the resumed run starts from the saved cursor", calls[0]["cursor"] == "c1",
          str(calls[0]["cursor"]))
    check("the resumed run completes", code == fx.EXIT_OK)
    state = json.load(open(fx.STATE_FILE))
    check("resume keeps the rows the first attempt fetched",
          len(state["followers"]) == 30, str(len(state["followers"])))
    check("the checkpoint is cleared", not os.path.exists(fx.CURSOR_FILE))


def test_stale_checkpoint_discarded():
    print("\nstale checkpoint")
    monkey = {"reported": 10}
    setup_dir()
    old = (datetime.now() - timedelta(hours=fx.CHECKPOINT_MAX_AGE_H + 5)).isoformat()
    fx._write_json_atomic(fx.CURSOR_FILE, {"run_id": old, "mode": "full", "cursor": "zz",
                                           "pages_done": 4, "page_size": 10,
                                           "started_at": old, "reason": "paging",
                                           "partial_csv": None})
    calls = stub_network([([user(i) for i in range(1, 11)], None)], monkey)
    try:
        fx.run_export(Args(page_size=10))
    finally:
        restore(monkey)
    check("a cursor older than the max age is not replayed", calls[0]["cursor"] is None,
          str(calls[0]["cursor"]))


def test_reconcile():
    print("\nreconcile")
    queue = [
        # still following, they follow us -> mutual
        {"username": "user1", "source_list": "listA", "status": "followed",
         "timestamp": (datetime.now() - timedelta(days=2)).isoformat()},
        # unfollowed at day 3, they followed back at day 40 -> follower_only, late
        {"username": "user2", "source_list": "listA", "status": "unfollowed",
         "timestamp": (datetime.now() - timedelta(days=40)).isoformat()},
        # the same handle sits in three lists; only one row is attributed
        {"username": "user3", "source_list": "listA", "status": "followed",
         "timestamp": (datetime.now() - timedelta(days=1)).isoformat()},
        {"username": "user3", "source_list": "listB", "status": "skipped",
         "skip_reason": "duplicate_actioned_from:listA",
         "timestamp": (datetime.now() - timedelta(days=1)).isoformat()},
        {"username": "user3", "source_list": "listC", "status": "pending_follow"},
        # renamed since we followed them; matched by id
        {"username": "oldhandle", "user_id": "1004", "source_list": "listA",
         "status": "unfollowed", "timestamp": (datetime.now() - timedelta(days=9)).isoformat()},
        # not a follower
        {"username": "user9", "source_list": "listA", "status": "unfollowed",
         "timestamp": (datetime.now() - timedelta(days=5)).isoformat()},
    ]
    setup_dir(queue)
    # followed.csv is the authoritative follow clock: an unfollowed row's queue
    # timestamp is the *unfollow* time, so the elapsed days must not come from there.
    with open("followed.csv", "w") as f:
        f.write("username,follow_timestamp\n")
        f.write(f"user1,{(datetime.now() - timedelta(days=2)).isoformat()}\n")
        f.write(f"user2,{(datetime.now() - timedelta(days=44)).isoformat()}\n")
        f.write(f"user3,{(datetime.now() - timedelta(days=1)).isoformat()}\n")
        f.write(f"oldhandle,{(datetime.now() - timedelta(days=9)).isoformat()}\n")
    watching = (datetime.now() - timedelta(days=90)).isoformat()
    followers = {
        "1001": {"u": "user1"}, "1002": {"u": "user2"}, "1003": {"u": "user3"},
        "1004": {"u": "newhandle"},
    }
    stats = fx.reconcile(followers, "full", 4, watching)
    q = json.load(open("queue.json"))
    by = lambda u: [e for e in q if e.get("username") == u]

    check("every follower with a queue row is matched", stats["matched"] == 4,
          str(stats["matched"]))
    check("mutual counted", stats["mutual"] == 2, str(stats["mutual"]))
    check("follower_only counted", stats["follower_only"] == 2, str(stats["follower_only"]))
    check("still-following row is mutual", by("user1")[0]["follows_back_state"] == "mutual")
    check("already-unfollowed row is follower_only",
          by("user2")[0]["follows_back_state"] == "follower_only",
          "this is the 40-day win the whole job exists for")
    check("the flag lands on ALL rows for a handle",
          all(e.get("follows_back") for e in by("user3")), "or a reset elsewhere re-follows them")
    check("exactly one row is attributed",
          sum(1 for e in by("user3") if e.get("follows_back_attributed")) == 1)
    check("attribution picks the row that did the follow",
          [e for e in by("user3") if e.get("follows_back_attributed")][0]["source_list"] == "listA")
    check("a renamed follower is matched by id",
          by("oldhandle")[0].get("follows_back") is True)
    check("the rename does not overwrite the handle",
          by("oldhandle")[0]["username"] == "oldhandle",
          "followed.csv joins on the handle")
    check("a rename is counted", stats["renamed"] == 1)
    check("a non-follower is untouched", "follows_back" not in by("user9")[0])
    check("the source is recorded",
          by("user1")[0]["follows_back_source"] == "followers_export")

    ledger = {e["username"]: e for e in stats["ledger"]}
    check("the late followback is flagged late", ledger["user2"]["late"] is True)
    check("elapsed days come from followed.csv, not the unfollow timestamp",
          ledger["user2"]["days_to_followback"] >= 43,
          f"got {ledger['user2']['days_to_followback']} — the queue row says 40")
    check("a same-week mutual is not late", ledger["user1"]["late"] is False)
    check("the ledger attributes the source list", ledger["user3"]["source_list"] == "listA")
    check("everything here is measured", stats["measured"] == 4 and stats["unmeasured"] == 0)

    # --- follows that predate the job cannot be timed ---
    setup_dir(queue)
    with open("followed.csv", "w") as f:
        f.write("username,follow_timestamp\n")
        f.write(f"user1,{(datetime.now() - timedelta(days=400)).isoformat()}\n")
    stats_b = fx.reconcile({"1001": {"u": "user1"}}, "full", 4,
                           (datetime.now() - timedelta(days=30)).isoformat())
    entry = stats_b["ledger"][0]
    check("a follow older than the job is not counted as measured",
          stats_b["unmeasured"] == 1 and stats_b["measured"] == 0)
    check("it carries an upper bound, not a measurement",
          entry["timing"] == "upper_bound" and "days_to_followback" not in entry)
    check("and withholds `late` entirely", "late" not in entry,
          "they may have followed back on day one and we simply never looked")
    check("the upper bound is still recorded",
          entry["days_to_followback_max"] >= 399)
    check("it still counts as a followback", stats_b["newly_marked"] == 1)

    # --- no follow record at all ---
    # An unfollowed row with no followed.csv entry has no usable follow time at all:
    # its queue timestamp is when we unfollowed, which says nothing about the follow.
    setup_dir(queue)
    stats_c = fx.reconcile({"1002": {"u": "user2"}}, "full", 4, watching)
    entry = stats_c["ledger"][0]
    check("an unfollowed row with no follow clock has unknown timing",
          entry["timing"] == "unknown", str(entry))
    check("and no elapsed figure is invented", entry["days_to_followback"] is None)
    check("it still counts as a followback", stats_c["newly_marked"] == 1)

    # A row we are still following is different: there its timestamp IS the follow time.
    setup_dir(queue)
    entry = fx.reconcile({"1003": {"u": "user3"}}, "full", 4, watching)["ledger"][0]
    check("a still-followed row falls back to its queue timestamp",
          entry["timing"] == "measured" and entry["followed_at"], str(entry))

    setup_dir(queue)
    with open("followed.csv", "w") as f:
        f.write("username,follow_timestamp\n")
        f.write(f"user1,{(datetime.now() - timedelta(days=2)).isoformat()}\n")
        f.write(f"user2,{(datetime.now() - timedelta(days=44)).isoformat()}\n")
        f.write(f"user3,{(datetime.now() - timedelta(days=1)).isoformat()}\n")
        f.write(f"oldhandle,{(datetime.now() - timedelta(days=9)).isoformat()}\n")
    stats = fx.reconcile(followers, "full", 4, watching)

    # --- idempotency ---
    # Re-read: the blocks above worked in their own throwaway dirs, so the closure from
    # the first pass is pointing at a queue.json that is no longer the one on disk.
    q = json.load(open("queue.json"))
    by = lambda u: [e for e in q if e.get("username") == u]
    first_at = by("user1")[0]["follows_back_at"]
    stats2 = fx.reconcile(followers, "full", 4, watching)
    q = json.load(open("queue.json"))
    by = lambda u: [e for e in q if e.get("username") == u]
    check("a second run marks nothing new", stats2["newly_marked"] == 0,
          str(stats2["newly_marked"]))
    check("a second run emits no ledger lines", stats2["ledger"] == [])
    check("follows_back_at is never overwritten", by("user1")[0]["follows_back_at"] == first_at)
    check("checked_at is refreshed", by("user1")[0].get("follows_back_checked_at"))

    # --- losing a follower ---
    stats3 = fx.reconcile({k: v for k, v in followers.items() if k != "1002"},
                          "full", 4, watching)
    q = json.load(open("queue.json"))
    row = [e for e in q if e.get("username") == "user2"][0]
    check("a lost follower is recorded", stats3["lost"] == 1)
    check("but the flag is NOT cleared", row.get("follows_back") is True,
          "clearing it would let a reset re-follow a row we still follow")
    check("the loss is timestamped", row.get("follows_back_lost_at"))

    # --- an incremental run must never conclude someone is gone ---
    setup_dir(queue)
    fx.reconcile(followers, "full", 4, watching)
    stats4 = fx.reconcile({"1001": {"u": "user1"}}, "incremental", 4, watching)
    check("an incremental run never marks anyone lost", stats4["lost"] == 0,
          "absence from 4 pages proves nothing")


def test_perspective_is_gated():
    print("\nperspectival following field")
    queue = [{"username": "user1", "source_list": "listA", "status": "followed",
              "timestamp": datetime.now().isoformat()}]
    followers = {"1001": {"u": "user1", "x_following": False}}

    setup_dir(queue)
    stats = fx.reconcile(followers, "full", 4, None, self_view=False)
    check("an export run from another account reports no drift",
          stats["state_drift"] == 0,
          "'the export account does not follow them' is true of nearly everyone")

    setup_dir(queue)
    stats = fx.reconcile(followers, "full", 4, None, self_view=True)
    check("but running as the listed account, the same row IS drift",
          stats["state_drift"] == 1)

    # ...and the field must not even be stored when it is not our own view.
    monkey = {"reported": 20}
    setup_dir(queue)
    with open(fx.BOT_COOKIE_FILE, "w") as f:
        json.dump([{"name": "twid", "value": "u%3D5555555555"},
                   {"name": "ct0", "value": "c"}, {"name": "auth_token", "value": "a"}], f)
    rows = [dict(user(i), following=(i == 1)) for i in range(1, 11)]
    stub_network([(rows, None)], monkey)
    try:
        fx.run_export(Args(page_size=10))
    finally:
        restore(monkey)
    state = json.load(open(fx.STATE_FILE))
    check("x_following is not folded into state for a cross-account export",
          not any("x_following" in v for v in state["followers"].values()),
          "it would be the export account's relationships, not the bot's")
    check("and the status says whose view it was",
          json.load(open(fx.STATUS_FILE))["account"]["self_view"] is False)

    setup_dir(queue)
    stub_network([(rows, None)], monkey)
    try:
        fx.run_export(Args(page_size=10))          # no bot jar -> session IS the target
    finally:
        restore(monkey)
    state = json.load(open(fx.STATE_FILE))
    check("but it IS kept when the export runs as the listed account",
          any("x_following" in v for v in state["followers"].values()))


def test_dry_run_never_writes():
    print("\ndry runs")
    queue = [{"username": "user1", "source_list": "listA", "status": "followed",
              "timestamp": datetime.now().isoformat()}]
    setup_dir(queue)
    before = open("queue.json").read()
    stats = fx.reconcile({"1001": {"u": "user1"}}, "full", 4, dry_run=True)
    check("a dry run still reports what it would do", stats["newly_marked"] == 1)
    check("a dry run leaves queue.json byte-identical", open("queue.json").read() == before)


def test_suspicious_guard():
    print("\nsuspicious-result guard")
    setup_dir([{"username": "somebodyelse", "status": "followed", "source_list": "l"}])
    followers = {str(i): {"u": f"nobody{i}"} for i in range(fx.SUSPICIOUS_MIN_FOLLOWERS + 10)}
    status = {"mode": "full", "pages": 3, "followers_total": len(followers)}
    before = open("queue.json").read()
    code = fx._apply_reconcile(status, followers, "full", Args())
    check("matching nothing at scale is refused", code == fx.EXIT_SHAPE, str(code))
    check("and queue.json is not written", open("queue.json").read() == before)
    check("the outcome says suspicious", status["outcome"] == "suspicious")


def test_egress_gate():
    print("\negress gate")
    setup_dir()
    monkey = {"reported": 5}
    stub_network([], monkey)
    fx.egress_ok = lambda: False
    try:
        code = fx.run_export(Args())
    finally:
        restore(monkey)
    check("no egress means no requests and a clean exit", code == fx.EXIT_EGRESS, str(code))
    status = json.load(open(fx.STATUS_FILE))
    check("the outcome is egress_down", status["outcome"] == "egress_down")
    check("nothing about the VPN is touched",
          "nmcli" not in open(os.path.join(
              os.path.dirname(os.path.abspath(fx.__file__)), "x_followers_export.py")).read(),
          "the watchdog and the bot already contend for it")


def test_auth_error_never_falls_back():
    print("\nauth failure")
    setup_dir()
    with open(fx.BOT_COOKIE_FILE, "w") as f:
        json.dump([{"name": "twid", "value": "u%3D5555555555"},
                   {"name": "ct0", "value": "MAINCT0"},
                   {"name": "auth_token", "value": "MAINAUTH"}], f)
    monkey = {"reported": 5}
    stub_network([], monkey)
    sessions = []

    def fake_session(cookies, screen_name=None):
        sessions.append(dict(cookies))
        return object()

    fx.build_session = fake_session

    def dead(session, user_id):
        raise fx.ExporterError("AUTH_ERROR", "HTTP 401")

    fx.fetch_account = dead
    try:
        code = fx.run_export(Args())
    finally:
        restore(monkey)
    check("a dead session exits with the auth code", code == fx.EXIT_AUTH, str(code))
    status = json.load(open(fx.STATUS_FILE))
    check("the outcome is auth_error", status["outcome"] == "auth_error")
    check("the message tells you what to do", "re-export" in status["message"])
    check("it never built a session from the bot's jar",
          all(s.get("auth_token") != "MAINAUTH" for s in sessions),
          "that jar belongs to Selenium")


CF_BODY = ('<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title>'
           '</head><body><div class="cf-error-details">Sorry, you have been blocked'
           '</div></body></html>')


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.headers = {}
    @property
    def text(self):
        return self._body


def test_cloudflare_is_not_a_dead_session():
    print("\ncloudflare edge block")
    meta = {"status": 403, "limit": None, "remaining": None, "reset_at": None,
            "elapsed": 0.1, "returned": 0}
    try:
        fx._classify(_Resp(403, CF_BODY), meta)
        check("a Cloudflare 403 raises", False)
    except fx.ExporterError as e:
        check("a Cloudflare 403 is EDGE_BLOCKED, not AUTH_ERROR", e.kind == "EDGE_BLOCKED",
              e.kind)
    try:
        fx._classify(_Resp(403, '{"errors":[{"code":32,"message":"Could not authenticate"}]}'),
                     meta)
        check("a real 403 raises", False)
    except fx.ExporterError as e:
        check("a genuine 403 is still AUTH_ERROR", e.kind == "AUTH_ERROR", e.kind)

    # A blocked GraphQL lookup must not kill an export whose real work is REST.
    monkey = {"reported": 20}
    setup_dir([{"username": "user1", "status": "followed", "source_list": "listA",
                "timestamp": datetime.now().isoformat()}])
    pages = [([user(i) for i in range(1, 11)], None)]
    calls = stub_network(pages, monkey)

    def blocked(session, user_id):
        raise fx.ExporterError("EDGE_BLOCKED", "HTTP 403 from Cloudflare", body=CF_BODY)

    fx.fetch_account = blocked
    try:
        code = fx.run_export(Args(page_size=10))
    finally:
        restore(monkey)
    check("the export still completes", code == fx.EXIT_OK, str(code))
    check("and it still paged the REST endpoint", len(calls) == 1)
    status = json.load(open(fx.STATUS_FILE))
    check("the outcome is ok, not auth_error", status["outcome"] == "ok", status["outcome"])
    check("the blocked lookup is recorded", status.get("account_lookup") == "EDGE_BLOCKED")
    check("followers were still folded into state",
          len(json.load(open(fx.STATE_FILE))["followers"]) == 10)

    # A genuine auth failure must still stop everything.
    setup_dir([])
    stub_network([], monkey)

    def dead(session, user_id):
        raise fx.ExporterError("AUTH_ERROR", "HTTP 401")

    fx.fetch_account = dead
    try:
        code = fx.run_export(Args(page_size=10))
    finally:
        restore(monkey)
    check("a real auth failure still exits 3", code == fx.EXIT_AUTH, str(code))

    # And the advice must differ: never tell the user to re-export working cookies.
    status = {}
    fx._fail(status, fx.ExporterError("EDGE_BLOCKED", "HTTP 403 from Cloudflare"))
    check("an edge block never blames the cookies",
          "re-export" not in status["message"] and "exit" in status["message"],
          status["message"])
    check("and it retries rather than failing the unit",
          status["exit_code"] == fx.EXIT_PARTIAL)


def test_shape_change():
    print("\nendpoint shape change")
    setup_dir()
    monkey = {"reported": 50}

    def wrong_shape(session, user_id, cursor=None, count=10, timeout=30):
        raise fx.ExporterError("SHAPE_CHANGED", "no users[] in a 200 response",
                               body='{"data":{}}')

    stub_network([], monkey)
    fx.fetch_followers_page = wrong_shape
    try:
        code = fx.run_export(Args())
    finally:
        restore(monkey)
    check("a shape change is its own exit code", code == fx.EXIT_SHAPE, str(code))
    status = json.load(open(fx.STATUS_FILE))
    check("the response body is kept for diagnosis", status.get("body"))


def test_drift_forces_full():
    print("\ndrift backstop")
    check("a big shortfall forces a full re-page", fx.drift_forces_full(5000, 4000))
    check("a handful of new followers does not", not fx.drift_forces_full(5000, 4990))
    check("a small account still trips on 50+", fx.drift_forces_full(200, 140))
    check("a mass unfollow forces a re-page", fx.drift_forces_full(5000, 5300))
    check("no reported count means no opinion", not fx.drift_forces_full(None, 100))


def test_queue_store_concurrency():
    print("\nqueue_store")
    setup_dir([{"username": "a", "status": "pending_follow"}])
    with queue_store.queue_transaction() as q:
        pass
    check("a no-op transaction writes nothing",
          json.load(open("queue.json")) == [{"username": "a", "status": "pending_follow"}])

    # A write landing while the transaction is open must not be lost.
    pid = os.fork()
    if pid == 0:
        try:
            with queue_store.queue_transaction() as q:
                q.append({"username": "child", "status": "followed"})
            os._exit(0)
        except Exception:
            os._exit(1)
    os.waitpid(pid, 0)
    with queue_store.queue_transaction() as q:
        names = {e["username"] for e in q}
        check("a transaction sees a write committed before it opened", "child" in names)
        q.append({"username": "parent", "status": "followed"})
    names = {e["username"] for e in json.load(open("queue.json"))}
    check("neither write is lost", {"a", "child", "parent"} <= names, str(names))

    check("an aborted transaction writes nothing", _aborts_cleanly())

    check("attribution prefers the followed row",
          queue_store.attributed_row([
              {"status": "skipped", "skip_reason": "duplicate_actioned_from:x"},
              {"status": "followed"}])["status"] == "followed")
    check("else the earliest unfollow",
          queue_store.attributed_row([
              {"status": "unfollowed", "timestamp": "2026-09-05T00:00:00"},
              {"status": "unfollowed", "timestamp": "2026-09-01T00:00:00"},
          ])["timestamp"] == "2026-09-01T00:00:00")
    check("shadow rows are never attributed",
          queue_store.attributed_row([
              {"status": "skipped", "skip_reason": "duplicate_actioned_from:x"},
              {"status": "skipped", "skip_reason": "not found/suspended"},
          ])["skip_reason"] == "not found/suspended")


def _aborts_cleanly():
    before = open("queue.json").read()
    try:
        with queue_store.queue_transaction() as q:
            q.append({"username": "never", "status": "followed"})
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    return open("queue.json").read() == before


def test_reset_scan_unchanged():
    print("\n_reset_scan semantics")
    import importlib
    cwd = os.getcwd()
    import dashboard
    importlib.reload(dashboard)
    os.chdir(cwd)

    base = [
        {"username": "u1", "source_list": "listA", "status": "unfollowed",
         "timestamp": "2026-09-01T00:00:00"},
        {"username": "u2", "source_list": "listA", "status": "unfollowed",
         "timestamp": "2026-09-01T00:00:00"},
    ]
    summary, rows = dashboard._reset_scan(base)
    before_resettable = summary["listA"]["resettable"]
    check("two finished rows are resettable", before_resettable == 2)

    flagged = [dict(base[0], follows_back=True, follows_back_state="follower_only"),
               dict(base[1])]
    summary, rows = dashboard._reset_scan(flagged)
    check("a flagged row leaves the resettable pool",
          summary["listA"]["resettable"] == 1,
          "otherwise a reset re-follows someone who already follows us")
    check("it lands in fans, not mutuals", summary["listA"]["fans"] == 1,
          "we are NOT following them; calling it a mutual would inflate the count")
    check("mutuals stays clean", summary["listA"]["mutuals"] == 0)

    still = [dict(base[0], status="followed", follows_back=True,
                  follows_back_state="mutual")]
    summary, rows = dashboard._reset_scan(still)
    check("a still-following flagged row is a mutual", summary["listA"]["mutuals"] == 1)
    check("and not a fan", summary["listA"]["fans"] == 0)

    legacy = [dict(base[0], follows_back=True)]      # flagged by the old unfollow pass
    summary, rows = dashboard._reset_scan(legacy)
    check("a legacy flag on an unfollowed row reads as a fan",
          summary["listA"]["fans"] == 1, "status alone classifies it")


def test_dashboard_routes():
    print("\ndashboard routes")
    import importlib
    queue = [
        # listA: 30 follows, 4 followbacks -> a real conversion number
        *[{"username": f"a{i}", "source_list": "listA", "status": "unfollowed",
           "timestamp": (datetime.now() - timedelta(days=20)).isoformat()}
          for i in range(30)],
        # listB: 3 follows, 1 followback -> too small to report a percentage
        *[{"username": f"b{i}", "source_list": "listB", "status": "followed",
           "timestamp": datetime.now().isoformat()} for i in range(3)],
        # a shadow row, which must not inflate listC
        {"username": "a0", "source_list": "listC", "status": "skipped",
         "skip_reason": "duplicate_actioned_from:listA"},
        # a dead handle we never actually followed
        {"username": "dead", "source_list": "listA", "status": "skipped",
         "skip_reason": "not found/suspended"},
    ]
    for i in range(4):
        queue[i].update(follows_back=True, follows_back_state="follower_only",
                        follows_back_attributed=True,
                        follows_back_at=datetime.now().isoformat(),
                        follows_back_source="followers_export")
    queue[30].update(follows_back=True, follows_back_state="mutual",
                     follows_back_attributed=True,
                     follows_back_at=datetime.now().isoformat(),
                     follows_back_source="followers_export")
    setup_dir(queue)

    now = datetime.now().isoformat()
    state = fx.blank_state()
    state["followers"] = {str(1000 + i): {"u": f"a{i}", "first_seen": now, "last_seen": now}
                          for i in range(4)}
    state["followers"]["2000"] = {"u": "b0", "first_seen": now, "last_seen": now}
    state["followers"]["3000"] = {"u": "randomfan", "first_seen": now, "last_seen": now}
    state["last_success_at"] = now
    state["ordering"]["verified"] = True
    fx._write_json_atomic(fx.STATE_FILE, state)
    fx._write_json_atomic(fx.STATUS_FILE, {
        "outcome": "ok", "mode": "incremental", "pages": 4, "finished_at": now,
        "followers_total": 6, "reported_followers_count": 6, "drift": 0,
        "queue_marked": 5, "queue_marked_mutual": 1, "queue_marked_follower_only": 4,
        "ordering_verified": True, "cookie": {"days_left": 200},
        "account": {"session_user_id": "999", "quota_isolated": True},
    })
    watching = (datetime.now() - timedelta(days=60)).isoformat()
    state["watching_since"] = watching
    # Everyone above arrived in the first run, so they share watching_since exactly.
    for info in state["followers"].values():
        info["first_seen"] = watching
    state["followers"]["7001"] = {"u": "brandnew",
                                  "first_seen": (datetime.now() - timedelta(hours=3)).isoformat(),
                                  "last_seen": now}
    state["followers"]["7002"] = {"u": "oldernew",
                                  "first_seen": (datetime.now() - timedelta(days=3)).isoformat(),
                                  "last_seen": now}
    state["removed"] = {"7003": {"u": "quitter", "first_seen": watching,
                                 "removed_at": (datetime.now() - timedelta(hours=5)).isoformat()}}
    fx._write_json_atomic(fx.STATE_FILE, state)
    fx.append_ledger([
        {"ts": now, "username": "a0", "source_list": "listA", "days_to_followback": 20,
         "late": True, "state": "follower_only", "timing": "measured"},
        {"ts": now, "username": "a1", "source_list": "listA", "days_to_followback": 30,
         "late": True, "state": "follower_only", "timing": "measured"},
        # Followed long before the job existed: a real followback, but the elapsed time
        # is an upper bound and must not reach the median or the late count.
        {"ts": now, "username": "a2", "source_list": "listA",
         "days_to_followback_max": 900, "state": "follower_only",
         "timing": "upper_bound"},
        {"ts": now, "username": "b0", "source_list": "listB", "days_to_followback": 1,
         "late": False, "state": "mutual", "timing": "measured"},
    ])

    import dashboard
    importlib.reload(dashboard)
    client = dashboard.app.test_client()

    r = client.get("/api/followers/status")
    check("/api/followers/status serves", r.status_code == 200)
    d = r.get_json()
    check("a fresh export is not stale", d["stale"] is False, str(d))
    check("hours since success is computed", d["hours_since_success"] is not None)

    r = client.get("/api/followers/conversion")
    check("/api/followers/conversion serves", r.status_code == 200)
    d = r.get_json()
    lists = {l["name"]: l for l in d["lists"]}
    check("shadow rows do not create a phantom list", lists["listC"]["followed"] == 0,
          "the list that actioned the handle gets the credit")
    check("not-found skips are not counted as follows", lists["listA"]["followed"] == 30,
          f"got {lists['listA']['followed']}")
    check("conversion is computed for a big enough list",
          lists["listA"]["conversion_pct"] == round(4 / 30 * 100, 1),
          str(lists["listA"]["conversion_pct"]))
    check("a 3-row list reports no percentage",
          lists["listB"]["conversion_pct"] is None and lists["listB"]["sample_too_small"],
          "1 of 3 is not a 33% list")
    check("late followbacks are counted from the ledger",
          lists["listA"]["late_followbacks"] == 2,
          str(lists["listA"]["late_followbacks"]))
    check("an untimed followback is not counted as late",
          lists["listA"]["untimed_followbacks"] == 1,
          "we cannot prove when it arrived, only that it did")
    check("and it is excluded from the median",
          lists["listA"]["median_days_to_followback"] == 25.0,
          "a 900-day upper bound would have dragged this to 30")
    check("the totals carry the untimed count",
          d["totals"]["untimed_followbacks"] == 1)
    check("the API reports when watching began", d["watching_since"])
    check("fans and mutuals are separated",
          lists["listA"]["fans"] == 4 and lists["listB"]["mutuals"] == 1)
    check("organic followers are the unattributed remainder",
          d["totals"]["organic_followers"] == 3, str(d["totals"]))
    check("the best-converting list sorts first", d["lists"][0]["name"] == "listA")

    r = client.get("/api/followers/mutuals")
    check("/api/followers/mutuals serves", r.status_code == 200)
    d = r.get_json()
    check("every known follower is listed", d["total"] == 8, str(d["total"]))
    states = {row["username"]: row["state"] for row in d["rows"]}
    check("a fan is labelled a fan", states["a0"] == "follower_only")
    check("a mutual is labelled a mutual", states["b0"] == "mutual")
    check("a follower from no list is organic", states["randomfan"] == "organic")

    d = client.get("/api/followers/mutuals?state=organic").get_json()
    check("filtering by state works",
          sorted(r["username"] for r in d["rows"]) == ["brandnew", "oldernew", "randomfan"],
          str([r["username"] for r in d["rows"]]))
    check("and it is newest-first",
          [r["username"] for r in d["rows"]] == ["brandnew", "oldernew", "randomfan"],
          str([r["username"] for r in d["rows"]]))
    d = client.get("/api/followers/mutuals?search=a1").get_json()
    check("searching by handle works", [r["username"] for r in d["rows"]] == ["a1"])

    r = client.get("/followers")
    check("the /followers page renders", r.status_code == 200)
    body = r.get_data(as_text=True)
    check("it is wired to its own nav entry", 'id="nav-followers"' in body)
    check("it loads the three endpoints",
          all(u in body for u in ("/api/followers/status", "/api/followers/conversion",
                                  "/api/followers/mutuals")))

    # --- recently followed ---
    r = client.get("/api/followers/recent")
    check("/api/followers/recent serves", r.status_code == 200)
    d = r.get_json()
    names = [x["username"] for x in d["arrivals"]]
    check("the initial backfill is excluded from arrivals",
          "a0" not in names and "a1" not in names,
          "1900 people did not just follow you on day one")
    check("it says how many were backfilled", d["backfilled"] == 6, str(d["backfilled"]))
    check("genuinely new followers are listed", names == ["brandnew", "oldernew"],
          f"got {names} — newest first")
    check("a new follower carries its relationship",
          d["arrivals"][0]["state"] in ("organic", "mutual", "follower_only"))
    check("24h and 7d counts are separated",
          d["counts"]["new_24h"] == 1 and d["counts"]["new_7d"] == 2,
          str(d["counts"]))
    check("departures are reported too",
          [x["username"] for x in d["departures"]] == ["quitter"])
    check("and counted", d["counts"]["lost_24h"] == 1)
    check("hours_ago is computed", d["arrivals"][0]["hours_ago"] < 24)

    d = client.get("/api/status").get_json()
    check("/api/status carries the 24h arrival count",
          d.get("followers_export") is not None)
    check("/api/status carries export health", "followers_export" in d)
    check("and it is not stale here", d["followers_export"]["stale"] is False)

    d = client.get("/api/queue/sources").get_json()
    by_name = {s["name"]: s for s in d["sources"]}
    check("/api/queue/sources gains fans", by_name["listA"]["fans"] == 4)
    check("and a followbacks total", by_name["listB"]["followbacks"] == 1)

    # A never-run exporter must be loud, not silent.
    setup_dir(queue)
    importlib.reload(dashboard)
    client = dashboard.app.test_client()
    d = client.get("/api/followers/status").get_json()
    check("a never-run exporter reports never_run", d["never_run"] is True)
    check("and counts as stale", d["stale"] is True)
    body = client.get("/followers").get_data(as_text=True)
    check("the page warns when it has never run", "never completed" in body)


def main():
    root = os.getcwd()
    tests = [
        test_cookies, test_preflight_identity, test_parse_row, test_adaptive_delay,
        test_ordering_verification, test_first_run_is_full_and_unverified,
        test_incremental_stop, test_partial_and_resume, test_stale_checkpoint_discarded,
        test_reconcile, test_perspective_is_gated, test_dry_run_never_writes,
        test_suspicious_guard,
        test_egress_gate, test_auth_error_never_falls_back,
        test_cloudflare_is_not_a_dead_session, test_shape_change,
        test_drift_forces_full, test_queue_store_concurrency, test_reset_scan_unchanged,
        test_dashboard_routes,
    ]
    try:
        for test in tests:
            test()
    finally:
        os.chdir(root)
    print(f"\nAll {len(PASSED)} checks passed")


if __name__ == "__main__":
    main()
