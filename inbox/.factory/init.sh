#!/usr/bin/env bash
set -euo pipefail

cd /Users/jwalinshah/projects/inbox

# Install dependencies (idempotent)
uv sync

# Ensure config directory exists
mkdir -p ~/.config/inbox

# Stop any stale server on port 9849
lsof -ti :9849 | xargs kill 2>/dev/null || true
