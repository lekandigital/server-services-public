import pandas as pd
import time
import random
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException, WebDriverException, InvalidSessionIdException
from webdriver_manager.chrome import ChromeDriverManager
import os
import json
from urllib3.exceptions import MaxRetryError
from urllib.error import URLError
import sys

from queue_dedupe import dedupe_best_pending

# Helper function to update queue.json status
def update_queue_status(username, new_status, skip_reason=None):
    """Update a user's status in queue.json and append to history.jsonl"""
    queue_file = "queue.json"
    if not os.path.exists(queue_file):
        return
    
    try:
        with open(queue_file, 'r') as f:
            queue = json.load(f)
        
        username_lower = username.lower()
        updated = False
        for entry in queue:
            if entry.get("username", "").lower() == username_lower:
                entry["status"] = new_status
                entry["timestamp"] = datetime.now().isoformat()
                if skip_reason:
                    entry["skip_reason"] = skip_reason
                updated = True
                break
        
        if updated:
            with open(queue_file, 'w') as f:
                json.dump(queue, f, indent=1)
            
            # Also append to history.jsonl for the dashboard's Recent Activity feed
            try:
                action_map = {
                    "followed": "follow",
                    "unfollowed": "unfollow",
                    "skipped": "skip",
                }
                action = action_map.get(new_status)
                if action:
                    history_entry = {
                        "action": action,
                        "username": username,
                        "timestamp": datetime.now().isoformat(),
                    }
                    if skip_reason:
                        history_entry["skip_reason"] = skip_reason
                    with open("history.jsonl", "a", encoding="utf-8") as hf:
                        hf.write(json.dumps(history_entry) + "\n")
            except Exception as he:
                print(f"⚠️ Error appending to history.jsonl: {he}")
    except Exception as e:
        print(f"⚠️ Error updating queue.json: {e}")

# Enhanced rate limiting class with burst detection and natural timing

def load_daily_counts():
    """Load daily follow/unfollow counts from persistent storage"""
    counts_file = "daily_counts.json"
    today = datetime.now().strftime("%Y-%m-%d")
    
    if os.path.exists(counts_file):
        try:
            with open(counts_file, 'r') as f:
                data = json.load(f)
            
            # If it's a new day, reset counts
            if data.get('date') != today:
                print(f"🌅 New day detected! Resetting daily counts (was {data.get('date', 'unknown')})")
                return {'date': today, 'follows': 0, 'unfollows': 0, 'likes': 0}
            
            print(f"📊 Loaded daily counts: {data['follows']} follows, {data['unfollows']} unfollows, {data['likes']} likes")
            return data
        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️ Error reading daily_counts.json: {e}. Creating new file.")
    
    # Create new file for today
    print("📝 Creating new daily counts file")
    return {'date': today, 'follows': 0, 'unfollows': 0, 'likes': 0}

def is_driver_alive(driver):
    """Check if the WebDriver session is still active"""
    try:
        driver.current_url
        return True
    except (InvalidSessionIdException, WebDriverException):
        return False

def save_daily_counts(follows, unfollows, likes):
    """Save daily follow/unfollow counts to persistent storage"""
    counts_file = "daily_counts.json"
    today = datetime.now().strftime("%Y-%m-%d")
    
    data = {
        'date': today,
        'follows': follows,
        'unfollows': unfollows,
        'likes': likes,
        'last_updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    try:
        with open(counts_file, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving daily counts: {e}")

def increment_daily_count(action_type):
    """Increment and save daily count for specific action"""
    counts = load_daily_counts()
    
    if action_type in ['follow', 'follows']:
        counts['follows'] += 1
    elif action_type in ['unfollow', 'unfollows']:
        counts['unfollows'] += 1
    elif action_type in ['like', 'likes']:
        counts['likes'] += 1
    
    save_daily_counts(counts['follows'], counts['unfollows'], counts['likes'])
    return counts

# Enhanced rate limiting class with burst detection and natural timing
def save_debug_snapshot(driver, prefix):
    """Save screenshot and page source for debugging"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not os.path.exists("debug"):
            os.makedirs("debug")
            
        screenshot_path = f"debug/{prefix}_{timestamp}.png"
        source_path = f"debug/{prefix}_{timestamp}.html"
        
        driver.save_screenshot(screenshot_path)
        with open(source_path, "w", encoding='utf-8') as f:
            f.write(driver.page_source)
            
        print(f"📸 Debug snapshot saved to {screenshot_path}")
    except Exception as e:
        print(f"⚠️ Failed to save debug snapshot: {e}")

class RateLimit:
    def __init__(self, daily_limit, hourly_limit, burst_limit=None, burst_window=300, action_type='unknown'):
        self.daily_limit = daily_limit
        self.hourly_limit = hourly_limit
        self.burst_limit = burst_limit or max(3, hourly_limit // 12)  # Conservative burst
        self.burst_window = burst_window  # 5 minutes
        self.action_type = action_type  # 'follow', 'unfollow', or 'like'
        
        # Load persistent counts
        daily_counts = load_daily_counts()
        if action_type == 'follow':
            self.count_day = daily_counts['follows']
        elif action_type == 'unfollow':
            self.count_day = daily_counts['unfollows']
        elif action_type == 'like':
            self.count_day = daily_counts['likes']
        else:
            self.count_day = 0
            
        self.count_hour = 0
        self.recent_actions = []  # Track burst activity
        self.last_action = datetime.now()
        self.hour_start = datetime.now()
        self.day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.hour_start = datetime.now()
        self.day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    def can_perform(self):
        now = datetime.now()
        
        # Reset daily counter - check if new day and reload persistent counts
        if now >= self.day_start + timedelta(days=1):
            # Reload counts in case another instance updated them
            daily_counts = load_daily_counts()
            if self.action_type == 'follow':
                self.count_day = daily_counts['follows']
            elif self.action_type == 'unfollow':
                self.count_day = daily_counts['unfollows']
            elif self.action_type == 'like':
                self.count_day = daily_counts['likes']
            else:
                self.count_day = 0
                
            self.day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            print("⏰ Daily counter reset.")
            
        # Reset hourly counter
        if (now - self.hour_start).total_seconds() >= 3600:
            self.count_hour = 0
            self.hour_start = now
            print("⏰ Hourly counter reset.")
            
        # Clean old burst actions
        self.recent_actions = [action_time for action_time in self.recent_actions 
                              if (now - action_time).total_seconds() < self.burst_window]
        
        # Check limits
        if self.count_day >= self.daily_limit:
            return False
        if self.count_hour >= self.hourly_limit:
            return False
        if len(self.recent_actions) >= self.burst_limit:
            wait_time = self.burst_window - (now - self.recent_actions[0]).total_seconds()
            if wait_time > 0:
                print(f"⏳ Burst limit reached. Waiting {round(wait_time)}s...")
                time.sleep(wait_time + random.uniform(10, 30))
                return self.can_perform()  # Recheck after wait
        
        # Minimum time between actions (anti-detection)
        elapsed = (now - self.last_action).total_seconds()
        min_wait = 45 + random.uniform(15, 45)  # 60-90 seconds between actions
        if elapsed < min_wait:
            wait = min_wait - elapsed + random.uniform(5, 15)
            print(f"⏳ Anti-detection delay: {round(wait)}s")
            time.sleep(wait)
            
        return True

    def record(self):
        now = datetime.now()
        self.count_day += 1
        self.count_hour += 1
        self.recent_actions.append(now)
        self.last_action = now
        
        # Update persistent storage
        if self.action_type != 'unknown':
            increment_daily_count(self.action_type)
        
        # Variable post-action delay based on recent activity
        activity_factor = len(self.recent_actions) / self.burst_limit
        base_delay = 30 + (activity_factor * 30)  # 30-60 seconds base
        extra_delay = random.uniform(base_delay, base_delay * 1.5)
        
        print(f"⏳ Post-action delay: {round(extra_delay)}s")
        time.sleep(extra_delay)

# Enhanced selenium driver setup with stealth features
def setup_driver():
    opts = Options()
    opts.set_capability("pageLoadStrategy", "eager")
    
    # Essential stability and stealth options
    opts.add_argument("--disable-gpu")
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1366,768")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-infobars")
    opts.add_argument("--mute-audio")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    
    # Throttling and backgrounding can cause issues during long runs
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    
    # Realistic user agent
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    )
    
    # Standard experimental options for stealth
    opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)
    
    # Disable image/media loading for performance
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2, "popups": 2, "geolocation": 2, 
            "notifications": 2, "media_stream": 2
        }
    }
    opts.add_experimental_option("prefs", prefs)
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(45)
    driver.implicitly_wait(10)
    
    # Anti-detection scripts
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = { runtime: {} };
        """
    })
    
    return driver

# Enhanced safe page load with better error handling
def safe_get(driver, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            # ADD THIS CHECK
            if not is_driver_alive(driver):
                print("⚠️ Driver session dead - needs recovery")
                return False
        except Exception:
            # If checking driver health raises, treat as dead
            print("⚠️ Driver health check raised an exception")
            return False
        try:
            if "twitter.com" in url:
                url = url.replace("twitter.com", "x.com")
            
            driver.get(url)
            
            # Wait for page to load completely
            WebDriverWait(driver, 25).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            # Check for rate limits with more specific detection
            page_source_lower = driver.page_source.lower()
            rate_limit_indicators = [
                "rate limit exceeded", "too many requests", "try again later",
                "temporarily unavailable", "over capacity"
            ]
            
            if any(indicator in page_source_lower for indicator in rate_limit_indicators):
                wait_time = min(180, 30 * attempt + random.uniform(30, 60))
                print(f"⚠️ Rate limited on {url} (attempt {attempt}/{retries}). Waiting {round(wait_time)}s...")
                time.sleep(wait_time)
                continue
                
            return True
            
        except Exception as e:
            print(f"❌ Error loading {url} (attempt {attempt}/{retries}): {str(e)[:100]}")
            
            if attempt < retries:
                wait_time = min(60, attempt * 15 + random.uniform(10, 20))
                print(f"⏳ Waiting {round(wait_time)}s before retry...")
                time.sleep(wait_time)
                
    print(f"❌ All {retries} attempts to load {url} failed.")
    return False

# Cookie loading function from twitter_monitor-2.py
def load_cookies_from_file(driver, cookie_file, domain):
    """Load cookies from a JSON file and add them to the WebDriver."""
    with open(cookie_file, "r") as f:
        cookies = json.load(f)
    driver.get(f"https://{domain}")
    for c in cookies:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }
        if "expirationDate" in c:
            cookie["expiry"] = int(c["expirationDate"])
        try:
            driver.add_cookie(cookie)
        except Exception as e:
            print(f"Error adding cookie {c['name']} for {domain}: {e}")


def check_session_alive(driver):
    """Check if the bot is still logged in to X/Twitter.
    Returns True if logged in, False if session has expired."""
    try:
        page_source = driver.page_source
        # Signs that we are logged OUT
        logged_out_signals = [
            "Don't miss what's happening",
            "New to X?",
            "Sign up now",
            "Sign up with Google",
            "Sign up with Apple",
            "Create account",
            'href="/i/flow/signup"',
            'href="/i/flow/login"',
        ]
        for signal in logged_out_signals:
            if signal in page_source:
                print(f"🚨 SESSION EXPIRED - detected logged-out signal: '{signal}'")
                return False
        return True
    except Exception as e:
        print(f"⚠️ Error checking session: {e}")
        return True  # Assume alive if we can't check


def recover_driver(driver):
    """Attempt to recover or restart the WebDriver"""
    print("🔄 Recovering WebDriver session...")
    try:
        driver.quit()
    except:
        pass
    
    time.sleep(5)
    try:
        new_driver = setup_driver()
    except Exception as e:
        print(f"❌ Could not setup new driver: {e}")
        return None
    
    try:
        load_cookies_from_file(new_driver, "cacheforlogin.json", "x.com")
        if safe_get(new_driver, "https://x.com/home"):
            time.sleep(3)
            if check_session_alive(new_driver):
                print("✅ Driver recovered and session is alive!")
                return new_driver
            else:
                print("❌ Driver recovered but session is still expired. Need new cookies.")
                return None
    except Exception as e:
        print(f"❌ Recovery failed: {e}")
    
    return None

# Enhanced human-like scrolling with natural patterns
def human_scroll(driver):
    """More natural scrolling behavior to avoid detection"""
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        
        # Random scrolling patterns
        scroll_actions = [
            lambda: body.send_keys(Keys.PAGE_DOWN),
            lambda: body.send_keys(Keys.ARROW_DOWN * random.randint(3, 7)),
            lambda: driver.execute_script(f"window.scrollBy(0, {random.randint(200, 600)});"),
        ]
        
        # Execute random scroll action
        action = random.choice(scroll_actions)
        action()
        time.sleep(random.uniform(1, 3))
        
        # Sometimes scroll back up (human behavior)
        if random.random() < 0.3:
            if random.random() < 0.5:
                body.send_keys(Keys.PAGE_UP)
            else:
                body.send_keys(Keys.ARROW_UP * random.randint(1, 3))
            time.sleep(random.uniform(0.5, 1.5))
            
    except Exception:
        pass  # Fail silently

# Natural clicking with human-like hesitation
def natural_click(driver, element):
    """Perform human-like clicking with movement and timing"""
    try:
        # Scroll element into view
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", element)
        time.sleep(random.uniform(0.5, 1.2))
        
        # Move to element with ActionChains
        actions = ActionChains(driver)
        actions.move_to_element(element)
        
        # Add slight hesitation before click
        actions.pause(random.uniform(0.3, 0.8))
        actions.click(element)
        actions.perform()
        
        return True
    except StaleElementReferenceException:
        # This exception is now handled by the robust_click wrapper
        raise StaleElementReferenceException
    except Exception as e:
        print(f"⚠️ Click failed: {str(e)[:50]}")
        return False

# Wrapper for robust clicking with retries for stale elements
def robust_click(driver, selector, retries=3, delay=1):
    """Finds an element by its selector and clicks it, retrying if it becomes stale."""
    for attempt in range(retries):
        try:
            element = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, selector))
            )
            if natural_click(driver, element):
                return True
        except StaleElementReferenceException:
            print(f"⚠️ Stale element on attempt {attempt + 1}/{retries} for selector: {selector[:50]}...")
            time.sleep(delay)
        except Exception as e:
            # If it's a critical connection error, re-raise it to be caught by the main loop
            if "Connection refused" in str(e) or "HTTPConnectionPool" in str(e):
                raise e
            print(f"❌ Failed to find/click element with selector {selector[:50]}: {e}")
            return False
    print(f"❌ Failed to click element after {retries} retries: {selector[:50]}...")
    return False

# Comprehensive follow function with intelligent liking
def follow_user(driver, username, like_limiter):
    """Follow a user and like their recent posts with anti-detection measures"""
    try:
        url = f"https://x.com/i/user/{username}" if username.isdigit() else f"https://x.com/{username}"
        if not safe_get(driver, url):
            return False
            
        # Initial browsing delay - human behavior simulation
        browse_delay = random.uniform(3, 8)
        print(f"👀 Browsing @{username}'s profile for {round(browse_delay, 1)}s...")
        time.sleep(browse_delay)
        
        # Detect deleted/suspended/non-existent pages before wasting time
        try:
            page_text = driver.page_source.lower()
            if "this page doesn" in page_text or "this account doesn" in page_text:
                print(f"⚠️ @{username}: page doesn't exist (deleted/invalid). Skipping permanently.")
                return "not_found"
            if "account suspended" in page_text or "account is suspended" in page_text:
                print(f"⚠️ @{username}: account suspended. Skipping permanently.")
                return "not_found"
        except Exception:
            pass
        
        # Natural scrolling to view content
        for _ in range(random.randint(2, 4)):
            human_scroll(driver)
            time.sleep(random.uniform(1, 3))
        
        # Check if already following or pending with VERY precise detection
        try:
            # Check for following status
            following_indicators = [
                f"//button[@aria-label='Following @{username}']",  # Very specific aria-label
                f"//span[text()='Following' and ancestor::button[contains(@aria-label, '@{username}')]]"  # Following text in user-specific button
            ]
            
            # Check for pending status
            pending_indicators = [
                f"//button[@aria-label='Pending @{username}']",    # Very specific pending
                f"//button[contains(@data-testid, '-cancel')]//span[text()='Pending']",  # Pending button with cancel testid
                f"//span[text()='Pending' and ancestor::button[contains(@data-testid, 'cancel')]]"  # Pending text in cancel button
            ]
            
            already_following = False
            is_pending = False
            found_indicator = None
            
            # Check following status first
            for indicator in following_indicators:
                try:
                    element = WebDriverWait(driver, 1).until(
                        EC.presence_of_element_located((By.XPATH, indicator))
                    )
                    if element and element.is_displayed():
                        # Triple-check: verify the element is actually about this specific user
                        aria_label = element.get_attribute("aria-label") or ""
                        if f"@{username}" in aria_label.lower():
                            already_following = True
                            found_indicator = f"{indicator} -> '{aria_label}'"
                            break
                except:
                    continue
            
            # Check pending status if not already following
            if not already_following:
                for indicator in pending_indicators:
                    try:
                        element = WebDriverWait(driver, 1).until(
                            EC.presence_of_element_located((By.XPATH, indicator))
                        )
                        if element and element.is_displayed():
                            is_pending = True
                            found_indicator = f"{indicator} -> pending detected"
                            break
                    except:
                        continue
            
            if already_following:
                print(f"ℹ️ Already following @{username}. Skipping.")
                return "skipped"
            elif is_pending:
                print(f"ℹ️ Follow request pending for @{username}. Skipping.")
                return "skipped"
            else:
                # Proceeding with follow - no need to announce
                pass
                
        except Exception as e:
            print(f"⚠️ Error checking follow status for @{username}: {str(e)[:50]}")
            # If check fails, assume NOT following and try to follow
        
        # Like posts before following (more natural engagement pattern)
        liked_count = 0
        if like_limiter.can_perform():
            liked_count = like_recent_posts(driver, username, like_limiter, max_likes=random.randint(1, 3))
        
        # Find and click follow button with robust retries
        follow_selectors = [
            f"//button[@aria-label='Follow @{username}' and not(contains(@aria-label, 'Following'))]", # Specific
            "//button[.//span[text()='Follow'] and not(.//span[text()='Following'])]",                 # Generic
            "//div[@data-testid='placementTracking']//button[.//span[text()='Follow']]",              # Contextual
            "//button[contains(@aria-label, 'Follow')]"                                               # Fallback
        ]
        
        followed_successfully = False
        for selector in follow_selectors:
            if robust_click(driver, selector):
                print(f"✅ Followed @{username} using selector: {selector[:40]}...")
                followed_successfully = True
                break
        
        if followed_successfully:
            # Post-follow verification
            time.sleep(random.uniform(2, 4))
            verified = False
            try:
                # Relaxed verification: Just look for "Following" or "Pending" anywhere in a button
                verify_selectors = [
                    f"//button[contains(@aria-label, 'Following @{username}')]",
                    f"//button[contains(@aria-label, 'Pending @{username}')]",
                    "//button[.//span[text()='Following']]",
                    "//button[.//span[text()='Pending']]"
                ]
                
                for v_sel in verify_selectors:
                    try:
                        WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.XPATH, v_sel)))
                        verified = True
                        break
                    except:
                        continue
                
                if verified:
                    print(f"✅ Follow confirmed for @{username}")
                else:
                    print(f"⚠️ Could not verify follow state change for @{username}")
                    save_debug_snapshot(driver, f"verify_fail_{username}")
                    # Check if session is still alive - if not, this follow was fake
                    if not check_session_alive(driver):
                        print(f"🚨 SESSION EXPIRED! Follow of @{username} was NOT real. Aborting.")
                        return False
            except Exception as e:
                print(f"⚠️ Error verifying follow for @{username}: {e}")
                save_debug_snapshot(driver, f"verify_error_{username}")
            
            if liked_count > 0:
                print(f"💝 Bonus: Liked {liked_count} posts during follow")
            
            print(f"📝 @{username} followed; will unfollow after 3 days")
            return True
        else:
            print(f"❌ No suitable follow button found for @{username}")
            save_debug_snapshot(driver, f"follow_fail_{username}")
            return False
        
    except Exception as e:
        print(f"❌ Failed to follow @{username}: {str(e)[:100]}")
        save_debug_snapshot(driver, f"follow_exception_{username}")
        return False

def like_recent_posts(driver, username, like_limiter, max_likes=2):
    """Like recent posts with anti-detection timing and natural behavior"""
    liked_count = 0
    
    try:
        # Find tweet elements with multiple strategies
        tweet_selectors = [
            "//article[@role='article']",
            "//div[@data-testid='tweet']",
            "//article[@data-testid='tweet']"
        ]
        
        tweets = []
        for selector in tweet_selectors:
            try:
                tweets = driver.find_elements(By.XPATH, selector)
                if tweets:
                    break
            except:
                continue
        
        if not tweets:
            print(f"ℹ️ No tweets found for @{username}")
            return 0
        
        print(f"👁️ Found {len(tweets)} posts for @{username}")
        
        # Process tweets with natural timing
        for i, tweet in enumerate(tweets[:max_likes * 2]):  # Check more than we plan to like
            if liked_count >= max_likes:
                break
                
            if not like_limiter.can_perform():
                break
            
            try:
                # Multiple like button selectors for resilience
                like_selectors = [
                    ".//button[@data-testid='like']",
                    ".//div[@data-testid='like']",
                    ".//button[contains(@aria-label, 'Like')]",
                    ".//button[.//div[@data-testid='like']]"
                ]
                
                like_button = None
                
                for selector in like_selectors:
                    try:
                        # Check if the element exists within the tweet context
                        btn = tweet.find_element(By.XPATH, selector)
                        # Ensure it's not already liked (sanity check)
                        aria = btn.get_attribute('aria-label') or ""
                        lower_aria = aria.lower()
                        if "liked" in lower_aria or "unlike" in lower_aria:
                            # print(f"ℹ️ Post {i+1} already liked (checked during selection)")
                            continue
                            
                        like_button = btn
                        break
                    except:
                        continue
                
                if not like_button:
                    # Often silent failure is better than spamming logs if post is already liked
                    continue

                # Natural hesitation before liking
                hesitation = random.uniform(2, 6)
                print(f"🤔 Considering post {i+1}... ({round(hesitation, 1)}s)")
                time.sleep(hesitation)
                
                # Use natural_click with the element directly
                if natural_click(driver, like_button):
                    print(f"❤️ Liked post {i+1} for @{username}")
                    liked_count += 1
                    like_limiter.record()

                    # Save success snapshot for verification (DISABLED for production)
                    # try:
                    #     save_debug_snapshot(driver, f"success_like_{username}_{i+1}")
                    # except:
                    #     pass
                    
                    # Delay between likes
                    if liked_count < max_likes:
                        inter_like_delay = random.uniform(8, 20)
                        print(f"⏳ Inter-like delay: {round(inter_like_delay, 1)}s")
                        time.sleep(inter_like_delay)
                else:
                    print(f"❌ Failed to click like on post {i+1}")
                    save_debug_snapshot(driver, f"like_click_fail_{username}_{i}")
                    
            except Exception as e:
                print(f"⚠️ Error processing post {i+1}: {str(e)[:50]}")
                save_debug_snapshot(driver, f"like_error_{username}_{i}")
                continue
        
        return liked_count
        
    except Exception as e:
        print(f"❌ Error in like_recent_posts: {str(e)[:100]}")
        return 0

# Enhanced unfollow function with better detection avoidance
def unfollow_user(driver, username):
    """Unfollow a user with a robust, multi-path strategy."""
    try:
        url = f"https://x.com/i/user/{username}" if username.isdigit() else f"https://x.com/{username}"
        if not safe_get(driver, url):
            return False
            
        browse_delay = random.uniform(2, 5)
        print(f"👀 Checking @{username}'s profile for {round(browse_delay, 1)}s...")
        time.sleep(browse_delay)
        
        # Detect deleted/suspended/non-existent pages before wasting time
        try:
            page_text = driver.page_source.lower()
            if "this page doesn" in page_text or "this account doesn" in page_text:
                print(f"⚠️ @{username}: page doesn't exist (deleted/invalid). Skipping.")
                return True
            if "account suspended" in page_text or "account is suspended" in page_text:
                print(f"⚠️ @{username}: account suspended. Skipping.")
                return True
        except Exception:
            pass
        
        human_scroll(driver)
        time.sleep(random.uniform(1, 3))

        # Check if they follow us back, which is a reason to skip
        try:
            if driver.find_element(By.XPATH, "//span[contains(text(), 'Follows you')]").is_displayed():
                print(f"ℹ️ @{username} follows you back. Skipping unfollow.")
                return "follows_back"  # Distinct return value - don't record as unfollowed
        except:
            pass

        # --- UNFOLLOW STRATEGY ---
        print(f"Attempting to unfollow @{username}...")

        # --- STRATEGY 1: Cancel Pending Follow Request ---
        # Check if there's a pending follow request that can be cancelled
        pending_cancel_selectors = [
            f"//button[contains(@data-testid, '{username}') and contains(@data-testid, 'cancel')]",
            f"//button[contains(@data-testid, 'cancel')]//span[text()='Pending']"
        ]
        
        for selector in pending_cancel_selectors:
            if robust_click(driver, selector):
                print("✅ Found pending follow request. Clicking to cancel.")
                time.sleep(random.uniform(1, 2))
                
                # Click the "Discard" confirmation button
                discard_button_selector = "//button[@data-testid='confirmationSheetConfirm']//span[text()='Discard']"
                if robust_click(driver, discard_button_selector):
                    print(f"✅ Cancelled pending follow request for @{username}.")
                    return True
                else:
                    print("❌ Failed to click 'Discard' button. Trying other methods.")
                    break

        # --- STRATEGY 2: Standard "Following" Button ---
        # This is the most common button for established follows.
        standard_unfollow_selector = f"//div[@data-testid='UserProfileHeader_Items']//button[contains(@aria-label, 'Following @{username}')]"
        if robust_click(driver, standard_unfollow_selector):
            print("✅ Clicked standard 'Following' button.")
            time.sleep(random.uniform(1, 2))
            
            confirm_button_selector = "//button[@data-testid='confirmationSheetConfirm']"
            if robust_click(driver, confirm_button_selector):
                print(f"✅ Unfollowed @{username} (Standard Flow).")
                return True
            else:
                print("❌ Failed to click confirmation button. Assuming unfollow failed but moving on.")
                return True # Return True to avoid getting stuck

        # --- STRATEGY 3: Premium "More Actions" Menu (ARIA-LABEL BASED) ---
        print("ℹ️ Standard button not found or failed. Trying Premium unfollow via aria-label trigger.")
        # Try specific selector first, then generic (for numeric IDs where screen name differs)
        premium_trigger_selectors = [
            f"//button[@aria-label='Unfollow @{username}' and @aria-haspopup='menu']",
            "//button[starts-with(@aria-label, 'Unfollow @') and @aria-haspopup='menu']"
        ]
        premium_menu_opened = False
        for premium_selector in premium_trigger_selectors:
            if robust_click(driver, premium_selector):
                print(f"✅ Opened Premium menu via aria-label trigger.")
                premium_menu_opened = True
                time.sleep(random.uniform(1, 1.8))
                
                # Now click the actual 'Unfollow' option in the dropdown (generic — works for any screen name)
                unfollow_menu_item_selectors = [
                    f"//div[@role='menu']//div[@role='menuitem']//span[text()='Unfollow @{username}']",
                    "//div[@role='menu']//div[@role='menuitem']//span[contains(text(), 'Unfollow @')]"
                ]
                for menu_sel in unfollow_menu_item_selectors:
                    if robust_click(driver, menu_sel):
                        print(f"✅ Unfollowed @{username} via Premium menu (aria-label path).")
                        return True
                print("⚠️ Failed to click 'Unfollow' in opened Premium menu.")
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                break
        if not premium_menu_opened:
            print(f"ℹ️ Premium unfollow trigger not found for @{username}.")

        # Path 1: Try the "Premium" account flow (three-dots menu / userActions button)
        more_options_selectors = [
            f"//a[@href='/{username}']//ancestor::div[2]//div[@data-testid='userActions']",
            "//div[@data-testid='userActions']"  # Generic fallback for numeric IDs (redirected URL)
        ]
        for more_options_selector in more_options_selectors:
            if robust_click(driver, more_options_selector):
                print("ℹ️ 'More' menu button clicked. Trying premium unfollow.")
                time.sleep(random.uniform(1, 1.5))
                
                unfollow_menu_item_selector = "//div[@role='menuitem']//span[contains(text(), 'Unfollow @')]"
                if robust_click(driver, unfollow_menu_item_selector):
                    print(f"✅ Unfollowed @{username} (Premium Flow).")
                    return True
                else:
                    print("⚠️ Failed to click unfollow in menu. Closing menu and trying standard flow.")
                    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                break
        
        # Path 2: Try the "Standard" account flow (the "Following" button)
        print("ℹ️ Premium flow failed or not applicable. Trying standard flow.")
        following_selectors = [
            f"//button[@aria-label='Following @{username}']",
            f"//button[@data-testid='{username}-unfollow']",
            "//button[starts-with(@aria-label, 'Following @')]"  # Generic fallback for numeric IDs
        ]

        for selector in following_selectors:
            if robust_click(driver, selector):
                print(f"✅ Standard 'Following' button clicked using: {selector[:50]}...")
                time.sleep(random.uniform(1, 2)) # Wait for confirmation dialog

                confirm_button_selector = "//button[@data-testid='confirmationSheetConfirm']"
                if robust_click(driver, confirm_button_selector):
                    print(f"✅ Unfollowed @{username} (Standard Flow).")
                    return True
                else:
                    print("❌ Failed to click confirmation button. Assuming action completed or failed.")
                    return True # Return True to avoid getting stuck

        print(f"❌ All unfollow methods failed for @{username}.")
        save_debug_snapshot(driver, f"unfollow_allfail_{username}")
        # Check if session is still alive
        if not check_session_alive(driver):
            print(f"🚨 SESSION EXPIRED! Unfollow of @{username} was NOT real. Aborting.")
            return False
        # If session is alive, user may genuinely already be unfollowed
        print(f"ℹ️ Session is alive but unfollow buttons not found. Assuming already unfollowed.")
        return True

    except Exception as e:
        print(f"❌ An unexpected error occurred in unfollow_user for @{username}: {e}")
        return False

# Enhanced break system with activity-based timing
def smart_break(recent_activity_count, base_minutes=5):
    """Take breaks based on recent activity to avoid detection patterns"""
    
    # Calculate break time based on activity intensity
    if recent_activity_count >= 8:  # High activity
        break_time = random.uniform(base_minutes * 3, base_minutes * 5)
        break_type = "Long break (high activity)"
    elif recent_activity_count >= 5:  # Medium activity  
        break_time = random.uniform(base_minutes * 1.5, base_minutes * 2.5)
        break_type = "Medium break (moderate activity)"
    else:  # Low activity
        break_time = random.uniform(base_minutes * 0.5, base_minutes * 1.2)
        break_type = "Short break (low activity)"
    
    print(f"🍽️ {break_type}: {round(break_time, 1)} min")
    time.sleep(break_time * 60)

def activity_based_delay(follow_count, unfollow_count, like_count):
    """Calculate delay based on recent activity to maintain natural patterns"""
    total_activity = follow_count + unfollow_count + like_count
    
    if total_activity == 0:
        return random.uniform(60, 120)  # Base delay
    
    # More activity = longer delays
    base_delay = 30
    activity_multiplier = 1 + (total_activity * 0.3)
    delay = base_delay * activity_multiplier + random.uniform(15, 45)
    
    return min(delay, 300)  # Cap at 5 minutes


def scroll_to_bottom_then_top(driver, max_scrolls=100):
    """Scroll to the very bottom of the page slowly, ensuring ALL content loads"""
    print("\n" + "="*70)
    print("⏬ AGGRESSIVE SCROLLING TO BOTTOM TO LOAD ALL USERS")
    print("="*70)
    
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_count = 0
    no_change_count = 0
    pause_after_load = False
    stuck_count = 0  # Track how many times we're truly stuck
    last_user_count = 0  # Track actual user elements found

    while scroll_count < max_scrolls:
        # More aggressive scrolling to load content faster
        scroll_amount = random.randint(800, 1200)  # Larger scrolls
        driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
        scroll_count += 1
        
        # Shorter wait times initially, longer if stuck
        base_wait = 1.5 if stuck_count < 3 else 3.0
        wait_time = base_wait + random.uniform(0.3, 0.8)
        print(f"  📌 Scroll #{scroll_count}: Waiting {round(wait_time, 1)}s...")
        time.sleep(wait_time)
        
        # Extra pause to ensure DOM updates complete
        # This helps ensure "Follows you" indicators render properly
        if scroll_count % 3 == 0:  # Every 3rd scroll
            time.sleep(random.uniform(0.5, 1.0))
        
        # Check for Twitter loading indicators
        try:
            loading_elements = driver.find_elements(By.CSS_SELECTOR, "[role='progressbar'], [data-testid='loadingIndicator']")
            if loading_elements:
                print("  🔄 Loading indicator detected - waiting extra time...")
                time.sleep(random.uniform(2.0, 3.0))
        except Exception:
            pass
        
        # Count actual user elements to verify we're loading more
        current_user_count = 0
        try:
            current_user_count = len(driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']"))
            print(f"  👥 Currently loaded: {current_user_count} users")
            
            # If user count hasn't changed, we might be stuck
            if current_user_count == last_user_count:
                stuck_count += 1
                print(f"  ⚠️ User count unchanged ({stuck_count}/5)")
            else:
                stuck_count = 0  # Reset if we loaded new users
                last_user_count = current_user_count
        except Exception:
            pass
        
        # Check page height
        new_height = driver.execute_script("return document.body.scrollHeight")
        print(f"  📏 Page height: {last_height} -> {new_height}")
        
        if new_height == last_height:
            no_change_count += 1
            print(f"  🛑 No height change detected ({no_change_count}/5)")
            
            # Try different scrolling methods when stuck
            if no_change_count == 1:
                print("  🔄 Trying jump to absolute bottom...")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(random.uniform(2.0, 3.0))
            elif no_change_count == 2:
                print("  🔄 Trying keyboard PageDown...")
                try:
                    body = driver.find_element(By.TAG_NAME, "body")
                    for _ in range(5):
                        body.send_keys(Keys.PAGE_DOWN)
                        time.sleep(0.5)
                except:
                    pass
            elif no_change_count == 3:
                print("  🔄 Trying aggressive scroll loop...")
                for _ in range(10):
                    driver.execute_script("window.scrollBy(0, 500);")
                    time.sleep(0.3)
            elif no_change_count >= 5 and stuck_count >= 5:
                # Both height AND user count haven't changed - truly at bottom
                print(f"✅ Confirmed bottom: height stable for 5 checks AND user count stable")
                break
        else:
            no_change_count = 0
            pause_after_load = True
        
        last_height = new_height
        
        # Progress updates
        if scroll_count % 10 == 0:
            print(f"  📊 Progress: {scroll_count} scrolls, {current_user_count} users loaded")
            # Longer break every 20 scrolls
            if scroll_count % 20 == 0:
                break_time = random.uniform(8, 15)
                print(f"  ☕ Break to avoid rate limits ({round(break_time)}s)...")
                time.sleep(break_time)

    # Small pause at bottom
    time.sleep(random.uniform(2, 4))
    
    # Now return to top with human-like scrolling
    print("\n" + "="*70)
    print("⏫ SCROLLING BACK TO TOP (HUMAN-LIKE PATTERN)")
    print("="*70)
    
    current_position = driver.execute_script("return window.pageYOffset;")
    while current_position > 0:
        scroll_up_amount = random.randint(200, 600)
        driver.execute_script(f"window.scrollBy(0, -{scroll_up_amount});")
        time.sleep(random.uniform(0.3, 0.8))
        current_position = driver.execute_script("return window.pageYOffset;")
    
    print("✅ Successfully returned to top of page")
    time.sleep(random.uniform(2, 3))
    
    return scroll_count


def verify_all_users_loaded(driver):
    """Verify we've loaded all users by checking multiple indicators"""
    try:
        # Method 1: Check for "end of list" text
        page_text = driver.page_source.lower()
        end_indicators = [
            "you've reached the end",
            "end of list",
            "no more results",
            "that's all"
        ]
        
        for indicator in end_indicators:
            if indicator in page_text:
                print(f"✅ Found end indicator: '{indicator}'")
                return True
        
        # Method 2: Count users and verify against displayed count
        user_cells = driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']")
        loaded_count = len(user_cells)
        
        # Try to find the "following" count from the profile
        try:
            # Look for the following count in the page
            following_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/following']")
            for link in following_links:
                text = link.text
                if "Following" in text:
                    # Extract number (e.g., "1,234 Following")
                    import re
                    match = re.search(r'([\d,]+)\s*Following', text)
                    if match:
                        expected_count = int(match.group(1).replace(',', ''))
                        print(f"📊 Expected {expected_count} users, loaded {loaded_count}")
                        
                        # Allow small margin of error
                        if loaded_count >= expected_count - 5:
                            print(f"✅ Loaded count matches expected count")
                            return True
                        else:
                            print(f"⚠️ Still missing {expected_count - loaded_count} users")
                            return False
        except Exception as e:
            print(f"⚠️ Could not verify count: {e}")
        
        # Method 3: If we have a lot of users, assume we're done
        if loaded_count > 500:  # Adjust based on your expected following count
            print(f"✅ Loaded {loaded_count} users - assuming complete")
            return True
        
        return False
        
    except Exception as e:
        print(f"⚠️ Error in verification: {e}")
        return False


def verify_user_cell_quality(driver):
    """Verify that user cells are properly loaded with all data"""
    try:
        user_cells = driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']")
        
        if not user_cells:
            print("⚠️ No user cells found for verification")
            return False
        
        # Sample first 10 cells to check quality
        sample_size = min(10, len(user_cells))
        properly_loaded = 0
        
        print(f"\n🔍 Verifying quality of {sample_size} sample user cells...")
        
        for i, cell in enumerate(user_cells[:sample_size]):
            try:
                # Check if cell has username link
                username_link = cell.find_element(By.CSS_SELECTOR, "a[href*='/']")
                
                # Check if cell is fully rendered (has text content)
                cell_text = cell.text
                
                if username_link and len(cell_text) > 10:
                    properly_loaded += 1
                    print(f"  ✓ Cell {i+1}: Properly loaded")
                else:
                    print(f"  ✗ Cell {i+1}: Incomplete (text length: {len(cell_text)})")
                    
            except Exception as e:
                print(f"  ✗ Cell {i+1}: Error - {str(e)[:30]}")
        
        quality_ratio = properly_loaded / sample_size
        print(f"📊 Quality check: {properly_loaded}/{sample_size} cells properly loaded ({quality_ratio*100:.0f}%)")
        
        if quality_ratio < 0.7:
            print("⚠️ Less than 70% of cells properly loaded - may need another scroll pass")
            return False
        
        print("✅ User cells appear to be properly loaded")
        return True
        
    except Exception as e:
        print(f"⚠️ Error in quality verification: {e}")
        return False


def debug_cell_content(cell, username):
    """Debug function to see what's actually in a cell"""
    print(f"\n🔍 DEBUG: Content analysis for @{username}")
    print("="*60)
    
    try:
        # Full text
        full_text = cell.text
        print(f"Full cell text:\n{full_text}\n")
        
        # Check for indicator element
        try:
            indicator = cell.find_element(By.CSS_SELECTOR, "[data-testid='userFollowIndicator']")
            print(f"✓ Found userFollowIndicator: '{indicator.text}'")
        except:
            print("✗ No userFollowIndicator found")
        
        # Check for "Follows you" in various places
        if "Follows you" in full_text:
            print("✓ 'Follows you' found in full text")
        else:
            print("✗ 'Follows you' NOT found in full text")
        
        # Check all spans
        spans = cell.find_elements(By.TAG_NAME, "span")
        follows_you_spans = [s for s in spans if "Follows you" in s.text]
        if follows_you_spans:
            print(f"✓ Found {len(follows_you_spans)} span(s) with 'Follows you'")
        
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"Debug error: {e}\n")


def verify_follows_back_detection(driver, sample_size=10):
    """Manually verify that 'Follows you' detection is working"""
    print("\n" + "="*70)
    print("🔍 VERIFYING 'FOLLOWS YOU' DETECTION ACCURACY")
    print("="*70)
    
    user_cells = driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']")
    
    if len(user_cells) < sample_size:
        sample_size = len(user_cells)
    
    import random as rand
    sample_indices = rand.sample(range(len(user_cells)), sample_size) if sample_size > 0 else []
    
    correct_detections = 0
    total_checked = 0
    
    for idx in sample_indices:
        cell = user_cells[idx]
        
        try:
            # Get username
            username_element = cell.find_element(By.CSS_SELECTOR, "a[href*='/']")
            username = username_element.get_attribute("href").split("/")[-1]
            
            # Get full cell text
            full_text = cell.text
            
            # Our detection
            our_detection = False
            try:
                indicator = cell.find_element(By.CSS_SELECTOR, "[data-testid='userFollowIndicator']")
                if indicator.is_displayed() and "Follows you" in indicator.text:
                    our_detection = True
            except:
                pass
            
            # Backup check
            if not our_detection and "Follows you" in full_text:
                our_detection = True
            
            print(f"\n@{username}:")
            print(f"  Cell text preview: {full_text[:80].replace(chr(10), ' | ')}")
            print(f"  Our detection: {'FOLLOWS BACK' if our_detection else 'DOES NOT FOLLOW'}")
            
            response = input(f"  Is this correct? [y/n/s(skip)]: ").lower().strip()
            
            if response == 's':
                continue
            elif response == 'y':
                correct_detections += 1
                total_checked += 1
            elif response == 'n':
                total_checked += 1
                print("  ❌ INCORRECT DETECTION")
                # Show more details
                debug_cell_content(cell, username)
            
        except Exception as e:
            print(f"Error checking user: {e}")
            continue
    
    if total_checked > 0:
        accuracy = (correct_detections / total_checked) * 100
        print(f"\n📊 Accuracy: {correct_detections}/{total_checked} ({accuracy:.1f}%)")
        
        if accuracy < 90:
            print("⚠️ WARNING: Detection accuracy is below 90%")
            print("This may indicate Twitter's HTML structure has changed")
            return False
        else:
            print("✅ Detection accuracy is acceptable")
            return True
    
    return True


def manual_verification_sample(driver, user_cells, sample_size=5):
    """Show a sample of users to manually verify detection accuracy"""
    print("\n" + "="*70)
    print("🔍 MANUAL VERIFICATION SAMPLE")
    print("="*70)
    print(f"Showing {sample_size} random users for manual verification...\n")
    
    import random as rand
    sample_indices = rand.sample(range(len(user_cells)), min(sample_size, len(user_cells)))
    
    for idx in sample_indices:
        cell = user_cells[idx]
        try:
            # Get username
            username_element = cell.find_element(By.CSS_SELECTOR, "a[href*='/']")
            username = username_element.get_attribute("href").split("/")[-1]
            
            # Check for follow indicator
            follows_back = False
            try:
                follow_indicator = cell.find_element(By.CSS_SELECTOR, "[data-testid='userFollowIndicator']")
                if follow_indicator.is_displayed():
                    follows_back = True
            except:
                # Try text check
                if "Follows you" in cell.text:
                    follows_back = True
            
            # Show the cell text for manual inspection
            cell_text_preview = cell.text[:150].replace('\n', ' | ')
            
            print(f"User #{idx+1}: @{username}")
            print(f"  Detected as: {'FOLLOWS BACK ✓' if follows_back else 'DOES NOT FOLLOW ✗'}")
            print(f"  Cell preview: {cell_text_preview}")
            print()
            
        except Exception as e:
            print(f"User #{idx+1}: Error - {str(e)[:50]}\n")
    
    choice = input("Does the detection look accurate? [y/n]: ").lower().strip()
    if choice != 'y':
        print("⚠️ Detection may be inaccurate. Consider:")
        print("  1. Taking a screenshot for debugging")
        print("  2. Running in dry-run mode first")
        print("  3. Checking Twitter's HTML structure for changes")
        return False
    
    return True


def get_current_username(driver):
    """Detect the logged-in user's Twitter username"""
    try:
        # Try multiple methods to get username
        methods = [
            lambda: driver.find_element(By.XPATH, "//a[@data-testid='AppTabBar_Profile_Link']").get_attribute("href").split("/")[-1],
            lambda: driver.find_element(By.XPATH, "//div[@data-testid='SideNav_AccountSwitcher_Button']//span[starts-with(text(), '@')]").text.strip("@"),
        ]
        
        for method in methods:
            try:
                username = method()
                if username and len(username) > 0:
                    return username
            except:
                continue
        
        print("⚠️ Could not auto-detect username. Please enter manually.")
        username = input("Enter your Twitter username (without @): ").strip()
        return username
        
    except Exception as e:
        print(f"❌ Error detecting username: {e}")
        username = input("Enter your Twitter username (without @): ").strip()
        return username


def load_whitelist():
    """Load whitelist of accounts to never unfollow"""
    whitelist_file = "whitelist.txt"
    if os.path.exists(whitelist_file):
        with open(whitelist_file, 'r') as f:
            whitelist = set(line.strip().lower() for line in f if line.strip())
        print(f"📋 Loaded {len(whitelist)} accounts from whitelist")
        return whitelist
    return set()


def save_cleanup_progress(processed_users, unfollowed_users):
    """Save cleanup progress to resume later if interrupted"""
    progress_file = "cleanup_progress.json"
    data = {
        'last_run': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'processed': list(processed_users),
        'unfollowed': unfollowed_users
    }
    try:
        with open(progress_file, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save progress: {e}")


def load_cleanup_progress():
    """Load previous cleanup progress"""
    progress_file = "cleanup_progress.json"
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r') as f:
                data = json.load(f)
            # Only use progress if from today
            last_run = datetime.strptime(data['last_run'], "%Y-%m-%d %H:%M:%S")
            if last_run.date() == datetime.now().date():
                print(f"📂 Found cleanup progress from today ({data['last_run']})")
                return set(data['processed']), data['unfollowed']
        except Exception as e:
            print(f"⚠️ Could not load progress: {e}")
    return set(), []


def clean_following_list(driver, activity_tracker, unfollow_limiter, batch_size=50, dry_run=False):
    """
    Enhanced following list cleanup with batch processing, whitelist, and progress tracking.
    
    Args:
        driver: Selenium WebDriver instance
        activity_tracker: Activity tracking dictionary
        unfollow_limiter: Rate limiter for unfollows
        batch_size: Number of users to process before taking a break (default: 50)
        dry_run: If True, only report who would be unfollowed without actually doing it
    """
    unfollowed_users = []
    skipped_users = []
    
    print("\n" + "="*70)
    print("🧹 ENHANCED FOLLOWING LIST CLEANUP")
    print("="*70)
    
    # Load whitelist
    whitelist = load_whitelist()
    if whitelist:
        print(f"🛡️ Whitelist active: {len(whitelist)} accounts protected")
    
    # Load previous progress
    processed_users, previous_unfollows = load_cleanup_progress()
    if processed_users:
        print(f"📂 Resuming from previous session: {len(processed_users)} already processed")
        unfollowed_users = previous_unfollows
    
    # Check remaining unfollow capacity
    remaining_unfollows = unfollow_limiter.daily_limit - unfollow_limiter.count_day
    print(f"📊 Remaining unfollow capacity today: {remaining_unfollows}/{unfollow_limiter.daily_limit}")
    
    if remaining_unfollows <= 0:
        print("❌ Daily unfollow limit reached. Cannot perform cleanup.")
        return unfollowed_users
    
    if dry_run:
        print("🔍 DRY RUN MODE: Will preview without actually unfollowing")
    
    # Auto-detect username
    username = get_current_username(driver)
    print(f"👤 Detected username: @{username}")
    
    # Navigate to following page
    following_url = f"https://x.com/{username}/following"
    if not safe_get(driver, following_url):
        print("❌ Failed to navigate to following page")
        return unfollowed_users
    
    print("⏳ Loading following page...")
    time.sleep(random.uniform(4, 7))  # Longer initial wait
    
    # Scroll to load users with progress indicator
    print("⏬ Scrolling to load all users...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_count = 0
    max_scrolls = 25
    
    while scroll_count < max_scrolls:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        scroll_count += 1
        
        # Progress indicator
        if scroll_count % 5 == 0:
            print(f"   Scroll progress: {scroll_count}/{max_scrolls}")
        
        time.sleep(random.uniform(1.5, 3.0))
        
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height and scroll_count > 5:
            print(f"✅ Reached end after {scroll_count} scrolls")
            break
        last_height = new_height
    
    # Find all user cells
    try:
        print("🔍 Analyzing users...")
        user_cells = driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']")
        total_users = len(user_cells)
        print(f"📊 Found {total_users} users to analyze")
        
        users_to_unfollow = []
        
        # First pass: identify who to unfollow
        print("\n" + "="*70)
        print("PHASE 1: IDENTIFICATION")
        print("="*70)
        
        for i, cell in enumerate(user_cells):
            try:
                username_element = cell.find_element(By.CSS_SELECTOR, "[data-testid='UserCell'] a[href]")
                user = username_element.get_attribute("href").split("/")[-1]
                
                # Skip if already processed
                if user in processed_users:
                    continue
                
                # Mark as processed
                processed_users.add(user)
                
                # Check whitelist
                if user.lower() in whitelist:
                    print(f"[{i+1}/{total_users}] 🛡️ @{user} - PROTECTED (whitelist)")
                    skipped_users.append({'user': user, 'reason': 'whitelist'})
                    continue
                
                # Check if user follows back
                follows_back = False
                try:
                    follows_indicator = cell.find_element(By.CSS_SELECTOR, "[data-testid='userFollowIndicator']")
                    if "Follows you" in follows_indicator.text:
                        follows_back = True
                        print(f"[{i+1}/{total_users}] ✅ @{user} - follows back (keeping)")
                        skipped_users.append({'user': user, 'reason': 'follows_back'})
                        continue
                except:
                    pass
                
                # Add to unfollow list
                if not follows_back:
                    print(f"[{i+1}/{total_users}] ⚠️ @{user} - doesn't follow back (marking for unfollow)")
                    users_to_unfollow.append({'username': user, 'cell_index': i})
                
                # Save progress periodically
                if len(processed_users) % 20 == 0:
                    save_cleanup_progress(processed_users, unfollowed_users)
                
            except Exception as e:
                print(f"⚠️ Error processing user {i+1}: {str(e)[:50]}")
                continue
        
        print("\n" + "="*70)
        print("IDENTIFICATION COMPLETE")
        print(f"  Total analyzed: {len(processed_users)}")
        print(f"  To unfollow: {len(users_to_unfollow)}")
        print(f"  Skipped: {len(skipped_users)}")
        print("="*70)
        
        if dry_run:
            print("\n🔍 DRY RUN SUMMARY:")
            print(f"Would unfollow {len(users_to_unfollow)} accounts:")
            for item in users_to_unfollow[:20]:  # Show first 20
                print(f"  - @{item['username']}")
            if len(users_to_unfollow) > 20:
                print(f"  ... and {len(users_to_unfollow) - 20} more")
            return unfollowed_users
        
        # Second pass: actually unfollow in batches
        if users_to_unfollow:
            print("\n" + "="*70)
            print("PHASE 2: UNFOLLOWING")
            print("="*70)
            
            for idx, user_info in enumerate(users_to_unfollow):
                user = user_info['username']
                
                # Check if we hit daily limit
                if not unfollow_limiter.can_perform():
                    print(f"⚠️ Daily unfollow limit reached. Stopped at {len(unfollowed_users)} unfollows")
                    break
                
                print(f"\n[{idx+1}/{len(users_to_unfollow)}] Unfollowing @{user}...")
                
                if unfollow_user(driver, user):
                    unfollowed_users.append(user)
                    
                    # Record in unfollowed.csv
                    with open("unfollowed.csv", "a") as f:
                        f.write(f"{user},{datetime.now()}\n")
                    
                    # Update tracking
                    unfollow_limiter.record()
                    activity_tracker['unfollows_today'] += 1
                    activity_tracker['recent_actions'].append({
                        'type': 'unfollow',
                        'time': datetime.now(),
                        'user': user
                    })
                    
                    print(f"✅ Successfully unfollowed @{user}")
                else:
                    print(f"⚠️ Failed to unfollow @{user}")
                
                # Save progress after each unfollow
                save_cleanup_progress(processed_users, unfollowed_users)
                
                # Batch break
                if (idx + 1) % batch_size == 0 and idx + 1 < len(users_to_unfollow):
                    break_time = random.uniform(3, 6)
                    print(f"\n☕ Batch complete. Taking {round(break_time)} minute break...")
                    time.sleep(break_time * 60)
                    print("🔄 Resuming unfollows...")
                else:
                    # Smart delay between unfollows
                    delay = activity_based_delay(
                        len([a for a in activity_tracker['recent_actions'] if a['type'] == 'follow']),
                        len([a for a in activity_tracker['recent_actions'] if a['type'] == 'unfollow']),
                        activity_tracker['likes_today']
                    )
                    time.sleep(min(delay, 60))
        
        # Final summary
        print("\n" + "="*70)
        print("✅ CLEANUP COMPLETE")
        print("="*70)
        print(f"📊 Statistics:")
        print(f"   Total analyzed: {len(processed_users)}")
        print(f"   Unfollowed: {len(unfollowed_users)}")
        print(f"   Protected (whitelist): {len([s for s in skipped_users if s['reason'] == 'whitelist'])}")
        print(f"   Follows back: {len([s for s in skipped_users if s['reason'] == 'follows_back'])}")
        print("="*70)
        
        return unfollowed_users
        
    except Exception as e:
        print(f"❌ Critical error in cleanup: {str(e)}")
        save_cleanup_progress(processed_users, unfollowed_users)
        return unfollowed_users


def ask_clean_following_list():
    """Ask user about following list cleanup with enhanced options"""
    print("\n" + "="*70)
    print("🧹 FOLLOWING LIST CLEANUP OPTIONS")
    print("="*70)
    
    while True:
        print("\nOptions:")
        print("  1. Run cleanup (unfollow accounts that don't follow back)")
        print("  2. Dry run (preview who would be unfollowed)")
        print("  3. Skip cleanup")
        
        choice = input("\nSelect option [1/2/3]: ").strip()
        
        if choice == '1':
            print("✅ Full cleanup mode enabled")
            return {'enabled': True, 'dry_run': False}
        elif choice == '2':
            print("🔍 Dry run mode enabled - will preview only")
            return {'enabled': True, 'dry_run': True}
        elif choice == '3':
            print("⏭️ Skipping cleanup")
            return {'enabled': False, 'dry_run': False}
        else:
            print("❌ Invalid choice. Please enter 1, 2, or 3")


def unfollow_from_following_page(driver, username):
    """Unfollow a user directly from the following page without navigating to their profile"""
    try:
        # Find the user cell containing the username
        user_cells = driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']")
        
        for cell in user_cells:
            try:
                # Get the username from the cell
                username_element = cell.find_element(By.CSS_SELECTOR, "a[href*='/" + username + "']")
                # If we found the username, this is our target cell
                if username_element:
                    # Find the unfollow button in this cell
                    unfollow_button = None
                    
                    # Try multiple selectors for the unfollow button
                    selectors = [
                        f"button[data-testid$='-unfollow']",
                        "button[aria-label*='Following @{}']".format(username),
                        "button span:contains('Following')"
                    ]
                    
                    for selector in selectors:
                        try:
                            # Try direct CSS selector
                            unfollow_button = cell.find_element(By.CSS_SELECTOR, selector)
                            if unfollow_button.is_displayed():
                                break
                        except:
                            continue
                    
                    if not unfollow_button:
                        # Try XPath as fallback
                        xpath_selectors = [
                            f".//button[contains(@data-testid, '-unfollow')]",
                            f".//button[contains(@aria-label, 'Following @{username}')]"
                        ]
                        
                        for xpath in xpath_selectors:
                            try:
                                unfollow_button = cell.find_element(By.XPATH, xpath)
                                if unfollow_button.is_displayed():
                                    break
                            except:
                                continue
                    
                    if unfollow_button and unfollow_button.is_displayed():
                        print(f"🎯 Found unfollow button for @{username} on following page")
                        
                        # Click the unfollow button
                        driver.execute_script("arguments[0].click();", unfollow_button)
                        time.sleep(random.uniform(1, 2))
                        
                        # Handle confirmation dialog
                        try:
                            confirm_button = WebDriverWait(driver, 3).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='confirmationSheetConfirm']"))
                            )
                            driver.execute_script("arguments[0].click();", confirm_button)
                            print(f"✅ Successfully unfollowed @{username} from following page")
                            
                            # Wait for confirmation
                            time.sleep(random.uniform(2, 3))
                            return True
                        except Exception as e:
                            print(f"⚠️ Error confirming unfollow for @{username}: {str(e)}")
                            return False
            except Exception:
                continue
        
        print(f"❌ Could not find unfollow button for @{username} on following page")
        return False
    except Exception as e:
        print(f"❌ Error unfollowing @{username} from following page: {str(e)}")
        return False


def capture_user_positions(driver, user_cells, whitelist):
    """Capture positions and details of users who don't follow back"""
    users_to_unfollow = []
    processed_users = set()
    
    print("\n🔍 CAPTURING USER POSITIONS")
    print("="*50)
    
    for i, cell in enumerate(user_cells):
        try:
            # Get the cell's location and size
            location = cell.location
            size = cell.size
            y_position = location['y'] + size.get('height', 0) / 2  # Center of the element
            
            # Try to extract username using multiple approaches
            username = None
            
            # Method 1: Look for anchor tags with href containing '/'
            try:
                username_elements = cell.find_elements(By.CSS_SELECTOR, "a[href*='/']")
                for el in username_elements:
                    href = el.get_attribute("href")
                    if href and "status" not in href and "photo" not in href and "video" not in href:
                        username = href.split("/")[-1].lower()
                        if "@" in username:
                            username = username.split("@")[1]
                        break
            except:
                pass
            
            # Method 2: Look for aria-label containing @username
            if not username:
                try:
                    aria_elements = cell.find_elements(By.CSS_SELECTOR, "[aria-label*='@']")
                    for el in aria_elements:
                        aria_label = el.get_attribute("aria-label") or ""
                        if "@" in aria_label:
                            # Extract username from aria-label
                            start_idx = aria_label.find("@") + 1
                            end_idx = aria_label.find(" ", start_idx)
                            if end_idx == -1:
                                end_idx = len(aria_label)
                            username = aria_label[start_idx:end_idx].lower()
                            break
                except:
                    pass
            
            # Method 3: Look for text containing @username
            if not username:
                try:
                    cell_text = cell.text
                    if "@" in cell_text:
                        import re
                        matches = re.findall(r'@(\w+)', cell_text)
                        if matches:
                            username = matches[0].lower()
                except:
                    pass
            
            if not username or username in processed_users:
                continue
                
            # Skip if in whitelist
            if username in whitelist:
                print(f"[{i+1}] 🛡️ @{username} - PROTECTED (whitelist)")
                processed_users.add(username)
                continue
            
            # Check if user follows back - SIMPLIFIED & ACCURATE DETECTION
            follows_back = False
            
            try:
                # PRIMARY METHOD: Check for userFollowIndicator element (most reliable based on HTML)
                # This element ONLY exists when user follows you
                try:
                    indicator = cell.find_element(By.CSS_SELECTOR, "[data-testid='userFollowIndicator']")
                    if indicator.is_displayed():
                        # Double-check it contains the right text
                        if "Follows you" in indicator.text:
                            follows_back = True
                except:
                    # Element doesn't exist = user doesn't follow back
                    follows_back = False
                
                # BACKUP METHOD: Text search (in case Twitter changes structure)
                if not follows_back:
                    try:
                        full_text = cell.text
                        # Check for exact phrase with capital F
                        if "Follows you" in full_text:
                            follows_back = True
                    except:
                        pass
                
            except Exception as e:
                # If any error, assume doesn't follow back (safer)
                follows_back = False
            
            processed_users.add(username)
            
            if follows_back:
                print(f"[{i+1}] ✅ @{username} - follows back (keeping)")
            else:
                # Debug: show why we think they don't follow back
                try:
                    cell_preview = cell.text[:100].replace('\n', ' | ')
                    print(f"[{i+1}] ⚠️ @{username} - doesn't follow back")
                    print(f"    Preview: {cell_preview}")
                except:
                    print(f"[{i+1}] ⚠️ @{username} - doesn't follow back (marking for unfollow)")
                    
                users_to_unfollow.append({
                    'username': username,
                    'position': y_position,
                    'cell_index': i,
                    'element_id': f"user-{i}"
                })
                
        except Exception as e:
            print(f"⚠️ Error processing cell {i+1}: {str(e)[:50]}")
            continue
    
    # Sort by position (bottom to top)
    users_to_unfollow.sort(key=lambda x: x['position'], reverse=True)
    print(f"✅ Captured {len(users_to_unfollow)} users to unfollow")
    return users_to_unfollow


def scroll_up_and_unfollow_incremental(driver, users_to_unfollow, unfollow_limiter, activity_tracker, batch_size=10):
    """Scroll up from bottom and unfollow users as they're encountered"""
    unfollowed_users = []
    remaining_users = users_to_unfollow.copy()
    
    print("\n" + "="*70)
    print("⏫ SCROLLING UP & UNFOLLOWING INCREMENTALLY")
    print("="*70)
    
    # Start at the bottom
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(random.uniform(2, 3))
    print("✅ Started at bottom of page")
    
    # Current position tracking
    current_position = driver.execute_script("return window.pageYOffset;")
    scroll_amount = 300  # Amount to scroll up each time
    unfollow_attempts = {}
    
    while remaining_users and unfollow_limiter.can_perform():
        # Scroll up a bit
        driver.execute_script(f"window.scrollBy(0, -{scroll_amount});")
        current_position -= scroll_amount
        time.sleep(random.uniform(0.8, 1.5))
        
        print(f"⬆️ Scrolled up to position {current_position}")
        
        # Check if we've reached any users to unfollow
        users_in_view = []
        for user_info in remaining_users[:]:
            # Check if user is now in view or close to it
            if user_info['position'] > current_position - 200:
                users_in_view.append(user_info)
        
        # Process visible users
        if users_in_view:
            print(f"👀 Found {len(users_in_view)} users in view to potentially unfollow")

            # Print each discovered username as we scroll and discover them
            for discovered in users_in_view:
                try:
                    uname = discovered.get('username') if isinstance(discovered, dict) else None
                    pos = int(discovered.get('position', 0)) if isinstance(discovered, dict) else 0
                    if uname:
                        print(f"  ➤ Discovered @{uname} (position: {pos})")
                    else:
                        print(f"  ➤ Discovered user (no username available) - {discovered}")
                except Exception:
                    print(f"  ➤ Discovered user (error reading username): {discovered}")

            for user_info in users_in_view:
                username = user_info['username']
                print(f"🎯 Attempting to unfollow @{username}")
                
                # Try to unfollow directly from the page
                if unfollow_from_following_page(driver, username):
                    unfollowed_users.append(username)
                    # Record in unfollowed.csv
                    with open("unfollowed.csv", "a") as f:
                        f.write(f"{username},{datetime.now()}\n")
                    
                    # Update tracking
                    unfollow_limiter.record()
                    activity_tracker['unfollows_today'] += 1
                    activity_tracker['recent_actions'].append({
                        'type': 'unfollow',
                        'time': datetime.now(),
                        'user': username
                    })
                    print(f"✅ Successfully unfollowed @{username}")
                    
                    # Remove from remaining users
                    remaining_users.remove(user_info)
                else:
                    # Track failed attempts to avoid infinite loops
                    unfollow_attempts[username] = unfollow_attempts.get(username, 0) + 1
                    if unfollow_attempts[username] >= 3:
                        print(f"⚠️ Failed to unfollow @{username} after 3 attempts. Skipping.")
                        remaining_users.remove(user_info)
            
            # Take a break after processing a batch
            if len(unfollowed_users) % batch_size == 0 and len(unfollowed_users) > 0:
                break_time = random.uniform(2, 4)
                print(f"☕ Batch of {batch_size} unfollows complete. Taking {round(break_time, 1)} minute break...")
                time.sleep(break_time * 60)
                print("🔄 Resuming unfollows...")
        
        # Check if we've reached the top
        if current_position <= 0:
            print("🔝 Reached the top of the page")
            break
        
        # Check unfollow limit
        if not unfollow_limiter.can_perform():
            print("⚠️ Daily unfollow limit reached")
            break
    
    return unfollowed_users


def clean_following_list_incremental(driver, activity_tracker, unfollow_limiter, batch_size=10, dry_run=False):
    """Enhanced following list cleanup with bottom-to-top incremental unfollowing"""
    unfollowed_users = []
    skipped_users = []
    processed_users = set()
    
    print("\n" + "="*70)
    print("🧹 BOTTOM-TO-TOP INCREMENTAL FOLLOWING LIST CLEANUP")
    print("="*70)
    
    # Auto-detect username
    username = get_current_username(driver)
    print(f"👤 Detected username: @{username}")
    
    # Navigate to following page
    following_url = f"https://x.com/{username}/following"
    if not safe_get(driver, following_url):
        print("❌ Failed to navigate to following page")
        return unfollowed_users
    
    print("⏳ Loading following page...")
    time.sleep(random.uniform(3, 5))
    
    # Step 1: Scroll to the bottom to capture all users
    print("\n" + "="*70)
    print("⏬ SCROLLING TO BOTTOM TO CAPTURE ALL USERS")
    print("="*70)
    # First, check if we're already at the bottom
    last_height = driver.execute_script("return document.body.scrollHeight")
    print(f"📏 Initial page height: {last_height}px")

    # Use the improved scrolling function
    actual_scrolls = scroll_to_bottom_then_top(driver, max_scrolls=100)  # More scrolls allowed
    print(f"📊 Completed {actual_scrolls} scrolls to load all accounts")
    
    # Step 2: Find all user cells and capture positions
    print("\n" + "="*70)
    print("🔍 ANALYZING USERS AND CAPTURING POSITIONS")
    print("="*70)
    # Instead of finding all cells at once, find them in sections
    user_cells = []
    unique_usernames = set()
    
    print("👥 Looking for user cells in the page...")
    try:
        # Try multiple selectors for user cells
        selectors = [
            "[data-testid='UserCell']",
            "[data-testid='cellInnerDiv']:has([data-testid='UserCell'])",
            "div[data-testid='cellInnerDiv']:has(a[href*='/following'])",
            "div[aria-label*='Timeline'] > div > div > div > div > div"
        ]
        
        for selector in selectors:
            try:
                found_cells = driver.find_elements(By.CSS_SELECTOR, selector)
                print(f"  📌 Found {len(found_cells)} cells using selector: {selector}")
                if found_cells:
                    user_cells.extend(found_cells)
                    break  # Use the first selector that finds elements
            except Exception:
                continue
        
        # Remove duplicates based on position
        unique_cells = []
        positions = set()
        for cell in user_cells:
            try:
                location = cell.location
                pos_key = (location['x'], location['y'])
                if pos_key not in positions:
                    positions.add(pos_key)
                    unique_cells.append(cell)
            except:
                continue
        
        user_cells = unique_cells
        total_users = len(user_cells)
        print(f"✅ Found {total_users} unique user cells to analyze")
    
    except Exception as e:
        print(f"❌ Error finding user cells: {str(e)}")
        driver.save_screenshot("user_cells_error.png")
        print("📸 Screenshot saved as 'user_cells_error.png'")
        return unfollowed_users

    if total_users == 0:
        print("❌ No user cells found. Taking screenshot for debugging...")
        driver.save_screenshot("no_users_found.png")
        print("📸 Screenshot saved as 'no_users_found.png'")
        return unfollowed_users
    
    # Load whitelist
    whitelist = load_whitelist()
    if whitelist:
        print(f"🛡️ Whitelist active: {len(whitelist)} accounts protected")

    # Optional: Manual verification sample
    if not dry_run:
        print("\n" + "="*70)
        choice = input("Would you like to verify 'Follows you' detection accuracy? [y/n]: ").lower().strip()
        if choice == 'y':
            if not verify_follows_back_detection(driver, sample_size=5):
                print("⚠️ Detection accuracy is poor. Consider stopping.")
                stop_choice = input("Continue anyway? [y/n]: ").lower().strip()
                if stop_choice != 'y':
                    print("Aborting cleanup.")
                    return unfollowed_users

    # Capture positions of users who don't follow back
    users_to_unfollow = capture_user_positions(driver, user_cells, whitelist)
    
    # If dry run, just show the results
    if dry_run:
        print("\n" + "="*70)
        print("🔍 DRY RUN SUMMARY")
        print("="*70)
        print(f"Would unfollow {len(users_to_unfollow)} accounts:")
        for i, user_info in enumerate(users_to_unfollow[:20]):  # Show first 20
            print(f" {i+1}. @{user_info['username']} (position: {user_info['position']})")
        if len(users_to_unfollow) > 20:
            print(f" ... and {len(users_to_unfollow) - 20} more")
        return unfollowed_users
    
    # Step 3: Scroll up incrementally and unfollow
    unfollowed_users = scroll_up_and_unfollow_incremental(
        driver, 
        users_to_unfollow, 
        unfollow_limiter, 
        activity_tracker,
        batch_size=batch_size
    )
    
    # Final summary
    print("\n" + "="*70)
    print("✅ CLEANUP COMPLETE")
    print("="*70)
    print(f"📊 Statistics:")
    print(f" Total analyzed: {total_users}")
    print(f" Unfollowed: {len(unfollowed_users)}")
    print(f" Remaining to unfollow: {len(users_to_unfollow) - len(unfollowed_users)}")
    print("="*70)
    
    return unfollowed_users


def clean_following_list_efficient(driver, activity_tracker, unfollow_limiter, batch_size=50, dry_run=False):
    """Enhanced following list cleanup that unfollows directly from the following page"""
    unfollowed_users = []
    skipped_users = []
    processed_users = set()
    
    print("\n" + "="*70)
    print("🧹 EFFICIENT FOLLOWING LIST CLEANUP")
    print("="*70)
    
    # Auto-detect username
    username = get_current_username(driver)
    print(f"👤 Detected username: @{username}")
    
    # Navigate to following page
    following_url = f"https://x.com/{username}/following"
    if not safe_get(driver, following_url):
        print("❌ Failed to navigate to following page")
        return unfollowed_users
    
    print("⏳ Loading following page...")
    time.sleep(random.uniform(3, 5))
    
    # Use bottom-then-top scrolling to ensure all accounts load
    print("\n" + "="*70)
    print("🔄 LOADING ALL FOLLOWING ACCOUNTS (AGGRESSIVE MODE)")
    print("="*70)
    
    # First pass - aggressive scrolling
    actual_scrolls = scroll_to_bottom_then_top(driver, max_scrolls=150)
    print(f"📊 First pass: {actual_scrolls} scrolls completed")
    
    # Verify we got everything using combined checks
    time.sleep(2)
    loaded_ok = verify_all_users_loaded(driver)
    quality_ok = verify_user_cell_quality(driver)

    if not (loaded_ok and quality_ok):
        print("⚠️ Verification incomplete or low quality - attempting second pass and offering manual sample")
        # Second pass with even more scrolls
        additional_scrolls = scroll_to_bottom_then_top(driver, max_scrolls=50)
        print(f"📊 Second pass: {additional_scrolls} additional scrolls")

        # Re-run quick checks
        loaded_ok = verify_all_users_loaded(driver)
        quality_ok = verify_user_cell_quality(driver)

        # Offer manual verification if still unsure
        if not (loaded_ok and quality_ok):
            print("⚠️ Automated verification still uncertain.")
            choice = input("Would you like to view a manual sample before proceeding? [y/n]: ").lower().strip()
            if choice == 'y':
                # Gather user cells for manual sample
                try:
                    sample_cells = driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']")
                    if not manual_verification_sample(driver, sample_cells, sample_size=5):
                        print("⚠️ Manual verification indicated potential issues. Aborting efficient cleanup.")
                        return unfollowed_users
                except Exception as e:
                    print(f"⚠️ Could not perform manual sample: {e}")
                    return unfollowed_users
    else:
        print("✅ Verification passed - all users loaded and quality is good")
    
    # Final user count
    final_count = len(driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']"))
    print(f"📊 FINAL COUNT: {final_count} users loaded")
    
    # Find all user cells with retry logic
    print("🔍 Analyzing users...")
    user_cells = []
    max_retries = 3
    
    for attempt in range(1, max_retries + 1):
        try:
            # Try multiple selectors
            selectors = [
                "[data-testid='UserCell']",
                "div[data-testid='cellInnerDiv']",
                "article[role='article']"
            ]
            
            for selector in selectors:
                found_cells = driver.find_elements(By.CSS_SELECTOR, selector)
                if found_cells and len(found_cells) > len(user_cells):
                    user_cells = found_cells
                    print(f"  ✅ Found {len(found_cells)} cells with selector: {selector}")
            
            if user_cells:
                break
            else:
                print(f"  ⚠️ Attempt {attempt}/{max_retries}: No cells found, retrying...")
                time.sleep(3)
                # Try scrolling a bit
                driver.execute_script("window.scrollBy(0, -500);")
                time.sleep(1)
                driver.execute_script("window.scrollBy(0, 500);")
                time.sleep(2)
                
        except Exception as e:
            print(f"  ❌ Attempt {attempt}/{max_retries} error: {str(e)}")
            if attempt < max_retries:
                time.sleep(3)
    
    total_users = len(user_cells)
    print(f"📊 Found {total_users} users to analyze")
    
    if total_users == 0:
        print("❌ Could not find any user cells after all attempts")
        driver.save_screenshot("no_users_debug.png")
        print("📸 Debug screenshot saved")
        return unfollowed_users
    
    # Optional: Verify detection accuracy before processing
    if not dry_run:
        print("\n" + "="*70)
        choice = input("Would you like to verify 'Follows you' detection accuracy? [y/n]: ").lower().strip()
        if choice == 'y':
            if not verify_follows_back_detection(driver, sample_size=5):
                print("⚠️ Detection accuracy is poor. Consider stopping.")
                stop_choice = input("Continue anyway? [y/n]: ").lower().strip()
                if stop_choice != 'y':
                    print("Aborting cleanup.")
                    return unfollowed_users

    users_to_unfollow = []
    
    # First pass: identify who to unfollow
    print("\n" + "="*70)
    print("PHASE 1: IDENTIFICATION")
    print("="*70)
    
    # Load whitelist
    whitelist = load_whitelist()
    if whitelist:
        print(f"🛡️ Whitelist active: {len(whitelist)} accounts protected")
    
    for i, cell in enumerate(user_cells):
        try:
            # Get username
            username_element = cell.find_element(By.CSS_SELECTOR, "a[href]")
            user = username_element.get_attribute("href").split("/")[-1].lower()
            
            # Skip if already processed
            if user in processed_users:
                continue
                
            processed_users.add(user)
            
            # Check whitelist
            if user in whitelist:
                print(f"[{i+1}/{total_users}] 🛡️ @{user} - PROTECTED (whitelist)")
                skipped_users.append({'user': user, 'reason': 'whitelist'})
                continue
            
            # Check if user follows back - SIMPLIFIED & ACCURATE DETECTION
            follows_back = False
            try:
                # PRIMARY METHOD: Check for userFollowIndicator element (most reliable)
                try:
                    indicator = cell.find_element(By.CSS_SELECTOR, "[data-testid='userFollowIndicator']")
                    if indicator.is_displayed() and "Follows you" in indicator.text:
                        follows_back = True
                except:
                    follows_back = False

                # BACKUP METHOD: Check full cell text
                if not follows_back:
                    try:
                        full_text = cell.text
                        if "Follows you" in full_text:
                            follows_back = True
                    except:
                        pass

                if follows_back:
                    print(f"[{i+1}/{total_users}] ✅ @{user} - follows back (keeping)")
                    skipped_users.append({'user': user, 'reason': 'follows_back'})
                    continue
            except Exception as e:
                print(f"[{i+1}/{total_users}] ⚠️ Error checking follow status for @{user}: {str(e)[:50]}")
                follows_back = False
            
            # Check if it's us (don't unfollow ourselves)
            if user == username.lower():
                print(f"[{i+1}/{total_users}] 🙋 @{user} - that's you (skipping)")
                continue
            
            # Add to unfollow list if they don't follow back
            if not follows_back:
                print(f"[{i+1}/{total_users}] ⚠️ @{user} - doesn't follow back (marking for unfollow)")
                users_to_unfollow.append({'username': user})
                
        except Exception as e:
            print(f"⚠️ Error processing user {i+1}: {str(e)}")
            continue
    
    print("\n" + "="*70)
    print("IDENTIFICATION COMPLETE")
    print(f" Total analyzed: {len(processed_users)}")
    print(f" To unfollow: {len(users_to_unfollow)}")
    print(f" Skipped: {len(skipped_users)}")
    print("="*70)
    
    # If dry run, just return the list
    if dry_run:
        print("🔍 DRY RUN SUMMARY:")
        print(f"Would unfollow {len(users_to_unfollow)} accounts:")
        for item in users_to_unfollow[:20]:  # Show first 20
            print(f" - @{item['username']}")
        if len(users_to_unfollow) > 20:
            print(f" ... and {len(users_to_unfollow) - 20} more")
        return unfollowed_users
    
    # Phase 2: Actually unfollow users directly from the page
    print("\n" + "="*70)
    print("PHASE 2: UNFOLLOWING DIRECTLY FROM PAGE")
    print("="*70)
    
    print("\n" + "="*70)
    print("PHASE 2: UNFOLLOWING (BOTTOM TO TOP)")
    print("="*70)

    # Process from bottom to top (reverse order)
    for idx, user_info in enumerate(reversed(users_to_unfollow)):
        user = user_info['username']
        current_position = len(users_to_unfollow) - idx

        # Check if we hit daily limit
        if not unfollow_limiter.can_perform():
            print(f"⚠️ Daily unfollow limit reached. Stopped at {len(unfollowed_users)} unfollows")
            break

        print(f"[{current_position}/{len(users_to_unfollow)}] Unfollowing @{user} directly from following page...")

        if unfollow_from_following_page(driver, user):
            unfollowed_users.append(user)

            # Record in unfollowed.csv
            with open("unfollowed.csv", "a") as f:
                f.write(f"{user},{datetime.now()}\n")

            # Update tracking
            unfollow_limiter.record()
            activity_tracker['unfollows_today'] += 1
            activity_tracker['recent_actions'].append({
                'type': 'unfollow',
                'time': datetime.now(),
                'user': user
            })

            print(f"✅ Successfully unfollowed @{user}")
        else:
            print(f"⚠️ Failed to unfollow @{user}")
        
        # Batch break
        if (idx + 1) % batch_size == 0 and idx + 1 < len(users_to_unfollow):
            break_time = random.uniform(3, 6)
            print(f"☕ Batch complete. Taking {round(break_time)} minute break...")
            time.sleep(break_time * 60)
            print("🔄 Resuming unfollows...")
    
    # Final summary
    print("\n" + "="*70)
    print("✅ CLEANUP COMPLETE")
    print("="*70)
    print(f"📊 Statistics:")
    print(f" Total analyzed: {len(processed_users)}")
    print(f" Unfollowed: {len(unfollowed_users)}")
    print(f" Protected (whitelist): {len([s for s in skipped_users if s['reason'] == 'whitelist'])}")
    print(f" Follows back: {len([s for s in skipped_users if s['reason'] == 'follows_back'])}")
    print("="*70)
    
    return unfollowed_users

def ask_unfollow_only_mode():
    """Check CLI args or ask user if they want to run in unfollow-only mode"""
    # Check CLI argument first: `python3 twitter_bot.py unfollow`
    if len(sys.argv) > 1 and sys.argv[1].lower() == 'unfollow':
        print("🔄 UNFOLLOW-ONLY MODE activated via CLI argument")
        return True
    if not sys.stdin.isatty():
        print("🤖 Non-interactive mode detected: Defaulting to NORMAL MODE")
        return False
    while True:
        choice = input("\n🤔 Do you want to run in UNFOLLOW-ONLY mode? (skip new follows, just process unfollow queue)\n[y/n]: ").lower().strip()
        if choice in ['y', 'yes']:
            print("🔄 UNFOLLOW-ONLY MODE activated - will only process unfollow queue")
            return True
        elif choice in ['n', 'no']:
            print("🔄 NORMAL MODE - will do both follows and unfollows")
            return False
        else:
            print("Please enter 'y' for yes or 'n' for no")

def simple_scroll_load(driver, max_scrolls=50, pause=2):
    """Simple scroll to load content with progress feedback"""
    print("⏬ Loading users...")
    for i in range(max_scrolls):
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        except Exception:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(pause)
        if (i + 1) % 10 == 0:
            try:
                user_count = len(driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']"))
                print(f"  Loaded {user_count} users so far... (scroll {i+1}/{max_scrolls})")
            except Exception:
                print(f"  Scrolled {i+1}/{max_scrolls}...")

    try:
        final_count = len(driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']"))
        print(f"✅ Finished loading - found {final_count} total users")
    except Exception:
        print("✅ Finished loading")

    try:
        driver.execute_script("window.scrollTo(0, 0);")
    except Exception:
        driver.execute_script("window.scrollTo(0,0)")
    time.sleep(1)


def check_follows_back(cell):
    """Simple, reliable check if user follows you back"""
    try:
        indicator = cell.find_element(By.CSS_SELECTOR, "[data-testid='userFollowIndicator']")
        if indicator and indicator.is_displayed():
            return True
    except Exception:
        pass

    try:
        if "Follows you" in cell.text:
            return True
    except Exception:
        pass

    return False


def extract_username_from_cell(cell):
    """Extract username from a user cell - FIXED VERSION"""
    try:
        # Method 1: Look for @username text directly
        username_spans = cell.find_elements(By.XPATH, ".//span[starts-with(text(), '@')]")
        for span in username_spans:
            text = span.text.strip()
            if text.startswith('@') and len(text) > 1:
                username = text[1:].lower()  # Remove @ and lowercase
                # Validate it's actually a username (no spaces, reasonable length)
                if ' ' not in username and 3 <= len(username) <= 15:
                    return username

        # Method 2: Look in href attributes
        links = cell.find_elements(By.CSS_SELECTOR, "a[href]")
        for link in links:
            href = link.get_attribute("href")
            if not href:
                continue

            # Parse href like: https://x.com/username or /username
            if 'x.com/' in href or 'twitter.com/' in href:
                # Extract everything after the domain
                parts = href.split('/')
                # Find username (usually after domain, not 'status', 'photo', etc)
                for part in parts:
                    if part and part not in ['', 'https:', 'http:', 'x.com', 'twitter.com', 'status', 'photo', 'video', 'following', 'followers']:
                        if len(part) >= 3 and len(part) <= 15:
                            return part.lower()

        print(f"⚠️ Could not extract username from cell")
        return None
        
    except Exception as e:
        print(f"⚠️ Error extracting username: {str(e)[:50]}")
        return None


def unfollow_user_direct(driver, username):
    """Unfollow user by clicking their Following button on profile"""
    try:
        print(f"🎯 Unfollowing @{username}...")
        url = f"https://x.com/i/user/{username}" if username.isdigit() else f"https://x.com/{username}"
        if not safe_get(driver, url):
            return False
        time.sleep(random.uniform(2, 4))

        following_button_xpaths = [
            f"//button[@aria-label='Following @{username}']",
            "//button[.//span[text()='Following']]",
            "//div[@data-testid='placementTracking']//button[contains(@aria-label, 'Following')]"
        ]

        clicked = False
        for xpath in following_button_xpaths:
            try:
                btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                btn.click()
                clicked = True
                print("  ✓ Clicked Following button")
                break
            except Exception:
                continue

        if not clicked:
            print("  ✗ Could not find Following button")
            return False

        time.sleep(random.uniform(1, 2))
        try:
            confirm = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[@data-testid='confirmationSheetConfirm']")))
            confirm.click()
            print("  ✓ Confirmed unfollow")
            time.sleep(random.uniform(2, 3))
            return True
        except Exception:
            print("  ✗ Could not confirm unfollow")
            return False

    except Exception as e:
        print(f"  ✗ Error: {str(e)[:50]}")
        return False


def clean_following_list_simple(driver, activity_tracker, unfollow_limiter, dry_run=False):
    """
    Simple, reliable following list cleanup:
    1. Load the following page
    2. Scan all users and identify non-followers
    3. Unfollow them one by one
    """
    
    print("\n" + "="*70)
    print("🧹 SIMPLE FOLLOWING LIST CLEANUP")
    print("="*70)
    
    if dry_run:
        print("🔍 DRY RUN MODE - Will only preview, not actually unfollow")
    
    # Get current user
    current_user = get_current_username(driver)
    print(f"👤 Logged in as: @{current_user}")
    
    # Load whitelist
    whitelist = load_whitelist()
    
    # Navigate to following page
    following_url = f"https://x.com/{current_user}/following"
    print(f"🌐 Navigating to {following_url}")
    driver.get(following_url)
    time.sleep(random.uniform(3, 5))
    
    # Load all users by scrolling
    simple_scroll_load(driver, max_scrolls=50, pause=2)
    
    # Find all user cells
    print("\n📊 Analyzing users...")
    user_cells = driver.find_elements(By.CSS_SELECTOR, "[data-testid='UserCell']")
    total_users = len(user_cells)
    print(f"Found {total_users} users")
    
    if total_users == 0:
        print("❌ No users found - something went wrong")
        return []
    
    # Analyze each user
    users_to_unfollow = []
    follows_back_count = 0
    whitelist_count = 0
    extraction_failures = 0
    
    print("\n🔍 Checking who follows you back...")
    for i, cell in enumerate(user_cells, 1):
        username = extract_username_from_cell(cell)
        
        if not username:
            extraction_failures += 1
            continue
        
        # Skip if in whitelist
        if username in whitelist:
            whitelist_count += 1
            print(f"  [{i}/{total_users}] 🛡️ @{username} - whitelist")
            continue
        
        # Check if follows back
        follows_back = check_follows_back(cell)
        
        if follows_back:
            follows_back_count += 1
            print(f"  [{i}/{total_users}] ✅ @{username} - follows back")
        else:
            # Add to unfollow list
            users_to_unfollow.append(username)
            print(f"  [{i}/{total_users}] ❌ @{username} - doesn't follow back")
    
    # Summary
    print("\n" + "="*70)
    print("📊 ANALYSIS COMPLETE")
    print("="*70)
    print(f"Total users: {total_users}")
    print(f"Successfully analyzed: {total_users - extraction_failures}")
    print(f"Failed to extract username: {extraction_failures}")
    print(f"Follows back: {follows_back_count}")
    print(f"Whitelisted: {whitelist_count}")
    print(f"To unfollow: {len(users_to_unfollow)}")
    print("="*70)
    
    # If dry run, just show the list
    if dry_run:
        print("\n🔍 USERS TO UNFOLLOW (DRY RUN):")
        for i, username in enumerate(users_to_unfollow[:20], 1):
            print(f"  {i}. @{username}")
        if len(users_to_unfollow) > 20:
            print(f"  ... and {len(users_to_unfollow) - 20} more")
        return []
    
    if len(users_to_unfollow) == 0:
        print("✅ No users to unfollow - everyone follows you back!")
        return []
    
    # Ask for confirmation
    print(f"\n⚠️ About to unfollow {len(users_to_unfollow)} users")
    confirm = input("Continue? [y/n]: ").lower().strip()
    
    if confirm != 'y':
        print("❌ Cancelled")
        return []
    
    # Unfollow users
    print("\n" + "="*70)
    print("🔄 UNFOLLOWING USERS")
    print("="*70)
    
    unfollowed = []
    failed = []
    
    for i, username in enumerate(users_to_unfollow, 1):
        # Check rate limit
        if not unfollow_limiter.can_perform():
            print(f"\n⚠️ Rate limit reached after {len(unfollowed)} unfollows")
            break
        
        print(f"\n[{i}/{len(users_to_unfollow)}] @{username}")
        
        if unfollow_user_direct(driver, username):
            unfollowed.append(username)
            
            # Record unfollow
            with open("unfollowed.csv", "a") as f:
                f.write(f"{username},{datetime.now()}\n")
            
            # Update limiter and tracker
            unfollow_limiter.record()
            activity_tracker['unfollows_today'] += 1
            activity_tracker['recent_actions'].append({
                'type': 'unfollow',
                'time': datetime.now(),
                'user': username
            })
            
            print(f"✅ Successfully unfollowed ({len(unfollowed)} total)")
            
            # Pause between unfollows
            if i < len(users_to_unfollow):
                pause = random.uniform(3, 6)
                print(f"⏳ Waiting {pause:.1f}s...")
                time.sleep(pause)
        else:
            failed.append(username)
            print(f"❌ Failed to unfollow")
    
    # Final summary
    print("\n" + "="*70)
    print("✅ CLEANUP COMPLETE")
    print("="*70)
    print(f"Successfully unfollowed: {len(unfollowed)}")
    print(f"Failed: {len(failed)}")
    print(f"Remaining to unfollow: {len(users_to_unfollow) - len(unfollowed) - len(failed)}")
    print("="*70)
    
    return unfollowed


def ask_cleanup_mode():
    """Ask user what cleanup mode they want (simple)"""
    if not sys.stdin.isatty():
        print("🤖 Non-interactive mode detected: Defaulting to SKIP CLEANUP")
        return {'enabled': False, 'dry_run': False}
    print("\n" + "="*70)
    print("🧹 FOLLOWING LIST CLEANUP")
    print("="*70)
    print("\nOptions:")
    print("  1. Full cleanup (unfollow non-followers)")
    print("  2. Dry run (preview only)")
    print("  3. Skip cleanup")
    
    while True:
        choice = input("\nSelect [1/2/3]: ").strip()
        
        if choice == '1':
            return {'enabled': True, 'dry_run': False}
        elif choice == '2':
            return {'enabled': True, 'dry_run': True}
        elif choice == '3':
            return {'enabled': False, 'dry_run': False}
        else:
            print("Invalid choice. Please enter 1, 2, or 3")


def main():
    """Main bot function with optimized limits and anti-detection measures"""
    driver = setup_driver()
    
    # Ask user for mode preference
    unfollow_only_mode = ask_unfollow_only_mode()
    
    # Conservative daily limits to avoid detection
    follow_limiter = RateLimit(daily_limit=320, hourly_limit=25, burst_limit=3, action_type='follow')  # 320/day, 25/hour, 3 burst
    unfollow_limiter = RateLimit(daily_limit=800, hourly_limit=60, burst_limit=5, action_type='unfollow')  # 800/day, 60/hour, 5 burst  
    like_limiter = RateLimit(daily_limit=600, hourly_limit=40, burst_limit=4, action_type='like')     # 600/day, 40/hour, 4 burst

    # Load persistent daily counts
    daily_counts = load_daily_counts()
    
    # Activity tracking for intelligent timing (now uses persistent counts)
    activity_tracker = {
        'follows_today': daily_counts['follows'],
        'unfollows_today': daily_counts['unfollows'], 
        'likes_today': daily_counts['likes'],
        'recent_actions': [],
        'last_reset': datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    }

    try:
        print("🚀 Starting ULTRA-STEALTH Twitter bot...")
        if unfollow_only_mode:
            print("🔄 UNFOLLOW-ONLY MODE: Will only process unfollow queue")
            print(f"📊 Daily unfollow limit: {unfollow_limiter.daily_limit}")
            print(f"📊 Progress today: {daily_counts['unfollows']}/{unfollow_limiter.daily_limit} unfollows completed")
        else:
            print(f"📊 Daily limits: {follow_limiter.daily_limit} follows, {unfollow_limiter.daily_limit} unfollows, {like_limiter.daily_limit} likes")
            print(f"📊 Progress today: {daily_counts['follows']}/{follow_limiter.daily_limit} follows, {daily_counts['unfollows']}/{unfollow_limiter.daily_limit} unfollows, {daily_counts['likes']}/{like_limiter.daily_limit} likes")
        print("🛡️ Maximum stealth mode activated - conservative limits and natural patterns")
        
        # Load cookies and navigate to Twitter
        load_cookies_from_file(driver, "cacheforlogin.json", "x.com")
        if not safe_get(driver, "https://x.com/home"):
            print("❌ Could not load Twitter home page; exiting.")
            return

        # Simple following list cleanup
        cleanup_config = ask_cleanup_mode()
        if cleanup_config['enabled']:
            unfollowed_list = clean_following_list_simple(
                driver,
                activity_tracker,
                unfollow_limiter,
                dry_run=cleanup_config['dry_run']
            )
            
            if not cleanup_config['dry_run'] and unfollowed_list:
                print(f"\n📋 Cleanup Results: {len(unfollowed_list)} accounts unfollowed")
                # Take a break after cleanup
                break_time = random.uniform(2, 4)
                print(f"⏳ Taking {round(break_time)} minute break after cleanup...")
                time.sleep(break_time * 60)
            elif cleanup_config['dry_run']:
                print("\n✅ Dry run complete. No accounts were unfollowed.")

        while True:
            # Check driver health before each action
            if not is_driver_alive(driver):
                print("🚨 Driver session lost - attempting recovery...")
                driver = recover_driver(driver)
                if driver is None:
                    print("❌ Could not recover driver - exiting")
                    break
                continue

            try:
                # Reset daily counters - reload from persistent storage
                current_time = datetime.now()
                if current_time >= activity_tracker['last_reset'] + timedelta(days=1):
                    # Reload persistent counts
                    daily_counts = load_daily_counts()
                    activity_tracker.update({
                        'follows_today': daily_counts['follows'],
                        'unfollows_today': daily_counts['unfollows'],
                        'likes_today': daily_counts['likes'],
                        'recent_actions': [],
                        'last_reset': current_time.replace(hour=0, minute=0, second=0, microsecond=0)
                    })
                    print("🌅 New day - counters reset from persistent storage!")
                
                # Clean old actions (last hour)
                hour_ago = current_time - timedelta(hours=1)
                activity_tracker['recent_actions'] = [
                    action for action in activity_tracker['recent_actions'] 
                    if action['time'] > hour_ago
                ]
                
                recent_activity_count = len(activity_tracker['recent_actions'])
                
                print(f"\n⏰ Current time: {current_time.strftime('%H:%M:%S')}")
                
                # Periodic session check - detect expired login before performing actions
                if not check_session_alive(driver):
                    print("🚨 Session expired! Attempting recovery...")
                    new_driver = recover_driver(driver)
                    if new_driver:
                        driver = new_driver
                        print("✅ Session recovered successfully!")
                    else:
                        print("❌ Session recovery failed. Waiting 5 minutes before retrying...")
                        print("   ➡️ To fix: update cacheforlogin.json with fresh cookies and restart the bot.")
                        time.sleep(300)
                        continue
                
                action_performed = False

                # Load followed and unfollowed data FIRST (needed for filtering)
                if os.path.exists("followed.csv"):
                    followed_df = pd.read_csv("followed.csv", parse_dates=["follow_timestamp"])
                else:
                    followed_df = pd.DataFrame(columns=["username", "follow_timestamp"])
                    followed_df.to_csv("followed.csv", index=False)

                if os.path.exists("unfollowed.csv"):
                    unfollowed_df = pd.read_csv("unfollowed.csv")
                    unfollowed_set = set(u.lower() for u in unfollowed_df["username"])
                else:
                    with open("unfollowed.csv", "w") as f:
                        f.write("username,unfollow_timestamp\n")
                    unfollowed_set = set()
                
                followed_set = set(followed_df["username"].str.lower().values)

                queue = []
                usernames = []
                queue_file = "queue.json"
                if os.path.exists(queue_file):
                    try:
                        with open(queue_file, 'r') as f:
                            queue = json.load(f)
                        print(f"📋 Loaded queue.json: {len(queue)} total entries")

                        pending_entries = [
                            e for e in queue if e.get("status") == "pending_follow"
                        ]

                        deduped_ordered, _sizes = dedupe_best_pending(pending_entries)

                        skipped_followed = 0
                        skipped_unfollowed = 0

                        for e in deduped_ordered:
                            u = e.get("username", "")
                            u_lower = u.lower()
                            if u_lower in followed_set:
                                skipped_followed += 1
                                continue
                            if u_lower in unfollowed_set:
                                skipped_unfollowed += 1
                                continue
                            usernames.append(u)

                        total_raw = len(pending_entries)
                        dup_removed = total_raw - len(deduped_ordered)
                        if not pending_entries:
                            print("⚠️ No pending follows in queue.json")
                        else:
                            print(f"📋 Pending rows: {total_raw}, unique usernames: {len(deduped_ordered)} ({dup_removed} duplicate rows dropped)")
                            print(f"   Follow order: smallest source list first, then smallest followers_count (missing last)")
                            print(f"   Eligible for follow actions now: {len(usernames)}")
                            print(f"   Skipped: {skipped_followed} already in followed.csv, {skipped_unfollowed} in unfollowed.csv")
                    except Exception as e:
                        print(f"⚠️ Error loading queue.json: {e}")
                        usernames = []
                else:
                    print("⚠️ No queue.json found")

                # Calculate following difference (case-insensitive comparison)
                followed_usernames_lower = followed_df["username"].str.lower()
                followed_not_unfollowed = followed_df[~followed_usernames_lower.isin(unfollowed_set)]
                current_difference = len(followed_not_unfollowed)
                print(f"📊 Current following difference: {current_difference}")
                print(f"📊 Today's activity: F:{activity_tracker['follows_today']}/{follow_limiter.daily_limit} U:{activity_tracker['unfollows_today']}/{unfollow_limiter.daily_limit} L:{activity_tracker['likes_today']}/{like_limiter.daily_limit}")

                # UNFOLLOW LOGIC - Prioritize if above 430 following difference
                # 430 = ~370 organic gap + 60 bot headroom  (keeps following/followers ≤ ~1.3)
                above_threshold = current_difference > 430
                
                if unfollow_limiter.can_perform() and not followed_not_unfollowed.empty:
                    # 3-day minimum before unfollowing (tighter cap → faster slot reclaim)
                    cutoff = datetime.now() - timedelta(days=3)
                    eligible_unfollows = followed_not_unfollowed[
                        followed_not_unfollowed["follow_timestamp"] < cutoff
                    ].sort_values("follow_timestamp", ascending=True)
                    
                    if not eligible_unfollows.empty:
                        username = eligible_unfollows.iloc[0]["username"]
                        print(f"🎯 Unfollowing: @{username} (difference: {current_difference}/{len(usernames)})")
                        
                        unfollow_result = unfollow_user(driver, username)
                        
                        if unfollow_result == "follows_back":
                            # User follows back - add to whitelist behavior (record in unfollowed to skip next time)
                            print(f"🛡️ @{username} follows back - marking as processed to avoid retry")
                            with open("unfollowed.csv", "a") as f:
                                f.write(f"{username},{datetime.now()}\n")
                            unfollowed_set.add(username.lower())
                            # Don't count as unfollow action, just move on
                            action_performed = False
                        elif unfollow_result:
                            # Successful unfollow
                            with open("unfollowed.csv", "a") as f:
                                f.write(f"{username},{datetime.now()}\n")
                            unfollowed_set.add(username.lower())
                            # Update queue.json status
                            update_queue_status(username, "unfollowed")
                            unfollow_limiter.record()
                            
                            # Update tracking (sync from persistent storage)
                            current_counts = load_daily_counts()
                            activity_tracker['unfollows_today'] = current_counts['unfollows']
                            activity_tracker['recent_actions'].append({
                                'type': 'unfollow',
                                'time': datetime.now(),
                                'user': username
                            })
                            
                            action_performed = True
                            smart_break(recent_activity_count, base_minutes=3)

                # FOLLOW LOGIC - Only if not too far above threshold AND not in unfollow-only mode
                can_follow = current_difference < 455 and not unfollow_only_mode  # Allow some buffer above 430
                
                if can_follow and follow_limiter.can_perform():
                    # usernames is already filtered (no followed, no unfollowed, no multi-list dupes)
                    if usernames:
                        username = usernames[0]
                        print(f"🎯 Following: @{username} (difference: {current_difference}, pending: {len(usernames)})")
                        
                        follow_result = follow_user(driver, username, like_limiter)
                        if follow_result and follow_result != "not_found":
                            # Only record as successful follow if we actually followed (not skipped)
                            if follow_result == "skipped":
                                print(f"⏭️ Skipped @{username} - adding to followed.csv to avoid rechecking")
                                # Add to followed.csv even if skipped to avoid infinite loop
                                new_entry = pd.DataFrame([[username, datetime.now()]], 
                                                       columns=["username", "follow_timestamp"])
                                followed_df = pd.concat([followed_df, new_entry], ignore_index=True)
                                followed_df.to_csv("followed.csv", index=False)
                                # Update queue.json status
                                update_queue_status(username, "skipped", "already_following")
                                # Don't increment activity counters, just continue
                                action_performed = False  # Don't count as action, move to next user
                            else:
                                # Record successful follow
                                new_entry = pd.DataFrame([[username, datetime.now()]], 
                                                       columns=["username", "follow_timestamp"])
                                followed_df = pd.concat([followed_df, new_entry], ignore_index=True)
                                followed_df.to_csv("followed.csv", index=False)
                                # Update queue.json status
                                update_queue_status(username, "followed")
                                follow_limiter.record()
                                
                                # Update tracking (sync from persistent storage)
                                current_counts = load_daily_counts()
                                activity_tracker['follows_today'] = current_counts['follows']
                                activity_tracker['recent_actions'].append({
                                    'type': 'follow',
                                    'time': datetime.now(),
                                    'user': username
                                })
                                
                                action_performed = True
                                smart_break(recent_activity_count, base_minutes=2)
                        else:
                            # Follow failed or page not found -- record in followed.csv
                            # so the bot skips this user and moves to the next one
                            reason = "not found/suspended" if follow_result == "not_found" else "follow failed"
                            print(f"⏭️ Skipping @{username} ({reason}) - adding to followed.csv to avoid retrying")
                            new_entry = pd.DataFrame([[username, datetime.now()]], 
                                                   columns=["username", "follow_timestamp"])
                            followed_df = pd.concat([followed_df, new_entry], ignore_index=True)
                            followed_df.to_csv("followed.csv", index=False)
                            # Update queue.json status
                            update_queue_status(username, "skipped", reason)
                            action_performed = False

                # If no actions performed, take appropriate break
                if not action_performed:
                    if unfollow_only_mode:
                        if eligible_unfollows.empty:
                            print("✅ Unfollow-only mode: No eligible unfollows - queue is empty")
                            wait_time = random.uniform(30*60, 60*60)  # 30-60 min wait when queue empty
                            print(f"⏳ Waiting {round(wait_time/60, 1)} min before checking again")
                        else:
                            # Rate limited in unfollow-only mode
                            wait_time = random.uniform(5*60, 10*60)  # 5-10 min wait
                            print(f"⏳ Unfollow rate limited. Waiting {round(wait_time/60, 1)} min")
                    elif above_threshold and eligible_unfollows.empty:
                        wait_time = random.uniform(10*60, 20*60)  # 10-20 min wait when above threshold
                        print(f"🚨 Above threshold but no eligible unfollows. Waiting {round(wait_time/60, 1)} min")
                    elif current_difference >= 405:  # Approaching threshold
                        wait_time = random.uniform(5*60, 10*60)   # 5-10 min wait
                        print(f"⚠️ Approaching threshold. Waiting {round(wait_time/60, 1)} min")
                    else:
                        # Calculate smart delay based on activity
                        delay = activity_based_delay(
                            len([a for a in activity_tracker['recent_actions'] if a['type'] == 'follow']),
                            len([a for a in activity_tracker['recent_actions'] if a['type'] == 'unfollow']), 
                            activity_tracker['likes_today']
                        )
                        wait_time = delay
                        print(f"⏳ Smart delay: {round(wait_time)}s (activity: {recent_activity_count})")
                    
                    time.sleep(wait_time)

            except (InvalidSessionIdException, WebDriverException) as e:
                print(f"🚨 Session error: {e}")
                print("🔄 Attempting to recover driver...")
                driver = recover_driver(driver)
                if driver is None:
                    print("❌ Recovery failed - exiting")
                    break
                continue

            except (WebDriverException, ConnectionRefusedError, URLError, MaxRetryError) as e:
                print(f"🚨 CRITICAL: WebDriver connection lost: {e}")
                print("🛠️ Attempting to restart WebDriver...")
                try:
                    driver.quit()
                except:
                    pass
                time.sleep(30) # Wait before restarting
                driver = setup_driver()
                print("🚀 WebDriver restarted. Resuming operations...")
                # Re-login after driver restart
                load_cookies_from_file(driver, "cacheforlogin.json", "x.com")
                if not safe_get(driver, "https://x.com/home"):
                    print("❌ Could not load Twitter home page after restart; exiting.")
                    break # Exit the main loop
                continue # Continue to the next iteration of the while loop


    except KeyboardInterrupt:
        print("⚠️ Bot stopped by user.")
    finally:
        print("🛑 Shutting down bot...")
        driver.quit()

if __name__ == "__main__":
    # Usage: python3 twitter_bot.py [run|unfollow]
    #   run      - normal mode (follow + unfollow)
    #   unfollow - unfollow-only mode
    main()