# 🧮 Almathus

A kid-friendly mental-math game — additions, subtractions, multiplications and
divisions of small positive integers, with big colorful numbers, a live
stopwatch, and per-card timing so a parent can see which operations need work.

Pure Python + Tkinter (no external dependencies), so it runs anywhere Python
has Tk.

## Run

```bash
uv run almathus
# or
uv run python main.py
# or, without uv:
python3 main.py
```

## How it plays

1. **Settings** — pick how many cards (default **20**) and tap one of four big
   mode buttons: **Addition · Soustraction · Multiplication · Division**.
2. **5-4-3-2-1-GO!** countdown.
3. **Cards** — each operation is shown in big numbers (e.g. `8 − 2`). Answer with
   the keyboard (Enter to validate) or the on-screen number pad.
4. **Reinforcement round** — every card missed on the first try comes back at the
   end to be retried at least once. 🔁
5. **Recap** — score, total time, average time/card, an encouraging emoji, and the
   5 slowest operations so you know what to practise.

## Timing & progress tracking

- A **total stopwatch** counts up (bottom-right) and a **per-card timer** shows how
  long the current card is taking (bottom-left).
- Each session is saved to `stats/<timestamp>-<mode>.json`, including per-card
  time, attempts, and correctness — handy for spotting the operations your kid
  struggles with over time.

## Ranges

- Addition / subtraction: operands **0–20** (subtraction never goes negative).
- Multiplication / division: factors **0–10** (division always divides evenly).

Tune these in the `RANGES` dict at the top of `main.py`.

## Voice 🎙️

Handled by `voice.py`, split into two independent pieces that each degrade to a
no-op if unavailable — the game always stays fully playable with the keypad.

### 🔊 Spoken encouragement (works out of the box on macOS)

On the recap screen a warm feminine voice reads the same message shown on screen
(e.g. *“Bravo ! Tu as eu 18 sur 20.”*). Uses the built-in macOS `say` command with
the **Amélie** French voice — nothing to install, fully offline.

- Pick another voice: `export ALMATHUS_TTS_VOICE="Audrey"` (add voices in
  *System Settings → Accessibility → Spoken Content → System Voice → Manage Voices*).
- Off macOS it falls back to `pyttsx3` if installed.

### 🎤 Answer by voice — the two-stage pipeline

When enabled (toggle on the settings screen), voice input runs **continuously and
in parallel with the keypad** — there is no mic button. Each card is read aloud,
then the app keeps listening and re-arming until the card changes. A spoken number
is auto-filled and auto-submitted; the keypad stays fully usable the whole time.

Two different libraries do two different jobs — they are **complementary, not
alternatives**:

```
mic audio ──▶ [VAD record] ──▶ parakeet ──▶ text2num ──▶ integer ──▶ auto-submit
              (voice.py)       speech→text   words→number
```

1. **parakeet** (`parakeet-mlx`) is the **speech-to-text** — it turns the recorded
   audio into a raw transcript string, e.g. `"vingt-deux."`. Runs locally; the
   default model is `mlx-community/parakeet-tdt-0.6b-v3` (**multilingual**, incl.
   French — note `v2` is English-only).
2. **text2num** (imported as `text_to_num`) is the **words→number** step — it turns
   parakeet's transcript into an `int`, e.g. `"vingt-deux."` → `22`. This is what
   absorbs the trailing `". "` parakeet adds and any filler words. A small built-in
   parser is the fallback if `text2num` isn't installed.

Voice-activity detection records from first speech until ~0.7 s of silence (or a
~6 s cap), so you don't press anything.

**Number-grammar fallback (Vosk).** parakeet is a general recognizer and mis-hears
some isolated single words — notably the French **"un"** (heard as "Mm"/"On"). When
parakeet returns no number, the audio is re-run through **Vosk** with a grammar
restricted to number words, which reliably recovers "un" → 1 (and rescues other
mis-hears like "sept" heard as "Set"). Vosk returns `[unk]` for non-numbers, so it
doesn't force a number onto hesitations. It needs a model in `./models`:

```bash
curl -L -o /tmp/vosk-fr.zip https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip
unzip /tmp/vosk-fr.zip -d models/
# override location with ALMATHUS_VOSK_MODEL=/path/to/model
```

If the model is absent, the fallback is silently disabled (parakeet still handles
everything except the hardest single words).

Enable it:

```bash
uv sync --extra voice        # sounddevice + numpy + text2num + parakeet-mlx + vosk
```

- First run downloads the parakeet model (~600 MB). **macOS microphone permission
  is required** — grant your terminal app access under *System Settings → Privacy &
  Security → Microphone*. If capture is silent, the card shows a permission hint.
- Not on Apple Silicon, or already have a parakeet CLI? Skip `parakeet-mlx` and
  point the app at your command:
  `export ALMATHUS_PARAKEET_CMD='my-parakeet {wav}'` (its stdout is the transcript).
- Override the STT model: `export ALMATHUS_PARAKEET_MODEL="mlx-community/parakeet-tdt-0.6b-v3"`.

Both TTS and STT degrade to no-ops if unavailable — the game stays fully playable
with the keypad, and voice input layers on top when it's ready.

## Language

French by default. Set `LANG = "en"` near the top of `main.py` for English
(TTS voice, spoken phrases, and the number parser all follow). A settings-screen
toggle is a good next iteration.

## Roadmap / next iterations

- Language toggle on the settings screen.
- A small dashboard that reads `stats/*.json` and charts progress per operation.
- Tune VAD thresholds / timeouts in `voice.py` if your room is noisy.
