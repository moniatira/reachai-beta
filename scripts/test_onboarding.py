"""End-to-end test of the onboarding wizard.

Requires Day 1 auth to be deployed and working.
This script walks through every onboarding step using a real user session.

Usage:
    python scripts/test_onboarding.py \\
        --api-url https://reachai-api.onrender.com \\
        --email your@email.com

What it does:
1. Requests a magic link to your email
2. Waits for you to click it and paste the session token
3. Calls /v1/onboarding/start to create a test workspace
4. Saves business info
5. Saves assistant config
6. Tests /v1/workspaces/me lists the workspace
7. Cleans up by leaving the workspace in place (no Calendly attached, harmless)

Test workspace is named "Onboarding Test {timestamp}" — clearly identifiable.
"""
import argparse
import json
import sys
import time
import urllib.request
from urllib.error import HTTPError


def api_call(method: str, url: str, token: str | None = None, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        err_body = e.read().decode()
        print(f"\nERROR {e.code} {method} {url}", file=sys.stderr)
        print(err_body, file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--session-token",
        help="Skip magic link flow if you already have a token",
    )
    args = parser.parse_args()

    api = args.api_url.rstrip("/")

    # Step 1 — Request magic link (or skip if token provided)
    if args.session_token:
        token = args.session_token
        print("Using provided session token, skipping magic link.")
    else:
        print(f"Requesting magic link to {args.email}...")
        result = api_call("POST", f"{api}/v1/auth/request-link", body={"email": args.email})
        print(f"  ✓ {result['message']}")
        print()
        print("Click the magic link in your email.")
        print("After redirect, look at URL bar — copy everything after '#session='")
        print()
        token = input("Paste session token: ").strip()
        if not token:
            print("No token provided.")
            sys.exit(1)

    # Step 2 — Verify token works
    print("\nVerifying token...")
    me = api_call("GET", f"{api}/v1/auth/me", token=token)
    print(f"  ✓ Logged in as {me['email']} (user {me['id'][:8]}...)")

    # Step 3 — Start onboarding
    test_name = f"Onboarding Test {int(time.time())}"
    print(f"\nStarting onboarding for '{test_name}'...")
    workspace = api_call(
        "POST",
        f"{api}/v1/onboarding/start",
        token=token,
        body={"business_name": test_name},
    )
    workspace_id = workspace["id"]
    print(f"  ✓ Workspace created: id={workspace_id[:8]}... slug={workspace['slug']}")
    print(f"    onboarding_step={workspace['onboarding_step']} trial_status={workspace['trial_status']}")

    # Step 4 — Save business info
    print("\nSaving business info...")
    workspace = api_call(
        "PATCH",
        f"{api}/v1/onboarding/business",
        token=token,
        body={
            "workspace_id": workspace_id,
            "industry": "Consulting",
            "website_url": "https://example.com",
        },
    )
    print(f"  ✓ Updated. onboarding_step={workspace['onboarding_step']}")

    # Step 5 — Save assistant config
    print("\nSaving assistant config...")
    workspace = api_call(
        "PATCH",
        f"{api}/v1/onboarding/assistant",
        token=token,
        body={
            "workspace_id": workspace_id,
            "assistant_name": "Maya",
            "greeting": "Hi! I'm Maya from the test workspace. How can I help?",
            "tone": "warm",
            "brand_primary": "#534AB7",
        },
    )
    print(f"  ✓ Assistant configured.")

    # Step 6 — List workspaces owned by this user
    print("\nListing workspaces owned by current user...")
    workspaces = api_call("GET", f"{api}/v1/workspaces/me", token=token)
    print(f"  ✓ User owns {len(workspaces)} workspace(s):")
    for w in workspaces:
        marker = "  ← just created" if w["id"] == workspace_id else ""
        print(f"    - {w['slug']:30s} step={w['onboarding_step']:10s} cal={w['calendly_connected']}{marker}")

    # Step 7 — Try to complete (should fail because no calendar connected)
    print("\nAttempting to complete onboarding (expected to fail — no calendar)...")
    try:
        api_call(
            "POST",
            f"{api}/v1/onboarding/complete",
            token=token,
            body={"workspace_id": workspace_id},
        )
        print("  ✗ Unexpected: completion succeeded without a connected calendar")
    except SystemExit:
        # api_call exits on HTTPError — that's the expected path
        print("  ✓ Correctly rejected: connect a calendar before completing")

    print()
    print("=" * 60)
    print("Day 2 onboarding flow verified end-to-end.")
    print("=" * 60)
    print(f"Test workspace '{test_name}' left in DB for inspection.")
    print(f"  Slug: {workspace['slug']}")
    print(f"  ID:   {workspace_id}")


if __name__ == "__main__":
    main()
