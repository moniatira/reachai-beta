"""End-to-end smoke test: have a real conversation with the AI.

Usage:
    python scripts/test_e2e.py \\
        --api-url https://reachai-api.up.railway.app \\
        --slug acme-salon

Sends a sequence of test messages and prints each reply.
The workspace must already have Calendly connected.
"""
import argparse
import json
import sys
import urllib.request


TEST_MESSAGES = [
    "Hi, what services do you offer?",
    "I'd like to book the haircut. What's available this week?",
    "The first one works for me.",
    "My name is Test User, email test@example.com.",
]


def send(api_url: str, slug: str, session_id: str | None, message: str):
    body = {"workspace_slug": slug, "message": message}
    if session_id:
        body["session_id"] = session_id

    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/v1/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--messages",
        nargs="*",
        help="Override the default test messages",
    )
    args = parser.parse_args()

    messages = args.messages or TEST_MESSAGES
    session_id = None

    print(f"Testing workspace '{args.slug}' at {args.api_url}\n")

    for i, msg in enumerate(messages, 1):
        print(f"[Turn {i}]")
        print(f"  Customer : {msg}")
        try:
            result = send(args.api_url, args.slug, session_id, msg)
        except urllib.error.HTTPError as e:
            print(f"  Error    : {e.code} {e.read().decode()}")
            sys.exit(1)
        session_id = result["session_id"]
        print(f"  {result['assistant_name']:<8} : {result['reply']}")
        print()

    print(f"✓ Conversation complete (session: {session_id})")


if __name__ == "__main__":
    main()
