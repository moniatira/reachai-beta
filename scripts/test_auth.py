"""End-to-end test of the magic link auth flow.

Usage:
    python scripts/test_auth.py \\
        --api-url https://reachai-api.onrender.com \\
        --email your@email.com

What it does:
1. Requests a magic link to your email
2. Pauses for you to click the link
3. Asks you to paste the session token from the redirect URL
4. Verifies the token works with /v1/auth/me
"""
import argparse
import json
import sys
import urllib.request
from urllib.error import HTTPError


def request_link(api_url: str, email: str) -> None:
    body = json.dumps({"email": email}).encode()
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/v1/auth/request-link",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            print(f"✓ {data['message']}")
    except HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)


def verify_me(api_url: str, token: str) -> None:
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            print()
            print("✓ Session token works! User info:")
            print(f"  ID:         {data['id']}")
            print(f"  Email:      {data['email']}")
            print(f"  Created:    {data['created_at']}")
            print(f"  Last login: {data.get('last_login_at', 'never')}")
            print()
            print("Auth flow verified end-to-end.")
    except HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    print(f"Sending magic link to {args.email}...")
    request_link(args.api_url, args.email)
    print()
    print("Check your email for a sign-in link from ReachAI.")
    print("Click the link — you'll see a success page that redirects.")
    print()
    print("After redirect, look at the URL — it ends with #session=eyJ...")
    print("Copy everything after '#session=' (the JWT token).")
    print()
    token = input("Paste the session token here: ").strip()

    if not token:
        print("No token provided — exiting.")
        sys.exit(1)

    verify_me(args.api_url, token)


if __name__ == "__main__":
    main()
