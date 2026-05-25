"""Test the website extraction pipeline against any URL.

Usage:
    python scripts/test_extraction.py \\
        --api-url https://reachai-api.onrender.com \\
        --admin-key YOUR_ADMIN_KEY \\
        --slug sambaluk-consulting \\
        --website https://www.sambaluk-consulting.com
"""
import argparse
import json
import sys
import urllib.request
from urllib.error import HTTPError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--admin-key", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--website", required=True, help="Website URL to extract from")
    args = parser.parse_args()

    body = json.dumps({"website_url": args.website}).encode()
    req = urllib.request.Request(
        f"{args.api_url.rstrip('/')}/v1/workspaces/{args.slug}/extract",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Admin-Key": args.admin_key,
        },
        method="POST",
    )

    print(f"Extracting business info for {args.slug} from {args.website}")
    print("This takes 15-30 seconds (crawl + Claude extraction)...")
    print()

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        print(f"Error {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print(f"✓ Extraction complete")
    print("=" * 70)
    print(f"  Pages crawled: {data['pages_crawled']}")
    for p in data["pages"]:
        print(f"    - {p['url']} ({p['text_length']} chars)")
    print()

    extracted = data["extracted"]

    print("BUSINESS SUMMARY")
    print("-" * 70)
    print(f"  {extracted.get('business_summary', '(none)')}")
    print()

    services = extracted.get("services") or []
    print(f"SERVICES ({len(services)})")
    print("-" * 70)
    for s in services:
        name = s.get("name", "Untitled")
        desc = s.get("description", "")
        print(f"  - {name}")
        if desc:
            print(f"    {desc}")
    print()

    print("TARGET CUSTOMERS")
    print("-" * 70)
    print(f"  {extracted.get('target_customers') or '(none)'}")
    print()

    contact = extracted.get("contact") or {}
    print("CONTACT")
    print("-" * 70)
    print(f"  Email:   {contact.get('email') or '(none)'}")
    print(f"  Phone:   {contact.get('phone') or '(none)'}")
    print(f"  Address: {contact.get('address') or '(none)'}")
    print()

    if extracted.get("hours"):
        print(f"HOURS: {extracted['hours']}")
        print()

    if extracted.get("pricing_signals"):
        print(f"PRICING: {extracted['pricing_signals']}")
        print()

    if extracted.get("unique_value"):
        print(f"UNIQUE VALUE: {extracted['unique_value']}")
        print()

    faqs = extracted.get("faqs") or []
    if faqs:
        print(f"FAQS ({len(faqs)})")
        print("-" * 70)
        for f in faqs:
            print(f"  Q: {f.get('question', '')}")
            print(f"  A: {f.get('answer', '')}")
            print()

    if extracted.get("confidence_notes"):
        print(f"NOTES: {extracted['confidence_notes']}")
        print()

    print("Now test the chat to see Sarah using this knowledge:")
    print(f"  - Visit {args.website}")
    print(f"  - Click the chat bubble")
    print(f"  - Ask: 'Tell me about your services'")


if __name__ == "__main__":
    main()
