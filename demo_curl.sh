#!/usr/bin/env bash
# AGIHouse demo walkthrough — every step shows WHY it fires, what memory
# is touched, and what the user would see on the glasses HUD.
#
# Run interactively:
#     bash demo_curl.sh
#
# Press <enter> between sections to pace yourself.

set -u

API=http://localhost:9876
SIM=http://127.0.0.1:9898

c_blue()  { printf "\033[34m%s\033[0m\n" "$*"; }
c_green() { printf "\033[32m%s\033[0m\n" "$*"; }
c_dim()   { printf "\033[2m%s\033[0m\n" "$*"; }
c_bold()  { printf "\033[1m%s\033[0m\n" "$*"; }

step() {
  echo
  c_bold "──────────────────────────────────────────────────────"
  c_bold "▸ $1"
  c_dim   "  why: $2"
  c_bold "──────────────────────────────────────────────────────"
}

run() {
  c_blue "$ $*"
  eval "$@" | python3 -m json.tool 2>/dev/null || eval "$@"
}

pause() { read -r -p $'\033[2m[enter to continue]\033[0m '; }

# ─────────────────────────────────────────────────────────────
step "0. Health" \
     "Is the trigger_server alive, is GROQ_API_KEY loaded, are SSE clients connected?"
run "curl -s $API/health"
run "curl -s $API/diagnostics"
c_dim "  → keys_present.GROQ_API_KEY=true means transcription is ON"
c_dim "  → subscribers=2 means the glasses HUD is connected via SSE"
pause

# ─────────────────────────────────────────────────────────────
step "1. State snapshot — what does the assistant 'know' right now?" \
     "Calendar + reminders + memories are the persistent context. Everything the agent reasons about lives here."
c_green "── reminders (read from REAL macOS Reminders.app) ──"
run "curl -s '$API/transcript' -X POST -H 'Content-Type: application/json' -d '{\"text\":\"list my reminders\"}'"
c_green "── calendar (sqlite-backed sandbox; sourced from calendar.json on first boot) ──"
run "curl -s '$API/transcript' -X POST -H 'Content-Type: application/json' -d '{\"text\":\"what is on my calendar\"}'"
c_green "── memories (free-form notes the assistant has saved over time) ──"
run "curl -s '$API/memories'"
c_green "── memory edges (subject → relation → object knowledge graph) ──"
run "curl -s '$API/memory/edges?limit=20'"
c_dim "  Edges are how the agent learns relationships from conversation:"
c_dim "  e.g. (subject=Tarun, relation=likes, object=cold brew) gets recalled later."
pause

# ─────────────────────────────────────────────────────────────
step "2. Push a HUD message — verify the glasses display loop" \
     "Sanity-check that whatever the agent decides to surface actually reaches the lens."
run "curl -s -X POST $API/push -H 'Content-Type: application/json' -d '{\"text\":\"DEMO: hello from curl\"}'"
c_dim "  → Look at the glasses simulator: text should appear instantly."
c_dim "  → 'subscribers' field tells you how many lens UIs are listening."
pause

# ─────────────────────────────────────────────────────────────
step "3. Send a transcript — see the FULL extraction reasoning" \
     "/transcript runs the same path live audio takes after Groq. The response is annotated: action, payload, confidence, contact-resolution. This is the 'why' you wanted."
run "curl -s -X POST $API/transcript -H 'Content-Type: application/json' \
     -d '{\"text\":\"text Tarun saying running 5 minutes late\"}'"
c_dim "  Field-by-field:"
c_dim "    transcript    — raw input"
c_dim "    event.action  — what extractor decided to do"
c_dim "    event.confidence — extractor's certainty (proposal_first uses ≥0.7 threshold)"
c_dim "    event.payload — params; 'Tarun' was resolved to +17168032645 via contacts.json"
c_dim "    result.status — proposed | fired"
c_dim "    result.reason — why it stopped at proposal vs fired (awaiting_user_confirmation = WRITE_ACTION held for confirm)"
pause

# ─────────────────────────────────────────────────────────────
step "4. Inspect the proposal queue" \
     "WRITE_ACTIONS never auto-fire — they're queued and shown as a HUD card. User says 'confirm' to fire."
run "curl -s $API/proposals"
c_dim "  Each proposal carries:"
c_dim "    transcript — what was heard"
c_dim "    payload    — what would be executed"
c_dim "    status     — proposed (pending) or fired (executed)"
c_dim "    confidence — extractor score; surfaced on the HUD"
pause

# ─────────────────────────────────────────────────────────────
step "5. Confirm the latest proposal — fires the action AND learns from it" \
     "Confirming does TWO things: (a) executes the action (e.g. iMessage), (b) writes a memory edge so the agent remembers this preference later."
run "curl -s -X POST $API/proposals/confirm-latest"
c_dim "  → 'learned' array shows new memory edges written. Look for fields like:"
c_dim "      subject  → who/what the fact is about"
c_dim "      relation → predicate"
c_dim "      object   → value"
c_dim "      evidence → which transcript taught it"
c_dim "  These edges are queryable later via /memory/edges."
pause

# ─────────────────────────────────────────────────────────────
step "6. Re-read the memory graph to see what got added" \
     "Same endpoint as step 1, but now you should see the new edge from step 5."
run "curl -s '$API/memory/edges?limit=10'"
pause

# ─────────────────────────────────────────────────────────────
step "7. Audit log — every fire/propose/reject is logged, signed, and timestamped" \
     "The audit trail is what makes this safe to demo: nothing happens without a row written."
run "curl -s $API/audit/summary"
run "curl -s $API/actions/recent"
pause

# ─────────────────────────────────────────────────────────────
step "8. Tensor recall — semantic lookup across past meetings/messages" \
     "Tensor store sits in demos/assistant_store.pt. It's a pre-indexed corpus of contacts/messages/events. Triggers on phrases like 'upcoming events', 'meeting context', 'followups'."
c_green "── tensor: upcoming events with messages ──"
run "curl -s -X POST $API/transcript -H 'Content-Type: application/json' \
     -d '{\"text\":\"what is the context on my upcoming meeting\"}'"
c_green "── tensor: followups (unanswered threads) ──"
run "curl -s -X POST $API/transcript -H 'Content-Type: application/json' \
     -d '{\"text\":\"any followups I am missing\"}'"
c_dim "  Note: tensor_recall is currently wired into voice_trigger.py (laptop mic)."
c_dim "  For the glasses-audio path it's not invoked yet — the same store can be hit"
c_dim "  directly via demos/assistant_query.py if you want to show the raw retrieval."
pause

# ─────────────────────────────────────────────────────────────
step "9. Reject path — show that 'no' wins" \
     "Send a proposal, then reject it. Memory edge writes 'subject preferred not_to do object' — agent learns the negative too."
run "curl -s -X POST $API/transcript -H 'Content-Type: application/json' \
     -d '{\"text\":\"text Sanjay asking about the projector\"}'"
c_green "── reject latest ──"
LATEST_ID=$(curl -s $API/proposals | python3 -c "import json,sys;d=json.load(sys.stdin);p=[x for x in d['proposals'] if x['status']=='proposed'];print(p[0]['id']) if p else print('')")
if [[ -n "$LATEST_ID" ]]; then
  run "curl -s -X POST $API/proposals/$LATEST_ID/reject"
else
  c_dim "  (no pending proposal to reject — extractor may have decided no_action)"
fi
pause

# ─────────────────────────────────────────────────────────────
step "10. Live mic check — what is actually being heard right now?" \
     "tail of trigger_server log shows Groq output in real time. This is how you debug 'why isn't it firing'."
c_blue "$ grep '[glasses-audio]' /tmp/trigger_server.log | tail -10"
grep -aE '\[glasses-audio\]' /tmp/trigger_server.log | tail -10 || true
pause

c_bold "═══════════════════════════════════════════════════════"
c_green "Demo flow complete."
c_dim   "Tip: open another terminal and run"
c_dim   "    tail -f /tmp/trigger_server.log | grep glasses-audio"
c_dim   "to watch the live transcription stream while you speak."
c_bold "═══════════════════════════════════════════════════════"
