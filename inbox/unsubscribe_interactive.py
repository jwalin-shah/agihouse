#!/usr/bin/env python3
"""
Interactive newsletter unsubscribe tool.
One-by-one selection with preview.
"""

import httpx


def main():
    client = httpx.Client(base_url="http://localhost:9849", timeout=30)

    # Get unread Gmail
    print("Fetching unread emails...")
    r = client.get("/conversations?source=gmail&limit=100")
    convs = r.json()
    unread = [c for c in convs if c.get("unread")]

    if not unread:
        print("No unread emails found.")
        return

    # Filter to likely newsletters
    keywords = [
        "newsletter",
        "digest",
        "weekly",
        "daily",
        "alert",
        "product hunt",
        "linkedin",
        "news",
    ]
    candidates = [c for c in unread if any(k in c.get("name", "").lower() for k in keywords)]

    if not candidates:
        candidates = unread

    print(f"\nFound {len(candidates)} unread emails (showing first 20)\n")
    print("=" * 100)

    for i, c in enumerate(candidates[:20]):
        sender = c.get("name", "")[:35]
        snippet = c.get("snippet", "")[:50]
        unread_marker = "🔴" if c.get("unread") else "✓"
        print(f"{i:2d}. {unread_marker} {sender:<35} | {snippet:<50}")

    print("=" * 100)

    while True:
        try:
            choice = input("\nEnter email number to unsubscribe (or 'q' to quit): ").strip()
            if choice.lower() == "q":
                print("Exiting.")
                break

            idx = int(choice)
            if idx < 0 or idx >= len(candidates[:20]):
                print(f"Please enter a number between 0 and {len(candidates[:20]) - 1}")
                continue

            email = candidates[idx]
            print(f"\nUnsubscribing from: {email['name']}")
            print(f"Snippet: {email['snippet'][:60]}")

            r = client.post(f"/messages/gmail/{email['id']}/unsubscribe")
            result = r.json()

            if "error" in result:
                print(f"❌ Error: {result['error']}")
            else:
                method = result.get("method", "unknown")
                ok = result.get("ok", False)
                if method == "none":
                    print("⚠️  No unsubscribe method found in email headers.")
                else:
                    status = "✓" if ok else "⚠️"
                    print(f"{status} Unsubscribed via {method}")

        except ValueError:
            print("Invalid input. Please enter a number or 'q'.")
        except Exception as e:
            print(f"Error: {e}")

    client.close()


if __name__ == "__main__":
    main()
