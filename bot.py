"""
Mingle2 Bot - Automation & Interaction Engine
Powered by Playwright with stealth configurations, session persistence,
account syncing, automated like-back, and targeted search & like.
Resilient against DNS timeouts (ERR_NAME_NOT_RESOLVED), network drops, and ad script delays.
"""
import os
import json
import time
import random
import re
import threading
from typing import Dict, Any, List, Optional
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

def create_context(playwright_instance, headless: bool = True) -> BrowserContext:
    """Creates a browser context with stealth parameters, ad-blocking, and saved session state."""
    browser = playwright_instance.chromium.launch(
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

def headless_login(username_or_email: str, password: str) -> bool:
    """
    Performs fully automated headless login on cloud environments (like Render).
    Navigates to login page, enters credentials, submits, and saves session.
    """
    reset_stop()
    db.log_activity(f"Initiating headless login for '{username_or_email}'...", level="INFO")
    with sync_playwright() as p:
        context = create_context(p, headless=True)
        page = context.new_page()
        try:
            ok = safe_goto(page, "https://mingle2.com/user/login", timeout=35000, wait_until="commit")
            if not ok:
                db.log_activity("Could not reach Mingle2 login page.", level="ERROR")
                context.browser.close()
                return False

            page.wait_for_timeout(2500)
            dismiss_popups(page)

            user_input = page.query_selector("input[name*='user'], input[name*='email'], input[name*='login'], #username, #email")
            pass_input = page.query_selector("input[type='password'], input[name*='pass'], #password")

            if not user_input or not pass_input:
                db.log_activity("Login input fields not found.", level="ERROR")
                context.browser.close()
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
                context.browser.close()
                return True
            else:
                db.log_activity("Login failed: Verification check or CAPTCHA encountered. Please use session.json import.", level="WARNING")
                context.browser.close()
                return False
        except Exception as e:
            db.log_activity(f"Headless login error: {e}", level="ERROR")
            context.browser.close()
            return False

def interactive_login():
    """
    Launches a visible (headful) browser window so the user can easily log in,
    complete any CAPTCHAs or 2FA, and authenticate. Once logged in, session is saved
    and account is automatically synced.
    """
    reset_stop()
    db.log_activity("Launching browser for interactive login. Please log in to Mingle2...", level="INFO")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=get_stealth_args())
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

def run_like_back(max_likes: int = 30) -> int:
    """
    Visits Mutual Match & 'Who Is Interested In Me' streams, discovers users who
    liked our profile or are pending mutual matches, extracts their exact IDs,
    and accurately likes them back.
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
            # First sync live incoming likes badge
            _sync_incoming_likes(page)

            # 1. Check live Mutual Match Feed (swiping matching where users liked you or are mutual)
            db.log_activity("Checking live Mutual Match stream at: https://mingle2.com/mutual_match/match", level="INFO")
            ok = safe_goto(page, "https://mingle2.com/mutual_match/match", timeout=25000, wait_until="commit")
            
            if ok:
                for i in range(max_likes):
                    if is_stopped() or liked_count >= max_likes:
                        break
                    
                    # Extract current profile ID from URL or page
                    current_url = page.url
                    m = re.search(r'match/(\d+)', current_url)
                    uid = m.group(1) if m else ""
                    
                    if not uid:
                        # Check profile links on page
                        a_link = page.query_selector("a[href*='/users/view/'], a[href*='/user/view/']")
                        if a_link:
                            href = a_link.get_attribute("href") or ""
                            m = re.search(r'view/(\d+)', href)
                            if m:
                                uid = m.group(1)

                    if not uid:
                        db.log_activity("No more match candidates in current stream.", level="INFO")
                        break

                    # Check candidate username
                    username = f"User_{uid}"
                    title_el = page.query_selector("a.display-name, .username, h1, .match-portrait")
                    if title_el:
                        t = (title_el.inner_text() or "").split(",")[0].strip()
                        if t:
                            username = t

                    # Check if already liked in DB
                    if db.has_already_liked(uid):
                        db.log_activity(f"Skipping {username} (ID: {uid}) - Already liked.", level="DEBUG")
                        dismiss_popups(page)
                        # Click pass/no to move forward
                        skip_btn = page.query_selector("button[data-recaptcha-action='skip'], .rate-no-btn")
                        if skip_btn and skip_btn.is_visible():
                            try:
                                skip_btn.click(force=True, timeout=3000)
                            except Exception:
                                page.evaluate("() => { const b = document.querySelector('button[data-recaptcha-action=\"skip\"], .rate-no-btn'); if (b) b.click(); }")
                            page.wait_for_timeout(2000)
                        else:
                            page.goto("https://mingle2.com/mutual_match/match", wait_until="commit", timeout=20000)
                            page.wait_for_timeout(2000)
                        continue

                    db.log_activity(f"Executing Mutual Match LIKE for {username} (ID: {uid})...", level="INFO", user_id=uid)
                    dismiss_popups(page)
                    
                    # 1. Click the actual Like button in the form (with rating=Y)
                    liked = False
                    like_btn = page.query_selector("button[data-recaptcha-action='like']")
                    if like_btn:
                        try:
                            like_btn.click(force=True, timeout=4000)
                            liked = True
                            page.wait_for_timeout(2000)
                        except Exception:
                            pass

                    if not liked:
                        # Fallback A: JS evaluation to trigger like submission with rating=Y
                        try:
                            res = page.evaluate("""() => {
                                const btn = document.querySelector('button[data-recaptcha-action="like"]');
                                if (btn) { btn.click(); return true; }
                                const form = document.querySelector('form[action*="/mutual_match/rate/"] input[name="rating"][value="Y"]');
                                if (form && form.form) { form.form.submit(); return true; }
                                return false;
                            }""")
                            if res:
                                liked = True
                                page.wait_for_timeout(2000)
                        except Exception:
                            pass

                    if not liked:
                        # Fallback B: visit profile directly and click rate-btn (#rate-yes)
                        liked = _visit_and_like(page, uid, username)

                    if liked:
                        db.record_like_sent(
                            user_id=uid,
                            username=username,
                            source="like_back",
                            matched_criteria={"reason": "Mutual Match Stream"}
                        )
                        db.mark_liked_back(uid)
                        liked_count += 1
                        db.log_activity(f"Successfully liked back {username} (ID: {uid}) [{liked_count}/{max_likes}]", level="SUCCESS", user_id=uid)

                    human_delay(1.5, 3.0)

            # 2. Re-sync badge count and confirmed mutual matches after liking back
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
    return _click_like_button(page)

def dismiss_popups(page: Page):
    """Dismisses intrusive popups/modals (like share-location modal, upgrade dialogs, etc.)"""
    popup_selectors = [
        "#share-location-modal-container .simplemodal-close",
        "#share-location-modal-container a.close",
        "#share-location-modal-container a.allow-btn",
        ".simplemodal-close",
        "a[rel='modal:close']",
        ".modalCloseImg",
        "#osx-modal-close"
    ]
    for sel in popup_selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(force=True, timeout=2000)
                page.wait_for_timeout(500)
                break
        except Exception:
            pass

def _click_like_button(page: Page) -> bool:
    """Attempts to locate and click the like button on the current Mingle2 profile page."""
    dismiss_popups(page)
    selectors = [
        "#rate-yes",
        "a.rate-btn[data-id]",
        "#user_profile_commands .rate-btn",
        "button[data-recaptcha-action='like']",
        ".rate-btn",
        ".rate-yes-btn",
        "a[href*='rate']",
        "a:has-text('Like')",
        "button:has-text('Like')",
        "#user_profile_commands div.btn-with-text:has-text('Like')"
    ]
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.scroll_into_view_if_needed()
                human_delay(0.5, 1.0)
                try:
                    btn.click(force=True, timeout=4000)
                except Exception:
                    page.evaluate(f"() => {{ const el = document.querySelector('{sel}'); if (el) el.click(); }}")
                human_delay(1.0, 1.5)
                return True
        except Exception:
            continue
    return False

def search_and_like(max_likes: int = 30) -> int:
    """
    Searches for profiles matching all criteria:
    - Age range
    - Location
    - Skin colour / Ethnicity
    - Body Type / Size
    - Photo presence
    Accurately syncs and verifies User IDs, and likes matched candidates.
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
            # Sources for discovering candidates
            search_sources = [
                "https://mingle2.com/search",
                "https://mingle2.com/online_users",
                "https://mingle2.com/browse",
                "https://mingle2.com/mutual_match/match"
            ]

            discovered_users: List[Dict[str, Any]] = []

            for source_url in search_sources:
                if is_stopped() or len(discovered_users) >= max_likes * 4:
                    break
                db.log_activity(f"Scanning target profiles on: {source_url}", level="INFO")
                if safe_goto(page, source_url, timeout=25000, wait_until="commit"):
                    content = page.content()
                    found = scraper.parse_search_results(content)
                    db.log_activity(f"Discovered {len(found)} candidate cards on {source_url}", level="INFO")
                    discovered_users.extend(found)
                    human_delay(1.5, 2.5)

            # Deduplicate by user_id
            unique_candidates = {u["user_id"]: u for u in discovered_users}.values()
            db.log_activity(f"Total unique discovered candidates to inspect: {len(unique_candidates)}", level="INFO")

            for user_card in unique_candidates:
                if is_stopped() or liked_count >= max_likes:
                    break

                uid = user_card["user_id"]
                profile_url = user_card["profile_url"]
                
                # Skip if already liked
                if db.has_already_liked(uid):
                    continue

                inspected_count += 1
                db.log_activity(f"Inspecting Profile #{inspected_count}: {user_card.get('username')} (ID: {uid})...", level="INFO", user_id=uid)
                
                if not safe_goto(page, profile_url, timeout=25000, wait_until="commit"):
                    continue

                try:
                    profile_html = page.content()
                    full_profile = scraper.parse_profile_html(profile_html, profile_url)
                    
                    # Ensure user ID is matched accurately
                    if not full_profile["user_id"]:
                        full_profile["user_id"] = uid
                    
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
                        # Perform Like
                        success = _click_like_button(page)
                        if success:
                            db.record_like_sent(
                                user_id=uid,
                                username=full_profile["username"],
                                source="search_criteria",
                                matched_criteria=match_report
                            )
                            liked_count += 1
                            db.log_activity(f"Successfully liked {full_profile['username']} (ID: {uid}) [{liked_count}/{max_likes}]", level="SUCCESS", user_id=uid)
                        else:
                            db.log_activity(f"Failed to click like button for user {uid}", level="WARNING", user_id=uid)
                    else:
                        mismatch_reason = next(iter(match_report.values()), "Criteria mismatch")
                        db.log_activity(f"Skipping {full_profile['username']} (ID: {uid}) - Filter mismatch: {mismatch_reason}", level="DEBUG", user_id=uid)

                    human_delay()

                except Exception as e:
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
