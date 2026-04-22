"""Custom Lidl Plus auth and ticket helpers without Selenium/WebDriver."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import subprocess
from zoneinfo import ZoneInfo
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import requests

AUTH_API = "https://accounts.lidl.com"
TICKET_API = "https://tickets.lidlplus.com/api"
CLIENT_ID = "LidlPlusNativeClient"
CLIENT_SECRET = "secret"
REDIRECT_URI = "com.lidlplus.app://callback"
SCOPE = "openid profile offline_access lpprofile lpapis"
OS_HEADER = "Android"
APP_VERSION = "16.43.4"
USER_AGENT = "okhttp/5.3.2"
DEVICE_ID = "f7a31c44276c0651"
MODEL = "sdk_gphone64_x86_64"
BRAND = "Google"
TIMEOUT = 30
TOKEN_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class TokenState:
    access_token: str
    refresh_token: str
    expires_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.strftime(TOKEN_TIME_FORMAT),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TokenState":
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=datetime.strptime(payload["expires_at"], TOKEN_TIME_FORMAT),
        )


def _base64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _token_headers() -> dict[str, str]:
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _token_request(payload: dict[str, str]) -> TokenState:
    response = requests.post(
        f"{AUTH_API}/connect/token",
        data=payload,
        headers=_token_headers(),
        timeout=TIMEOUT,
    )
    try:
        body = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError("Token endpoint returned non-JSON response") from None

    if response.status_code != 200:
        reason = body.get("error_description") or body.get("error") or response.text
        raise RuntimeError(f"Token request failed: {reason}")

    if "access_token" not in body or "refresh_token" not in body or "expires_in" not in body:
        raise RuntimeError(f"Unexpected token response: {body}")

    return TokenState(
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        expires_at=datetime.now() + timedelta(seconds=int(body["expires_in"])),
    )


def build_login_url(language: str, country: str) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = _base64url_sha256(verifier)
    country_upper = country.upper()
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "Country": country_upper,
        "language": f"{language.lower()}-{country_upper}",
    }
    return f"{AUTH_API}/connect/authorize?{urlencode(params)}", verifier


def open_login_page(url: str) -> bool:
    browser_launchers = [
        ["/opt/microsoft/msedge/msedge", "--ozone-platform=x11", "--disable-vulkan", url],
        ["/usr/bin/firefox", url],
    ]
    for command in browser_launchers:
        try:
            subprocess.Popen(
                command,
                start_new_session=True,
            )
            return True
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return webbrowser.open(url, new=2)


def extract_auth_code(callback_url: str) -> str:
    raw = callback_url.strip()
    if not raw:
        raise ValueError("No callback input provided")

    parsed = urlparse(raw)
    if parsed.scheme:
        query = parse_qs(parsed.query)
        if "error" in query:
            raise RuntimeError(query.get("error_description", query["error"])[0])
        code = query.get("code", [""])[0]
        if code:
            return code

    match = re.search(r"(?:[?&#\s]|^)code=([^&#\s]+)", raw)
    if match:
        return unquote(match.group(1))

    if re.fullmatch(r"[A-Za-z0-9._~-]{16,}", raw):
        return raw

    raise ValueError("No authorization code found in input")


def exchange_code_for_tokens(code: str, code_verifier: str) -> TokenState:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    return _token_request(payload)

def refresh_access_token(refresh_token: str) -> TokenState:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    return _token_request(payload)


def load_token_file(path: str = "token.json") -> TokenState:
    with open(path, "r", encoding="utf-8") as file:
        return TokenState.from_dict(json.load(file))


def save_token_file(token: TokenState, path: str = "token.json") -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(token.to_dict(), file, ensure_ascii=False, indent=4)


def update_env_refresh_token(refresh_token: str, env_path: str = ".env") -> None:
    path = Path(env_path)
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated = False
    for index, line in enumerate(lines):
        if line.startswith("REFRESH_TOKEN="):
            lines[index] = f"REFRESH_TOKEN={refresh_token}"
            updated = True
            break

    if not updated:
        lines.append(f"REFRESH_TOKEN={refresh_token}")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _api_headers(access_token: str, language: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "App-Version": APP_VERSION,
        "Operating-System": OS_HEADER,
        "App": "com.lidl.eci.lidl.plus",
        "Accept-Language": language.lower(),
        "User-Agent": USER_AGENT,
        "OS-Version": "16",
        "Model": MODEL,
        "Brand": BRAND,
        "deviceid": DEVICE_ID,
        "Date": datetime.now(ZoneInfo("Europe/Budapest")).strftime("%a, %d %b %Y %H:%M:%S GMT"),
    }


class LidlTicketsClient:
    def __init__(self, country: str, language: str, access_token: str):
        self.country = country.upper()
        self.language = language.lower()
        self.access_token = access_token

    def _get(self, url: str, **params: Any) -> requests.Response:
        response = requests.get(
            url,
            params=params or None,
            headers=_api_headers(self.access_token, self.language),
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response

    def tickets(self, only_favorite: bool = False) -> list[dict[str, Any]]:
        url = f"{TICKET_API}/v2/{self.country}/tickets"
        first_page = self._get(url, pageNumber=1, onlyFavorite=str(only_favorite).lower()).json()
        tickets = list(first_page.get("tickets", []))
        total_count = int(first_page.get("totalCount", len(tickets)))
        page_size = int(first_page.get("size", len(tickets) or 1))
        total_pages = (total_count + page_size - 1) // page_size

        for page in range(2, total_pages + 1):
            payload = self._get(url, pageNumber=page, onlyFavorite=str(only_favorite).lower()).json()
            tickets.extend(payload.get("tickets", []))
        return tickets

    def ticket(self, ticket_id: str) -> dict[str, Any]:
        v2_url = f"{TICKET_API}/v2/{self.country}/tickets/{ticket_id}"
        response = requests.get(
            v2_url,
            headers=_api_headers(self.access_token, self.language),
            timeout=TIMEOUT,
        )
        if response.ok:
            try:
                return response.json()
            except ValueError:
                pass

        v3_url = f"{TICKET_API}/v3/{self.country}/tickets/{ticket_id}"
        fallback = requests.get(
            v3_url,
            headers=_api_headers(self.access_token, self.language),
            timeout=TIMEOUT,
        )
        fallback.raise_for_status()
        return fallback.json()
