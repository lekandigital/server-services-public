# Twitter Bot Dashboard (:8003)

Web dashboard to monitor and control the automated Twitter follow/unfollow bot. Also hosts the Server Portal on :8001.

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
