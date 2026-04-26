# Voice Pipeline Implementation Notes

## Overview
Four sub-features shipped under `voice-pipeline` milestone:
1. **voice-ambient-core** — availability checks, rolling transcript buffer, `/ambient/transcript`
2. **voice-extraction-notes** — extraction already existed; added search filter to `/ambient/notes`
3. **voice-dictation** — added `GET /dictation/status` endpoint and client method
4. **voice-actions-autostart** — voice config file, `/voice/config` CRUD, ambient autostart on boot, `route_voice_command`

## Key Components

### services.py additions
- `mlx_whisper_available()` / `sounddevice_available()` — importlib.util.find_spec checks
- `ambient_available() -> tuple[bool, str]` — composite check, returns reason on failure
- `AmbientService.get_transcript(max_segments)` — thread-safe rolling buffer read
- `TRANSCRIPT_MAXLEN = 200` — cap on rolling transcript segments
- `load_voice_config() / save_voice_config()` — JSON config at `~/.config/inbox/voice.json`
- `_VOICE_CONFIG_DEFAULTS` — `{ambient_autostart: True, dictation_hotkey: "f5", vault_dir: "~/vault"}`
- `route_voice_command(text) -> bool` — dispatches to registered handlers
- `register_voice_command_handler(handler)` — registers a callable

### inbox_server.py additions
- `VoiceConfigRequest` pydantic model
- `GET /ambient/transcript?limit=50` — returns `{segments: [...], count: N}`
- `GET /ambient/status` — extended with `available` and `reason` fields
- `GET /dictation/status` — returns `{running, available}`
- `GET /voice/config` — returns merged config
- `PUT /voice/config` — partial update (model_dump(exclude_none=True) + merge)
- Lifespan: reads voice config, calls `ambient_available()`, conditionally starts ambient

### inbox_client.py additions
- `dictation_status()` → `GET /dictation/status`
- `ambient_transcript(limit)` → `GET /ambient/transcript`
- `voice_config()` → `GET /voice/config`
- `voice_config_update(**kwargs)` → `PUT /voice/config`

## Test Harness Considerations
- `test_server.py` replaces `state.ambient` with `MagicMock` permanently — subsequent fixtures must reset it
- Fixtures in `test_server_endpoints.py` and `test_voice_pipeline.py` now create fresh `AmbientService` instances
- Autostart tests mock `state.ambient.start` with `patch.object` to avoid spawning real threads with mock deps
- `ambient_available()` is patched at `inbox_server.ambient_available` level in endpoint tests

## Config File
`~/.config/inbox/voice.json` — created on first `save_voice_config()` call. Missing keys are merged with defaults on load. Corrupt files fall back to defaults silently.
