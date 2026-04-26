#!/usr/bin/env python3
"""
Development script to test autocomplete modes interactively.
Starts the server and lets you experiment with both "complete" and "reply" modes.
"""

import asyncio
import sys

from inbox_client import InboxClient

# Colors
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RESET = "\033[0m"


async def main():
    client = InboxClient("http://localhost:9849")

    print(f"{BLUE}=== Inbox Autocomplete Dev Tool ==={RESET}\n")

    # Example 1: Complete mode
    print(f"{YELLOW}[Complete Mode] - Finish a message being typed{RESET}")
    messages = [
        {"sender": "Alice", "body": "Hey, want to grab lunch tomorrow?"},
        {"sender": "You", "body": "Maybe, what time were you thinking?"},
    ]
    draft = "I'm pretty free "
    result = client.autocomplete(draft=draft, messages=messages, mode="complete", temperature=0.5)
    print(f"  Messages: {messages}")
    print(f"  Draft: '{draft}'")
    print(f"  Suggestion: '{draft}{result if result else '[no suggestion]'}'")
    print()

    # Example 2: Complete mode with higher temperature (more creative)
    print(f"{YELLOW}[Complete Mode - Creative] - Same context, higher temperature{RESET}")
    result_creative = client.autocomplete(
        draft=draft, messages=messages, mode="complete", temperature=0.9
    )
    print(f"  Same draft: '{draft}'")
    print(
        f"  Suggestion (temp=0.9): '{draft}{result_creative if result_creative else '[no suggestion]'}'"
    )
    print()

    # Example 3: Reply mode
    print(f"{YELLOW}[Reply Mode] - Suggest a response to the last message{RESET}")
    messages_reply = [
        {"sender": "Bob", "body": "Are you free for a call tomorrow at 2pm?"},
    ]
    result_reply = client.autocomplete(
        draft="", messages=messages_reply, mode="reply", temperature=0.5
    )
    print(f"  Last message: {messages_reply[-1]['body']}")
    print(f"  Suggested reply: '{result_reply if result_reply else '[no suggestion]'}'")
    print()

    # Example 4: Reply mode with conversation context
    print(f"{YELLOW}[Reply Mode - With Context] - Suggest a response with history{RESET}")
    messages_with_context = [
        {"sender": "Bob", "body": "What have you been working on lately?"},
        {"sender": "You", "body": "Just wrapping up the inbox feature"},
        {"sender": "Bob", "body": "Oh cool! How's it going?"},
    ]
    result_context = client.autocomplete(
        draft="", messages=messages_with_context, mode="reply", temperature=0.6, max_tokens=50
    )
    print(
        f"  Context: {messages_with_context[0]['body']} → You: {messages_with_context[1]['body']}"
    )
    print(f"  Replying to: '{messages_with_context[-1]['body']}'")
    print(f"  Suggested: '{result_context if result_context else '[no suggestion]'}'")
    print()

    # Example 5: Long context, constrained tokens
    print(f"{YELLOW}[Complete Mode - Many messages] - With conversation history{RESET}")
    long_context = [
        {"sender": "Alice", "body": "Hey! How was your weekend?"},
        {"sender": "You", "body": "Pretty good, got a lot done"},
        {"sender": "Alice", "body": "Nice! What did you work on?"},
        {"sender": "You", "body": "Improved the autocomplete feature"},
        {"sender": "Alice", "body": "Oh really? That sounds great."},
    ]
    draft_long = "Yeah, it "
    result_long = client.autocomplete(
        draft=draft_long, messages=long_context, mode="complete", max_tokens=16
    )
    print(f"  Draft: '{draft_long}'")
    print(
        f"  Suggestion (max_tokens=16): '{draft_long}{result_long if result_long else '[no suggestion]'}'"
    )
    print()

    print(f"{GREEN}✓ Autocomplete tests complete!{RESET}")
    print(f"{BLUE}Tips:{RESET}")
    print("  • Use mode='complete' to finish a message the user is typing")
    print("  • Use mode='reply' to suggest a response to a received message")
    print("  • temperature: 0.0-1.0 (0.5 = balanced, 0.9+ = more creative)")
    print("  • max_tokens: limit how long the suggestion can be")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConnectionError:
        print(f"{YELLOW}⚠ Server not running!{RESET}")
        print("Start it with: uv run python inbox_server.py")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted{RESET}")
        sys.exit(0)
