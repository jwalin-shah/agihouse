#!/bin/bash
# Skill-like wrapper: organize inbox by tagging emails

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "📧 Inbox Organization Tool"
echo "=========================="
echo ""

# Check if server is running
if ! curl -s http://127.0.0.1:9849/health > /dev/null 2>&1; then
    echo "ℹ️  Server not running. Starting..."
    timeout 3 uv run python inbox_server.py > /dev/null 2>&1 &
    sleep 1
fi

# Verify server is accessible
if curl -s http://127.0.0.1:9849/health > /dev/null 2>&1; then
    echo "✓ Server running"
else
    echo "⚠️  Server not accessible at localhost:9849"
    echo "   Start it with: uv run python inbox_server.py"
    exit 1
fi

echo ""
echo "ℹ️  Note: Requires Gmail labels to exist (Finance, Jobs, Newsletters, Promotions)"
echo "   Create them manually in Gmail or via the TUI."
echo ""

# Run organization
uv run python organize_inbox.py
