# Piper sidecar

A separate program that owns the GPL voice stack (`piper-tts` and the espeak-ng
it links) so the game does not have to. The game starts it, sends text over
stdin and receives PCM over stdout; the two processes share no memory and the
game never imports Piper.

That boundary is the point: the app can be distributed under its own licence,
while this sidecar — and the engine inside it — travels under **GPL-3.0**. If
you ship a frozen copy, ship the corresponding source and build script with it.

## Running from a source checkout

```bash
uv sync --extra voice --extra sidecar --group dev
uv run python sidecar/piper_sidecar.py --model ~/path/to/fr_FR-siwis-medium.onnx
```

The game finds it automatically: a packaged app looks for a `piper-sidecar`
executable beside its own binary; a checkout falls back to running this script
with the current interpreter. `ALMATHUS_PIPER_SIDECAR=/path/to/piper-sidecar`
(or `.py`) overrides both.

The model (`.onnx` + `.onnx.json`) is downloaded by the game's language setup
into the per-user voices folder — the sidecar only loads the path it is given.
Multi-speaker voices take `--speaker <name>` (the game passes it from
`voices.py`).

## Protocol

UTF-8 JSON lines on stdin; JSON header plus raw PCM on stdout.

```text
{"text": "sept fois quatre", "length_scale": 1.0}
→ {"sample_rate": 22050, "n": 28160}\n
  28160 bytes of little-endian int16 mono PCM
```

`{"cmd": "ping"}` → `{"pong": true}`. `{"cmd": "quit"}` exits. Errors are
answered with `{"error": "..."}` and leave the process alive, so the game can
fall back for one phrase instead of losing the warm model. On startup the
sidecar sends `{"ready": true}` once the model is loaded; the game waits for it
in the background.

## Packaging

Freeze it as a **one-directory** build (not one-file: a self-extracting binary
pays its unpack cost on every launch) and place the result beside the app:

```bash
pyinstaller --onedir --name piper-sidecar sidecar/piper_sidecar.py
```

- macOS: `Contents/MacOS/piper-sidecar`, signed and notarised with the app.
- Windows: `piper-sidecar.exe` beside `Almathus.exe`.
- Linux: beside the app in `/opt/almathus` or inside the AppImage.

Include the GPL-3.0 text, the exact source for the frozen build (this directory
plus Piper and espeak-ng, or a written offer), and the voice models' own
licences. The app's wheel does not contain any of this.
