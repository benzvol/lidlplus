"""Lidl Plus auth script using external browser OAuth (no Selenium/WebDriver)."""

import sys

from dotenv import dotenv_values

from lidl_custom import (
    build_login_url,
    exchange_code_for_tokens,
    extract_auth_code,
    open_login_page,
    refresh_access_token,
    save_token_file,
    update_env_refresh_token,
)


def _clean_env_value(value: str | None, default: str = "") -> str:
    if value is None:
        return default
    cleaned = value.strip().strip('"').strip("'")
    return cleaned or default


def _interactive_login(language: str, country: str):
    login_url, code_verifier = build_login_url(language=language, country=country)
    opened = open_login_page(login_url)
    if opened:
        print("Opened browser for Lidl login.")
    else:
        print("Could not auto-open browser. Open this URL manually:")
    print(login_url)
    print()
    print("After login + 2FA, copy one of these and paste it here:")
    print("1) Full callback URL: com.lidlplus.app://callback?code=...")
    print("2) Just the `code` value")
    print("If browser prints a `gio: com.lidlplus.app://...` line, you can paste that full line too.")
    try:
        callback_url = input("Paste callback URL or code: ").strip()
    except EOFError as exc:
        raise RuntimeError("No callback URL provided on stdin") from exc
    code = extract_auth_code(callback_url)
    return exchange_code_for_tokens(code, code_verifier)


def main() -> None:
    config = dotenv_values(".env")
    language = _clean_env_value(config.get("LANGUAGE"), default="hu")
    country = _clean_env_value(config.get("COUNTRY"), default="HU")
    old_refresh_token = _clean_env_value(config.get("REFRESH_TOKEN"))

    token_state = None
    if old_refresh_token:
        try:
            token_state = refresh_access_token(old_refresh_token)
            print("✓ Token refreshed successfully")
        except Exception as exc:
            print(f"✗ Token refresh failed: {exc}")

    if token_state is None:
        print("Starting interactive browser login...")
        try:
            token_state = _interactive_login(language=language, country=country)
        except Exception as exc:
            print(f"✗ Interactive login failed: {exc}")
            sys.exit(1)
        print("✓ Login completed successfully")

    save_token_file(token_state, path="token.json")
    update_env_refresh_token(token_state.refresh_token, env_path=".env")
    print("✓ Tokens saved to token.json and .env")


if __name__ == "__main__":
    main()
