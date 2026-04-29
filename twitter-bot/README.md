# Twitter Bot Dashboard (:8003)

Web dashboard to monitor and control the automated Twitter follow/unfollow bot. Also hosts the Server Portal on :8001.

## Screenshots

### Dashboard Overview
Real-time bot status, queue summary, follow/unfollow progress bars, and recent activity feed.

![Twitter Bot Dashboard](screenshots/dashboard-main.png)

## How It Works

- `dashboard.py` — Flask web dashboard showing bot status, queue stats, follow/unfollow progress, and controls
- `twitter_bot.py` — Selenium-based bot that automates follow/unfollow actions using Chrome
- `portal.py` — The Server Portal (service directory), also runs from this directory on :8001
- Bot state tracked in `queue.json`, CSV logs, and `history.jsonl`
- Dashboard can start/stop the bot, trigger immediate actions, view logs

## Dependencies

```
Python 3.10+
Google Chrome + ChromeDriver
Selenium WebDriver

pip packages:
  flask==3.1.2
  flask-cors==6.0.2
  Flask-HTTPAuth==4.8.0
  pandas
  selenium==4.40.0
  webdriver-manager
  requests==2.32.5
```

### System packages (Ubuntu 22.04)

```bash
# Chrome
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update && sudo apt install -y google-chrome-stable

pip3 install -r requirements.txt
```

## Files

| File | Purpose |
|------|---------|
| `dashboard.py` | Flask dashboard (port 8003) |
| `twitter_bot.py` | Selenium bot — follow/unfollow automation |
| `portal.py` | Server Portal (port 8001) — shared working dir |
| `skip_trenton.py` | Skip-list processor for specific accounts |
| `diagnose.py` | Data diagnostic utility |
| `reconcile.py` | CSV/queue reconciliation tool |
| `reassign.py` | Queue status reassignment |
| `csvcheck.py` | CSV integrity checker |
| `resolve_ids.py` | Twitter ID resolver |
| `migrate_to_queue.py` | Migration tool for queue format |
| `audit_queue.py` | Offline audit for duplicate queue rows and CSV overlap (`--fix` repairs queue.json) |
| `queue_dedupe.py` | Shared dedupe / ordering (smallest source list first, then follower count) |
| `bot_config.json` | Bot configuration (timing, lists, etc.) |
| `run_bot.sh` | Shell launcher for the bot |
| `run_dashboard.sh` | Shell launcher for dashboard |
| `run_portal.sh` | Shell launcher for portal |

## Runtime Data (created at runtime, not in repo)

```
queue.json          — Master follow/unfollow queue
followed.csv        — Log of followed accounts
unfollowed.csv      — Log of unfollowed accounts
history.jsonl       — Action history log
daily_counts.json   — Daily rate-limit tracking
cacheforlogin.json  — Chrome session cookies
input.csv           — Input list of accounts to process
```

## Bundled Chrome Extensions

The `extensions/` directory contains two archived third-party Chrome extensions useful for exporting Twitter/X data (followers, following, posts). These are **not my code** — they are included here as an offline archive for convenience.

| Extension | What it does | Original link |
|-----------|-------------|---------------|
| **XPorter** | Export posts, followers & following to CSV / JSON / XLSX | [Chrome Web Store](https://chromewebstore.google.com/detail/xporter-free-x-twitter-ex/jghmghialodmkmbcpfnhkgllkmjafmja) |
| **xExport** | Export followers & following lists | [Chrome Web Store](https://chromewebstore.google.com/detail/xexport-export-twitter-fo/cppccmpggnomnbajdchpjahagpockdmc) |

### How to install (sideload) an extension

1. Open **Google Chrome** and navigate to `chrome://extensions`
2. Enable **Developer mode** using the toggle in the top-right corner
3. Click **Load unpacked**
4. Browse to the extension folder you want to install, e.g. `extensions/xporter/` or `extensions/xexport/`
5. The extension will appear in your toolbar — navigate to any X (Twitter) profile to use it

> **Note:** Because these are loaded unpacked (not from the Chrome Web Store), Chrome may show a "Developer mode extensions" warning on startup. This is normal and can be dismissed.

Each extension folder contains its own `README.md` with additional details.

### Queue, CSV imports, and duplicates

- **`twitter_csv.py`** maps XPorter / xExport columns (`username`, `id` / `User ID`, `followers_count` / `Follower Count`) into a common shape. Dashboard CSV imports store optional **`followers_count`** and **`user_id`** on queue rows when present.
- **`audit_queue.py`** checks `queue.json`, `followed.csv`, and `unfollowed.csv` for duplicate handles, overlapping identity keys (handle + numeric id), and pending rows that are already in the CSV logs. Run from your data directory, e.g. `python3 audit_queue.py --data-dir .` or `--scan-csvs list.csv`. Use **`--fix`** only after a backup: it dedupes the queue and aligns pending statuses with the CSVs.
- **`twitter_bot.py`** loads pending queue rows, **dedupes duplicate handles** (keeping the row whose **`source_list` has the fewest pending accounts** — smallest list wins — then lowest **`followers_count`**), sorts **smallest list first, then lowest follower count**, then skips accounts already listed in `followed.csv` / `unfollowed.csv`.

## Run Locally

```bash
pip3 install -r requirements.txt

# Start dashboard
python3 dashboard.py --port 8003

# Start bot (in separate terminal)
python3 twitter_bot.py run

# Start portal (in separate terminal)
python3 portal.py --port 8001
```
