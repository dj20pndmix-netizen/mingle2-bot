"""
Mingle2 Bot - Automation & Interaction Engine
Powered by Playwright with stealth configurations, session persistence,
account syncing, automated like-back, and targeted search & like.
Resilient against DNS timeouts (ERR_NAME_NOT_RESOLVED), network drops, and ad script delays.
"""
import os
import sys
import json
import time
import random
import re
import threading
from typing import Dict, Any, List, Optional, Tuple
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, BrowserContext, Page

import database as db
import scraper

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "session.json")

def init_session_from_env():
    """Restores session.json from environment variables (e.g. MINGLE2_SESSION_JSON) on cloud hosts like Render."""
    if not os.path.exists(SESSION_FILE):
        env_val = os.environ.get("MINGLE2_SESSION_JSON") or os.environ.get("SESSION_JSON")
        if env_val:
            try:
                os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
                data = json.loads(env_val)
                with open(SESSION_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                db.log_activity("Restored session.json from environment variable.", level="INFO")
            except Exception as e:
                db.log_activity(f"Failed to restore session from env: {e}", level="WARNING")

init_session_from_env()

STOP_FLAG = False

def request_stop():
    global STOP_FLAG
    STOP_FLAG = True
    db.log_activity("Stop requested by user.", level="WARNING")

def reset_stop():
    global STOP_FLAG
    STOP_FLAG = False

def is_stopped() -> bool:
    global STOP_FLAG
    return STOP_FLAG

def get_stealth_args() -> List[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--disable-browser-side-navigation",
        "--disable-gpu",
        "--dns-result-order=ipv4first",
        "--disable-features=AsyncDns",
        "--ignore-certificate-errors"
    ]

def setup_context_routes(context: BrowserContext):
    """
    Aborts requests to heavy third-party ad networks and tracking scripts
    which frequently cause DNS timeouts, high bandwidth usage, and domcontentloaded hangs.
    """
    ad_patterns = [
        "**/*adpushup*",
        "**/*doubleclick*",
        "**/*criteo*",
        "**/*googlesyndication*",
        "**/*adnxs*",
        "**/*amazon-adsystem*",
        "**/*taboola*"
    ]
    for pattern in ad_patterns:
        try:
            context.route(pattern, lambda route: route.abort())
        except Exception:
            pass

def launch_browser_safe(playwright_instance, headless: bool = True, args: list = None):
    """
    Safely launches Chromium with automatic recovery.
    If the Chromium browser binary is missing (e.g. Docker container after pip update),
    automatically runs 'playwright install chromium' to fetch the matching binary.
    """
    launch_args = args if args is not None else get_stealth_args()
    try:
        return playwright_instance.chromium.launch(headless=headless, args=launch_args)
    except Exception as e:
        err_msg = str(e)
        if "Executable doesn't exist" in err_msg or "playwright install" in err_msg:
            db.log_activity("Playwright browser binary missing. Running automatic install...", level="WARNING")
            try:
                import subprocess
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                db.log_activity("Chromium binary installed successfully! Retrying launch...", level="SUCCESS")
                return playwright_instance.chromium.launch(headless=headless, args=launch_args)
            except Exception as install_err:
                db.log_activity(f"Failed to auto-install Playwright chromium: {install_err}", level="ERROR")
        raise

def create_context(playwright_instance, headless: bool = True) -> BrowserContext:
    """Creates a browser context with stealth parameters, ad-blocking, and saved session state."""
    browser = launch_browser_safe(
        playwright_instance,
        headless=headless,
        args=get_stealth_args()
    )
    
    context_options = {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 800},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }
    
    context = None
    if os.path.exists(SESSION_FILE):
        try:
            context = browser.new_context(storage_state=SESSION_FILE, **context_options)
            db.log_activity("Loaded existing session credentials from session.json", level="INFO")
        except Exception as e:
            db.log_activity(f"Failed to load session file, creating fresh context: {e}", level="WARNING")

    if not context:
        context = browser.new_context(**context_options)
        
    setup_context_routes(context)
    return context

def human_delay(min_s: Optional[float] = None, max_s: Optional[float] = None):
    """Adds a humanized random pause between actions to avoid rate limits."""
    cfg = db.get_config()
    min_delay = min_s if min_s is not None else float(cfg.get("min_delay_seconds", 3))
    max_delay = max_s if max_s is not None else float(cfg.get("max_delay_seconds", 7))
    if max_delay < min_delay:
        max_delay = min_delay + 2.0
    delay = random.uniform(min_delay, max_delay)
    time.sleep(delay)

def safe_goto(page: Page, url: str, timeout: int = 30000, max_retries: int = 3, wait_until: str = "commit") -> bool:
    """
    Safely navigates to a URL with automatic retries and exponential backoff
    resilient against DNS drops (ERR_NAME_NOT_RESOLVED), network disconnection,
    and slow ad scripts.
    """
    for attempt in range(1, max_retries + 1):
        if is_stopped():
            return False
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            page.wait_for_timeout(1500) # allow page scripts to settle
            return True
        except Exception as e:
            err_str = str(e)
            is_net_error = any(k in err_str for k in [
                "ERR_NAME_NOT_RESOLVED",
                "ERR_CONNECTION_RESET",
                "ERR_INTERNET_DISCONNECTED",
                "ERR_CONNECTION_REFUSED",
                "ERR_TIMED_OUT",
                "ERR_NETWORK_CHANGED",
                "Timeout"
            ])
            if attempt < max_retries:
                wait_time = attempt * 3
                db.log_activity(
                    f"Network/DNS pause on {url}. Retrying in {wait_time}s (Attempt {attempt}/{max_retries})...",
                    level="WARNING"
                )
                time.sleep(wait_time)
            else:
                db.log_activity(
                    f"Navigation to {url} failed after {max_retries} attempts: {err_str}",
                    level="ERROR"
                )
                return False
    return False

def save_session(context: BrowserContext):
    """Saves browser cookies and storage state for future unattended logins."""
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    try:
        context.storage_state(path=SESSION_FILE)
        db.log_activity("Browser session saved successfully to session.json", level="INFO")
    except Exception as e:
        db.log_activity(f"Error saving session state: {e}", level="ERROR")

def save_raw_session(session_input: Any) -> bool:
    """Imports raw session JSON string or dict and triggers immediate account sync."""
    try:
        if isinstance(session_input, str):
            data = json.loads(session_input.strip())
        else:
            data = session_input
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        db.log_activity("Imported session credentials successfully via Session Manager.", level="SUCCESS")
        threading.Thread(target=sync_account, daemon=True).start()
        return True
    except Exception as e:
        db.log_activity(f"Failed to import session credentials: {e}", level="ERROR")
        return False

def direct_http_login(username_or_email: str, password: str) -> dict:
    """
    Direct HTTP authentication with Mingle2 without requiring a browser GUI.
    Fast (1-2s), extremely lightweight, works anywhere (Docker, Render, VPS, local).
    """
    reset_stop()
    db.log_activity(f"Attempting direct authentication for '{username_or_email}'...", level="INFO")
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Referer': 'https://mingle2.com/user/login',
        'Origin': 'https://mingle2.com'
    })
    
    try:
        r = session.get('https://mingle2.com/user/login', timeout=20)
        if r.status_code != 200:
            return {"success": False, "error": f"Mingle2 server unreachable (HTTP {r.status_code})"}
            
        soup = BeautifulSoup(r.text, 'html.parser')
        token_elem = soup.find('input', {'name': 'authenticity_token'})
        token_val = token_elem.get('value') if token_elem else ''
        
        payload = {
            'utf8': '✓',
            'authenticity_token': token_val,
            'login': username_or_email,
            'password': password,
            'commit': 'LOGIN NOW'
        }
        
        post_res = session.post('https://mingle2.com/user/verify_login', data=payload, timeout=25, allow_redirects=True)
        cookies = session.cookies.get_dict()
        
        # Check authentication indicators
        has_auth_cookies = 'known_user' in cookies or 'p' in cookies
        is_authenticated_page = any(w in post_res.text.lower() for w in ['logout', 'my profile', 'log out', 'edit profile'])
        
        # Verify by fetching edit_profile if needed
        if not (has_auth_cookies or is_authenticated_page):
            try:
                verify_check = session.get('https://mingle2.com/user/edit_profile', timeout=15)
                if 'logout' in verify_check.text.lower():
                    is_authenticated_page = True
            except Exception:
                pass
                
        if has_auth_cookies or is_authenticated_page:
            now = time.time()
            pw_cookies = []
            for cookie in session.cookies:
                c_dict = {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain if cookie.domain.startswith('.') else f".{cookie.domain}",
                    "path": cookie.path,
                    "expires": cookie.expires if cookie.expires else now + 86400 * 30,
                    "httpOnly": cookie.name in ['_session_id', 'p'],
                    "secure": cookie.secure,
                    "sameSite": "Lax"
                }
                pw_cookies.append(c_dict)
                
            storage_state = {
                "cookies": pw_cookies,
                "origins": [
                    {
                        "origin": "https://mingle2.com",
                        "localStorage": []
                    }
                ]
            }
            
            os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(storage_state, f, indent=2)
                
            db.log_activity(f"Authentication successful for '{username_or_email}'! Session saved.", level="SUCCESS")
            # Trigger background sync to get user info, incoming likes, mutual matches
            threading.Thread(target=sync_account, daemon=True).start()
            return {"success": True, "message": f"Successfully authenticated as {username_or_email}!"}
        else:
            err_msg = "Invalid username/email or password."
            err_div = soup.find(class_=lambda c: c and 'error' in c.lower())
            if err_div and err_div.get_text(strip=True):
                err_msg = err_div.get_text(strip=True)
            return {"success": False, "error": err_msg}
    except Exception as e:
        db.log_activity(f"Direct authentication attempt: {e}", level="WARNING")
        return {"success": False, "error": str(e)}

def perform_login(username_or_email: str, password: str) -> dict:
    """
    Unified high-level login handler.
    1. Attempts direct HTTP authentication (fast, lightweight, 1-2 seconds).
    2. If direct HTTP encounters challenge/network issue, falls back to Playwright headless automation.
    """
    res = direct_http_login(username_or_email, password)
    if res.get("success"):
        return res
        
    err_str = res.get("error", "").lower()
    if "invalid" in err_str or "incorrect" in err_str or "password" in err_str:
        return res
        
    db.log_activity("Falling back to automated headless browser authentication...", level="INFO")
    pw_ok = headless_login(username_or_email, password)
    if pw_ok:
        return {"success": True, "message": f"Successfully authenticated as {username_or_email}!"}
    return res

def headless_login(username_or_email: str, password: str) -> bool:
    """
    Performs fully automated headless login on cloud environments (like Render).
    Navigates to login page, enters credentials, submits, and saves session.
    """
    reset_stop()
    db.log_activity(f"Initiating headless login for '{username_or_email}'...", level="INFO")
    with sync_playwright() as p:
        browser = launch_browser_safe(p, headless=True, args=get_stealth_args())
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        setup_context_routes(context)
        page = context.new_page()
        try:
            ok = safe_goto(page, "https://mingle2.com/user/login", timeout=35000, wait_until="commit")
            if not ok:
                db.log_activity("Could not reach Mingle2 login page.", level="ERROR")
                browser.close()
                return False

            page.wait_for_timeout(2000)
            dismiss_popups(page)

            user_input = page.query_selector("input#login, input[name='login'], input[name*='user'], input[name*='email'], #username, #email")
            pass_input = page.query_selector("input#password, input[name='password'], input[type='password'], input[name*='pass'], #password")

            if not user_input or not pass_input:
                db.log_activity("Login input fields not found.", level="ERROR")
                browser.close()
                return False

            user_input.fill(username_or_email)
            human_delay(0.5, 1.0)
            pass_input.fill(password)
            human_delay(0.5, 1.0)

            submit_btn = page.query_selector("button[type='submit'], input[type='submit'], .login-btn, form button")
            if submit_btn:
                submit_btn.click()
            else:
                pass_input.press("Enter")

            page.wait_for_timeout(5000)
            dismiss_popups(page)

            current_url = page.url
            content = page.content()

            if any(k in content.lower() for k in ["logout", "my profile", "account", "inbox", "mutual match"]) or "/home" in current_url:
                db.log_activity("Headless cloud login successful! Saving session...", level="SUCCESS")
                save_session(context)
                _extract_and_save_my_account(page)
                _sync_incoming_likes(page)
                _sync_your_matches(page)
                browser.close()
                return True
            else:
                db.log_activity("Login failed: Verification check or CAPTCHA encountered. Please use session.json import.", level="WARNING")
                browser.close()
                return False
        except Exception as e:
            db.log_activity(f"Headless login error: {e}", level="ERROR")
            try:
                browser.close()
            except Exception:
                pass
            return False

def interactive_login():
    """
    Launches a visible (headful) browser window so the user can easily log in on desktop,
    complete any CAPTCHAs or 2FA, and authenticate. Once logged in, session is saved
    and account is automatically synced.
    """
    reset_stop()
    # Guard against headless cloud containers (like Render or Docker without X11)
    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        db.log_activity(
            "Interactive desktop browser cannot be opened in headless Linux / cloud container. "
            "Please use the Username/Password form or Session Import in the dashboard.",
            level="WARNING"
        )
        return False

    db.log_activity("Launching desktop browser for interactive login. Please log in to Mingle2...", level="INFO")
    try:
        with sync_playwright() as p:
            browser = launch_browser_safe(p, headless=False, args=get_stealth_args())
            context_opts = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 850}
            }
            if os.path.exists(SESSION_FILE):
                try:
                    context = browser.new_context(storage_state=SESSION_FILE, **context_opts)
                except Exception:
                    context = browser.new_context(**context_opts)
            else:
                context = browser.new_context(**context_opts)

            setup_context_routes(context)
            page = context.new_page()
            safe_goto(page, "https://mingle2.com/user/login", timeout=45000, wait_until="commit")
            
            db.log_activity("Browser open. Please log into your Mingle2 account. Waiting for login confirmation...", level="INFO")
            
            # Poll until user is logged in
            max_wait = 180  # 3 minutes
            start_t = time.time()
            logged_in = False
            
            while time.time() - start_t < max_wait:
                if is_stopped():
                    break
                
                try:
                    current_url = page.url
                    page_text = page.inner_text("body") if page else ""
                    if any(k in page_text.lower() for k in ["log out", "logout", "my profile", "inbox", "mutual match"]) or "/home" in current_url or "/inbox" in current_url:
                        logged_in = True
                        break
                except Exception:
                    pass
                
                time.sleep(2)

            if logged_in:
                db.log_activity("Login detected! Saving session and syncing account...", level="SUCCESS")
                save_session(context)
                _extract_and_save_my_account(page)
            else:
                db.log_activity("Login timed out or window was closed before completing login.", level="WARNING")

            browser.close()
            return logged_in
    except Exception as e:
        db.log_activity(f"Interactive login could not launch browser: {e}. Please use in-app credentials or session import.", level="ERROR")
        return False

def _extract_and_save_my_account(page: Page) -> Optional[Dict[str, Any]]:
    """Extracts our own authenticated account details and ID from Mingle2."""
    try:
        safe_goto(page, "https://mingle2.com/user/edit_profile", timeout=25000, wait_until="commit")
        human_delay(1.5, 2.5)
        
        content = page.content()
        soup = scraper.BeautifulSoup(content, "html.parser")
        
        my_user_id = ""
        my_username = ""
        my_photo = ""
        my_location = ""
        
        # Check user ID in profile links
        user_link = soup.find("a", href=re.compile(r'/(?:users|user)/view/(\d+)'))
        if user_link and user_link.get("href"):
            m = re.search(r'/(?:users|user)/view/(\d+)', user_link["href"])
            if m:
                my_user_id = m.group(1)
        
        # Check user ID in forms
        uid_input = soup.find("input", attrs={"name": re.compile(r'user_id|id', re.I)})
        if not my_user_id and uid_input and uid_input.get("value"):
            my_user_id = uid_input["value"]
            
        # Check current URL
        if not my_user_id:
            m = re.search(r'view/(\d+)', page.url)
            if m:
                my_user_id = m.group(1)

        # Fallback to known_user cookie in storage
        if not my_user_id and os.path.exists(SESSION_FILE):
            try:
                with open(SESSION_FILE, "r") as f:
                    sdata = json.load(f)
                    for c in sdata.get("cookies", []):
                        if c.get("name") == "known_user" and c.get("value"):
                            my_user_id = c["value"]
                            break
            except Exception:
                pass

        # Extract username
        uname_el = soup.find(class_="username") or soup.find("input", attrs={"name": "user[username]"})
        if uname_el:
            my_username = uname_el.get("value") or uname_el.get_text().strip()
            
        if not my_username:
            my_username = f"MingleUser_{my_user_id or 'Me'}"

        # Extract photo
        avatar_img = soup.find("img", class_=re.compile(r'portrait|avatar|profile', re.I))
        if avatar_img and avatar_img.get("src"):
            my_photo = avatar_img["src"]

        if my_user_id:
            db.save_account_sync(
                user_id=my_user_id,
                username=my_username,
                profile_url=f"https://mingle2.com/users/view/{my_user_id}",
                avatar_url=my_photo,
                location=my_location
            )
            return {"user_id": my_user_id, "username": my_username}
        else:
            db.log_activity("Logged in, but could not resolve primary user ID automatically yet.", level="WARNING")
    except Exception as e:
        db.log_activity(f"Error during account sync: {e}", level="ERROR")
    return None

def _sync_incoming_likes(page: Page) -> Dict[str, Any]:
    """Scrapes live incoming likes count badge and fans list from Mingle2."""
    try:
        ok = safe_goto(page, "https://mingle2.com/mutual_match/who_is_interested_in_me", timeout=25000, wait_until="commit")
        if not ok:
            return {"badge": "", "fans": []}
            
        content = page.content()
        parsed = scraper.parse_mutual_match_page(content)
        badge = parsed.get("badge_count", "")
        
        # If badge not found on who_is_interested, check /mutual_match
        if not badge:
            safe_goto(page, "https://mingle2.com/mutual_match", timeout=20000, wait_until="commit")
            p2 = scraper.parse_mutual_match_page(page.content())
            badge = p2.get("badge_count", "")
            
        if badge:
            db.set_incoming_badge(badge)
            db.log_activity(f"Synchronized Incoming Likes: {badge} users liked your profile!", level="SUCCESS")
            
        # Record parsed fans into database
        for u in parsed.get("users", []):
            db.record_like_received(
                user_id=u["user_id"],
                username=u["username"],
                photo_url=u.get("photo_url", ""),
                age=u.get("age", 0),
                location=u.get("location", ""),
                received_at_str=u.get("received_at", "")
            )
            
        return {"badge": badge, "count": len(parsed.get("users", []))}
    except Exception as e:
        db.log_activity(f"Error syncing incoming likes: {e}", level="WARNING")
        return {"badge": "", "fans": []}

def _sync_your_matches(page: Page) -> List[Dict[str, Any]]:
    """Scrapes confirmed mutual matches from Mingle2 /mutual_match/your_matches."""
    try:
        ok = safe_goto(page, "https://mingle2.com/mutual_match/your_matches", timeout=25000, wait_until="commit")
        if not ok:
            return []
        page.wait_for_timeout(2500)
        dismiss_popups(page)
        content = page.content()
        matches = scraper.parse_your_matches_page(content)
        for m in matches:
            db.record_mutual_match(
                user_id=m["user_id"],
                username=m["username"],
                photo_url=m.get("photo_url", ""),
                age=m.get("age", 0),
                gender=m.get("gender", ""),
                location=m.get("location", ""),
                matched_at=m.get("matched_at", ""),
                profile_url=m.get("profile_url", "")
            )
        if matches:
            db.log_activity(f"Synchronized {len(matches)} Confirmed Mutual Matches from Mingle2!", level="SUCCESS")
        return matches
    except Exception as e:
        db.log_activity(f"Error syncing mutual matches: {e}", level="WARNING")
        return []

def sync_account() -> bool:
    """Headless account, incoming likes, and mutual matches synchronization using saved session."""
    reset_stop()
    if not os.path.exists(SESSION_FILE):
        db.log_activity("No saved session found. Please run interactive login first.", level="WARNING")
        return False

    db.log_activity("Starting full account and mutual matches synchronization...", level="INFO")
    with sync_playwright() as p:
        context = create_context(p, headless=True)
        page = context.new_page()
        try:
            safe_goto(page, "https://mingle2.com/user/edit_profile", timeout=30000, wait_until="commit")
            res = _extract_and_save_my_account(page)
            
            # Synchronize incoming likes badge and fans
            _sync_incoming_likes(page)
            
            # Synchronize confirmed mutual matches
            _sync_your_matches(page)
            
            save_session(context)
            context.browser.close()
            return res is not None
        except Exception as e:
            db.log_activity(f"Failed to sync account: {e}", level="ERROR")
            context.browser.close()
            return False

def safe_query_selector(page: Page, selector: str, max_retries: int = 3):
    """
    Safely queries an element on the page, handling Playwright's
    'Execution context was destroyed, most likely because of a navigation' error
    with automatic retries and DOM stabilization.
    """
    for attempt in range(max_retries):
        try:
            return page.query_selector(selector)
        except Exception as e:
            err = str(e)
            if "Execution context was destroyed" in err or "navigation" in err:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    time.sleep(0.8)
            else:
                return None
    return None

def safe_evaluate(page: Page, expression: str, arg: Any = None, max_retries: int = 3):
    """Safely evaluates JS expression on page, handling navigation context destroy errors."""
    for attempt in range(max_retries):
        try:
            if arg is not None:
                return page.evaluate(expression, arg)
            return page.evaluate(expression)
        except Exception as e:
            err = str(e)
            if "Execution context was destroyed" in err or "navigation" in err:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    time.sleep(0.8)
            else:
                return None
    return None

def check_and_dismiss_match_popup(page: Page, user_id: str = "", username: str = "") -> bool:
    """
    Detects if the 'It's A Match!' modal popup appeared on Mingle2 (as shown in user photo).
    If detected:
    - Logs confirmed mutual match in the dashboard: 'MUTUAL MATCH CONFIRMED! 'It's A Match!' notification popped up!'
    - Records the match in mutual_matches DB table
    - Clicks the 'X' (close) button at the top-right to dismiss the modal cleanly.
    """
    match_selectors = [
        "div:has-text(\"It's A Match!\")",
        "div:has-text(\"It's a Match!\")",
        "h1:has-text(\"It's A Match!\")",
        "h2:has-text(\"It's A Match!\")",
        ".match-modal",
        ".remodal[data-remodal-id*='match']"
    ]
    
    is_match_detected = False
    for sel in match_selectors:
        try:
            el = safe_query_selector(page, sel)
            if el and el.is_visible():
                is_match_detected = True
                break
        except Exception:
            pass

    # Fallback text check in DOM
    if not is_match_detected:
        try:
            res = safe_evaluate(page, "() => { return document.body && (document.body.innerText.includes(\"It's A Match!\") || document.body.innerText.includes(\"It's a Match!\")); }")
            if res:
                is_match_detected = True
        except Exception:
            pass

    if is_match_detected:
        db.log_activity(
            f"MUTUAL MATCH CONFIRMED! 'It's A Match!' notification popped up for {username or 'User'} (ID: {user_id})! 🎉",
            level="MATCH",
            user_id=user_id if user_id else None
        )
        if user_id:
            db.record_mutual_match(
                user_id=user_id,
                username=username,
                profile_url=f"https://mingle2.com/users/view/{user_id}",
                matched_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            db.mark_liked_back(user_id)

        # Click the 'X' close button at the top right of the modal
        close_selectors = [
            ".remodal-close",
            "button[data-remodal-action='close']",
            ".match-modal .close",
            ".modalCloseImg",
            "a[rel='modal:close']",
            "button.close",
            "a.close",
            ".close-button",
            "#close-match-modal"
        ]
        for csel in close_selectors:
            try:
                cbtn = safe_query_selector(page, csel)
                if cbtn and cbtn.is_visible():
                    cbtn.click(force=True, timeout=2000)
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                pass
        
        try:
            safe_evaluate(page, "() => { const b = document.querySelector('.remodal-close, [data-remodal-action=\"close\"], a.close, button.close'); if (b) b.click(); }")
        except Exception:
            pass
        return True
    return False

def dismiss_popups(page: Page):
    """Dismisses intrusive popups/modals (like share-location modal, upgrade dialogs, match popups, etc.)"""
    popup_selectors = [
        "#share-location-modal-container .simplemodal-close",
        "#share-location-modal-container a.close",
        "#share-location-modal-container a.allow-btn",
        ".simplemodal-close",
        "a[rel='modal:close']",
        ".modalCloseImg",
        "#osx-modal-close",
        ".remodal-close",
        "button[data-remodal-action='close']",
        "button[data-remodal-action='cancel']",
        "a.close",
        "button.close"
    ]
    for sel in popup_selectors:
        try:
            btn = safe_query_selector(page, sel)
            if btn and btn.is_visible():
                btn.click(force=True, timeout=2000)
                page.wait_for_timeout(500)
        except Exception:
            pass

def _click_like_button(page: Page, user_id: str = "", username: str = "") -> Tuple[bool, bool]:
    """
    Accurately executes like on Mingle2:
    1. Checks if already liked (e.g. #rate-yes.selected / .disabled).
    2. Submits verified like via /mutual_match/rate_only with CSRF token directly in page.
    3. Clicks #rate-yes or like button as fallback.
    4. Detects and handles 'It's A Match!' modal popup.
    Returns: (liked_successfully: bool, is_mutual_match: bool)
    """
    dismiss_popups(page)
    
    uid = user_id
    if not uid:
        m = re.search(r'/(?:users|user)/view/(\d+)', page.url)
        if m:
            uid = m.group(1)
    if not uid:
        data_el = safe_query_selector(page, "#rate-yes[data-id], a.rate-btn[data-id]")
        if data_el:
            uid = data_el.get_attribute("data-id") or ""

    if not uid:
        db.log_activity("Could not resolve target user ID on page to execute like.", level="WARNING")
        return (False, False)

    # 1. Check if already liked on Mingle2
    already_selected = safe_evaluate(page, """(id) => {
        const btn = document.querySelector('#rate-yes, a.rate-btn[data-id="' + id + '"]');
        if (btn && (btn.classList.contains('selected') || btn.classList.contains('disabled'))) {
            return true;
        }
        return false;
    }""", uid)
    if already_selected:
        db.log_activity(f"Candidate {username or uid} (ID: {uid}) is already liked on Mingle2.", level="DEBUG", user_id=uid)
        db.record_like_sent(user_id=uid, username=username or f"User_{uid}", source="search_criteria")
        return (True, False)

    liked = False
    is_mutual = False

    # 2. Method A: Official Mingle2 AJAX rate_only execution with CSRF token
    try:
        eval_res = safe_evaluate(page, """(id) => {
            const csrfMeta = document.querySelector('meta[name="csrf-token"]');
            const token = csrfMeta ? csrfMeta.content : '';
            return new Promise((resolve) => {
                if (window.jQuery) {
                    jQuery.ajax({
                        url: '/mutual_match/rate_only',
                        type: 'POST',
                        data: { id: id, rating: 'Y' },
                        headers: {
                            'X-CSRF-Token': token,
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        dataType: 'json',
                        timeout: 10000,
                        success: function(data) {
                            resolve({ ok: true, data: data });
                        },
                        error: function(xhr) {
                            try {
                                resolve({ ok: false, error: JSON.parse(xhr.responseText) });
                            } catch(e) {
                                resolve({ ok: false, status: xhr.status });
                            }
                        }
                    });
                } else {
                    fetch('/mutual_match/rate_only', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                            'X-CSRF-Token': token,
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        body: 'id=' + encodeURIComponent(id) + '&rating=Y'
                    })
                    .then(r => r.json())
                    .then(d => resolve({ ok: true, data: d }))
                    .catch(e => resolve({ ok: false, error: String(e) }));
                }
            });
        }""", uid)
        
        if eval_res and eval_res.get("ok"):
            liked = True
            data = eval_res.get("data") or {}
            if data.get("is_mutual") or data.get("mutual_match_connection_id"):
                is_mutual = True
    except Exception as e:
        db.log_activity(f"AJAX rate_only attempt notice for user {uid}: {e}", level="DEBUG", user_id=uid)

    # 3. Method B: Fallback click on #rate-yes
    if not liked:
        btn = safe_query_selector(page, "#rate-yes, a.rate-btn[data-id]")
        if btn and btn.is_visible():
            try:
                btn.click(force=True, timeout=4000)
                liked = True
                page.wait_for_timeout(2000)
            except Exception:
                pass

    page.wait_for_timeout(1500)
    
    # 4. Check if 'It's A Match!' modal popup appeared
    popup_matched = check_and_dismiss_match_popup(page, user_id=uid, username=username)
    if popup_matched:
        is_mutual = True

    return (liked, is_mutual)

def run_like_back(max_likes: int = 30) -> int:
    """
    Visits 'Who Is Interested In Me' and Mutual Match streams.
    Discovers users who liked our profile, accurately likes them back,
    handles 'It's A Match!' popup notifications, and prevents navigation errors.
    """
    reset_stop()
    if not os.path.exists(SESSION_FILE):
        db.log_activity("No saved session found. Please log in first.", level="WARNING")
        return 0

    db.log_activity(f"Starting 'Like Back' / Mutual Match routine (Limit: {max_likes})...", level="INFO")
    liked_count = 0

    with sync_playwright() as p:
        context = create_context(p, headless=True)
        page = context.new_page()
        try:
            # 1. Sync live incoming likes & fans
            _sync_incoming_likes(page)
            
            # Sync verified mutual matches directly from Mingle2
            _sync_your_matches(page)
            
            # 2. PHASE 1: Like back actual users who liked our profile from likes_received
            pending_fans = db.get_pending_like_backs(limit=max_likes)
            if pending_fans:
                db.log_activity(f"Found {len(pending_fans)} pending unblurred fans who liked you! Prioritizing Like-Back for guaranteed mutual matches...", level="INFO")
                for fan in pending_fans:
                    if is_stopped() or liked_count >= max_likes:
                        break
                    
                    f_uid = str(fan["user_id"]).strip()
                    f_name = fan.get("username") or f"User_{f_uid}"
                    
                    # Strictly ensure f_uid is a real Mingle2 user profile ID (8 digits starting with 9)
                    if not f_uid.isdigit() or len(f_uid) != 8 or f_uid.startswith(("10", "11", "fan_", "blurred_")):
                        db.mark_liked_back(f_uid)
                        continue
                    
                    if db.has_already_liked(f_uid):
                        db.mark_liked_back(f_uid)
                        continue
                        
                    db.log_activity(f"Liking back fan {f_name} (ID: {f_uid})...", level="INFO", user_id=f_uid)
                    
                    profile_url = f"https://mingle2.com/users/view/{f_uid}"
                    ok = safe_goto(page, profile_url, timeout=25000, wait_until="commit")
                    if not ok:
                        continue
                    
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=6000)
                    except Exception:
                        page.wait_for_timeout(1500)

                    liked, is_mutual = _click_like_button(page, user_id=f_uid, username=f_name)
                    if liked:
                        db.record_like_sent(
                            user_id=f_uid,
                            username=f_name,
                            source="like_back",
                            matched_criteria={"reason": "Liked our profile (Fan)"}
                        )
                        db.mark_liked_back(f_uid)
                        liked_count += 1
                        db.log_activity(f"Successfully liked back {f_name} (ID: {f_uid}) [{liked_count}/{max_likes}]", level="SUCCESS", user_id=f_uid)
                        if is_mutual:
                            db.record_mutual_match(
                                user_id=f_uid,
                                username=f_name,
                                photo_url=fan.get("photo_url", ""),
                                age=fan.get("age", 0),
                                location=fan.get("location", ""),
                                profile_url=profile_url
                            )
                    
                    human_delay(1.5, 3.0)

            # 3. PHASE 2: If we still have likes remaining, explore live Mutual Match stream (/mutual_match/match)
            if liked_count < max_likes and not is_stopped():
                db.log_activity(f"Continuing Like Back in Mutual Match Stream: https://mingle2.com/mutual_match/match ({max_likes - liked_count} remaining)...", level="INFO")
                ok = safe_goto(page, "https://mingle2.com/mutual_match/match", timeout=25000, wait_until="commit")
                if ok:
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=8000)
                    except Exception:
                        page.wait_for_timeout(2000)

                    attempts = 0
                    max_attempts = (max_likes - liked_count) * 4
                    while liked_count < max_likes and attempts < max_attempts:
                        if is_stopped():
                            break
                        attempts += 1
                        
                        try:
                            dismiss_popups(page)
                            check_and_dismiss_match_popup(page)
                            
                            current_url = page.url
                            m = re.search(r'match/(\d+)', current_url)
                            uid = m.group(1) if m else ""
                            
                            # Priority 1: Check rate form action /mutual_match/rate/<id> (exact active swipe target)
                            if not uid:
                                rate_form = safe_query_selector(page, "form[action*='/mutual_match/rate/']")
                                if rate_form:
                                    action = rate_form.get_attribute("action") or ""
                                    m_form = re.search(r'/rate/(\d+)', action)
                                    if m_form:
                                        uid = m_form.group(1)

                            # Priority 2: Check profile link on active card
                            if not uid:
                                a_link = safe_query_selector(page, ".match-portrait a[href*='/users/view/'], a[href*='/users/view/'], a[href*='/user/view/']")
                                if a_link:
                                    href = a_link.get_attribute("href") or ""
                                    m_link = re.search(r'view/(\d+)', href)
                                    if m_link:
                                        uid = m_link.group(1)

                            if not uid:
                                db.log_activity("No more match candidates in current stream.", level="INFO")
                                break

                            username = f"User_{uid}"
                            title_el = safe_query_selector(page, ".match-info h1, .match-info a.display-name, a.display-name, .username, h1, .match-portrait")
                            if title_el:
                                try:
                                    t = (title_el.inner_text() or "").split(",")[0].strip()
                                    if t and not t.lower().startswith("rate") and not t.lower().startswith("match"):
                                        username = t
                                except Exception:
                                    pass

                            if db.has_already_liked(uid):
                                db.log_activity(f"Skipping {username} (ID: {uid}) - Already liked.", level="DEBUG", user_id=uid)
                                skip_btn = safe_query_selector(page, "button[data-recaptcha-action='skip'], .rate-no-btn")
                                if skip_btn and skip_btn.is_visible():
                                    try:
                                        skip_btn.click(force=True, timeout=3000)
                                    except Exception:
                                        safe_evaluate(page, "() => { const b = document.querySelector('button[data-recaptcha-action=\"skip\"], .rate-no-btn'); if (b) b.click(); }")
                                else:
                                    page.goto("https://mingle2.com/mutual_match/match", wait_until="commit", timeout=20000)
                                
                                try:
                                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                                except Exception:
                                    page.wait_for_timeout(2000)
                                continue

                            db.log_activity(f"Executing Mutual Match LIKE for {username} (ID: {uid})...", level="INFO", user_id=uid)
                            
                            liked = False
                            is_mutual = False
                            
                            like_btn = safe_query_selector(page, "button[data-recaptcha-action='like'], .rate-yes-btn")
                            if like_btn and like_btn.is_visible():
                                try:
                                    like_btn.click(force=True, timeout=4000)
                                    liked = True
                                except Exception:
                                    pass

                            if not liked:
                                res = safe_evaluate(page, """() => {
                                    const btn = document.querySelector('button[data-recaptcha-action="like"], .rate-yes-btn');
                                    if (btn) { btn.click(); return true; }
                                    const form = document.querySelector('form[action*="/mutual_match/rate/"] input[name="rating"][value="Y"]');
                                    if (form && form.form) { form.form.submit(); return true; }
                                    return false;
                                }""")
                                if res:
                                    liked = True

                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=6000)
                            except Exception:
                                page.wait_for_timeout(2000)

                            popup_matched = check_and_dismiss_match_popup(page, user_id=uid, username=username)
                            if popup_matched:
                                is_mutual = True

                            if liked:
                                db.record_like_sent(
                                    user_id=uid,
                                    username=username,
                                    source="like_back",
                                    matched_criteria={"reason": "Mutual Match Stream"}
                                )
                                db.mark_liked_back(uid)
                                liked_count += 1
                                db.log_activity(f"Successfully liked {username} (ID: {uid}) [{liked_count}/{max_likes}]", level="SUCCESS", user_id=uid)
                                if is_mutual:
                                    db.record_mutual_match(
                                        user_id=uid,
                                        username=username,
                                        profile_url=f"https://mingle2.com/users/view/{uid}"
                                    )

                            human_delay(1.5, 3.0)

                        except Exception as candidate_err:
                            err_str = str(candidate_err)
                            if "Execution context was destroyed" in err_str or "navigation" in err_str:
                                db.log_activity("Page navigated to next candidate, continuing stream...", level="DEBUG")
                                try:
                                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                                except Exception:
                                    page.wait_for_timeout(2000)
                            else:
                                db.log_activity(f"Notice during candidate processing: {candidate_err}", level="WARNING")
                            continue

            # 4. Synchronize confirmations after routine completes
            _sync_incoming_likes(page)
            _sync_your_matches(page)
            save_session(context)
        except Exception as e:
            db.log_activity(f"Error during Like Back routine: {e}", level="ERROR")
        finally:
            context.browser.close()

    db.log_activity(f"Like Back routine completed. Total profiles liked: {liked_count}", level="SUCCESS")
    return liked_count

def _visit_and_like(page: Page, user_id: str, username: str) -> bool:
    """Navigates to profile page and clicks the rate/like button."""
    profile_url = f"https://mingle2.com/users/view/{user_id}"
    ok = safe_goto(page, profile_url, timeout=25000, wait_until="commit")
    if not ok:
        return False
    liked, _ = _click_like_button(page, user_id=user_id, username=username)
    return liked

def search_and_like(max_likes: int = 30) -> int:
    """
    Searches for profiles matching all criteria:
    - Age range, Location, Ethnicity, Body Type, Photo presence.
    - Paginates through search results to discover fresh candidates.
    - Deduplicates and verifies against DB so no profile is ever liked or inspected twice!
    - Accurately executes likes and handles 'It's A Match!' popup notifications.
    """
    reset_stop()
    if not os.path.exists(SESSION_FILE):
        db.log_activity("No saved session found. Please log in first.", level="WARNING")
        return 0

    cfg = db.get_config()
    db.log_activity(
        f"Starting Search & Auto-Like routine. Filters: Age [{cfg.get('min_age')}-{cfg.get('max_age')}], "
        f"Ethnicities: {cfg.get('target_ethnicities')}, Body: {cfg.get('target_body_types')}, "
        f"Locations: '{cfg.get('target_locations')}'. (Limit: {max_likes})",
        level="INFO"
    )

    liked_count = 0
    inspected_count = 0

    with sync_playwright() as p:
        context = create_context(p, headless=True)
        page = context.new_page()
        try:
            discovered_users: List[Dict[str, Any]] = []
            seen_uids = set()

            # Paginate through search pages to discover fresh, uninspected candidates
            max_pages = 5
            for page_num in range(1, max_pages + 1):
                if is_stopped() or len(discovered_users) >= max_likes * 4:
                    break
                
                search_url = f"https://mingle2.com/search?page={page_num}" if page_num > 1 else "https://mingle2.com/search"
                db.log_activity(f"Scanning target profiles on search results: Page {page_num}", level="INFO")
                
                if safe_goto(page, search_url, timeout=25000, wait_until="commit"):
                    content = page.content()
                    found = scraper.parse_search_results(content)
                    
                    new_on_page = 0
                    for u in found:
                        uid = str(u.get("user_id", "")).strip()
                        if uid and uid not in seen_uids:
                            seen_uids.add(uid)
                            # Pre-filter: only queue if NOT already liked and NOT already inspected
                            if not db.has_already_liked(uid) and not db.has_already_inspected(uid):
                                discovered_users.append(u)
                                new_on_page += 1
                                
                    db.log_activity(f"Page {page_num}: Found {len(found)} candidate cards ({new_on_page} fresh candidates)", level="INFO")
                    human_delay(1.5, 2.5)

            db.log_activity(f"Total fresh unique candidates to inspect: {len(discovered_users)}", level="INFO")

            for user_card in discovered_users:
                if is_stopped() or liked_count >= max_likes:
                    break

                uid = str(user_card["user_id"]).strip()
                username = user_card.get("username") or f"User_{uid}"
                profile_url = user_card.get("profile_url") or f"https://mingle2.com/users/view/{uid}"

                # Strict check: skip if already liked or inspected
                if db.has_already_liked(uid) or db.has_already_inspected(uid):
                    db.log_activity(f"Skipping {username} (ID: {uid}) - Already liked or inspected previously.", level="DEBUG", user_id=uid)
                    continue

                inspected_count += 1
                db.log_activity(f"Inspecting Profile #{inspected_count}: {username} (ID: {uid})...", level="INFO", user_id=uid)

                if not safe_goto(page, profile_url, timeout=25000, wait_until="commit"):
                    continue

                try:
                    page.wait_for_load_state("domcontentloaded", timeout=6000)
                except Exception:
                    page.wait_for_timeout(1500)

                try:
                    profile_html = page.content()
                    full_profile = scraper.parse_profile_html(profile_html, profile_url)

                    if not full_profile["user_id"]:
                        full_profile["user_id"] = uid
                    if not full_profile["username"] or full_profile["username"].startswith("User_"):
                        full_profile["username"] = username

                    # Save discovered profile to DB
                    db.save_discovered_profile(full_profile)

                    # Evaluate against target criteria
                    is_match, match_report = scraper.matches_criteria(full_profile, cfg)

                    if is_match:
                        db.log_activity(
                            f"MATCH FOUND: {full_profile['username']} (ID: {uid}) matches all criteria! "
                            f"[Age: {full_profile['age']}, Eth: {full_profile['ethnicity']}, Body: {full_profile['body_type']}, Loc: {full_profile['location']}]",
                            level="MATCH",
                            user_id=uid
                        )
                        # Perform verified like
                        liked, is_mutual = _click_like_button(page, user_id=uid, username=full_profile["username"])
                        if liked:
                            db.record_like_sent(
                                user_id=uid,
                                username=full_profile["username"],
                                source="search_criteria",
                                matched_criteria=match_report
                            )
                            db.record_profile_inspected(
                                user_id=uid,
                                username=full_profile["username"],
                                is_match=True
                            )
                            liked_count += 1
                            db.log_activity(f"Successfully liked {full_profile['username']} (ID: {uid}) [{liked_count}/{max_likes}]", level="SUCCESS", user_id=uid)
                            if is_mutual:
                                db.record_mutual_match(
                                    user_id=uid,
                                    username=full_profile["username"],
                                    photo_url=full_profile.get("photo_url", ""),
                                    age=full_profile.get("age", 0),
                                    gender=full_profile.get("gender", ""),
                                    location=full_profile.get("location", ""),
                                    profile_url=profile_url
                                )
                        else:
                            db.log_activity(f"Failed to click like button for user {uid}", level="WARNING", user_id=uid)
                    else:
                        mismatch_reason = next(iter(match_report.values()), "Criteria mismatch")
                        db.record_profile_inspected(
                            user_id=uid,
                            username=full_profile["username"],
                            is_match=False,
                            mismatch_reason=mismatch_reason
                        )
                        db.log_activity(f"Skipping {full_profile['username']} (ID: {uid}) - Filter mismatch: {mismatch_reason}", level="DEBUG", user_id=uid)

                    human_delay()

                except Exception as e:
                    err_str = str(e)
                    if "Execution context was destroyed" in err_str:
                        db.log_activity(f"Page transition during inspection of {uid}, continuing...", level="DEBUG")
                    else:
                        db.log_activity(f"Error inspecting user {uid}: {e}", level="WARNING")

            save_session(context)
        except Exception as e:
            db.log_activity(f"Error during Search & Like routine: {e}", level="ERROR")
        finally:
            context.browser.close()

    db.log_activity(f"Search & Like routine completed. Liked {liked_count} profiles out of {inspected_count} inspected.", level="SUCCESS")
    return liked_count

if __name__ == "__main__":
    db.init_db()
    print("Mingle2 Automation Bot Engine ready.")
