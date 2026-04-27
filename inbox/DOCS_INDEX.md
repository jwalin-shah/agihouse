# Documentation Index

Complete guide to inbox documentation. Start here to find what you need.

## 📖 Getting Started

**New to inbox?**
- **[README.md](README.md)** — Overview, features, quick start, key bindings
- **[CLAUDE.md](CLAUDE.md)** — Detailed project context, architecture, all systems
- **[CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md)** — Connector architecture direction, normalization plan, phased execution

## 🔌 Server API

**Building with the API?**
- **[README.md](README.md#api-reference)** — Quick endpoint reference
- **[CLAUDE.md](CLAUDE.md#api-endpoints-localhost9849)** — Full endpoint list (all systems)
- System-specific docs:
  - Gmail: See CLAUDE.md
  - Calendar: See CLAUDE.md
  - Drive: See CLAUDE.md

**Multi-account?**
- See [CLAUDE.md](CLAUDE.md#multi-account-google) for OAuth setup

## 🛠️ Development

**Contributing or modifying?**
- **[CLAUDE.md](CLAUDE.md)** — Architecture, key design decisions, data sources
- **[CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md)** — What to build next for first-class connectors and cleaner model-facing data
- Dev commands:
  ```bash
  uv run ruff check --fix .  # Lint
  uv run pyright             # Type check
  uv run pytest              # Tests
  ```

## 📋 Documentation Files

### Core
| File | Purpose |
|------|---------|
| [README.md](README.md) | Project overview, quick start, features, key bindings |
| [CLAUDE.md](CLAUDE.md) | Complete project context, architecture, all endpoints, all systems |
| [CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md) | Connector strategy, phased implementation, source-of-truth rules |
| [DOCS_INDEX.md](DOCS_INDEX.md) | This file — documentation navigation |

### Other
| File | Purpose |
|------|---------|
| [MCP_V1_PLAN.md](MCP_V1_PLAN.md) | MCP server planning (in progress) |
| [CONNECTOR_ROADMAP.md](CONNECTOR_ROADMAP.md) | First-class connector roadmap and model-facing data plan |

## 🚀 Quick Navigation

### I want to...

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
→ [CLAUDE.md](CLAUDE.md#key-design-decisions)

## 📊 Documentation Coverage

| System | Reference | Examples |
|--------|-----------|----------|
| **Gmail** | [CLAUDE.md](CLAUDE.md) | ✅ In API section |
| **Calendar** | [CLAUDE.md](CLAUDE.md) | ✅ In API section |
| **Drive** | [CLAUDE.md](CLAUDE.md) | ✅ In API section |
| **iMessage** | [CLAUDE.md](CLAUDE.md) | ✅ In API section |
| **Notes/Reminders** | [CLAUDE.md](CLAUDE.md) | ✅ In API section |
| **GitHub** | [CLAUDE.md](CLAUDE.md) | ✅ In API section |

## 🔧 Configuration

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

## 📚 External References

- [Google OAuth 2.0 Guide](https://developers.google.com/identity/protocols/oauth2)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Textual (TUI Framework)](https://textual.textualize.io/)
