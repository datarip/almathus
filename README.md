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

## 👑 Girly mode

A button in the settings footer (*Mode princesse*) flips the whole app from the
dark blue palette to a peach-pink one, and swaps the Mario next to the title for
Peach. It is purely cosmetic — nothing about the game changes — and the choice
is remembered in `prefs.json` between runs.

Both palettes are the `THEMES` dict at the top of `main.py`; add a third one
there and it needs nothing else but a way to select it. Each names its drawing
in `assets/` (`mario.svg`, `peach.svg`) and an emoji to fall back on if that
file cannot be rendered. They are SVG so Tk 9 can rasterize them straight to the
title's height — a PNG would only scale in whole steps.

## Timing & progress tracking

- A **total stopwatch** counts up (bottom-right) and a **per-card timer** shows how
  long the current card is taking (bottom-left).
- Each session is saved twice:
  - `results/results.csv` — one summary line per session, hand-editable, and the
    file the **progress screen** reads (see below);
  - `stats/<timestamp>-<mode>.json` — the full per-card detail (time, attempts,
    correctness), for finer analysis.

## 📈 Progress screen

Reachable from the **settings screen** (*Mes progrès*) and from the **recap** after
a game. It reads `results/results.csv` and shows:

- a **chart** of time over the dates — **one point per day**, averaging that
  day's sessions (three tries in an afternoon used to stack into a vertical
  spike that said nothing about progress). x is real calendar time, so gaps and
  plateaus are visible. Hover a dot for its date, families, session count and
  average. The curve is a monotone cubic through the points: soft, but it never
  dips below a value the kid actually scored;
- the **table** of every session, newest first, with a *temps moyen / carte*
  column;
- a **📄 PDF** button in the top-right corner (hover it for the *Exporter en PDF*
  bubble). It asks where to save, then writes an **A4** report — the same chart,
  redrawn as vectors, and the whole table flowing over as many pages as it
  needs, each one repeating the header. The paper uses **the palette currently on
  screen**, so an export made in girly mode comes out pink. Written by
  `pdfreport.py`, which builds the file by hand — no print library, no new
  dependency.

## The results log — `results/results.csv`

The digital continuation of the paper *Calcul mental* sheet (Date / Famille /
Temps), plus the average column. **It is the database**: every finished session is
appended at the bottom, and anything you edit by hand is what the app shows next
time. The app only ever appends — it never rewrites what you typed.

### Header definition

Canonical French header (written when `LANG = "fr"`):

```csv
date,famille,temps,cartes,temps_moyen_par_carte
2026-06-16,-1,0:31,,
2026-07-28,-2 et -3,1:35.4,20,4.8
```

Canonical English header (written when `LANG = "en"`):

```csv
date,family,time,cards,average_time_per_card
```

| field | 🇫🇷 header | 🇬🇧 header | meaning |
|---|---|---|---|
| date | `date` | `date` | day the session was played |
| famille | `famille` | `family` | which family was drilled — `-3`, `-1 et -2`, `x7` |
| temps | `temps` | `time` | total time of the whole session |
| cartes | `cartes` | `cards` | how many cards were asked |
| temps moyen | `temps_moyen_par_carte` | `average_time_per_card` | `temps / cartes` |

**Either language is read**, in any column order. Header matching ignores case,
accents, spaces and underscores (`Temps`, `temps`, `Temps moyen par carte` and
`temps_moyen` all work), and a few synonyms are accepted — see `ALIASES` in
`results.py`. Columns the app doesn't know (a `notes` column of your own) are
left untouched when it appends.

### Editing it by hand

| column | what you can write |
|---|---|
| **date** | `2026-06-16`, `16/06/2026`, or just `16/06`. **Leave it empty to repeat the row above** — exactly like the blank cells on the paper sheet. |
| **famille** | free text. The app writes the operator sign then the numbers that were switched on: `-3`, `-1 et -2`, `x7`, `+1–10`. Signs are ASCII (`+ - x /`) so the column stays easy to type. |
| **temps** | `1:23` · `1:23.4` · `1'23` · `1'23"4` · `83"` · `83` · `83.4` · `1,23` · `2,01,95`. A comma followed by exactly two digits below 60 is the minute separator (`1,29` = 1 min 29 s); otherwise it's a decimal point (`32,60` = 32.6 s). **Use a dot when you mean a decimal.** |
| **cartes** | an integer, or empty when unknown (the old paper rows). |
| **temps moyen** | plain seconds. Leave it empty and it's computed as `temps / cartes`; fill it in for rows where `cartes` is unknown. |

Rows that can't be read at all are skipped, never rewritten.

> The *temps moyen / carte* here is **not** the average on the recap screen. The
> recap averages the time spent thinking on each card; this column divides the
> whole session — answer pauses and the reinforcement round included — by the card
> count, so it always matches the two columns beside it.

The file ships seeded with the sessions from the paper sheet (16/06 → 12/07).
Blank date cells there were carried down from the row above, the way the sheet
reads.

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

### 🐛 Debugging the voice path — `ALMATHUS_VOICE_DEBUG`

An **environment variable**, not a command-line flag (the app takes no arguments).
Set it to anything other than empty or `0` and `voice.py` traces the whole pipeline
to stderr, every line prefixed with `[voice]`:

```bash
ALMATHUS_VOICE_DEBUG=1 uv run almathus
```

What you get:

- capture setup — sample rate, block size, which detector is active
  (`webrtcvad` or the energy fallback), and a warning when the device opens at a
  different rate than requested;
- why a recording ended — no speech before the pre-timeout, no sustained speech,
  or a silent/failed capture — each with the measured peak level;
- recognition results — the raw Vosk alternatives and the parakeet transcript,
  with the parsed number and Vosk's elapsed time in ms;
- model load and warmup failures that are otherwise swallowed silently.

Each capture is also copied to `/tmp/almathus_capture.wav`, so you can listen back
to exactly what the recognizer heard.

Nothing outside `voice.py` reads the variable — the keypad, timing, and results
log behave identically whether it is set or not.

## Language

French by default. Set `LANG = "en"` near the top of `main.py` for English
(TTS voice, spoken phrases, and the number parser all follow). A settings-screen
toggle is a good next iteration.

## Roadmap / next iterations

- Language toggle on the settings screen.
- Per-operation breakdown on the progress screen, from the richer `stats/*.json`
  (which facts are still slow, not just the session total).
- Tune VAD thresholds / timeouts in `voice.py` if your room is noisy.
