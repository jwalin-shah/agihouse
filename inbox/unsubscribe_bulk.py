#!/usr/bin/env python3
"""
Bulk newsletter unsubscribe tool.
Auto-detect and unsubscribe from all newsletters matching patterns.
"""

import httpx


def main():
    client = httpx.Client(base_url="http://localhost:9849", timeout=60)

    # Get all Gmail
    print("Fetching Gmail conversations...")
    r = client.get("/conversations?source=gmail&limit=200")
    convs = r.json()

    # Filter to likely newsletters
    keywords = [
        "newsletter",
        "digest",
        "weekly",
        "daily",
        "alert",
        "product hunt",
        "linkedin job",
        "news",
    ]
    newsletters = [
        c
        for c in convs
        if c.get("unread") and any(k in c.get("name", "").lower() for k in keywords)
    ]

    if not newsletters:
        print("No newsletters found.")
        return

    print(f"\nFound {len(newsletters)} newsletters to unsubscribe from:\n")
    print("=" * 100)

    for i, c in enumerate(newsletters):
        sender = c.get("name", "")[:35]
        snippet = c.get("snippet", "")[:50]
        print(f"{i + 1:2d}. {sender:<35} | {snippet:<50}")

    print("=" * 100)

    confirm = input(f"\nUnsubscribe from all {len(newsletters)} newsletters? (y/n): ")
    if confirm.lower() != "y":
        print("Cancelled.")
        return

    # Bulk unsubscribe
    msg_ids = [c["id"] for c in newsletters]
    print(f"\nUnsubscribing from {len(msg_ids)} emails...")

    r = client.post("/messages/gmail/bulk-unsubscribe", json={"msg_ids": msg_ids})
    result = r.json()

    # Show results
    print(f"\nResults: {result['total']} total")
    success_count = 0
    fail_count = 0

    for item in result.get("results", []):
        msg_id = item["msg_id"]
        email = next((c for c in newsletters if c["id"] == msg_id), None)

        if "error" in item:
            print(f"  ❌ {email['name'][:40]:40s} - {item['error']}")
            fail_count += 1
        else:
            method = item.get("method", "unknown")
            ok = item.get("ok", False)
            if method == "none":
                print(f"  ⚠️  {email['name'][:40]:40s} - No unsubscribe header")
            else:
                status = "✓" if ok else "⚠️"
                print(f"  {status}  {email['name'][:40]:40s} - {method}")
                if ok:
                    success_count += 1
                else:
                    fail_count += 1

    print(f"\n{'=' * 100}")
    print(f"Summary: {success_count} successful, {fail_count} failed/skipped")

    client.close()


if __name__ == "__main__":
    main()
