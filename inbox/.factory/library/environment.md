# Environment

Environment variables, external dependencies, and setup notes.

**What belongs here:** Required env vars, external API keys/services, dependency quirks, platform-specific notes.
**What does NOT belong here:** Service ports/commands (use `.factory/services.yaml`).

---

## Python Environment

- Python 3.12+ required (currently 3.14.3 installed)
- Package manager: `uv` (lockfile: `uv.lock`)
- Virtual environment managed by `uv`

## External Dependencies

- **Google OAuth**: `credentials.json` in project root (never commit). Tokens stored in `tokens/` directory.
- **GitHub Token**: `github_token.txt` in project root (never commit). Needs `notifications` + `repo` scopes.
- **macOS Databases**: Read-only access to iMessage, Notes, Reminders, AddressBook SQLite databases. Requires Full Disk Access permission.
- **Obsidian Vault**: `~/vault/daily/` for ambient notes.
- **MLX Models**: Downloaded from HuggingFace Hub to `~/.cache/huggingface/`. Qwen3.5-0.8B-MLX-4bit and Qwen2.5-3B-MLX.

## Platform Notes

- macOS-only features: Desktop notifications (pyobjc), AppleScript (Notes/Reminders mutations), audio capture (sounddevice), keyboard injection (pyobjc CGEvent)
- These features must gracefully degrade on non-macOS platforms

## Config Directory

- `~/.config/inbox/` stores user preferences:
  - `notifications.json` — notification rules, quiet hours
  - `preferences.json` — theme, poll interval
  - `keybindings.json` — custom key mappings
  - `favorites.json` — favorited contacts
