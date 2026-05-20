"""Whitelist + create a new workspace.

Usage:
    python scripts/whitelist.py \\
        --api-url https://reachai-api.up.railway.app \\
        --admin-key YOUR_ADMIN_KEY \\
        --slug acme-salon \\
        --name "Acme Salon" \\
        --owner-email jordan@acmesalon.com \\
        [--industry "Salon & spa"] \\
        [--assistant-name Sarah] \\
        [--tone warm] \\
        [--brand-color "#534AB7"]

Prints the Calendly connect URL and embed code at the end.
"""
import argparse
import json
import sys
import urllib.request


def main():
    parser = argparse.ArgumentParser(description="Whitelist an SMB into the ReachAI beta")
    parser.add_argument("--api-url", required=True, help="ReachAI API base URL")
    parser.add_argument("--admin-key", required=True, help="X-Admin-Key value")
    parser.add_argument("--slug", required=True, help="URL-safe ID (e.g. acme-salon)")
    parser.add_argument("--name", required=True, help="Business name")
    parser.add_argument("--owner-email", required=True, help="SMB owner's email")
    parser.add_argument("--industry", default=None)
    parser.add_argument("--assistant-name", default="Sarah")
    parser.add_argument("--tone", default="warm")
    parser.add_argument("--brand-color", default="#534AB7")
    args = parser.parse_args()

    body = {
        "slug": args.slug,
        "name": args.name,
        "owner_email": args.owner_email,
        "industry": args.industry,
        "assistant_name": args.assistant_name,
        "tone": args.tone,
        "brand_primary": args.brand_color,
    }
    body = {k: v for k, v in body.items() if v is not None}

    req = urllib.request.Request(
        f"{args.api_url.rstrip('/')}/v1/workspaces",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Admin-Key": args.admin_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"Error {e.code}: {err}", file=sys.stderr)
        sys.exit(1)

    print()
    print("=" * 60)
    print(f"✓ Workspace created: {data['name']}")
    print("=" * 60)
    print(f"  Workspace ID : {data['id']}")
    print(f"  Slug         : {data['slug']}")
    print(f"  Owner        : {data['owner_email']}")
    print()
    print("STEP 1 — send the SMB this Calendly connect URL:")
    print()
    print(f"  {data['calendly_connect_url']}")
    print()
    print("STEP 2 — after they connect, send them this embed code:")
    print()
    print(f"  {data['embed_code']}")
    print()
    print("They paste the embed code on their website. Done.")
    print()


if __name__ == "__main__":
    main()
