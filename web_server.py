"""
Mingle2 Bot - Web Control Center & REST API Server
Asynchronous management dashboard powered by aiohttp.
"""
import os
import sys
import json
import asyncio
import threading
from typing import Dict, Any, Optional
from aiohttp import web

import database as db
import bot

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

async def manifest_handler(request):
    manifest_path = os.path.join(STATIC_DIR, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = f.read()
        return web.Response(text=data, content_type="application/manifest+json")
    return web.Response(text="{}", content_type="application/manifest+json")

async def sw_handler(request):
    sw_path = os.path.join(STATIC_DIR, "sw.js")
    if os.path.exists(sw_path):
        with open(sw_path, "r", encoding="utf-8") as f:
            data = f.read()
        return web.Response(text=data, content_type="application/javascript", headers={"Service-Worker-Allowed": "/"})
    return web.Response(text="", content_type="application/javascript")

async def api_session_export(request):
    if os.path.exists(bot.SESSION_FILE):
        with open(bot.SESSION_FILE, "r", encoding="utf-8") as f:
            data = f.read()
        return web.Response(
            text=data,
            content_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="session.json"'}
        )
    return web.json_response({"status": "error", "message": "No session.json file found"}, status=404)

async def api_session_import(request):
    try:
        data = await request.json()
        raw_session = data.get("session") or data
        ok = bot.save_raw_session(raw_session)
        if ok:
            return web.json_response({"status": "success", "message": "Session imported successfully! Synchronizing account..."})
        else:
            return web.json_response({"status": "error", "message": "Invalid session JSON structure."}, status=400)
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Import failed: {e}"}, status=400)

async def api_session_cloud_login(request):
    if get_current_task():
        return web.json_response({"status": "error", "message": f"Task '{get_current_task()}' already running."}, status=400)
    try:
        data = await request.json()
        email = data.get("username_or_email", "").strip() or data.get("email", "").strip() or data.get("username", "").strip()
        pwd = data.get("password", "").strip()
        if not email or not pwd:
            return web.json_response({"status": "error", "message": "Username/Email and Password are required."}, status=400)
        
        set_current_task("Signing In")
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, bot.perform_login, email, pwd)
        set_current_task(None)
        
        if res.get("success"):
            account = db.get_my_account()
            return web.json_response({
                "status": "success",
                "message": res.get("message", "Logged in successfully!"),
                "account": account
            })
        else:
            return web.json_response({
                "status": "error",
                "message": res.get("error", "Authentication failed. Please verify credentials.")
            }, status=400)
    except Exception as e:
        set_current_task(None)
        return web.json_response({"status": "error", "message": str(e)}, status=400)

CURRENT_TASK: Optional[str] = None
TASK_LOCK = threading.Lock()

def set_current_task(name: Optional[str]):
    global CURRENT_TASK
    with TASK_LOCK:
        CURRENT_TASK = name

def get_current_task() -> Optional[str]:
    with TASK_LOCK:
        return CURRENT_TASK

# Background Thread Runner for Bot Actions
def run_in_background(target_func, *args):
    def worker():
        try:
            target_func(*args)
        except Exception as e:
            db.log_activity(f"Background task execution error: {e}", level="ERROR")
        finally:
            set_current_task(None)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

# API Handlers
async def api_get_status(request):
    account = db.get_my_account()
    stats = db.get_stats()
    session_exists = os.path.exists(bot.SESSION_FILE)
    return web.json_response({
        "account": account,
        "stats": stats,
        "session_exists": session_exists,
        "current_task": get_current_task()
    })

async def api_get_config(request):
    cfg = db.get_config()
    return web.json_response(cfg)

async def api_update_config(request):
    try:
        data = await request.json()
        db.update_config(data)
        db.log_activity("Updated bot configuration settings.", level="CONFIG")
        return web.json_response({"status": "success", "config": db.get_config()})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)

async def api_get_logs(request):
    limit = int(request.query.get("limit", 50))
    conn = db.get_connection()
    rows = conn.execute("SELECT id, timestamp, level, message, user_id FROM activity_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    logs = [dict(r) for r in reversed(rows)]
    return web.json_response({"logs": logs})

async def api_get_profiles(request):
    tab = request.query.get("tab", "likes_sent")
    limit = int(request.query.get("limit", 50))
    conn = db.get_connection()
    
    if tab == "likes_sent":
        rows = conn.execute("""
            SELECT ls.id, ls.user_id, ls.username, ls.source, ls.matched_criteria, ls.sent_at,
                   tp.photo_url, tp.age, tp.location, tp.ethnicity, tp.body_type
            FROM likes_sent ls
            LEFT JOIN target_profiles tp ON ls.user_id = tp.user_id
            ORDER BY ls.id DESC LIMIT ?
        """, (limit,)).fetchall()
    elif tab == "likes_received":
        rows = conn.execute("SELECT * FROM likes_received ORDER BY received_at DESC LIMIT ?", (limit,)).fetchall()
    elif tab == "matches":
        rows = conn.execute("SELECT * FROM mutual_matches ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    else: # all discovered
        rows = conn.execute("SELECT * FROM target_profiles ORDER BY discovered_at DESC LIMIT ?", (limit,)).fetchall()
        
    conn.close()
    items = []
    for r in rows:
        d = dict(r)
        if "matched_criteria" in d and isinstance(d["matched_criteria"], str):
            try:
                d["matched_criteria"] = json.loads(d["matched_criteria"])
            except Exception:
                pass
        items.append(d)
    return web.json_response({"items": items})

async def api_trigger_login(request):
    if get_current_task():
        return web.json_response({"status": "error", "message": f"Task '{get_current_task()}' already running."}, status=400)
    
    # Check if login credentials were provided in POST body
    try:
        if request.can_read_body and request.content_length and request.content_length > 0:
            data = await request.json()
            if data and (data.get("username_or_email") or data.get("email") or data.get("username")):
                return await api_session_cloud_login(request)
    except Exception:
        pass

    # For headful browser launch: verify display is available
    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        return web.json_response({
            "status": "error",
            "message": "Desktop browser window cannot be opened on cloud containers or mobile phones. Please use the Sign-In modal to enter your username & password."
        }, status=400)

    set_current_task("Interactive Login")
    run_in_background(bot.interactive_login)
    return web.json_response({"status": "started", "task": "Interactive Login"})

async def api_trigger_sync(request):
    if get_current_task():
        return web.json_response({"status": "error", "message": f"Task '{get_current_task()}' already running."}, status=400)
    set_current_task("Sync Account")
    run_in_background(bot.sync_account)
    return web.json_response({"status": "started", "task": "Sync Account"})

async def api_trigger_like_back(request):
    if get_current_task():
        return web.json_response({"status": "error", "message": f"Task '{get_current_task()}' already running."}, status=400)
    cfg = db.get_config()
    max_likes = cfg.get("max_likes_per_session", 25)
    set_current_task("Like Back")
    run_in_background(bot.run_like_back, max_likes)
    return web.json_response({"status": "started", "task": "Like Back"})

async def api_trigger_search_like(request):
    if get_current_task():
        return web.json_response({"status": "error", "message": f"Task '{get_current_task()}' already running."}, status=400)
    cfg = db.get_config()
    max_likes = cfg.get("max_likes_per_session", 25)
    set_current_task("Search & Auto-Like")
    run_in_background(bot.search_and_like, max_likes)
    return web.json_response({"status": "started", "task": "Search & Auto-Like"})

async def api_trigger_stop(request):
    bot.request_stop()
    set_current_task(None)
    return web.json_response({"status": "stopped"})

# UI Handler
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>Mingle2 Pro Bot - Control Center</title>
  
  <!-- PWA & Mobile Web App Manifest -->
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#ec4899">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Mingle2 Bot">
  <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
  <link rel="icon" type="image/png" sizes="192x192" href="/static/icon-192.png">
  <link rel="icon" type="image/svg+xml" href="/static/icon.svg">

  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body { background-color: #0b0f19; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; -webkit-tap-highlight-color: transparent; }
    .glass-card { background: rgba(18, 24, 38, 0.85); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; }
    .btn-gradient-pink { background: linear-gradient(135deg, #ec4899 0%, #db2777 100%); }
    .btn-gradient-blue { background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); }
    .btn-gradient-emerald { background: linear-gradient(135deg, #10b981 0%, #047857 100%); }
    .glow-pink:hover { box-shadow: 0 0 20px rgba(236, 72, 153, 0.4); }
    .glow-blue:hover { box-shadow: 0 0 20px rgba(59, 130, 246, 0.4); }
    .terminal-scroll::-webkit-scrollbar { width: 6px; }
    .terminal-scroll::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }
  </style>
</head>
<body class="p-3 sm:p-5 md:p-8">
  <div class="max-w-7xl mx-auto space-y-5">

    <!-- PWA Mobile Install Banner -->
    <div id="pwa-install-banner" class="hidden glass-card p-3.5 border border-pink-500/50 bg-gradient-to-r from-pink-950/60 to-purple-950/60 flex items-center justify-between gap-3 shadow-xl rounded-xl">
      <div class="flex items-center gap-3">
        <img src="/static/icon-192.png" class="w-10 h-10 rounded-xl shadow border border-pink-500/30 shrink-0">
        <div>
          <div class="font-bold text-white text-xs sm:text-sm">Install Mingle2 Bot App</div>
          <p class="text-[11px] text-pink-200/80">Install on your Android / iPhone home screen for fast native app control.</p>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button id="pwa-install-btn" class="px-3.5 py-1.5 rounded-lg btn-gradient-pink text-white text-xs font-bold shadow-md hover:scale-105 transition flex items-center gap-1.5 shrink-0">
          <i class="fa-solid fa-download"></i> Install
        </button>
        <button onclick="dismissInstallBanner()" class="text-slate-400 hover:text-white p-1 text-xs">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </div>

    <!-- Top Navigation Header -->
    <header class="glass-card p-4 sm:p-5 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
      <div class="flex items-center space-x-3 sm:space-x-4">
        <img src="/static/icon-192.png" class="w-11 h-11 sm:w-12 sm:h-12 rounded-xl shadow border border-pink-500/30 shrink-0">
        <div>
          <h1 class="text-xl sm:text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            Mingle2 Bot Center
            <span class="text-[10px] sm:text-xs px-2 py-0.5 rounded-full bg-pink-500/20 text-pink-300 border border-pink-500/30">PWA Active</span>
          </h1>
          <p class="text-xs text-slate-400">Precision ID Sync, Filtered Targeting & Reciprocal Matching</p>
        </div>
      </div>

      <!-- Live Bot Status & Quick Actions -->
      <div class="flex flex-wrap items-center gap-2 sm:gap-3 w-full md:w-auto justify-end">
        <div id="bot-status-badge" class="px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2 bg-slate-800 text-slate-400 border border-slate-700">
          <span class="w-2 h-2 rounded-full bg-slate-500 animate-pulse"></span>
          <span>IDLE</span>
        </div>
        <button onclick="openLoginModal('direct')" class="px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 bg-blue-600/25 hover:bg-blue-600/40 text-blue-300 border border-blue-500/30 transition shadow">
          <i class="fa-solid fa-key"></i>
          <span>Sign In</span>
        </button>
        <button id="header-install-btn" onclick="handlePWAInstallClick()" class="px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1.5 btn-gradient-pink text-white shadow-md hover:scale-105 transition">
          <i class="fa-solid fa-mobile-screen"></i>
          <span>Install App</span>
        </button>
        <button onclick="stopBot()" class="px-3 py-1.5 rounded-lg bg-rose-600/20 text-rose-300 hover:bg-rose-600/40 border border-rose-500/30 text-xs font-semibold flex items-center gap-1.5 transition">
          <i class="fa-solid fa-hand"></i> Stop
        </button>
      </div>
    </header>

    <!-- Stats & Account Banner -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <!-- Account Card -->
      <div class="glass-card p-5 relative overflow-hidden">
        <div class="text-xs text-slate-400 font-medium uppercase tracking-wider mb-2 flex justify-between items-center">
          <span>Synced Account</span>
          <span id="session-badge" class="text-xs text-rose-400 font-semibold">No Session</span>
        </div>
        <div class="flex items-center gap-3">
          <img id="my-avatar" src="https://via.placeholder.com/48" class="w-12 h-12 rounded-full border border-slate-700 object-cover bg-slate-800">
          <div class="overflow-hidden">
            <div id="my-username" class="font-bold text-white truncate">Not Synced</div>
            <div id="my-userid" class="text-xs text-pink-400 font-mono truncate">ID: ---</div>
          </div>
        </div>
        <div class="mt-4 grid grid-cols-3 gap-1.5 text-center">
          <button onclick="openLoginModal('direct')" class="py-1.5 text-[11px] font-medium rounded-lg bg-blue-600/20 hover:bg-blue-600/30 text-blue-300 border border-blue-500/30 transition truncate" title="Direct Sign-In with Username & Password">
            <i class="fa-solid fa-key"></i> Sign In
          </button>
          <button onclick="syncAccount()" class="py-1.5 text-[11px] font-medium rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 transition truncate" title="Sync Account ID & Matches">
            <i class="fa-solid fa-arrows-rotate"></i> Sync ID
          </button>
          <button onclick="openLoginModal('import')" class="py-1.5 text-[11px] font-medium rounded-lg bg-purple-600/20 hover:bg-purple-600/30 text-purple-300 border border-purple-500/30 transition truncate" title="Session Import & Backup">
            <i class="fa-solid fa-cloud"></i> Backup
          </button>
        </div>
      </div>

      <!-- Stat 1: Who Liked Me (Incoming) -->
      <div class="glass-card p-5 relative border-pink-500/30 bg-gradient-to-br from-pink-950/20 to-slate-900/60">
        <div class="text-xs text-pink-300 font-medium uppercase tracking-wider mb-1 flex items-center justify-between">
          <span>Who Liked Me (Incoming)</span>
          <i class="fa-solid fa-heart-pulse text-pink-400 animate-pulse"></i>
        </div>
        <div class="flex items-baseline gap-2 mt-1">
          <div id="stat-likes-received" class="text-3xl font-extrabold text-pink-400">0</div>
          <span class="text-xs text-pink-300/70 font-medium">waiting fans</span>
        </div>
        <div class="mt-3">
          <button onclick="startLikeBack()" class="w-full py-1.5 text-xs font-bold rounded-lg btn-gradient-pink text-white shadow hover:opacity-90 transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-heart-circle-check"></i> Like Them Back
          </button>
        </div>
      </div>

      <!-- Stat 2: Likes Sent -->
      <div class="glass-card p-5">
        <div class="text-xs text-slate-400 font-medium uppercase tracking-wider mb-1 flex items-center justify-between">
          <span>Likes Sent</span>
          <i class="fa-solid fa-heart text-blue-400"></i>
        </div>
        <div id="stat-likes-sent" class="text-3xl font-extrabold text-blue-400 mt-1">0</div>
        <p class="text-xs text-slate-500 mt-2">Criteria matches & reciprocal likes</p>
      </div>

      <!-- Stat 3: Mutual Matches -->
      <div class="glass-card p-5">
        <div class="text-xs text-slate-400 font-medium uppercase tracking-wider mb-1 flex items-center justify-between">
          <span>Mutual Matches</span>
          <i class="fa-solid fa-fire text-amber-400"></i>
        </div>
        <div id="stat-mutual-liked" class="text-3xl font-extrabold text-amber-400 mt-1">0</div>
        <p class="text-xs text-slate-500 mt-2">Reciprocated matches confirmed</p>
      </div>
    </div>

    <!-- Live Incoming Likes Announcement Banner -->
    <div id="incoming-banner" class="glass-card p-4 border border-pink-500/40 bg-pink-950/20 flex flex-col sm:flex-row items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-full bg-pink-500/20 text-pink-400 flex items-center justify-center text-xl">
          <i class="fa-solid fa-sparkles text-pink-400"></i>
        </div>
        <div>
          <div class="font-bold text-white text-sm flex items-center gap-2">
            <span id="banner-count" class="px-2 py-0.5 rounded bg-pink-500/30 text-pink-300 font-mono">0</span> New Fans Have Liked Your Profile!
          </div>
          <p class="text-xs text-slate-400">Accurately synchronized with your Mingle2 account.</p>
        </div>
      </div>
      <button onclick="startLikeBack()" class="px-5 py-2 rounded-lg btn-gradient-pink glow-pink text-white text-xs font-bold shadow-lg transition flex items-center gap-2">
        <i class="fa-solid fa-heart-circle-bolt"></i> Auto Like-Back All
      </button>
    </div>

    <!-- Main Grid: Automation Actions & Search Criteria Filters -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

      <!-- Left 2 Cols: Search & Filter Settings + Automation Controls -->
      <div class="lg:col-span-2 space-y-6">

        <!-- Action Control Buttons Card -->
        <div class="glass-card p-6">
          <h2 class="text-lg font-bold text-white mb-4 flex items-center gap-2">
            <i class="fa-solid fa-bolt text-yellow-400"></i> 1-Click Bot Automation Engines
          </h2>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            
            <button onclick="startLikeBack()" class="btn-gradient-pink glow-pink p-4 rounded-xl text-left font-semibold text-white transition flex flex-col justify-between shadow-lg">
              <div class="flex items-center justify-between">
                <span class="text-base"><i class="fa-solid fa-heart-circle-check mr-2"></i> Auto Like-Back</span>
                <i class="fa-solid fa-arrow-right opacity-80"></i>
              </div>
              <p class="text-xs font-normal text-pink-100 mt-2 opacity-90">
                Scrapes "Who Is Interested In Me", extracts their exact User IDs, and likes them back instantly.
              </p>
            </button>

            <button onclick="startSearchLike()" class="btn-gradient-blue glow-blue p-4 rounded-xl text-left font-semibold text-white transition flex flex-col justify-between shadow-lg">
              <div class="flex items-center justify-between">
                <span class="text-base"><i class="fa-solid fa-crosshairs mr-2"></i> Search & Auto-Like</span>
                <i class="fa-solid fa-arrow-right opacity-80"></i>
              </div>
              <p class="text-xs font-normal text-blue-100 mt-2 opacity-90">
                Scans Mingle2 targets, screens age, skin colour/ethnicity, body size, and likes exact matches.
              </p>
            </button>

          </div>
        </div>

        <!-- Target Criteria Filter Form Card -->
        <div class="glass-card p-6">
          <div class="flex justify-between items-center mb-5">
            <h2 class="text-lg font-bold text-white flex items-center gap-2">
              <i class="fa-solid fa-sliders text-pink-400"></i> Target Criteria & Demographic Filters
            </h2>
            <button onclick="saveConfig()" class="px-4 py-2 rounded-lg bg-pink-600 hover:bg-pink-500 text-white text-xs font-semibold shadow transition">
              <i class="fa-solid fa-floppy-disk mr-1.5"></i> Save Criteria
            </button>
          </div>

          <form id="config-form" class="space-y-5" onsubmit="event.preventDefault(); saveConfig();">
            
            <!-- Age Range Filter -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label class="block text-xs font-medium text-slate-300 mb-1.5">Minimum Age</label>
                <div class="flex items-center gap-2">
                  <input type="number" id="min_age" min="18" max="99" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-pink-500">
                  <span class="text-xs text-slate-500">years</span>
                </div>
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-300 mb-1.5">Maximum Age</label>
                <div class="flex items-center gap-2">
                  <input type="number" id="max_age" min="18" max="99" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-pink-500">
                  <span class="text-xs text-slate-500">years</span>
                </div>
              </div>
            </div>

            <!-- Skin Colour / Ethnicity Filter -->
            <div>
              <label class="block text-xs font-medium text-slate-300 mb-1.5">
                Skin Colour / Ethnicity Preferences
              </label>
              <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs" id="ethnicities-container">
                <!-- Checkboxes populated via JS -->
              </div>
            </div>

            <!-- Size / Body Type Filter -->
            <div>
              <label class="block text-xs font-medium text-slate-300 mb-1.5">
                Size & Body Type Preferences
              </label>
              <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs" id="body-types-container">
                <!-- Checkboxes populated via JS -->
              </div>
            </div>

            <!-- Target Location Filter -->
            <div>
              <label class="block text-xs font-medium text-slate-300 mb-1.5">
                Target Locations (Cities, Regions, or Countries - Comma separated)
              </label>
              <input type="text" id="target_locations" placeholder="e.g. Kampala, Uganda, Central Region, London, New York" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-pink-500">
              <p class="text-xs text-slate-500 mt-1">Leaves empty or include multiple keywords to match profile location fields.</p>
            </div>

            <!-- Safety, Photo & Limit Controls -->
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-2 border-t border-slate-800">
              <div>
                <label class="block text-xs font-medium text-slate-300 mb-1.5">Max Likes Per Session</label>
                <input type="number" id="max_likes_per_session" min="1" max="200" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-pink-500">
              </div>
              <div>
                <label class="block text-xs font-medium text-slate-300 mb-1.5">Human Delay (Min / Max s)</label>
                <div class="flex gap-2">
                  <input type="number" id="min_delay_seconds" min="1" max="20" class="w-1/2 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-pink-500">
                  <input type="number" id="max_delay_seconds" min="2" max="30" class="w-1/2 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-pink-500">
                </div>
              </div>
              <div class="flex items-center pt-5">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" id="require_photo" class="w-4 h-4 rounded text-pink-600 bg-slate-900 border-slate-700 focus:ring-0">
                  <span class="text-xs text-slate-300 font-medium">Must have Profile Photo</span>
                </label>
              </div>
            </div>

          </form>
        </div>

      </div>

      <!-- Right 1 Col: Realtime Activity Terminal -->
      <div class="glass-card p-5 flex flex-col h-[650px]">
        <div class="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
            <h3 class="text-sm font-bold text-white">Live Activity Stream</h3>
          </div>
          <button onclick="fetchLogs()" class="text-xs text-slate-400 hover:text-white transition">
            <i class="fa-solid fa-rotate-right"></i>
          </button>
        </div>
        
        <div id="logs-container" class="flex-1 overflow-y-auto space-y-2 font-mono text-xs pr-1 terminal-scroll">
          <div class="text-slate-500">Connecting to bot logger...</div>
        </div>
      </div>

    </div>

    <!-- Data Tables: Sent Likes, Incoming Likes, and Discovered Targets -->
    <div class="glass-card p-6">
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-5 border-b border-slate-800 pb-4">
        <div>
          <h2 class="text-lg font-bold text-white">Profile Records & Exact ID Matches</h2>
          <p class="text-xs text-slate-400">Every liked user and discovery logged with exact Mingle2 Member IDs</p>
        </div>
        
        <!-- Table Tabs -->
        <div class="flex flex-wrap bg-slate-900 p-1 rounded-lg border border-slate-800 text-xs font-semibold gap-1">
          <button onclick="switchTab('likes_sent')" id="tab-btn-likes_sent" class="px-3 py-1.5 rounded-md bg-pink-600 text-white transition">
            Likes Sent (<span id="count-sent">0</span>)
          </button>
          <button onclick="switchTab('likes_received')" id="tab-btn-likes_received" class="px-3 py-1.5 rounded-md text-slate-400 hover:text-white transition">
            Who Liked Me (<span id="count-received">0</span>)
          </button>
          <button onclick="switchTab('matches')" id="tab-btn-matches" class="px-3 py-1.5 rounded-md text-amber-400 hover:text-amber-300 transition">
            🔥 Confirmed Matches (<span id="count-matches">0</span>)
          </button>
          <button onclick="switchTab('all_discovered')" id="tab-btn-all_discovered" class="px-3 py-1.5 rounded-md text-slate-400 hover:text-white transition">
            All Discovered (<span id="count-discovered">0</span>)
          </button>
        </div>
      </div>

      <!-- Profiles Table -->
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs text-slate-300">
          <thead class="bg-slate-900/50 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-800">
            <tr>
              <th class="py-3 px-4">User</th>
              <th class="py-3 px-4">Exact Mingle2 ID</th>
              <th class="py-3 px-4">Demographics</th>
              <th class="py-3 px-4">Source / Match Details</th>
              <th class="py-3 px-4">Timestamp</th>
              <th class="py-3 px-4 text-right">Action</th>
            </tr>
          </thead>
          <tbody id="profiles-table-body" class="divide-y divide-slate-800/60 font-mono">
            <tr>
              <td colspan="6" class="text-center py-6 text-slate-500">Loading records...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </div>

  <script>
    const ALL_ETHNICITIES = [
      "Black / African descent",
      "Caucasian / White",
      "Asian",
      "Hispanic / Latino",
      "Mixed",
      "Any"
    ];

    const ALL_BODY_TYPES = [
      "Slender",
      "Average",
      "Athletic",
      "Curvy",
      "Large",
      "Any"
    ];

    let currentTab = "likes_sent";

    // Initialize UI
    function renderCheckboxes() {
      const ethContainer = document.getElementById("ethnicities-container");
      ethContainer.innerHTML = ALL_ETHNICITIES.map(eth => `
        <label class="flex items-center gap-2 p-2 rounded bg-slate-900/60 border border-slate-800 cursor-pointer hover:border-slate-700">
          <input type="checkbox" name="target_ethnicities" value="${eth}" class="w-3.5 h-3.5 rounded text-pink-600 bg-slate-950 border-slate-700">
          <span class="truncate text-slate-300">${eth}</span>
        </label>
      `).join("");

      const btContainer = document.getElementById("body-types-container");
      btContainer.innerHTML = ALL_BODY_TYPES.map(bt => `
        <label class="flex items-center gap-2 p-2 rounded bg-slate-900/60 border border-slate-800 cursor-pointer hover:border-slate-700">
          <input type="checkbox" name="target_body_types" value="${bt}" class="w-3.5 h-3.5 rounded text-pink-600 bg-slate-950 border-slate-700">
          <span class="truncate text-slate-300">${bt}</span>
        </label>
      `).join("");
    }

    async function fetchStatus() {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        
        // Status badge
        const badge = document.getElementById("bot-status-badge");
        if (data.current_task) {
          badge.className = "px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2 bg-pink-900/30 text-pink-400 border border-pink-700/50";
          badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-pink-500 animate-ping"></span><span>RUNNING: ${data.current_task}</span>`;
        } else {
          badge.className = "px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2 bg-slate-800 text-slate-400 border border-slate-700";
          badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-slate-500"></span><span>IDLE</span>`;
        }

        // Account info
        if (data.account && data.account.user_id) {
          document.getElementById("my-username").innerText = data.account.username || "Connected";
          document.getElementById("my-userid").innerText = "ID: " + data.account.user_id;
          if (data.account.avatar_url) document.getElementById("my-avatar").src = data.account.avatar_url;
          document.getElementById("session-badge").className = "text-xs text-emerald-400 font-semibold";
          document.getElementById("session-badge").innerText = "● Authenticated";
        } else if (data.session_exists) {
          document.getElementById("session-badge").className = "text-xs text-blue-400 font-semibold";
          document.getElementById("session-badge").innerText = "Session Saved (Syncing...)";
        } else {
          document.getElementById("session-badge").className = "text-xs text-rose-400 font-semibold";
          document.getElementById("session-badge").innerText = "No Session";
        }

        // Stats
        const incomingCount = data.stats.total_likes_received || 0;
        document.getElementById("stat-likes-received").innerText = incomingCount;
        document.getElementById("banner-count").innerText = incomingCount;
        document.getElementById("stat-likes-sent").innerText = data.stats.total_likes_sent || 0;
        document.getElementById("stat-mutual-liked").innerText = data.stats.total_confirmed_matches || data.stats.total_mutual_liked || 0;
        document.getElementById("count-sent").innerText = data.stats.total_likes_sent || 0;
        document.getElementById("count-received").innerText = incomingCount;
        if (document.getElementById("count-matches")) {
          document.getElementById("count-matches").innerText = data.stats.total_confirmed_matches || 0;
        }
        document.getElementById("count-discovered").innerText = data.stats.total_discovered || 0;
      } catch (e) {
        console.error("Status error:", e);
      }
    }

    async function fetchConfig() {
      try {
        const res = await fetch("/api/config");
        const cfg = await res.json();
        
        document.getElementById("min_age").value = cfg.min_age || 20;
        document.getElementById("max_age").value = cfg.max_age || 38;
        document.getElementById("target_locations").value = cfg.target_locations || "";
        document.getElementById("max_likes_per_session").value = cfg.max_likes_per_session || 25;
        document.getElementById("min_delay_seconds").value = cfg.min_delay_seconds || 3;
        document.getElementById("max_delay_seconds").value = cfg.max_delay_seconds || 7;
        document.getElementById("require_photo").checked = !!cfg.require_photo;

        const targetEth = cfg.target_ethnicities || [];
        document.querySelectorAll("input[name='target_ethnicities']").forEach(cb => {
          cb.checked = targetEth.includes(cb.value);
        });

        const targetBt = cfg.target_body_types || [];
        document.querySelectorAll("input[name='target_body_types']").forEach(cb => {
          cb.checked = targetBt.includes(cb.value);
        });
      } catch (e) {
        console.error("Config fetch error:", e);
      }
    }

    async function saveConfig() {
      const targetEth = Array.from(document.querySelectorAll("input[name='target_ethnicities']:checked")).map(cb => cb.value);
      const targetBt = Array.from(document.querySelectorAll("input[name='target_body_types']:checked")).map(cb => cb.value);

      const payload = {
        min_age: parseInt(document.getElementById("min_age").value) || 18,
        max_age: parseInt(document.getElementById("max_age").value) || 99,
        target_ethnicities: targetEth,
        target_body_types: targetBt,
        target_locations: document.getElementById("target_locations").value,
        max_likes_per_session: parseInt(document.getElementById("max_likes_per_session").value) || 25,
        min_delay_seconds: parseInt(document.getElementById("min_delay_seconds").value) || 3,
        max_delay_seconds: parseInt(document.getElementById("max_delay_seconds").value) || 7,
        require_photo: document.getElementById("require_photo").checked
      };

      try {
        const res = await fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const resp = await res.json();
        alert("Criteria settings saved successfully!");
      } catch (e) {
        alert("Failed to save settings: " + e);
      }
    }

    async function fetchLogs() {
      try {
        const res = await fetch("/api/logs?limit=40");
        const data = await res.json();
        const container = document.getElementById("logs-container");
        if (!data.logs || data.logs.length === 0) {
          container.innerHTML = '<div class="text-slate-500">No logs recorded yet.</div>';
          return;
        }

        container.innerHTML = data.logs.map(log => {
          let color = "text-slate-400";
          if (log.level === "SUCCESS") color = "text-emerald-400 font-semibold";
          else if (log.level === "MATCH") color = "text-amber-400 font-semibold";
          else if (log.level === "LIKE_SENT") color = "text-pink-400 font-semibold";
          else if (log.level === "WARNING") color = "text-yellow-400";
          else if (log.level === "ERROR") color = "text-rose-400 font-semibold";

          return `
            <div class="border-b border-slate-900/60 pb-1">
              <span class="text-slate-600 text-[10px]">${log.timestamp.split(" ")[1] || log.timestamp}</span>
              <span class="text-[10px] px-1 py-0.5 rounded bg-slate-900 text-slate-300 ml-1 font-bold">[${log.level}]</span>
              ${log.user_id ? `<span class="text-pink-400 text-[10px]">#${log.user_id}</span>` : ""}
              <span class="${color} break-words">${log.message}</span>
            </div>
          `;
        }).join("");

        container.scrollTop = container.scrollHeight;
      } catch (e) {
        console.error("Logs error:", e);
      }
    }

    async function switchTab(tab) {
      currentTab = tab;
      ["likes_sent", "likes_received", "matches", "all_discovered"].forEach(t => {
        const btn = document.getElementById("tab-btn-" + t);
        if (btn) {
          if (t === tab) {
            btn.className = "px-3 py-1.5 rounded-md bg-pink-600 text-white transition";
          } else {
            btn.className = "px-3 py-1.5 rounded-md text-slate-400 hover:text-white transition";
          }
        }
      });
      fetchProfiles();
    }

    async function fetchProfiles() {
      try {
        const res = await fetch(`/api/profiles?tab=${currentTab}&limit=50`);
        const data = await res.json();
        const tbody = document.getElementById("profiles-table-body");
        
        if (!data.items || data.items.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" class="text-center py-6 text-slate-500">No records found.</td></tr>';
          return;
        }

        tbody.innerHTML = data.items.map(item => {
          const photo = item.photo_url || "https://via.placeholder.com/32";
          const username = item.username || `User_${item.user_id}`;
          const isFan = currentTab === "likes_received";
          const isMatch = currentTab === "matches";
          const profileLink = item.user_id && !isNaN(item.user_id) ? `https://mingle2.com/users/view/${item.user_id}` : `https://mingle2.com/mutual_match/your_matches`;
          
          let demo = "";
          if (item.gender) demo += `${item.gender} • `;
          if (item.age) demo += `${item.age} yrs • `;
          if (item.ethnicity) demo += `${item.ethnicity} • `;
          if (item.body_type) demo += `${item.body_type} • `;
          if (item.location) demo += `${item.location}`;
          if (!demo) demo = "Demographics saved in profile";

          let matchInfo = item.source || (isFan ? "Incoming Fan" : (isMatch ? "Confirmed Match" : "Discovered"));
          if (isMatch) {
            matchInfo = '<span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-semibold text-[10px]">🔥 Confirmed Match</span>';
          } else if (isFan) {
            matchInfo = item.liked_back ? 
              '<span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-semibold text-[10px]">✓ Matched / Liked Back</span>' : 
              '<span class="px-2 py-0.5 rounded bg-pink-500/20 text-pink-300 font-semibold text-[10px] animate-pulse">Pending Like Back</span>';
          } else if (item.matched_criteria) {
            matchInfo += " (" + Object.values(item.matched_criteria).join(", ") + ")";
          }

          let timeStr = item.matched_at || item.sent_at || item.received_at || item.discovered_at || "Recent";
          
          let actionBtn = "";
          if (isMatch) {
            actionBtn = `
              <div class="flex items-center justify-end gap-1.5">
                <a href="${profileLink}" target="_blank" class="px-2.5 py-1 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 text-xs font-semibold transition">
                  <i class="fa-solid fa-comments mr-1"></i> Chat
                </a>
                <a href="${profileLink}" target="_blank" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition">
                  <i class="fa-solid fa-arrow-up-right-from-square"></i>
                </a>
              </div>
            `;
          } else if (isFan && !item.liked_back) {
            actionBtn = `
              <button onclick="startLikeBack()" class="px-2.5 py-1 rounded btn-gradient-pink text-white text-xs font-semibold shadow hover:opacity-90 transition">
                <i class="fa-solid fa-heart mr-1"></i> Like Back
              </button>
            `;
          } else {
            actionBtn = `
              <a href="${profileLink}" target="_blank" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition">
                <i class="fa-solid fa-arrow-up-right-from-square"></i> View
              </a>
            `;
          }

          return `
            <tr class="hover:bg-slate-900/40 transition">
              <td class="py-3 px-4 flex items-center gap-3">
                <img src="${photo}" class="w-8 h-8 rounded-full object-cover bg-slate-800 border border-slate-700">
                <span class="font-medium text-white">${username}</span>
              </td>
              <td class="py-3 px-4 text-pink-400 font-bold">${item.user_id}</td>
              <td class="py-3 px-4 text-slate-300 text-xs">${demo}</td>
              <td class="py-3 px-4 text-slate-400 text-xs">${matchInfo}</td>
              <td class="py-3 px-4 text-slate-500 text-xs">${timeStr}</td>
              <td class="py-3 px-4 text-right">
                ${actionBtn}
              </td>
            </tr>
          `;
        }).join("");
      } catch (e) {
        console.error("Profiles error:", e);
      }
    }

    // Smooth Toast Notification System
    function showToast(message, type = "info") {
      const container = document.getElementById("toast-container");
      if (!container) return;
      const toast = document.createElement("div");
      toast.className = `px-4 py-2.5 rounded-xl shadow-2xl text-xs font-semibold flex items-center gap-2.5 pointer-events-auto transition-all duration-300 transform translate-y-2 opacity-0 max-w-sm border backdrop-blur-md`;
      
      let icon = "fa-circle-info";
      let colorClasses = "bg-slate-900/95 text-slate-200 border-slate-700 shadow-slate-950/50";
      if (type === "success") {
        icon = "fa-circle-check";
        colorClasses = "bg-emerald-950/95 text-emerald-200 border-emerald-500/50 shadow-emerald-950/50";
      } else if (type === "error") {
        icon = "fa-circle-exclamation";
        colorClasses = "bg-rose-950/95 text-rose-200 border-rose-500/50 shadow-rose-950/50";
      } else if (type === "warning") {
        icon = "fa-triangle-exclamation";
        colorClasses = "bg-amber-950/95 text-amber-200 border-amber-500/50 shadow-amber-950/50";
      }
      
      toast.className += ` ${colorClasses}`;
      toast.innerHTML = `<i class="fa-solid ${icon} shrink-0 text-sm"></i><span class="flex-1 leading-tight">${message}</span>`;
      container.appendChild(toast);
      
      setTimeout(() => {
        toast.classList.remove("translate-y-2", "opacity-0");
      }, 10);
      
      setTimeout(() => {
        toast.classList.add("opacity-0", "translate-x-4");
        setTimeout(() => toast.remove(), 350);
      }, 4000);
    }

    // Bot Action Triggers
    async function syncAccount() {
      showToast("Starting account & match synchronization...", "info");
      try {
        const res = await fetch("/api/action/sync", { method: "POST" });
        const data = await res.json();
        if (data.status === "started") showToast("Account ID synchronization started in background.", "success");
        else showToast(data.message || "Error syncing account", "error");
      } catch (e) {
        showToast("Network error: " + e.message, "error");
      }
    }

    async function startLikeBack() {
      showToast("Launching Like-Back routine...", "info");
      try {
        const res = await fetch("/api/action/like_back", { method: "POST" });
        const data = await res.json();
        if (data.status === "started") showToast("Like-Back Routine launched! Monitoring incoming likes.", "success");
        else showToast(data.message || "Error launching Like-Back", "error");
      } catch (e) {
        showToast("Network error: " + e.message, "error");
      }
    }

    async function startSearchLike() {
      showToast("Launching Search & Auto-Like routine...", "info");
      try {
        const res = await fetch("/api/action/search_like", { method: "POST" });
        const data = await res.json();
        if (data.status === "started") showToast("Search & Auto-Like Routine launched! Filtering by criteria.", "success");
        else showToast(data.message || "Error launching Search & Like", "error");
      } catch (e) {
        showToast("Network error: " + e.message, "error");
      }
    }

    async function stopBot() {
      await fetch("/api/action/stop", { method: "POST" });
      showToast("Stop signal sent to bot.", "warning");
    }

    // Unified Authentication Modal Controls
    function openLoginModal(tab = 'direct') {
      document.getElementById('login-modal').classList.remove('hidden');
      switchLoginTab(tab);
    }

    function closeLoginModal() {
      document.getElementById('login-modal').classList.add('hidden');
      const feedback = document.getElementById('login-feedback-box');
      if (feedback) feedback.classList.add('hidden');
    }

    function switchLoginTab(tab) {
      const panelDirect = document.getElementById('panel-direct');
      const panelImport = document.getElementById('panel-import');
      const panelBrowser = document.getElementById('panel-browser');
      const tabDirect = document.getElementById('tab-btn-direct');
      const tabImport = document.getElementById('tab-btn-import');
      const tabBrowser = document.getElementById('tab-btn-browser');

      panelDirect.classList.add('hidden');
      panelImport.classList.add('hidden');
      panelBrowser.classList.add('hidden');

      tabDirect.className = "py-2 px-3 text-xs font-semibold rounded-lg text-slate-400 hover:text-white transition flex items-center justify-center gap-1.5 flex-1";
      tabImport.className = "py-2 px-3 text-xs font-semibold rounded-lg text-slate-400 hover:text-white transition flex items-center justify-center gap-1.5 flex-1";
      tabBrowser.className = "py-2 px-3 text-xs font-semibold rounded-lg text-slate-400 hover:text-white transition flex items-center justify-center gap-1.5 flex-1";

      if (tab === 'direct') {
        panelDirect.classList.remove('hidden');
        tabDirect.className = "py-2 px-3 text-xs font-bold rounded-lg bg-blue-600/30 text-blue-200 border border-blue-500/40 shadow transition flex items-center justify-center gap-1.5 flex-1";
        setTimeout(() => document.getElementById('login-username')?.focus(), 50);
      } else if (tab === 'import') {
        panelImport.classList.remove('hidden');
        tabImport.className = "py-2 px-3 text-xs font-bold rounded-lg bg-purple-600/30 text-purple-200 border border-purple-500/40 shadow transition flex items-center justify-center gap-1.5 flex-1";
      } else if (tab === 'browser') {
        panelBrowser.classList.remove('hidden');
        tabBrowser.className = "py-2 px-3 text-xs font-bold rounded-lg bg-pink-600/30 text-pink-200 border border-pink-500/40 shadow transition flex items-center justify-center gap-1.5 flex-1";
      }
    }

    function togglePasswordVisibility() {
      const passInput = document.getElementById('login-password');
      const eyeIcon = document.getElementById('eye-icon');
      if (passInput.type === 'password') {
        passInput.type = 'text';
        eyeIcon.classList.replace('fa-eye', 'fa-eye-slash');
      } else {
        passInput.type = 'password';
        eyeIcon.classList.replace('fa-eye-slash', 'fa-eye');
      }
    }

    async function submitUnifiedLogin() {
      const email = document.getElementById('login-username').value.trim();
      const pwd = document.getElementById('login-password').value.trim();
      const btn = document.getElementById('login-submit-btn');
      const feedback = document.getElementById('login-feedback-box');

      if (!email || !pwd) {
        feedback.className = "p-3 rounded-lg bg-rose-950/80 border border-rose-500/50 text-rose-200 text-xs flex items-center gap-2";
        feedback.innerHTML = '<i class="fa-solid fa-circle-exclamation shrink-0"></i><span>Please enter both username/email and password.</span>';
        feedback.classList.remove('hidden');
        return;
      }

      btn.disabled = true;
      btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Authenticating with Mingle2...';
      feedback.className = "p-3 rounded-lg bg-blue-950/80 border border-blue-500/50 text-blue-200 text-xs flex items-center gap-2";
      feedback.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin shrink-0"></i><span>Verifying credentials with Mingle2 server...</span>';
      feedback.classList.remove('hidden');

      try {
        const res = await fetch('/api/session/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username_or_email: email, password: pwd })
        });
        const data = await res.json();

        if (res.ok && data.status === 'success') {
          feedback.className = "p-3 rounded-lg bg-emerald-950/80 border border-emerald-500/50 text-emerald-200 text-xs flex items-center gap-2";
          feedback.innerHTML = '<i class="fa-solid fa-circle-check shrink-0"></i><span>Logged in successfully! Synchronizing account...</span>';
          showToast("Mingle2 authentication successful!", "success");
          fetchStatus();
          setTimeout(() => {
            closeLoginModal();
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Sign In to Mingle2';
          }, 1200);
        } else {
          btn.disabled = false;
          btn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Sign In to Mingle2';
          feedback.className = "p-3 rounded-lg bg-rose-950/80 border border-rose-500/50 text-rose-200 text-xs flex items-center gap-2";
          feedback.innerHTML = `<i class="fa-solid fa-circle-xmark shrink-0"></i><span>${data.message || 'Login failed. Please check credentials or import session.'}</span>`;
          showToast(data.message || "Login failed", "error");
        }
      } catch (err) {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Sign In to Mingle2';
        feedback.className = "p-3 rounded-lg bg-rose-950/80 border border-rose-500/50 text-rose-200 text-xs flex items-center gap-2";
        feedback.innerHTML = `<i class="fa-solid fa-triangle-exclamation shrink-0"></i><span>Network error: ${err.message}</span>`;
        showToast("Network error during login", "error");
      }
    }

    function handleSessionFileUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById('modal-session-text').value = e.target.result;
      };
      reader.readAsText(file);
    }

    async function submitImportSessionModal() {
      const text = document.getElementById('modal-session-text').value.trim();
      if (!text) {
        showToast("Please paste session JSON content or select a file.", "warning");
        return;
      }
      try {
        const payload = JSON.parse(text);
        const res = await fetch('/api/session/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status === 'success') {
          showToast("Session imported successfully! Synchronizing account.", "success");
          closeLoginModal();
          fetchStatus();
        } else {
          showToast("Import error: " + (data.message || "Invalid session data"), "error");
        }
      } catch (e) {
        showToast("JSON parsing error: " + e.message, "error");
      }
    }

    async function launchDesktopBrowserModal() {
      showToast("Requesting desktop browser launch...", "info");
      try {
        const res = await fetch('/api/action/login', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'started') {
          showToast("Desktop browser opened. Log in to Mingle2 in the window.", "info");
          closeLoginModal();
        } else {
          showToast(data.message || "Cannot launch desktop browser.", "error");
        }
      } catch (e) {
        showToast("Error: " + e.message, "error");
      }
    }

    // PWA Mobile Installation Handler & Guide Modal
    let deferredPrompt = null;
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      deferredPrompt = e;
      const banner = document.getElementById('pwa-install-banner');
      if (banner && !localStorage.getItem('pwa_dismissed')) {
        banner.classList.remove('hidden');
      }
    });

    window.addEventListener('appinstalled', () => {
      deferredPrompt = null;
      document.getElementById('pwa-install-banner')?.classList.add('hidden');
      showToast("Mingle2 Bot successfully installed on your device!", "success");
    });

    function handlePWAInstallClick() {
      if (deferredPrompt) {
        deferredPrompt.prompt();
        deferredPrompt.userChoice.then((choice) => {
          if (choice.outcome === 'accepted') {
            showToast("Installing Mingle2 Bot...", "success");
          }
          deferredPrompt = null;
          document.getElementById('pwa-install-banner')?.classList.add('hidden');
        });
      } else {
        openPWAGuideModal();
      }
    }

    function openPWAGuideModal() {
      document.getElementById('pwa-guide-modal').classList.remove('hidden');
    }

    function closePWAGuideModal() {
      document.getElementById('pwa-guide-modal').classList.add('hidden');
    }

    function dismissInstallBanner() {
      document.getElementById('pwa-install-banner').classList.add('hidden');
      localStorage.setItem('pwa_dismissed', 'true');
    }

    // PWA Service Worker Registration
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js', { scope: '/' })
          .then(reg => console.log('ServiceWorker registered:', reg.scope))
          .catch(err => console.log('ServiceWorker registration error:', err));
      });
    }

    const installBtn = document.getElementById('pwa-install-btn');
    if (installBtn) {
      installBtn.addEventListener('click', handlePWAInstallClick);
    }

    // Polling Intervals
    renderCheckboxes();
    fetchConfig();
    fetchStatus();
    fetchLogs();
    fetchProfiles();

    setInterval(fetchStatus, 3000);
    setInterval(fetchLogs, 2500);
    setInterval(fetchProfiles, 10000);
  </script>

  <!-- Unified Sign-In & Authentication Modal -->
  <div id="login-modal" class="hidden fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-3 sm:p-4">
    <div class="glass-card max-w-lg w-full p-5 sm:p-6 border border-blue-500/40 bg-slate-900/98 space-y-4 shadow-2xl relative">
      <div class="flex items-center justify-between pb-3 border-b border-slate-800">
        <div class="flex items-center gap-2.5">
          <div class="w-8 h-8 rounded-lg bg-blue-500/20 text-blue-400 flex items-center justify-center">
            <i class="fa-solid fa-shield-halved"></i>
          </div>
          <div>
            <h3 class="text-base font-bold text-white">Mingle2 Authentication</h3>
            <p class="text-[11px] text-slate-400">Authenticate once to enable automated likes & matches.</p>
          </div>
        </div>
        <button onclick="closeLoginModal()" class="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <!-- Navigation Tabs -->
      <div class="flex rounded-lg bg-slate-950 p-1 border border-slate-800 gap-1">
        <button id="tab-btn-direct" onclick="switchLoginTab('direct')" class="py-2 px-3 text-xs font-bold rounded-lg bg-blue-600/30 text-blue-200 border border-blue-500/40 shadow transition flex items-center justify-center gap-1.5 flex-1">
          <i class="fa-solid fa-key"></i> Direct Sign-In
        </button>
        <button id="tab-btn-import" onclick="switchLoginTab('import')" class="py-2 px-3 text-xs font-semibold rounded-lg text-slate-400 hover:text-white transition flex items-center justify-center gap-1.5 flex-1">
          <i class="fa-solid fa-cloud-arrow-up"></i> Session Import
        </button>
        <button id="tab-btn-browser" onclick="switchLoginTab('browser')" class="py-2 px-3 text-xs font-semibold rounded-lg text-slate-400 hover:text-white transition flex items-center justify-center gap-1.5 flex-1">
          <i class="fa-solid fa-desktop"></i> PC Desktop
        </button>
      </div>

      <!-- Tab 1: Direct Sign-In Panel -->
      <div id="panel-direct" class="space-y-3.5">
        <div id="login-feedback-box" class="hidden"></div>

        <div class="space-y-1">
          <label class="text-xs text-slate-300 font-semibold flex items-center gap-1.5">
            <i class="fa-solid fa-user text-slate-400"></i> Email or Username
          </label>
          <input type="text" id="login-username" placeholder="your_email@example.com or Username" class="w-full text-xs p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-white focus:outline-none focus:border-blue-500">
        </div>

        <div class="space-y-1">
          <label class="text-xs text-slate-300 font-semibold flex items-center justify-between">
            <span class="flex items-center gap-1.5"><i class="fa-solid fa-lock text-slate-400"></i> Password</span>
            <button type="button" onclick="togglePasswordVisibility()" class="text-[11px] text-slate-400 hover:text-blue-400 transition flex items-center gap-1">
              <i id="eye-icon" class="fa-solid fa-eye"></i> Show
            </button>
          </label>
          <input type="password" id="login-password" placeholder="Your Mingle2 Password" class="w-full text-xs p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-white focus:outline-none focus:border-blue-500">
        </div>

        <button id="login-submit-btn" onclick="submitUnifiedLogin()" class="w-full py-2.5 rounded-lg btn-gradient-blue text-white text-xs font-bold shadow-lg hover:opacity-95 transition flex items-center justify-center gap-2">
          <i class="fa-solid fa-right-to-bracket"></i> Sign In to Mingle2
        </button>
        <p class="text-[10px] text-slate-500 text-center">Credentials are used locally to create an authenticated session and are never shared.</p>
      </div>

      <!-- Tab 2: Session Import & Export Panel -->
      <div id="panel-import" class="hidden space-y-3.5">
        <div class="space-y-2">
          <div class="text-xs font-bold text-white flex items-center justify-between">
            <span>Paste session.json / Cookies</span>
            <label class="text-[11px] text-pink-400 hover:underline cursor-pointer flex items-center gap-1">
              <i class="fa-solid fa-file-arrow-up"></i> Choose File 
              <input type="file" id="modal-session-file" accept=".json" class="hidden" onchange="handleSessionFileUpload(event)">
            </label>
          </div>
          <textarea id="modal-session-text" rows="3" placeholder='Paste your session.json content or cookies here...' class="w-full text-xs font-mono p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-slate-300 focus:outline-none focus:border-purple-500"></textarea>
          <button onclick="submitImportSessionModal()" class="w-full py-2 rounded-lg btn-gradient-pink text-white text-xs font-bold shadow transition flex items-center justify-center gap-1.5">
            <i class="fa-solid fa-cloud-arrow-up"></i> Import & Synchronize
          </button>
        </div>

        <div class="pt-3 border-t border-slate-800 flex items-center justify-between gap-3">
          <div>
            <div class="text-xs font-bold text-white">Export Session Backup</div>
            <p class="text-[11px] text-slate-400">Download current session for offline backup.</p>
          </div>
          <a href="/api/session/export" target="_blank" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-white text-xs font-semibold shrink-0 transition flex items-center gap-1.5 border border-slate-700">
            <i class="fa-solid fa-download"></i> Download
          </a>
        </div>
      </div>

      <!-- Tab 3: Desktop Browser Panel -->
      <div id="panel-browser" class="hidden space-y-3">
        <p class="text-xs text-slate-300">
          Launches a full visible Chrome browser window directly on this PC so you can log into Mingle2 manually and solve any challenges.
        </p>
        <div class="p-3 rounded-lg bg-slate-950/70 border border-slate-800 text-[11px] text-slate-400 space-y-1">
          <div class="font-bold text-slate-200 flex items-center gap-1.5"><i class="fa-solid fa-circle-info text-blue-400"></i> Desktop Mode Only</div>
          <p>This opens a GUI window on the PC screen. If you are accessing from your mobile phone or cloud containers without a monitor, please use the <b>Direct Sign-In</b> tab.</p>
        </div>
        <button onclick="launchDesktopBrowserModal()" class="w-full py-2.5 rounded-lg bg-pink-600/30 hover:bg-pink-600/50 text-pink-200 border border-pink-500/40 text-xs font-bold transition flex items-center justify-center gap-2">
          <i class="fa-solid fa-arrow-up-right-from-square"></i> Launch Desktop Browser Window
        </button>
      </div>
    </div>
  </div>

  <!-- PWA Install Guide Modal -->
  <div id="pwa-guide-modal" class="hidden fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-3 sm:p-4">
    <div class="glass-card max-w-md w-full p-5 sm:p-6 border border-pink-500/40 bg-slate-900/98 space-y-4 shadow-2xl relative">
      <div class="flex items-center justify-between pb-3 border-b border-slate-800">
        <div class="flex items-center gap-2.5">
          <img src="/static/icon-192.png" class="w-9 h-9 rounded-xl shadow border border-pink-500/30">
          <div>
            <h3 class="text-base font-bold text-white">Install Mingle2 Bot App</h3>
            <p class="text-[11px] text-pink-300/80">Standalone independent application</p>
          </div>
        </div>
        <button onclick="closePWAGuideModal()" class="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition">
          <i class="fa-solid fa-xmark text-lg"></i>
        </button>
      </div>

      <div class="space-y-3 text-xs">
        <!-- Android Guide -->
        <div class="p-3 rounded-xl bg-slate-950/80 border border-slate-800 space-y-1.5">
          <div class="font-bold text-emerald-400 flex items-center gap-2">
            <i class="fa-brands fa-android text-sm"></i> Android Phone (Google Chrome)
          </div>
          <ol class="list-decimal list-inside text-slate-300 space-y-1 pl-1 text-[11px]">
            <li>Tap the <b>three vertical dots</b> (<i class="fa-solid fa-ellipsis-vertical"></i>) in Chrome's top right corner.</li>
            <li>Tap <b>"Install app"</b> or <b>"Add to Home screen"</b>.</li>
            <li>Tap <b>Install</b>. The bot appears in your app drawer like any native Android app!</li>
          </ol>
        </div>

        <!-- iOS Guide -->
        <div class="p-3 rounded-xl bg-slate-950/80 border border-slate-800 space-y-1.5">
          <div class="font-bold text-blue-400 flex items-center gap-2">
            <i class="fa-brands fa-apple text-sm"></i> iPhone / iPad (Safari)
          </div>
          <ol class="list-decimal list-inside text-slate-300 space-y-1 pl-1 text-[11px]">
            <li>Tap the <b>Share button</b> (<i class="fa-solid fa-arrow-up-from-bracket"></i>) at the bottom bar.</li>
            <li>Scroll down and tap <b>"Add to Home Screen"</b>.</li>
            <li>Tap <b>Add</b> to place the standalone app on your home screen.</li>
          </ol>
        </div>

        <!-- Desktop Guide -->
        <div class="p-3 rounded-xl bg-slate-950/80 border border-slate-800 space-y-1.5">
          <div class="font-bold text-purple-400 flex items-center gap-2">
            <i class="fa-solid fa-laptop text-sm"></i> Desktop (Chrome / Edge)
          </div>
          <p class="text-slate-300 text-[11px]">
            Look for the <b>Install App</b> icon (<i class="fa-solid fa-circle-down"></i>) in the right side of your browser URL address bar and click <b>Install</b>.
          </p>
        </div>
      </div>

      <button onclick="closePWAGuideModal()" class="w-full py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-white text-xs font-semibold transition">
        Got It!
      </button>
    </div>
  </div>

  <!-- Toast Notification Floating Container -->
  <div id="toast-container" class="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none"></div>
</body>
</html>
"""

async def index(request):
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/manifest.json", manifest_handler)
    app.router.add_get("/sw.js", sw_handler)
    if os.path.exists(STATIC_DIR):
        app.router.add_static("/static/", path=STATIC_DIR, name="static")

    app.router.add_get("/api/status", api_get_status)
    app.router.add_get("/api/config", api_get_config)
    app.router.add_post("/api/config", api_update_config)
    app.router.add_get("/api/logs", api_get_logs)
    app.router.add_get("/api/profiles", api_get_profiles)
    
    app.router.add_get("/api/session/export", api_session_export)
    app.router.add_post("/api/session/import", api_session_import)
    app.router.add_post("/api/session/login", api_session_cloud_login)

    app.router.add_post("/api/action/login", api_trigger_login)
    app.router.add_post("/api/action/sync", api_trigger_sync)
    app.router.add_post("/api/action/like_back", api_trigger_like_back)
    app.router.add_post("/api/action/search_like", api_trigger_search_like)
    app.router.add_post("/api/action/stop", api_trigger_stop)
    return app

def start_server(host: str = "0.0.0.0", port: int = 5000):
    db.init_db()
    final_host = os.environ.get("HOST", host)
    final_port = int(os.environ.get("PORT", port))
    db.log_activity(f"Starting Mingle2 Control Dashboard at http://{final_host}:{final_port}", level="INFO")
    app = create_app()
    web.run_app(app, host=final_host, port=final_port)

if __name__ == "__main__":
    start_server()
