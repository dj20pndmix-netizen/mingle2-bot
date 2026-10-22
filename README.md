# 🤖 Mingle2 Pro Automation & ID Synchronization Bot

A bot designed for **Mingle2** (mingle2.com) that automates reciprocal liking ("Like-Back"), multi-criteria profile searching (Age, Location, Skin Colour / Ethnicity, Size / Body Type, Photo requirement), and account synchronization with exact User ID matching.

---

## 🌟 Key Features

1. **Exact User ID Matching & Syncing**:
   - Accurately tracks your own Mingle2 User ID and matches every discovered profile by their unique Mingle2 Member ID.
   - Saves all profiles, incoming likes, and sent likes in a local SQLite database (`data/mingle2_bot.db`).
   - Prevents duplicate likes or repeated interactions.

2. **Automated "Like Back" (Mutual Match Sync)**:
   - Scrapes the *"Who Is Interested In Me"* / Mutual Match section.
   - Extracts candidate user IDs and executes reciprocal likes with randomized human delays.
   - Tracks matched status so you always know who liked you and who you liked back.

3. **Multi-Criteria Targeted Search & Auto-Like**:
   - **Age Range**: Set min/max age (e.g. 21 - 35).
   - **Skin Colour / Ethnicity**: Targets specific ethnicities (e.g., *Black / African descent*, *Caucasian / White*, *Asian*, *Hispanic / Latino*, *Mixed*, or *Any*).
   - **Size / Body Type**: Filters for specific body shapes (e.g., *Slender*, *Average*, *Athletic*, *Curvy*, *Large*, or *Any*).
   - **Location**: Matches target cities, regions, or countries (e.g., *Kampala, Central Region, London, New York*).
   - **Profile Photo Requirement**: Enforces verified photos (ignoring placeholder/blank profiles).

4. **Anti-Detection & Account Safety**:
   - Powered by Playwright with stealth flags (`--disable-blink-features=AutomationControlled`, realistic user agents, human-like randomized sleep intervals).
   - Session persistence (`data/session.json`): Log in once (with 2FA/CAPTCHA support) and the bot runs unattended.
   - Emergency Stop trigger.

5. **Modern Web Control Dashboard**:
   - Real-time dark-themed web control center at `http://127.0.0.1:5000`.
   - Real-time activity log stream.
   - 1-Click execution buttons: *Log In*, *Sync ID*, *Auto Like-Back*, *Search & Auto-Like*, *Stop*.
   - Filter configuration form with instant save.
   - Interactive data tables for Sent Likes, Incoming Likes, and Discovered Targets.

---

## 🚀 Quick Start Guide

### 1. Launch the Web Dashboard (Recommended)

Run the following command in terminal:
```bash
python main.py
```
Open your browser and navigate to:
👉 **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

---

### 2. First-Time Authentication & Sync

1. On the dashboard, click **`🔑 Log In`** (or run `python main.py --login`).
2. A browser window will open to Mingle2's login page.
3. Enter your Mingle2 credentials, solve any CAPTCHA if prompted, and log into your account.
4. The bot automatically detects your login, saves session cookies to `data/session.json`, and syncs your **exact Account User ID**.
5. You can now close the browser—all subsequent operations can run in background!

---

### 3. Setting Your Search & Like Criteria

On the dashboard:
- Adjust **Min Age** and **Max Age** (e.g., 22 to 36).
- Check your preferred **Skin Colour / Ethnicity** (e.g. `Black / African descent`, `Caucasian / White`, etc.).
- Check your preferred **Size / Body Type** (e.g. `Athletic`, `Average`, `Curvy`, etc.).
- Enter your **Target Locations** (e.g., `Kampala, Uganda`).
- Toggle **Must have Profile Photo**.
- Click **Save Criteria**.

---

### 4. Running the Bot

You can trigger tasks directly from the dashboard with 1 click, or via CLI:

#### Auto Like-Back (Reciprocate Likes)
* **Dashboard**: Click **`Auto Like-Back`**
* **CLI**:
  ```bash
  python main.py --like-back
  ```

#### Targeted Search & Auto-Like
* **Dashboard**: Click **`Search & Auto-Like`**
* **CLI**:
  ```bash
  python main.py --search-like
  ```

#### Sync Account
* **Dashboard**: Click **`Sync ID`**
* **CLI**:
  ```bash
  python main.py --sync
  ```

---

## ☁️ Deploying on Render.com (24/7 Cloud Bot)

You can run this bot 24/7 in the cloud on [Render.com](https://render.com) for free!

### Step 1: Push Code to Your GitHub
1. Create a repository on GitHub (private or public).
2. Push your bot files:
   ```bash
   git add .
   git commit -m "Deploy Mingle2 Bot to Render"
   git remote add origin https://github.com/<YOUR_USERNAME>/<YOUR_REPO_NAME>.git
   git branch -M main
   git push -u origin main
   ```

### Step 2: Deploy on Render
1. Go to [dashboard.render.com](https://dashboard.render.com) and click **New +** -> **Web Service**.
2. Connect your GitHub repository.
3. Choose **Docker** as the Runtime (Render will automatically detect our pre-configured `Dockerfile` with Playwright Chromium).
4. Select the **Free** instance type.
5. In **Environment Variables**, add:
   * `PORT`: `10000` (Render's internal port)
   * `HOST`: `0.0.0.0`
   * *(Optional)* `MINGLE2_SESSION_JSON`: Paste the contents of your local `data/session.json` so your cloud bot starts pre-authenticated!
6. Click **Deploy Web Service**!
7. Once deployed, Render will provide a live HTTPS URL:
   👉 `https://your-bot-name.onrender.com`

---

## 📱 Installing on Android, iPhone & Desktop (PWA)

The bot is built as a Progressive Web App (PWA) so you can install it on your mobile home screen with native app performance.

### On Android Phone (Chrome, Brave, Samsung Internet):
1. Open your live bot URL (`https://your-bot-name.onrender.com`) on your phone.
2. Tap the in-app **"📲 Install App"** banner at the top, OR tap the Chrome menu (`⋮`) and select **"Install App"** (or **"Add to Home screen"**).
3. The Mingle2 Bot icon will appear on your home screen and in your app drawer!
4. Launches full-screen with no browser address bar!

### On iPhone / iPad (Safari):
1. Open your live bot URL in Safari.
2. Tap the **Share** button (`⎙` / square with up arrow) at the bottom.
3. Scroll down and tap **"Add to Home Screen"**.
4. Tap **Add**. The bot will open as a native iOS app!

---

## 🔑 Cloud Session Manager

When running on Render in the cloud, you can manage your Mingle2 account session directly from your phone or browser:
1. Tap **`Cloud Session`** in the header or on the account card.
2. **Export / Backup**: Download your current session JSON with 1 click.
3. **Import / Sync**: Upload or paste `session.json` directly from your device.
4. **Direct Headless Login**: Enter your Mingle2 username/email and password to authenticate headlessly on Render!

---

## 📁 Project Structure

```
c:\mingle 2bot\
├── main.py            # Main entry point (CLI & Dashboard launcher, cloud port detection)
├── web_server.py      # Web Control Center (aiohttp web app, PWA service worker, REST API)
├── bot.py             # Playwright browser automation, headless login & liking engine
├── scraper.py         # High-precision HTML parser for profiles, incoming fans & confirmed matches
├── database.py        # SQLite database layer for accounts, IDs, mutual matches, and filters
├── Dockerfile         # Official Microsoft Playwright Chromium container for Render
├── render.yaml        # Render.com Blueprint configuration for 1-click deployment
├── requirements.txt   # Python production dependencies
├── static/            # PWA assets (manifest.json, sw.js, and high-res mobile icons)
│   ├── manifest.json  # Web app manifest for Android/iOS
│   ├── sw.js          # Service worker for offline shell caching
│   ├── icon-192.png   # 192x192 Android home screen icon
│   ├── icon-512.png   # 512x512 High-res app icon
│   └── icon-maskable.png # Adaptive Android maskable icon
├── data/
│   ├── mingle2_bot.db # SQLite database (WAL mode)
│   └── session.json   # Saved authentication cookies & session state
└── README.md          # Complete documentation & deployment manual
```

---

## 🔒 Safety & Rate Limiting

- Default session limit: 25-50 likes per run.
- Default randomized delay: 3 to 7 seconds between actions.
- All settings can be adjusted in the dashboard or via `bot_config` in SQLite.
