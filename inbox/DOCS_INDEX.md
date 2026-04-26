# Documentation Index

Complete guide to inbox documentation. Start here to find what you need.

## 📖 Getting Started

**New to inbox?**
- **[README.md](README.md)** — Overview, features, quick start, key bindings
- **[CLAUDE.md](CLAUDE.md)** — Detailed project context, architecture, all systems
- **[CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md)** — Connector architecture direction, normalization plan, phased execution

## 🎯 Google Sheets (New!)

**Want to use Sheets?**
- **[SHEETS_QUICKSTART.md](SHEETS_QUICKSTART.md)** — 30-second examples, 10 common patterns, cheat sheet
- **[SHEETS.md](SHEETS.md)** — Full API reference, curl examples, request/response formats, troubleshooting
- **[SHEETS_CHANGELOG.md](SHEETS_CHANGELOG.md)** — What's new, migration guide, re-auth requirements, design notes

## 🔌 Server API

**Building with the API?**
- **[README.md](README.md#api-reference)** — Quick endpoint reference
- **[CLAUDE.md](CLAUDE.md#api-endpoints-localhost9849)** — Full endpoint list (all systems)
- System-specific docs:
  - Sheets: [SHEETS.md](SHEETS.md)
  - Gmail: See CLAUDE.md
  - Calendar: See CLAUDE.md
  - Drive: See CLAUDE.md

**Multi-account?**
- See [SHEETS.md](SHEETS.md#multi-account) for Sheets example (applies to all)
- See [CLAUDE.md](CLAUDE.md#multi-account-google) for OAuth setup

## 🛠️ Development

**Contributing or modifying?**
- **[CLAUDE.md](CLAUDE.md)** — Architecture, key design decisions, data sources
- **[CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md)** — What to build next for first-class connectors and cleaner model-facing data
- **[SHEETS_CHANGELOG.md](SHEETS_CHANGELOG.md)** — Recent changes, implementation notes
- Dev commands:
  ```bash
  uv run ruff check --fix .  # Lint
  uv run pyright             # Type check
  uv run pytest              # Tests (736 pass)
  ```

## 📋 Documentation Files

### Core
| File | Purpose |
|------|---------|
| [README.md](README.md) | Project overview, quick start, features, key bindings |
| [CLAUDE.md](CLAUDE.md) | Complete project context, architecture, all endpoints, all systems |
| [CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md) | Connector strategy, phased implementation, source-of-truth rules |
| [DOCS_INDEX.md](DOCS_INDEX.md) | This file — documentation navigation |

### Google Sheets (New)
| File | Purpose |
|------|---------|
| [SHEETS_QUICKSTART.md](SHEETS_QUICKSTART.md) | 30-second examples, common patterns, cheat sheet |
| [SHEETS.md](SHEETS.md) | Full API reference, examples, error handling, performance |
| [SHEETS_CHANGELOG.md](SHEETS_CHANGELOG.md) | What's new, migration, design decisions, setup guide |

### Other
| File | Purpose |
|------|---------|
| [MCP_V1_PLAN.md](MCP_V1_PLAN.md) | MCP server planning (in progress) |
| [CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md) | First-class connector roadmap and model-facing data plan |

## 🚀 Quick Navigation

### I want to...

**...use Sheets from code**
→ [SHEETS_QUICKSTART.md](SHEETS_QUICKSTART.md) (examples in 30 seconds)

**...understand Sheets API**
→ [SHEETS.md](SHEETS.md) (full reference)

**...see all API endpoints**
→ [CLAUDE.md](CLAUDE.md#api-endpoints-localhost9849) (complete list)

**...set up a Google account**
→ [CLAUDE.md](CLAUDE.md#multi-account-google) (OAuth, tokens, re-auth)

**...understand the architecture**
→ [CLAUDE.md](CLAUDE.md#architecture) (services, data sources, design)

**...plan connector improvements**
→ [CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md) (phases, tool surface, normalization targets)

**...run the project**
→ [README.md](README.md#quick-start) (installation, setup)

**...debug/contribute**
→ [CLAUDE.md](CLAUDE.md#key-design-decisions) + [SHEETS_CHANGELOG.md](SHEETS_CHANGELOG.md#design-notes)

**...see what changed**
→ [SHEETS_CHANGELOG.md](SHEETS_CHANGELOG.md) (Sheets), [README.md](README.md) (overview)

## 📊 Documentation Coverage

| System | Reference | Examples | Quickstart | Changelog |
|--------|-----------|----------|-----------|-----------|
| **Sheets** | [SHEETS.md](SHEETS.md) | ✅ Many | [SHEETS_QUICKSTART.md](SHEETS_QUICKSTART.md) | [SHEETS_CHANGELOG.md](SHEETS_CHANGELOG.md) |
| **Gmail** | [CLAUDE.md](CLAUDE.md) | ✅ In API section | ❌ | ❌ |
| **Calendar** | [CLAUDE.md](CLAUDE.md) | ✅ In API section | ❌ | ❌ |
| **Drive** | [CLAUDE.md](CLAUDE.md) | ✅ In API section | ❌ | ❌ |
| **iMessage** | [CLAUDE.md](CLAUDE.md) | ✅ In API section | ❌ | ❌ |
| **Notes/Reminders** | [CLAUDE.md](CLAUDE.md) | ✅ In API section | ❌ | ❌ |
| **GitHub** | [CLAUDE.md](CLAUDE.md) | ✅ In API section | ❌ | ❌ |

## 🔧 Configuration

**Sheets re-auth needed?**
```bash
curl -X POST http://localhost:9849/accounts/reauth \
  -H 'Content-Type: application/json' \
  -d '{"email": "your-email@gmail.com"}'
```

**Server token auth?**
```bash
export INBOX_SERVER_TOKEN=your-token
uv run python inbox_server.py
```

**Check accounts?**
```bash
curl http://localhost:9849/accounts
curl http://localhost:9849/health
```

## 📝 Notes

- **Sheets is fully agent-accessible** — all operations available via API
- **TUI tab for Sheets** — coming later (agents can use API now)
- **Multi-account supported** — specify `account` param for all operations
- **Re-auth required once** — new `spreadsheets` OAuth scope
- **All 736 tests pass** — production-ready

## 🎓 Learning Paths

**Path 1: Agent Integration (5 min)**
1. [SHEETS_QUICKSTART.md](SHEETS_QUICKSTART.md) — Examples
2. [SHEETS.md](SHEETS.md) — API reference
3. Start building!

**Path 2: Full Understanding (30 min)**
1. [README.md](README.md) — Overview
2. [CLAUDE.md](CLAUDE.md) — Architecture
3. [CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md) — Connector direction and execution order
4. [SHEETS_CHANGELOG.md](SHEETS_CHANGELOG.md) — Design decisions
5. [SHEETS.md](SHEETS.md) — API details

**Path 3: Development (1 hour)**
1. [CLAUDE.md](CLAUDE.md) — Architecture, design
2. [CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md) — Prioritized connector work
3. [SHEETS_CHANGELOG.md](SHEETS_CHANGELOG.md) — What's new, code structure
4. Source code (services.py, inbox_server.py)
5. Tests (tests/)

## 📚 External References

- [Google Sheets API Docs](https://developers.google.com/sheets/api/reference/rest)
- [Google OAuth 2.0 Guide](https://developers.google.com/identity/protocols/oauth2)
- [A1 Notation Reference](https://developers.google.com/sheets/api/guides/concepts#a1_notation)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Textual (TUI Framework)](https://textual.textualize.io/)

---

**Last updated:** April 2026
**Sheets integration:** ✅ Complete, production-ready
**Total documentation:** 6 files (README, CLAUDE, 3 Sheets docs, this index)
