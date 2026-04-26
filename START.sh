#!/bin/bash
# Hackathon startup — runs everything you need in separate terminal tabs

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "=== G2 Ambient Copilot — Startup ==="
echo ""
echo "Step 1: Make sure OPENROUTER_API_KEY is set in your environment"
echo "  export OPENROUTER_API_KEY=sk-or-..."
echo ""
echo "Step 2: Start ngrok in a separate terminal:"
echo "  ngrok http 9849"
echo "  Then update g2-app/.env.local with the ngrok URL"
echo ""
echo "Step 3: Starting services..."
echo ""

# Start inbox server (agents run inside it)
echo "[1] Starting inbox server + G2 agents on port 9849..."
cd "$ROOT/inbox"
uv run python inbox_server.py &
INBOX_PID=$!
sleep 3

# Start G2 Vite app
echo "[2] Starting G2 Vite app on port 5173..."
cd "$ROOT/g2-app"
npm run dev &
VITE_PID=$!
sleep 2

# Open demo panel
echo "[3] Opening demo control panel..."
open "file://$ROOT/demo/index.html" 2>/dev/null || xdg-open "file://$ROOT/demo/index.html" 2>/dev/null || echo "  → Open demo/index.html manually in your browser"

echo ""
echo "=== All services started ==="
echo ""
echo "  Inbox server:   http://localhost:9849"
echo "  G2 app (Vite):  http://localhost:5173"
echo "  Demo panel:     demo/index.html (already opened)"
echo ""
echo "  G2 Simulator:   cd g2-app && npm run simulate"
echo "  QR for glasses: cd g2-app && npx evenhub qr --url http://\$(ipconfig getifaddr en0):5173"
echo ""
echo "Press Ctrl+C to stop everything"

wait $INBOX_PID $VITE_PID
