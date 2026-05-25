"""Test Day 3 calendar OAuth flows and status endpoints.

Usage:
    python scripts/test_calendar_connect.py \\
        --api-url https://reachai-api.onrender.com \\
        --admin-key YOUR_ADMIN_KEY \\
        --slug demo-salon \\
        --provider google

After running, you'll get a browser URL to authorize the provider.
Open it, authorize, then come back and the script verifies the
connection landed in the calendar_connections table.
"""
import argparse
import json
import sys
import urllib.request
import webbrowser
from urllib.error import HTTPError


def api_call(method: str, url: str, admin_key: str | None = None, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if admin_key:
        headers["X-Admin-Key"] = admin_key
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        print(f"\nERROR {e.code} {method} {url}", file=sys.stderr)
        print(e.read().decode(), file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--admin-key", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--provider",
        choices=["google", "outlook", "calendly"],
        required=True,
    )
    args = parser.parse_args()

    api = args.api_url.rstrip("/")

    # 1. Show current status BEFORE connecting
    print(f"=== BEFORE: Calendar status for {args.slug} ===")
    status = api_call("GET", f"{api}/v1/calendar/status/{args.slug}", admin_key=args.admin_key)
    print(f"  Primary provider: {status.get('primary_provider') or 'none'}")
    print(f"  Connected: {len(status['connections'])} provider(s)")
    for c in status["connections"]:
        flag = " (PRIMARY)" if c["is_primary"] else ""
        health = "✓ healthy" if c["healthy"] else "⚠ unhealthy"
        print(f"    - {c['provider']:10s} {c.get('account_email') or '(no email)'} [{health}]{flag}")
    print()

    # 2. Print connect URL
    connect_url = f"{api}/v1/{args.provider}/connect/{args.slug}"
    print(f"=== Opening OAuth flow for {args.provider} ===")
    print(f"URL: {connect_url}")
    print()
    print("Opening browser...")
    webbrowser.open(connect_url)
    print()
    print("Complete the authorization flow in your browser.")
    input("Press Enter once you see the green 'Connected!' page...")

    # 3. Verify connection landed
    print()
    print(f"=== AFTER: Calendar status for {args.slug} ===")
    status = api_call("GET", f"{api}/v1/calendar/status/{args.slug}", admin_key=args.admin_key)
    print(f"  Primary provider: {status.get('primary_provider')}")
    print(f"  Connected: {len(status['connections'])} provider(s)")
    for c in status["connections"]:
        flag = " (PRIMARY)" if c["is_primary"] else ""
        health = "✓ healthy" if c["healthy"] else "⚠ unhealthy"
        print(f"    - {c['provider']:10s} {c.get('account_email') or '(no email)'} [{health}]{flag}")

    # 4. Verify the new provider appears
    found = next((c for c in status["connections"] if c["provider"] == args.provider), None)
    if found:
        print()
        print(f"✓ {args.provider} successfully connected as {found.get('account_email')}")
        if found["healthy"]:
            print(f"✓ Health check passed — calendar API is reachable")
        else:
            print(f"⚠ Connection exists but health check failed — tokens may be invalid")
    else:
        print(f"✗ {args.provider} did NOT appear in connections — OAuth may have failed")


if __name__ == "__main__":
    main()
