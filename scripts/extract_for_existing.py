"""One-shot script to backfill business info for existing workspaces.

This solves Eric's "Sarah doesn't know anything about my business" problem
immediately by extracting the info from sambaluk-consulting.com.

Usage:
    python scripts/extract_for_existing.py \\
        --api-url https://reachai-api.onrender.com \\
        --admin-key YOUR_ADMIN_KEY
"""
import argparse
import json
import sys
import urllib.request
from urllib.error import HTTPError


WORKSPACES_TO_EXTRACT = [
    {
        "slug": "sambaluk-consulting",
        "website": "https://www.sambaluk-consulting.com",
    },
    # Add more workspaces here as you onboard them
]


def extract(api_url: str, admin_key: str, slug: str, website: str):
    body = json.dumps({"website_url": website}).encode()
    req = urllib.request.Request(
        f"{api_url.rstrip('/')}/v1/workspaces/{slug}/extract",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Admin-Key": admin_key,
        },
        method="POST",
    )
    print(f"  [{slug}] Extracting from {website}...")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        services = data["extracted"].get("services") or []
        print(f"    ✓ {data['pages_crawled']} pages crawled, {len(services)} services extracted")
        return True
    except HTTPError as e:
        print(f"    ✗ Error {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--admin-key", required=True)
    args = parser.parse_args()

    print(f"Backfilling {len(WORKSPACES_TO_EXTRACT)} workspace(s)...\n")

    successes = 0
    for ws in WORKSPACES_TO_EXTRACT:
        if extract(args.api_url, args.admin_key, ws["slug"], ws["website"]):
            successes += 1
        print()

    print("=" * 60)
    print(f"Complete: {successes}/{len(WORKSPACES_TO_EXTRACT)} workspaces extracted")
    print("=" * 60)
    print()
    print("Test in the chat — visit each SMB's site and ask 'tell me about your services'.")


if __name__ == "__main__":
    main()
