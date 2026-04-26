#!/usr/bin/env python3
"""
Clean all newsletters and junk from ALL Gmail accounts and ALL time.
Bulk unsubscribe + archive.
"""

import httpx


def main():
    client = httpx.Client(base_url="http://localhost:9849", timeout=120)

    # Get ALL Gmail (unread + read, across all time)
    print("Fetching all Gmail conversations from all accounts...")
    r = client.get("/conversations?source=gmail&limit=500")
    convs = r.json()
    print(f"Found {len(convs)} total conversations\n")

    # Newsletter + junk keywords
    newsletter_keywords = [
        "newsletter",
        "digest",
        "weekly",
        "daily",
        "alert",
        "product hunt",
        "linkedin",
        "news",
        "update",
        "report",
    ]

    junk_keywords = [
        "dealership",
        "dealer",
        "auto",
        "car sale",
        "vehicle",
        "financing",
        "insurance quote",
        "real estate",
        "mortgage",
        "loan",
        "credit",
        "spam",
        "promotional",
        "offer",
        "deal",
        "reward",
        "points",
    ]

    # Also include specific problematic senders
    problematic_senders = [
        "dealership",
        "cars.com",
        "autotrader",
        "edmunds",
        "craigslist",
        "zillow",
        "realtor",
        "redfin",
        "trulia",
        "lendingtree",
        "creditkarma",
        "capitalone",
        "chase",
        "bank of america",
    ]

    # WHITELIST: Keep these important senders/patterns
    whitelist = [
        "alameda county",
        "stanford",
        "patelco",
        "security alert",
        "security",
        "archive of google data",
        "confirm",
        "verification",
        "invoice",
        "receipt",
        "payment",
        "transaction",
        "jwalin shah",  # personal messages
        "deepresource",
        "jules",
    ]

    # Filter candidates
    candidates = []
    for c in convs:
        sender = c.get("name", "").lower()
        snippet = c.get("snippet", "").lower()
        text = f"{sender} {snippet}"

        # Skip whitelisted senders/patterns
        if any(w in text for w in whitelist):
            continue

        # Check newsletter keywords
        is_newsletter = any(k in text for k in newsletter_keywords)
        # Check junk keywords
        is_junk = any(k in text for k in junk_keywords)
        # Check problematic senders
        is_problematic = any(p in sender for p in problematic_senders)

        if is_newsletter or is_junk or is_problematic:
            candidates.append({**c, "reason": []})
            if is_newsletter:
                candidates[-1]["reason"].append("newsletter")
            if is_junk:
                candidates[-1]["reason"].append("junk")
            if is_problematic:
                candidates[-1]["reason"].append("problematic-sender")

    if not candidates:
        print("No newsletters/junk found.")
        return

    print(f"Found {len(candidates)} newsletters/junk emails\n")

    # Group by category
    by_category = {}
    for c in candidates:
        sender = c.get("name", "")
        if sender not in by_category:
            by_category[sender] = []
        by_category[sender].append(c)

    print("=" * 120)
    for sender in sorted(by_category.keys())[:30]:
        emails = by_category[sender]
        count = len(emails)
        reasons = ", ".join(emails[0].get("reason", ["unknown"]))
        print(f"{count:3d}x {sender[:50]:<50s} ({reasons})")

    if len(by_category) > 30:
        print(f"... and {len(by_category) - 30} more senders")

    print("=" * 120)

    confirm = input(f"\nUnsubscribe + archive all {len(candidates)} emails? (y/n): ")
    if confirm.lower() != "y":
        print("Cancelled.")
        return

    # Bulk unsubscribe in batches
    msg_ids = [c["id"] for c in candidates]
    print(f"\nProcessing {len(msg_ids)} emails in batches of 30...")

    batch_size = 30
    all_results = []

    for i in range(0, len(msg_ids), batch_size):
        batch = msg_ids[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(msg_ids) - 1) // batch_size + 1
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} emails)...", end=" ", flush=True)

        try:
            r = client.post(
                "/messages/gmail/bulk-unsubscribe", json={"msg_ids": batch}, timeout=120
            )
            batch_result = r.json()
            all_results.extend(batch_result.get("results", []))
            print("✓")
        except Exception as e:
            print(f"✗ ({str(e)[:40]})")

    result = {"total": len(msg_ids), "results": all_results}

    # Show results summary
    print(f"\nResults: {result['total']} total\n")

    success_count = 0
    no_header_count = 0
    error_count = 0

    by_status = {"success": [], "no_header": [], "error": []}

    for item in result.get("results", []):
        msg_id = item["msg_id"]
        email = next((c for c in candidates if c["id"] == msg_id), None)

        if "error" in item:
            error_count += 1
            by_status["error"].append((email["name"], item["error"]))
        else:
            method = item.get("method", "unknown")
            ok = item.get("ok", False)
            if method == "none":
                no_header_count += 1
                by_status["no_header"].append(email["name"])
            else:
                if ok:
                    success_count += 1
                    by_status["success"].append(email["name"])
                else:
                    error_count += 1
                    by_status["error"].append((email["name"], "unsubscribe failed"))

    print(f"✓  {success_count:3d} successfully unsubscribed")
    print(f"⚠️  {no_header_count:3d} archived (no unsubscribe header)")
    print(f"❌ {error_count:3d} failed")

    # Show top senders by category
    if by_status["success"]:
        print("\n✓ Successfully unsubscribed from:")
        for name in by_status["success"][:5]:
            print(f"    {name[:60]}")
        if len(by_status["success"]) > 5:
            print(f"    ... and {len(by_status['success']) - 5} more")

    if by_status["no_header"]:
        print("\n⚠️  Archived (no unsubscribe available):")
        for name in by_status["no_header"][:3]:
            print(f"    {name[:60]}")
        if len(by_status["no_header"]) > 3:
            print(f"    ... and {len(by_status['no_header']) - 3} more")

    print(f"\n{'=' * 120}")
    print(f"DONE: {len(candidates)} newsletters/junk removed from inbox")

    client.close()


if __name__ == "__main__":
    main()
