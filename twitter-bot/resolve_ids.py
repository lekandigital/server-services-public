#!/usr/bin/env python3
"""
resolve_ids.py — Resolve Twitter/X numeric User IDs to usernames.

Uses curl_cffi to bypass Cloudflare and call X.com's internal GraphQL API
to resolve User IDs to screen_names. Outputs clean CSVs ready for dashboard
import.

Usage:
    python3 resolve_ids.py /path/to/importcsv/
    python3 resolve_ids.py /path/to/importcsv/ --resume
"""

import csv
import io
import json
import os
import sys
import time
import random
import glob
from datetime import datetime

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("Installing curl_cffi...")
    os.system(f"{sys.executable} -m pip install curl_cffi -q")
    from curl_cffi import requests as cffi_requests

COOKIE_FILE = "cacheforlogin.json"
OUTPUT_DIR = "."
CHECKPOINT_FILE = "resolve_checkpoint.json"

GQL_URL = "https://x.com/i/api/graphql/xf3jd90KKBCUxdlI_tNHZw/UserByRestId"
BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
FEATURES = {
    "hidden_profile_subscriptions_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}


def build_session():
    cookie_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), COOKIE_FILE)
    if not os.path.exists(cookie_path):
        print(f"Cookie file not found: {cookie_path}")
        sys.exit(1)

    with open(cookie_path) as f:
        raw_cookies = json.load(f)

    session = cffi_requests.Session(impersonate="chrome120")
    for c in raw_cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".x.com"))

    ct0 = session.cookies.get("ct0")
    if not ct0:
        print("No ct0 cookie found - session may be expired")
        sys.exit(1)

    session.headers.update({
        "authorization": BEARER,
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "content-type": "application/json",
        "referer": "https://x.com/home",
        "origin": "https://x.com",
    })

    print(f"Session built with {len(raw_cookies)} cookies")
    return session


def resolve_one(session, user_id):
    variables = json.dumps({"userId": str(user_id), "withSafetyModeUserFields": True})
    features = json.dumps(FEATURES)
    try:
        resp = session.get(GQL_URL, params={"variables": variables, "features": features}, timeout=10)
        if resp.status_code == 429:
            return None, "rate_limited"
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        data = resp.json()
        user = data.get("data", {}).get("user", {}).get("result", {})
        typename = user.get("__typename", "")
        if typename == "UserUnavailable":
            reason = user.get("reason", "unknown")
            return None, f"unavailable:{reason}"
        screen_name = user.get("legacy", {}).get("screen_name")
        if screen_name:
            return screen_name, None
        return None, "no_screen_name"
    except Exception as e:
        return None, str(e)


def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {}


def save_checkpoint(resolved):
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(resolved, f)
    os.replace(tmp, CHECKPOINT_FILE)


def read_all_ids(csv_folder):
    csv_files = sorted(glob.glob(os.path.join(csv_folder, "*.csv")))
    all_ids = {}
    for path in csv_files:
        fname = os.path.basename(path)
        with open(path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        reader = csv.DictReader(io.StringIO(content))
        count = 0
        for row in reader:
            uid = row.get("User ID", "").strip()
            if uid and uid not in all_ids:
                all_ids[uid] = fname
                count += 1
        print(f"  {fname[:60]:60s} +{count} new IDs")
    return all_ids, csv_files


def write_outputs(all_ids, resolved, csv_files):
    unique_ids = list(all_ids.keys())
    for path in csv_files:
        fname = os.path.basename(path)
        with open(path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        reader = csv.DictReader(io.StringIO(content))
        ids = [r.get("User ID", "").strip() for r in reader]

        out_name = f"resolved_{fname}"
        out_path = os.path.join(OUTPUT_DIR, out_name)
        written = 0
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["screen_name", "user_id"])
            for uid in ids:
                uname = resolved.get(uid)
                if uname:
                    writer.writerow([uname, uid])
                    written += 1
        print(f"  {out_name}: {written}/{len(ids)}")

    combined_path = os.path.join(OUTPUT_DIR, "all_resolved.csv")
    written = 0
    with open(combined_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["screen_name", "user_id", "source_file"])
        for uid, source in all_ids.items():
            uname = resolved.get(uid)
            if uname:
                writer.writerow([uname, uid, source])
                written += 1
    print(f"  all_resolved.csv: {written} total")

    fail_count = sum(1 for uid in unique_ids if uid not in resolved)
    if fail_count:
        fail_path = os.path.join(OUTPUT_DIR, "failed_ids.txt")
        with open(fail_path, "w") as f:
            for uid in unique_ids:
                if uid not in resolved:
                    f.write(uid + "\n")
        print(f"  failed_ids.txt: {fail_count} unresolved")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 resolve_ids.py /path/to/csv/folder/ [--resume]")
        sys.exit(1)

    input_path = sys.argv[1]
    resume = "--resume" in sys.argv

    print(f"Reading CSVs from: {input_path}")
    if os.path.isdir(input_path):
        all_ids, csv_files = read_all_ids(input_path)
    else:
        csv_files = [input_path]
        all_ids = {}
        with open(input_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            uid = row.get("User ID", "").strip()
            if uid:
                all_ids[uid] = os.path.basename(input_path)

    unique_ids = list(all_ids.keys())
    print(f"\nTotal unique User IDs: {len(unique_ids)}")

    resolved = load_checkpoint() if resume else {}
    if resolved:
        print(f"Checkpoint: {len(resolved)} already resolved")

    remaining = [uid for uid in unique_ids if uid not in resolved]
    print(f"Remaining: {len(remaining)}")

    if not remaining:
        print("\nAll IDs already resolved!")
        write_outputs(all_ids, resolved, csv_files)
        return

    session = build_session()

    for test_attempt in range(10):
        test_name, test_err = resolve_one(session, "44196397")
        if test_name == "elonmusk":
            print(f"API test passed: 44196397 -> @elonmusk")
            break
        elif test_err == "rate_limited":
            wait = 5 * 60 + random.uniform(0, 60)
            print(f"API test rate limited (attempt {test_attempt+1}/10). Waiting {wait/60:.1f}m...")
            time.sleep(wait)
        else:
            print(f"API test FAILED: {test_err}")
            sys.exit(1)
    else:
        print("API test still rate limited after 10 attempts. Exiting.")
        sys.exit(1)

    est_mins = len(remaining) * 1.5 / 60
    print(f"\nResolving {len(remaining)} IDs (~{est_mins:.0f} minutes)...\n")
    start_time = time.time()
    success = 0
    failed = 0
    unavailable = 0
    rate_limited_count = 0
    consecutive_ok = 0

    for i, uid in enumerate(remaining):
        screen_name, err = resolve_one(session, uid)

        if screen_name:
            resolved[uid] = screen_name
            success += 1
            consecutive_ok += 1
        elif err and err.startswith("unavailable"):
            unavailable += 1
            consecutive_ok += 1
        elif err == "rate_limited":
            rate_limited_count += 1
            consecutive_ok = 0
            for backoff_attempt in range(5):
                wait = (5 + backoff_attempt * 5) * 60 + random.uniform(0, 60)
                print(f"\n  Rate limited at {i+1}/{len(remaining)} (attempt {backoff_attempt+1}/5). Waiting {wait/60:.1f}m...")
                save_checkpoint(resolved)
                time.sleep(wait)
                screen_name2, err2 = resolve_one(session, uid)
                if screen_name2:
                    resolved[uid] = screen_name2
                    success += 1
                    break
                elif err2 != "rate_limited":
                    if err2 and err2.startswith("unavailable"):
                        unavailable += 1
                    else:
                        failed += 1
                    break
            else:
                print(f"  Still rate limited after 5 retries. Saving and exiting. Re-run with --resume")
                save_checkpoint(resolved)
                write_outputs(all_ids, resolved, csv_files)
                sys.exit(2)
        else:
            failed += 1
            consecutive_ok += 1

        if (i + 1) % 500 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            eta = (len(remaining) - i - 1) / rate
            save_checkpoint(resolved)
            print(f"  [{i+1}/{len(remaining)}] {success} ok, {unavailable} unavail, {failed} fail | {rate:.1f}/s | ETA {eta/60:.0f}m")

        delay = random.uniform(0.8, 1.5)
        if consecutive_ok > 300:
            delay = random.uniform(0.5, 1.0)
        time.sleep(delay)

    save_checkpoint(resolved)
    elapsed = time.time() - start_time

    print(f"\n{'='*60}")
    print(f"Done in {elapsed/60:.1f} minutes")
    print(f"Resolved: {success}")
    print(f"Unavailable/suspended: {unavailable}")
    print(f"Failed: {failed}")
    print(f"Rate limited: {rate_limited_count} times")
    print(f"\nWriting output files...")
    write_outputs(all_ids, resolved, csv_files)
    print("\nDone! Import all_resolved.csv (or individual resolved_*.csv files) via the dashboard.")


if __name__ == "__main__":
    main()
