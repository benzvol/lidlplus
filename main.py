"""Lidl Plus main script - downloads tickets using token.json."""

from datetime import datetime, timedelta
import json
import os
import sys

import requests
from dotenv import dotenv_values

import receipt_parser
from lidl_custom import LidlTicketsClient, load_token_file, refresh_access_token, save_token_file, update_env_refresh_token


def _clean_env_value(value: str | None, default: str = "") -> str:
    if value is None:
        return default
    cleaned = value.strip().strip('"').strip("'")
    return cleaned or default


def _load_existing_tickets() -> tuple[list[dict], set[str]]:
    if not os.path.exists("tickets.json"):
        return [], set()
    with open("tickets.json", "r", encoding="utf-8") as file:
        existing = json.load(file)
    return existing, {ticket["id"] for ticket in existing}


def _refresh_if_needed(token_state):
    if datetime.now() + timedelta(minutes=1) < token_state.expires_at:
        return token_state
    refreshed = refresh_access_token(token_state.refresh_token)
    save_token_file(refreshed, path="token.json")
    update_env_refresh_token(refreshed.refresh_token, env_path=".env")
    print("✓ Access token refreshed")
    return refreshed


def main() -> None:
    if not os.path.exists("token.json"):
        print("✗ token.json not found. Please run auth.py first.")
        sys.exit(1)

    config = dotenv_values(".env")
    language = _clean_env_value(config.get("LANGUAGE"), default="hu")
    country = _clean_env_value(config.get("COUNTRY"), default="HU")

    token_state = load_token_file("token.json")
    try:
        token_state = _refresh_if_needed(token_state)
    except Exception as exc:
        print(f"✗ Token refresh failed: {exc}")
        print("Run auth.py to complete interactive login again.")
        sys.exit(1)
    client = LidlTicketsClient(country=country, language=language, access_token=token_state.access_token)

    existing_tickets, existing_ticket_ids = _load_existing_tickets()

    try:
        tickets_res = client.tickets()
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 401:
            raise
        try:
            token_state = refresh_access_token(token_state.refresh_token)
            save_token_file(token_state, path="token.json")
            update_env_refresh_token(token_state.refresh_token, env_path=".env")
            client = LidlTicketsClient(country=country, language=language, access_token=token_state.access_token)
            tickets_res = client.tickets()
        except Exception as refresh_exc:
            print(f"✗ Token refresh failed after 401: {refresh_exc}")
            print("Run auth.py to complete interactive login again.")
            sys.exit(1)

    tickets_res = [ticket for ticket in tickets_res if ticket["id"] not in existing_ticket_ids]

    print(f"Downloading {len(tickets_res)} tickets...")
    tickets: list[dict] = []
    failed: list[str] = []

    total = len(tickets_res)
    for index, ticket in enumerate(tickets_res, start=1):
        try:
            ext_ticket = client.ticket(ticket["id"])
        except Exception as exc:
            print(f"Failed to download ticket {ticket['id']}: {exc}")
            failed.append(ticket["id"])
            continue

        html_receipt = ext_ticket.get("htmlPrintedReceipt")
        if html_receipt:
            ext_ticket["articles"] = receipt_parser.parse_receipt(html_receipt)

        tickets.append(ext_ticket)
        print(f"Done: {index}/{total} ({index / total * 100:.2f}%)")

    tickets.extend(existing_tickets)
    with open("tickets.json", "w", encoding="utf-8") as file:
        json.dump(tickets, file, ensure_ascii=False)

    if failed:
        print(f"\n✗ Failed to download {len(failed)} tickets: {failed}")
    else:
        print("\n✓ All tickets downloaded successfully")


if __name__ == "__main__":
    main()
