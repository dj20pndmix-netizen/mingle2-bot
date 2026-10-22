"""
Mingle2 Bot - Main Entry Point
Supports running the Interactive Web Dashboard or standalone CLI commands.

Usage:
  python main.py                    # Launches Web Dashboard on http://127.0.0.1:5000
  python main.py --dashboard        # Launches Web Dashboard
  python main.py --login            # Launches visible browser to log in and save session
  python main.py --sync             # Verifies session and syncs your account User ID
  python main.py --like-back        # Runs Like-Back routine for incoming likes
  python main.py --search-like      # Runs Search & Auto-Like matching target criteria
  python main.py --port 8080        # Custom port for web dashboard
"""
import argparse
import sys
import os
import socket

# Ensure standard output safely handles all terminal encodings on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import database as db
import bot
import web_server

def get_lan_ip() -> str:
    """Discovers the computer's local Wi-Fi / Ethernet LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

BANNER = r"""
  __  __ _             _       ___   ____        _   
 |  \/  (_)_ __   __ _| | ___ |__ \ | __ )  ___ | |_ 
 | |\/| | | '_ \ / _` | |/ _ \  / / |  _ \ / _ \| __|
 | |  | | | | | | (_| | |  __/ / /_ | |_) | (_) | |_ 
 |_|  |_|_|_| |_|\__, |_|\___||____||____/ \___/ \__|
                 |___/                               
======================================================
  Mingle2 Pro Automation & ID Synchronization Bot
======================================================
"""

def main():
    parser = argparse.ArgumentParser(description="Mingle2 Pro Automation Bot")
    parser.add_argument("--dashboard", action="store_true", help="Launch Web Control Dashboard (Default)")
    parser.add_argument("--login", action="store_true", help="Launch browser for interactive login")
    parser.add_argument("--sync", action="store_true", help="Sync your account User ID and status")
    parser.add_argument("--like-back", action="store_true", help="Run Like-Back routine on incoming likes")
    parser.add_argument("--search-like", action="store_true", help="Run Search & Auto-Like with criteria filters")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host for dashboard server (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Port for dashboard server (default: 5000)")
    parser.add_argument("--public", "--tunnel", action="store_true", help="Generate instant public HTTPS tunnel for phone access")
    parser.add_argument("--max-likes", type=int, default=None, help="Max likes limit override for CLI runs")

    args = parser.parse_args()

    print(BANNER)
    db.init_db()

    if args.login:
        print("[*] Launching interactive login...")
        bot.interactive_login()
    elif args.sync:
        print("[*] Running account synchronization...")
        success = bot.sync_account()
        if success:
            acc = db.get_my_account()
            print(f"[+] Account synced successfully! Username: {acc.get('username')}, User ID: {acc.get('user_id')}")
        else:
            print("[-] Could not sync account. Check session.json or run `python main.py --login`.")
    elif args.like_back:
        cfg = db.get_config()
        limit = args.max_likes or cfg.get("max_likes_per_session", 25)
        print(f"[*] Starting Like-Back Routine (Limit: {limit})...")
        count = bot.run_like_back(max_likes=limit)
        print(f"[+] Like-Back finished. Total profiles liked: {count}")
    elif args.search_like:
        cfg = db.get_config()
        limit = args.max_likes or cfg.get("max_likes_per_session", 25)
        print(f"[*] Starting Search & Auto-Like Routine (Limit: {limit})...")
        count = bot.search_and_like(max_likes=limit)
        print(f"[+] Search & Like finished. Total profiles liked: {count}")
    else:
        # Default: Launch web dashboard (auto-detects Render PORT and HOST)
        env_port = os.environ.get("PORT")
        server_port = int(env_port) if env_port else args.port
        server_host = os.environ.get("HOST", args.host)
        lan_ip = get_lan_ip()

        tunnel_url = None
        # Start instant public HTTPS tunnel if requested or on mobile
        if args.public or os.environ.get("ENABLE_TUNNEL") == "1":
            try:
                from pycloudflared import try_cloudflare
                print("[*] Creating instant secure Cloudflare HTTPS tunnel for phone access...")
                t = try_cloudflare(server_port)
                tunnel_url = t.tunnel
            except Exception as e:
                print(f"[-] Could not establish Cloudflare tunnel: {e}")

        print("======================================================")
        print("  Mingle2 Bot Server is LIVE & READY!")
        print("======================================================")
        print(f"  * Desktop Local URL:        http://localhost:{server_port}")
        print(f"  * Android / iPhone (Wi-Fi): http://{lan_ip}:{server_port}")
        if tunnel_url:
            print(f"  * Public HTTPS URL:         {tunnel_url}")
        print("======================================================\n")

        web_server.start_server(host=server_host, port=server_port)

if __name__ == "__main__":
    main()
