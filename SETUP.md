# Lidl Plus Setup Guide

## Scripts

This project has been split into two scripts:

### 1. `auth.py` - Authentication Script
Handles login and token management with OAuth/PKCE in your normal browser. Run this script when you need to:
- Authenticate for the first time
- Refresh your authentication token
- Update the refresh token in `.env`

**Usage:**
```bash
python auth.py
```

**Requirements:**
- MS Edge or Firefox installed
- MS Edge: `/opt/microsoft/msedge/msedge` (preferred)
- Firefox: `/usr/bin/firefox` (fallback)

**What it does:**
1. Tries to refresh the existing token from `.env`
2. If refresh fails, opens your browser for manual login
3. You paste callback URL/code back into terminal
4. Saves tokens to `token.json` and updates `.env`

### 2. `main.py` - Ticket Download Script
Downloads and saves Lidl Plus tickets using stored token. Run this script regularly to fetch new tickets.

**Usage:**
```bash
python main.py
```

**Requirements:**
- `token.json` must exist (run `auth.py` first)

**What it does:**
1. Loads token from `token.json`
2. Fetches list of new tickets
3. Downloads full ticket details
4. Parses receipt data and extracts articles
5. Saves tickets to `tickets.json`

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create `.env` file with:
```
REFRESH_TOKEN=your_refresh_token
PASSWORD=your_password
PHONE=+36308851344
```

3. Run authentication:
```bash
python auth.py
```

4. Start downloading tickets:
```bash
python main.py
```

## Troubleshooting

### Browser does not open automatically
- Copy/paste the login URL printed by `auth.py` and open it manually

### Browser asks to run `xdg-open`, then nothing happens
- This is expected when Linux has no handler for `com.lidlplus.app://`
- Copy the callback URL (or just the `code=...` value) from the browser/dialog
- Paste it into `auth.py` prompt (`Paste callback URL or code:`)

### Token expired
- Run `python auth.py` to refresh the token
- The script first attempts refresh, then falls back to interactive browser login

### Import errors
If you get import errors when running the scripts, ensure all dependencies are installed:
```bash
pip install -r requirements.txt
```
