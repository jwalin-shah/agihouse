#!/usr/bin/env python
"""Smart inbox organization: tag emails by category.

Usage:
  uv run python organize_inbox.py

Does NOT archive, delete, or modify anything else.
Only applies labels to matching emails.
"""

import sys

from services import gmail_batch_modify, gmail_labels, gmail_search, google_auth_all

# Label name → search query mapping
LABEL_QUERIES = {
    "Newsletters": 'newsletter OR digest OR "weekly digest" OR "daily digest" OR "product hunt"',
    "Finance": "invoice OR receipt OR payment OR transaction OR statement OR billing OR tax",
    "Jobs": '"job opportunity" OR interview OR LinkedIn OR hiring OR recruiter OR "career"',
    "Promotions": "CATEGORY_PROMOTIONS OR deal OR offer OR discount OR promo OR coupon OR sale",
}


def main():
    """Tag emails by category using existing labels."""
    # Auth
    gmail_svcs, _, _, _, _, _ = google_auth_all()
    if not gmail_svcs:
        print("ERROR: No Gmail accounts authenticated")
        return 1

    service = list(gmail_svcs.values())[0]
    account = list(gmail_svcs.keys())[0]
    print(f"Using account: {account}\n")

    # Get existing labels
    existing_labels = {label["name"]: label["id"] for label in gmail_labels(service)}
    print(f"Found {len(existing_labels)} existing labels")

    # Tag emails by category
    print("\nTagging emails by category...")
    stats = {label: 0 for label in LABEL_QUERIES}

    for label_name, query in LABEL_QUERIES.items():
        if label_name not in existing_labels:
            print(f"  {label_name}: ⊘ label does not exist (skipped)")
            continue

        label_id = existing_labels[label_name]

        try:
            conversations = gmail_search(service, account, q=query, limit=1000)
            if not conversations:
                print(f"  {label_name}: 0 emails matched")
                continue

            # Get message IDs
            msg_ids = [conv["message_id"] for conv in conversations if conv.get("message_id")]

            if msg_ids:
                # Batch apply label (no removal, no archiving)
                success = gmail_batch_modify(
                    service,
                    msg_ids,
                    add_label_ids=[label_id],
                    remove_label_ids=[],
                )
                if success:
                    stats[label_name] = len(msg_ids)
                    print(f"  {label_name}: ✓ tagged {len(msg_ids)} emails")
                else:
                    print(f"  {label_name}: ✗ batch modify failed")
        except Exception as e:
            print(f"  {label_name}: ✗ {e}")

    print("\n=== Summary ===")
    total = sum(stats.values())
    for label, count in sorted(stats.items()):
        if count > 0:
            print(f"{label}: {count}")
    print(f"Total emails tagged: {total}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
