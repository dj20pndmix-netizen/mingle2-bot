"""
Mingle2 Bot - Scraper & Parser Engine
High-precision parsing for Mingle2 profiles, search results, and mutual match listings.
"""
import re
import json
from typing import Dict, Any, List, Tuple, Optional
from bs4 import BeautifulSoup

def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def parse_profile_html(html: str, profile_url: str = "") -> Dict[str, Any]:
    """
    Parses a complete Mingle2 profile page and extracts all structured demographic
    and physical attributes, user IDs, and media.
    """
    soup = BeautifulSoup(html, "html.parser")
    data: Dict[str, Any] = {
        "user_id": "",
        "username": "",
        "headline": "",
        "age": 0,
        "gender": "",
        "location": "",
        "city": "",
        "region": "",
        "height": "",
        "body_type": "",
        "ethnicity": "",
        "marital_status": "",
        "children": "",
        "smokes": "",
        "drinks": "",
        "religion": "",
        "photo_url": "",
        "online_status": "",
        "profile_url": profile_url,
        "raw_attributes": {}
    }

    # 1. User ID Extraction (Priority: explicit attributes, links, scripts)
    # Check data-reported-user-id
    reported_div = soup.find(attrs={"data-reported-user-id": True})
    if reported_div and reported_div.get("data-reported-user-id"):
        data["user_id"] = clean_text(reported_div.get("data-reported-user-id"))

    # Check blockeduser link
    if not data["user_id"]:
        block_link = soup.find("a", href=re.compile(r'/blockeduser/add/(\d+)'))
        if block_link and block_link.get("href"):
            m = re.search(r'/blockeduser/add/(\d+)', block_link["href"])
            if m:
                data["user_id"] = m.group(1)

    # Check profile URL if provided
    if not data["user_id"] and profile_url:
        m = re.search(r'/(?:users|user)/view/(\d+)', profile_url)
        if m:
            data["user_id"] = m.group(1)

    # Check hrefs in page
    if not data["user_id"]:
        user_link = soup.find("a", href=re.compile(r'/(?:users|user)/view/(\d+)'))
        if user_link and user_link.get("href"):
            m = re.search(r'/(?:users|user)/view/(\d+)', user_link["href"])
            if m:
                data["user_id"] = m.group(1)

    # Check full_image_filenames or images with ID
    if not data["user_id"]:
        for img in soup.find_all("img"):
            src = img.get("src", "")
            m = re.search(r'/images/users/\d+/\d+/(\d+)_\d+', src)
            if m:
                data["user_id"] = m.group(1)
                break

    # 2. Username & Headline
    username_el = soup.find(class_="username") or soup.find(attrs={"itemprop": "name"})
    if username_el:
        data["username"] = clean_text(username_el.get_text())
    
    headline_el = soup.find(class_="headline")
    if headline_el:
        data["headline"] = clean_text(headline_el.get_text()).strip('"').strip("'")

    # Fallback username from title
    if not data["username"]:
        title_el = soup.find("title")
        if title_el:
            t = title_el.get_text()
            if ":" in t:
                data["username"] = clean_text(t.split(":")[0])
            elif "|" in t:
                data["username"] = clean_text(t.split("|")[0])

    # 3. Age, Gender & Location from .home-country / .user_summary
    home_country_el = soup.find(class_="home-country") or soup.find(class_="user_summary")
    if home_country_el:
        full_summary_text = home_country_el.get_text()
        
        # Regex for age: e.g. "31 year old" or "25 years old"
        age_m = re.search(r'(\d+)\s+year', full_summary_text, re.IGNORECASE)
        if age_m:
            data["age"] = int(age_m.group(1))

        # Gender: Woman / Man / Female / Male
        gender_m = re.search(r'year\s+old\s+([A-Za-z]+)', full_summary_text, re.IGNORECASE)
        if gender_m:
            data["gender"] = clean_text(gender_m.group(1))

        # Locality & Region
        loc_el = home_country_el.find(attrs={"itemprop": "addressLocality"})
        if loc_el:
            data["city"] = clean_text(loc_el.get_text())

        reg_el = home_country_el.find(attrs={"itemprop": "addressRegion"})
        if reg_el:
            data["region"] = clean_text(reg_el.get_text())

        data["location"] = f"{data['city']}{', ' if data['city'] and data['region'] else ''}{data['region']}"

        # Online status
        online_el = home_country_el.find(class_="last_online") or home_country_el.find(id="online_now")
        if online_el:
            data["online_status"] = clean_text(online_el.get_text())

    # Fallback age search
    if data["age"] == 0:
        age_m = re.search(r'\b(\d{2})\s+years?\s+old\b', soup.get_text(), re.IGNORECASE)
        if age_m:
            data["age"] = int(age_m.group(1))

    # 4. Photo URL
    portrait_img = soup.find("img", id="user_portrait") or soup.find("img", class_="portrait")
    if portrait_img and portrait_img.get("src"):
        data["photo_url"] = portrait_img["src"]
    else:
        # Check script for full_image_filenames
        for script in soup.find_all("script"):
            stext = script.string or ""
            if "full_image_filenames" in stext:
                m = re.search(r'full_image_filenames\s*=\s*\["(.*?)"\]', stext)
                if m:
                    data["photo_url"] = m.group(1)
                    break

    # 5. Physical Appearance and Lifestyle Attributes
    # Mingle2 uses <table class="attributes"> with <div class="label"> and <div class="value">
    for table in soup.find_all("table", class_="attributes"):
        for td in table.find_all("td"):
            label_el = td.find("div", class_="label")
            value_el = td.find("div", class_="value")
            if label_el and value_el:
                key = clean_text(label_el.get_text()).lower()
                val = clean_text(value_el.get_text())
                data["raw_attributes"][key] = val

                if "height" in key:
                    data["height"] = val
                elif "body type" in key:
                    data["body_type"] = val
                elif "ethnicity" in key:
                    data["ethnicity"] = val
                elif "marital status" in key:
                    data["marital_status"] = val
                elif "children" in key:
                    data["children"] = val
                elif "smokes" in key:
                    data["smokes"] = val
                elif "drinks" in key:
                    data["drinks"] = val
                elif "religion" in key:
                    data["religion"] = val

    if not data["profile_url"] and data["user_id"]:
        data["profile_url"] = f"https://mingle2.com/users/view/{data['user_id']}"

    return data


def parse_mutual_match_page(html: str) -> Dict[str, Any]:
    """
    Parses 'who is interested in me' or 'mutual_match' page to find:
    1. Total badge count of users who liked me (e.g. '99+', '24', etc.)
    2. All fan item cards and user profiles who liked our account.
    """
    soup = BeautifulSoup(html, "html.parser")
    users: List[Dict[str, Any]] = []
    seen_ids = set()
    badge_count = ""

    # 1. Extract Badge Count specifically from Mutual Match dropdown or 'Who clicked YES on you'
    for ann in soup.find_all(class_="annotation"):
        parent_text = (ann.parent.get_text() or "").lower() if ann.parent else ""
        if "match" in parent_text or "like" in parent_text:
            badge_count = ann.get_text().strip()
            break

    # Fallback to Match99+ in nav or dropbtn
    if not badge_count:
        m = re.search(r'Match\s*(\d+\+?)', html, re.IGNORECASE)
        if m:
            badge_count = m.group(1).strip()

    # Fallback check from button or text: "Who clicked YES on you? (99+ new)"
    if not badge_count:
        m = re.search(r'Who clicked YES on you\?\s*\((.*?)\)', html, re.IGNORECASE)
        if m:
            raw_badge = m.group(1).replace("new", "").strip()
            if raw_badge:
                badge_count = raw_badge

    # 2. Parse Fan Items (.user-gallery-item.fan-item)
    fan_items = soup.find_all(class_=lambda c: c and "fan-item" in c)
    for item in fan_items:
        rating_id = item.get("data-rating-id", "").strip()
        rated_at = item.get("data-rated-at", "").strip()
        button_id = item.get("data-button-id", "").strip()
        uid = rating_id or button_id

        if not uid or uid in seen_ids:
            continue
        seen_ids.add(uid)

        # Name
        name_el = item.find(class_="display-name")
        username = clean_text(name_el.get_text()) if name_el else f"Fan_{uid}"

        # Age & Gender
        gender = ""
        age = 0
        gender_age_el = item.find(class_="gender-and-age")
        if gender_age_el:
            ga_text = gender_age_el.get_text()
            age_m = re.search(r'Age:\s*(\d+)', ga_text, re.I)
            if age_m:
                age = int(age_m.group(1))
            gender_m = re.search(r'([A-Za-z]+),', ga_text)
            if gender_m:
                gender = gender_m.group(1).strip()

        # Location
        loc_el = item.find(class_="location")
        location = clean_text(loc_el.get_text()) if loc_el else ""

        # Date received
        date_el = item.find(class_="date")
        date_str = clean_text(date_el.get_text()) if date_el else ""

        # Photo
        img_el = item.find("img")
        photo_url = img_el.get("src", "") if img_el else ""

        users.append({
            "user_id": uid,
            "rating_id": rating_id,
            "username": username,
            "age": age,
            "gender": gender,
            "location": location,
            "received_at": date_str or rated_at,
            "photo_url": photo_url,
            "is_fan_item": True
        })

    # 3. Parse direct user links if any (<a href="/users/view/<id>">)
    links = soup.find_all("a", href=re.compile(r'/(?:users|user)/view/(\d+)'))
    for link in links:
        href = link.get("href", "")
        m = re.search(r'/(?:users|user)/view/(\d+)', href)
        if not m:
            continue
        uid = m.group(1)
        if uid in seen_ids:
            continue
        seen_ids.add(uid)

        parent = link.find_parent("div") or link
        username = clean_text(link.get_text())
        img = parent.find("img")
        photo_url = img.get("src", "") if img else ""

        users.append({
            "user_id": uid,
            "rating_id": "",
            "username": username or f"User_{uid}",
            "profile_url": f"https://mingle2.com/users/view/{uid}",
            "photo_url": photo_url,
            "is_fan_item": False
        })

    return {
        "users": users,
        "badge_count": badge_count,
        "total_parsed": len(users)
    }


def parse_search_results(html: str) -> List[Dict[str, Any]]:
    """
    Extracts all profile preview cards from search results, browse, or online users pages.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    for a in soup.find_all("a", href=re.compile(r'/(?:users|user)/view/(\d+)')):
        href = a.get("href", "")
        m = re.search(r'/(?:users|user)/view/(\d+)', href)
        if not m:
            continue
        uid = m.group(1)
        if uid in seen_ids:
            continue
        seen_ids.add(uid)

        card = a.find_parent("div", class_=re.compile(r'profile|user|card|item|member', re.IGNORECASE)) or a
        img = card.find("img")
        photo_url = img.get("src", "") if img else ""
        text = clean_text(card.get_text())

        results.append({
            "user_id": uid,
            "username": clean_text(a.get_text()) or f"User_{uid}",
            "profile_url": f"https://mingle2.com/users/view/{uid}",
            "photo_url": photo_url,
            "snippet": text
        })

    return results


def matches_criteria(profile: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, Dict[str, str]]:
    """
    Checks if a profile strictly matches the user's defined target criteria:
    - Age (min_age to max_age)
    - Location (keywords match city, region, or general location)
    - Skin colour / Ethnicity (e.g. Black / African descent, Caucasian, Asian, etc.)
    - Size / Body Type (e.g. Slender, Average, Athletic, Curvy, Large, etc.)
    - Require Photo
    """
    reasons: Dict[str, str] = {}

    # 1. Age Filter
    age = profile.get("age", 0)
    min_age = config.get("min_age", 18)
    max_age = config.get("max_age", 99)
    if age > 0:
        if age < min_age or age > max_age:
            return False, {"age": f"Age {age} not in range [{min_age}-{max_age}]"}
    else:
        # Age unknown - allow or pass depending on strictness
        pass
    reasons["age"] = f"{age} (Allowed: {min_age}-{max_age})"

    # 2. Photo Filter
    if config.get("require_photo", True):
        photo = profile.get("photo_url", "")
        if not photo or "no_photo" in photo or "default" in photo:
            return False, {"photo": "Profile has no photo or default placeholder"}
        reasons["photo"] = "Has valid photo"

    # 3. Location Filter
    target_locations = config.get("target_locations", "")
    if target_locations and target_locations.strip():
        loc_keywords = [k.strip().lower() for k in target_locations.split(",") if k.strip()]
        user_loc = f"{profile.get('city', '')} {profile.get('region', '')} {profile.get('location', '')}".lower()
        if loc_keywords and not any(k in user_loc for k in loc_keywords):
            return False, {"location": f"'{profile.get('location', 'Unknown')}' does not match targets: {target_locations}"}
        reasons["location"] = f"Matched location: {profile.get('location', '')}"

    # 4. Skin colour / Ethnicity Filter
    target_ethnicities = config.get("target_ethnicities", [])
    if isinstance(target_ethnicities, str):
        target_ethnicities = [target_ethnicities]
    
    # If list has selections and doesn't contain "Any"
    if target_ethnicities and not any("any" in str(e).lower() for e in target_ethnicities):
        profile_eth = str(profile.get("ethnicity", "")).lower()
        if not profile_eth:
            # If profile ethnicity isn't specified, check if bio / text has clues or reject
            return False, {"ethnicity": "Ethnicity field is empty/unspecified"}
        
        matched_eth = False
        for target in target_ethnicities:
            t = str(target).lower()
            # Match partial keywords: e.g. "black" matches "Black / African descent", "caucasian" matches "Caucasian / White"
            first_kw = t.split("/")[0].strip()
            if first_kw in profile_eth or t in profile_eth:
                matched_eth = True
                break
        if not matched_eth:
            return False, {"ethnicity": f"'{profile.get('ethnicity')}' does not match target {target_ethnicities}"}
        reasons["ethnicity"] = f"Matched ethnicity: {profile.get('ethnicity')}"
    else:
        reasons["ethnicity"] = f"Any ({profile.get('ethnicity', 'Unspecified')})"

    # 5. Size / Body Type Filter
    target_body_types = config.get("target_body_types", [])
    if isinstance(target_body_types, str):
        target_body_types = [target_body_types]

    if target_body_types and not any("any" in str(b).lower() for b in target_body_types):
        profile_bt = str(profile.get("body_type", "")).lower()
        if not profile_bt:
            return False, {"body_type": "Body type field is empty/unspecified"}
        
        matched_bt = False
        for target in target_body_types:
            t = str(target).lower()
            if t in profile_bt:
                matched_bt = True
                break
        if not matched_bt:
            return False, {"body_type": f"'{profile.get('body_type')}' does not match target {target_body_types}"}
        reasons["body_type"] = f"Matched body type: {profile.get('body_type')}"
    else:
        reasons["body_type"] = f"Any ({profile.get('body_type', 'Unspecified')})"

    return True, reasons


def parse_your_matches_page(html: str) -> List[Dict[str, Any]]:
    """
    Parses confirmed mutual matches from /mutual_match/your_matches.
    Extracts confirmed matched users: user_id, username, photo_url, age, gender, location, matched_at.
    """
    soup = BeautifulSoup(html, "html.parser")
    matches: List[Dict[str, Any]] = []
    seen_ids = set()

    container = soup.find(id="list-your-matches") or soup.find(class_="gallery-users")
    items = container.find_all(class_="user-gallery-item") if container else soup.find_all(class_="user-gallery-item")
    
    for it in items:
        link_el = it.find("a", href=re.compile(r'/users/view/(\d+)'))
        if not link_el:
            continue
        m = re.search(r'/users/view/(\d+)', link_el.get("href", ""))
        if not m:
            continue
        uid = m.group(1)
        if uid in seen_ids:
            continue
        seen_ids.add(uid)

        name_el = it.find(class_="display-name")
        username = clean_text(name_el.get_text()) if name_el else f"User_{uid}"

        gender = ""
        age = 0
        ga_el = it.find(class_="gender-and-age")
        if ga_el:
            ga_text = ga_el.get_text()
            age_m = re.search(r'Age:\s*(\d+)', ga_text, re.I)
            if age_m:
                age = int(age_m.group(1))
            gender_m = re.search(r'([A-Za-z]+),', ga_text)
            if gender_m:
                gender = gender_m.group(1).strip()

        loc_el = it.find(class_="location")
        location = clean_text(loc_el.get_text()) if loc_el else ""

        date_el = it.find(class_="date")
        date_str = clean_text(date_el.get_text()) if date_el else ""

        img = it.find("img")
        photo_url = img.get("src", "") if img else ""

        matches.append({
            "user_id": uid,
            "username": username,
            "photo_url": photo_url,
            "age": age,
            "gender": gender,
            "location": location,
            "matched_at": date_str,
            "profile_url": f"https://mingle2.com/users/view/{uid}"
        })

    return matches
