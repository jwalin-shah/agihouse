# Inbox Connector Roadmap

## Goal

Make Inbox feel like a first-class personal connector platform rather than a thin wrapper around raw provider APIs.

Target outcome:
- the backend owns auth, routing, policy, and normalization
- the model sees a small set of high-signal tools
- Google writes default to the source-of-truth account
- thread, task, calendar, and file data arrive in a cleaner shape
- common personal workflows become explicit connector operations instead of prompt improvisation

## Working Model

Inbox should be treated as four layers:

1. Source adapters
   Gmail, Calendar, Drive, Tasks, iMessage, Notes, Reminders, GitHub.

2. Normalization layer
   Convert provider-specific payloads into stable Inbox objects with clear ownership and workflow metadata.

3. Policy layer
   Enforce account defaults, naming conventions, write preflights, confirmation rules, and source-of-truth routing.

4. Intent tools
   Expose high-level tools aligned to what the user is actually trying to do.

The model should mostly interact with layer 4, occasionally with layer 2, and almost never directly with raw provider payloads.

## Source-Of-Truth Policy

For Google Workspace, the default write account should be:

- `jshah1331@gmail.com`

This should be enforced in the backend via `INBOX_DEFAULT_GOOGLE_ACCOUNT`, not just documented in prompts.

Rules:
- all new Drive folders, Tasks, and Calendars default to `jshah1331@gmail.com`
- any write to another Google account must be explicit
- objects returned from Google providers must include `owning_account`
- replies and edits must route to the account that owns the object unless explicitly overridden

## Connector Design Principles

### 1. Normalize Before Exposing

Bad:
- raw Gmail message payloads
- provider-native field names
- hidden ownership and routing assumptions

Good:
- stable thread/message/event/task/file objects
- normalized actor identities
- explicit `owning_account`, `source`, `labels`, `thread_summary`, `action_items`, `needs_reply`

### 2. Prefer Intent-Level Tools

Bad:
- generic search endpoints that force the model to guess syntax
- many low-level mutation tools

Good:
- tools that match user workflows directly

Examples:
- `find_recruiter_threads`
- `find_followups_due`
- `create_google_task`
- `preflight_google_write`
- `route_gmail_reply`
- `summarize_thread_actions`

### 3. Separate Read Shape From Write Policy

Reads should be broad and normalized.

Writes should be constrained:
- explicit destination
- explicit account
- policy checked
- confirmation gated when risky

### 4. Put Routing In Code, Not Prompts

The model should not be responsible for:
- guessing which Google account to use
- inferring which Drive folder is canonical
- deciding whether a task belongs in Apple Reminders or Google Tasks

Those choices should be encoded in connector policy and preflight rules.

## Target Normalized Objects

### Email Thread

```json
{
  "thread_id": "...",
  "owning_account": "jshah1331@gmail.com",
  "participants": ["..."],
  "subject": "...",
  "last_message_at": "...",
  "labels": ["Jobs", "NeedsReply"],
  "summary": "...",
  "action_items": ["..."],
  "needs_reply": true,
  "workflow": "job_hunt"
}
```

### Task

```json
{
  "task_id": "...",
  "source": "google_tasks",
  "owning_account": "jshah1331@gmail.com",
  "list_name": "Job Hunt",
  "title": "...",
  "due": "...",
  "status": "needs_action",
  "linked_message_id": "...",
  "workflow": "job_hunt"
}
```

### Calendar Event

```json
{
  "event_id": "...",
  "owning_account": "jshah1331@gmail.com",
  "calendar_name": "Deadlines",
  "title": "...",
  "start": "...",
  "end": "...",
  "kind": "interview",
  "participants": ["..."],
  "workflow": "job_hunt"
}
```

### Drive Object

```json
{
  "file_id": "...",
  "owning_account": "jshah1331@gmail.com",
  "kind": "file",
  "name": "...",
  "folder_path": ["Job Hunt", "Applications"],
  "url": "...",
  "workflow": "job_hunt"
}
```

## Target Workflow Tools

These are the tools the model should prefer over raw CRUD.

### Cross-workflow

- `preflight_google_write(kind, account?, destination?, sharing?, naming?)`
- `resolve_source_of_truth(workflow)`
- `list_workflow_rules()`
- `explain_routing_decision(object_id or draft_action)`

### Email

- `find_threads(query, workflow?, account?, labels?, date_range?)`
- `summarize_thread(thread_id, include_actions=true)`
- `find_threads_needing_reply(workflow?, days_stale?)`
- `draft_reply_for_thread(thread_id, tone?, objective?)`
- `send_reply_for_thread(thread_id, body, confirm=true)`

### Tasks

- `create_google_task(title, workflow?, due?, notes?, account?)`
- `find_tasks(workflow?, status?, due_before?)`
- `link_task_to_thread(task_id, thread_id)`

### Calendar

- `create_workflow_event(kind, title, workflow?, start, end, account?)`
- `find_schedule_conflicts(workflow?, date_range?)`

### Drive

- `create_workflow_folder(workflow, parent?)`
- `move_object_to_workflow(file_id, workflow, destination?)`

## Phased Implementation

## Phase 0: Stabilize Runtime

Goal:
- make Inbox reliably reachable from local MCP clients and dev worktrees

Tasks:
- standardize primary vs dev backend routing
- ensure MCP config always passes `INBOX_SERVER_TOKEN`
- document the local testing split clearly
- add a startup/self-check endpoint for auth and connector health

Success criteria:
- no more silent primary-vs-dev confusion
- no more MCP `Unauthorized` surprises caused by missing backend token env

## Phase 1: Enforce Source-Of-Truth Writes

Goal:
- stop cross-account mistakes

Tasks:
- centralize account selection helper in backend
- enforce `INBOX_DEFAULT_GOOGLE_ACCOUNT` for Google writes
- add `owning_account` to all returned Google objects
- fix Gmail reply routing to use the message-owning account

Success criteria:
- all Google writes default to `jshah1331@gmail.com`
- reply routing is deterministic and correct

## Phase 2: Preflight Layer

Goal:
- make writes inspectable before execution

Tasks:
- add a `preflight_google_write` internal helper + tool
- validate destination folder/list/calendar before write
- validate naming convention
- validate sharing targets

Success criteria:
- write actions can explain where and why they are going
- the model no longer has to guess write targets

## Phase 3: Data Normalization

Goal:
- reduce prompt burden and make retrieval cleaner

Tasks:
- define stable normalized schemas for threads, tasks, events, and files
- add summaries and action-item extraction to thread results
- add workflow tagging (`job_hunt`, `legal`, `medical`, `finance`, `personal_admin`)
- ensure every object includes source and owning account metadata

Success criteria:
- workflow-oriented retrieval works without hand-written Gmail query tricks
- thread summaries are useful without opening full bodies first

## Phase 4: First-Class Workflow Tools

Goal:
- make the connector feel like a real assistant platform

Tasks:
- add intent-level tools for job hunt, legal, medical, and finance
- build thread-to-task and thread-to-calendar linking
- support “find what needs action” across sources

Success criteria:
- the model can complete common workflows using a small set of tools
- less prompt steering is needed to get reliable behavior

## Phase 5: Ranking, Summaries, and Cleaner Context

Goal:
- make model inputs smaller and more useful

Tasks:
- rank threads by freshness, importance, and pending action
- dedupe redundant search results
- add compact thread briefs
- add optional rich summaries for recruiter and deadline workflows

Success criteria:
- better output quality with less context
- fewer raw blob reads

## Immediate Execution Order

This is the right order to actually build it:

1. Runtime stability and MCP auth sanity
2. Google write-default enforcement
3. Gmail reply routing fix
4. Preflight write layer
5. Normalized read objects
6. Workflow-specific tools
7. Ranking and summarization improvements

## What To Build Next

The next concrete coding milestone should be:

### Milestone A

Backend account policy and reply routing.

Deliverables:
- one backend helper for preferred Google account resolution
- all Google write endpoints use it
- Gmail reply/send paths use the owning account
- returned objects expose `owning_account`

### Milestone B

Write preflight framework.

Deliverables:
- preflight object schema
- shared backend preflight helper
- initial coverage for Drive folders, Tasks, and Calendar events

### Milestone C

Normalized thread summaries for Gmail.

Deliverables:
- normalized thread shape
- extracted action items
- pending-reply detection
- workflow tag hooks

## Operating Rules For Agents

Until the implementation is complete, agents should follow these rules:
- prefer Inbox MCP over direct provider tools when possible
- assume Google writes belong in `jshah1331@gmail.com`
- verify `INBOX_SERVER_URL` before testing a dev worktree
- do not rely on prompt-only account routing
- prefer normalized summaries over raw message bodies when available

## Definition Of Done

Inbox is "first-class" when:
- the model mostly calls high-level workflow tools
- account routing is deterministic in code
- write targets are inspectable before mutation
- dev vs primary routing is explicit
- connector outputs are compact, typed, and workflow-aware

---

## Future: No-API Connectors via browser-harness

**Watching:** https://github.com/browser-use/browser-harness

Self-healing LLM browser harness built on raw CDP WebSocket (~592 lines Python). Agent writes missing `helpers.py` functions mid-task — no Playwright/Selenium layer.

**Why relevant:** LinkedIn messages, Twitter DMs, and similar sources have no public API. browser-harness would let inbox add these as connectors without scraping fragility of traditional automation.

**Highest-value target:** LinkedIn recruiter thread connector — natural fit given career-ops work.

**Status (2026-04-18):** Brand new (created 2026-04-17). Watch for stability before integrating. Revisit when inbox needs a no-API source connector.
