# Inbox Plan

## Goal

Turn `projects/inbox` into a local-first inbox operating system for daily use.

For this phase, the product is intentionally narrow:
- Gmail
- iMessage / SMS
- calendar context that supports inbox work
- local FastAPI server
- local SQLite operational index
- Textual TUI

This phase does **not** try to solve:
- full personal memory
- people graph across the whole life stack
- LinkedIn / ChatGPT / Claude / Meta ingestion
- external memory engines like Neocortex
- a general-purpose personal agent platform

Those can come later, but they should not shape the first shipping milestone.

## Product Definition

The inbox should answer five questions well:
- what changed since I last looked
- what needs a reply
- what can be archived or ignored
- what am I waiting on
- what upcoming event needs prep or follow-up

The inbox should **not** default to raw provider dumps.

## Architecture

The inbox should be built as four layers:

1. Raw sources
- Gmail remains the source of truth for email
- Messages DB remains the source of truth for iMessage / SMS
- Google Calendar remains the source of truth for events

2. Operational index
- local SQLite
- normalized items
- threads
- sync state
- derived classifications
- compact summaries

3. Inbox views
- actionable threads
- recent changes
- waiting on me
- waiting on others
- calendar-attached work

4. Interfaces
- Textual TUI for the human control plane
- local FastAPI API for internal clients and agents
- MCP only for a narrow, curated external surface

## Current State

What already exists:
- `inbox.py` provides the TUI shell
- `inbox_server.py` provides the local API backend
- `services.py` provides the source integrations
- `message_index_store.py` provides the local SQLite operational index
- `message_sync.py` provides bootstrap and incremental sync
- `/inbox/needs-action` already prefers indexed reads

What is still incomplete:
- bootstrap sync is not hardened enough for resumable long runs
- incremental sync needs stronger checkpoint guarantees
- classification is still heuristic and noisy
- indexed views are not yet the default TUI experience
- waiting-on and calendar-context views are not yet first-class

## Phase 1 Scope

Phase 1 is inbox-only and local-first.

Deliverables:
- reliable bootstrap sync
- reliable incremental sync
- compact thread summaries and action labels
- indexed endpoints for actionable / recent / waiting-on views
- a TUI home surface built on the index

Non-goals:
- general knowledge graph
- durable cross-app memory
- cloud hosting
- graph database migration
- external memory provider integration

## Milestones

### Milestone 1: Stabilize Sync And Index

Goal:
- make the local index trustworthy enough to become the default read path

Deliverables:
- bootstrap can resume cleanly after interruption
- sync checkpoints persist during long runs
- incremental sync only processes new or changed records
- deterministic upserts for `items`, `threads`, and `sync_state`
- a simple sync status surface in the API

Files:
- `message_sync.py`
- `message_index_store.py`
- `inbox_server.py`
- tests around sync and checkpoint behavior

Success criteria:
- a full backfill can be interrupted and resumed without corrupting state
- incremental sync does not re-read the world on every run
- the server can report index freshness and sync health

### Milestone 2: Improve Thread Intelligence

Goal:
- make indexed threads useful enough that raw thread reads become exceptional

Deliverables:
- stronger `reply / track / archive / ignore` classification
- better human vs automated detection
- better newsletter / OTP / receipt / appointment noise labeling
- better open-loop extraction
- compact summaries that explain why a thread matters

Files:
- `message_index_store.py`
- `message_sync.py`
- any summary / classification helpers extracted from those files
- tests for representative thread classes

Success criteria:
- actionable views are mostly high signal
- low-value automated mail stops surfacing as reply-worthy
- open loops are usable without reading full raw bodies first

### Milestone 3: Make Indexed Reads The Default

Goal:
- route the product through the index first and raw sources second

Deliverables:
- indexed endpoints for:
  - actionable threads
  - recent changes
  - waiting on me
  - waiting on others
  - sync status
- raw provider fetches used only for drill-down and explicit refresh
- clear distinction between indexed state and source-of-truth fetches

Files:
- `inbox_server.py`
- `inbox_client.py`
- tests for endpoint behavior and fallback rules

Success criteria:
- the main inbox flows do not depend on live provider fetches
- agents and TUI can work from compact indexed views
- token usage drops because raw blobs are not the default context

### Milestone 4: Build The Right TUI Surface

Goal:
- make the TUI a control plane for inbox work, not just source tabs

Deliverables:
- a `Now` view
- an `Actionable` view
- a `Waiting On` view
- a better calendar-context summary view
- source tabs remain available as drill-down surfaces

Files:
- `inbox.py`
- `inbox_client.py`
- any small TUI helpers needed for indexed models

Success criteria:
- the first screen answers what matters now
- the user can process inbox work from indexed views before opening sources
- source tabs feel secondary, not primary

### Milestone 5: Calendar Context For Inbox Work

Goal:
- bring in only the calendar data that improves inbox decision-making

Deliverables:
- upcoming event prep flags
- RSVP / confirmation / scheduling context surfaced near relevant threads
- basic thread-to-calendar context where obvious and high confidence

Files:
- `services.py`
- `inbox_server.py`
- `message_index_store.py`
- `inbox.py`

Success criteria:
- confirmations and scheduling threads have nearby event context
- the calendar view helps answer “what needs prep?” instead of listing every event

## Data Model Direction

The operational index should remain distinct from durable memory.

Core tables:
- `items`
- `threads`
- `sync_state`

Likely next additions:
- `open_loops`
- `thread_labels`
- `thread_people_refs`
- `thread_events`

This remains an operational inbox store, not a general-purpose memory graph.

## TUI Information Architecture

Primary views should become:
- `Now`
- `Actionable`
- `Waiting On`
- `Recent`
- `Calendar Context`
- `Sources`

`Sources` stays as drill-down:
- Gmail
- iMessage
- calendar
- notes
- reminders

The product should be organized around work to do, not raw providers.

## Risks

Main risks in this phase:
- sync logic becomes fragile under long-running backfills
- heuristic classification pollutes actionable views
- the TUI gets widened before the index is trustworthy
- live provider fetches remain embedded in supposedly indexed flows

Countermeasures:
- prioritize sync correctness before UI expansion
- add representative thread fixtures and tests
- keep the TUI changes thin until the API surface is stable
- treat raw reads as drill-down only

## Immediate Next Steps

Build in this order:

1. Harden bootstrap and incremental sync.
2. Add sync status and indexed view endpoints.
3. Tighten thread classification and open-loop extraction.
4. Add `Now` and `Actionable` TUI views.
5. Add `Waiting On`.
6. Add calendar context only after the index-driven inbox is stable.

## Definition Of Done For Phase 1

Phase 1 is done when:
- the local index can bootstrap and stay fresh reliably
- the main inbox views run off compact indexed state
- the TUI opens on a useful “what matters now” surface
- raw source fetches are mostly for drill-down, not default navigation
- the system is good enough to use daily without needing the broader memory platform
