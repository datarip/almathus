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
2. **3-2-1-GO!** countdown, each number on a beep of the sample.
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
  dips below a value the kid actually scored. Days that recorded a score also
  carry a small **note tag** over their dot — `18/20`, right answers over cards
  asked — which steps up out of the way when two tags would collide. Like the
  point it sits on, the tag **averages** the day rather than totalling it: three
  sessions of 20 cards read `16/20`, not `47/60`. Mixed decks average their
  ratios and are written over the deck played most that day (the smaller one on
  a tie), so a 20- and a 30-card session still read out of 20;
- the **table** of every session, newest first, with a *note* column (`18/20`)
  and a *temps moyen / carte* column;
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
time. The app only ever appends — the one exception is the header line, which
grows when a column it knows about is missing from your file (see below).

### Header definition

Canonical French header (written when `LANG = "fr"`):

```csv
date,famille,temps,cartes,note,temps_moyen_par_carte
2026-06-16,-1,0:31,,,
2026-07-28,-2 et -3,1:35.4,20,18,4.8
```

Canonical English header (written when `LANG = "en"`):

```csv
date,family,time,cards,grade,average_time_per_card
```

| field | 🇫🇷 header | 🇬🇧 header | meaning |
|---|---|---|---|
| date | `date` | `date` | day the session was played |
| famille | `famille` | `family` | which family was drilled — `-3`, `-1 et -2`, `x7` |
| temps | `temps` | `time` | total time of the whole session |
| cartes | `cartes` | `cards` | how many cards were asked |
| note | `note` | `grade` | cards answered right on the first try |
| temps moyen | `temps_moyen_par_carte` | `average_time_per_card` | `temps / cartes` |

**Either language is read**, in any column order. Header matching ignores case,
accents, spaces and underscores (`Temps`, `temps`, `Temps moyen par carte` and
`temps_moyen` all work), and a few synonyms are accepted — see `ALIASES` in
`results.py`. Columns the app doesn't know (a `commentaire` column of your own)
are left untouched when it appends. A column it *does* know but your file lacks
— `note` in a file written before that column existed — is added to the end of
the header the first time there is something to write in it; every existing row
keeps its cells.

### Editing it by hand

| column | what you can write |
|---|---|
| **date** | `2026-06-16`, `16/06/2026`, or just `16/06`. **Leave it empty to repeat the row above** — exactly like the blank cells on the paper sheet. |
| **famille** | free text. The app writes the operator sign then the numbers that were switched on: `-3`, `-1 et -2`, `x7`, `+1–10`. Signs are ASCII (`+ - x /`) so the column stays easy to type. |
| **temps** | `1:23` · `1:23.4` · `1'23` · `1'23"4` · `83"` · `83` · `83.4` · `1,23` · `2,01,95`. A comma followed by exactly two digits below 60 is the minute separator (`1,29` = 1 min 29 s); otherwise it's a decimal point (`32,60` = 32.6 s). **Use a dot when you mean a decimal.** |
| **cartes** | an integer, or empty when unknown (the old paper rows). |
| **note** | how many cards were right on the first try, as a bare number (`15`) — the denominator is the `cartes` cell beside it. A hand-written `15/20` is read too, keeping only the numerator. Screen, chart and PDF all show it as `15/20`. |
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

```
mic audio ─▶ [VAD record] ─▶ [is it a voice?] ─▶ Vosk ─▶ [numbers only?] ─▶ [sure?] ─▶ submit
             (voice.py)       level + pitch     ↘ parakeet ↗  strict rule    confirm
```

Each stage can only ever say "not an answer", in which case the card stays up and
the app keeps listening. **Nothing takes a card off the screen but an answer.**

1. **Record.** Voice-activity detection records from first speech until ~0.35 s of
   silence (or a ~6 s cap), so you don't press anything. Waiting for speech has no
   deadline — think for as long as you like.
2. **Was that a voice, said out loud?** The captured audio is judged on *voicing*
   — the vocal-fold pitch that speech has and noise, taps and unvoiced whispers do
   not — and then on level and signal-to-noise. The first keeps a chair or a cough
   out of the recognizer; the second two are the **whisper gate**. A murmured guess
   is a real voice by every acoustic measure there is, so only loudness separates
   it from an answer, and a whisper must never score a card: the child would lose
   the question without having decided to answer it. A voice that was heard but
   was too quiet is told so — *“🔊 Plus fort !”* — rather than silently dropped.
   Calibrate the two thresholds for your own mic and room with
   `uv run python mic_check.py`, which prints these measurements and the verdict.
3. **Recognize.** **Vosk** with a vocabulary restricted to number words is the
   primary recognizer (~100 ms, and the accurate one for isolated digits like the
   French "un"). **parakeet** (`parakeet-mlx`, default model
   `mlx-community/parakeet-tdt-0.6b-v3`, multilingual) is the recognizer for
   languages with no Vosk model — English out of the box — and an opt-in backup
   otherwise.
4. **Numbers only.** The transcript is accepted **only if every word in it is a
   number** (`voice.strict_numbers`). One stray word — a filler, an operator, a
   recognizer's `[unk]` — and the whole utterance is refused. Vosk is read the same
   strict way: its winning hypothesis must be made of number words alone *and*
   score within `ALMATHUS_VOICE_ALT_GAP` of its best hypothesis overall, and a
   second decode by the full un-constrained model can veto it.
5. **Answer.** A number that gets this far *is* the answer, exactly as if ✓ had
   been pressed — right or wrong. A wrong one plays the wrong-answer sound, shows
   the correct result, and puts the question in the reinforcement queue, where it
   is asked **once more at the end of the game** and not again after that. Nothing
   ever has to be repeated to confirm it.

This is the part that was wrong before. The parser used to pick the numbers out of
whatever it was handed and drop the rest, so a kid reading the question aloud
("quinze moins quatre") answered `4`, and "je sais pas" — which a number-grammar
recognizer hears as "quinze seize [unk]" — answered `16`. Measured over a 68-clip
French corpus, **19 of the 30 non-number utterances were scored as an answer**;
under the strict rule, 3 are (the one-syllable fillers "euh", "non", "oh non").
33 of 38 spoken numbers are still recognized. English, through parakeet, scores
33/33 numbers with no false accepts.

**Vosk models** live in `./models`. French:

```bash
curl -L -o /tmp/vosk-fr.zip https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip
unzip /tmp/vosk-fr.zip -d models/
# override location with ALMATHUS_VOSK_MODEL=/path/to/model
```

With no Vosk model for the language, parakeet is loaded instead and handles
everything (this is the English path — the model name just has to contain the
language code, e.g. `vosk-model-small-en-us-0.15`, for Vosk to be picked up).

Tuning, if a room needs it — all optional, all with measured defaults:

| Variable | Default | What it does |
|---|---|---|
| `ALMATHUS_VOICE_ALT_GAP` | `0.5` | how far behind the top hypothesis an all-numbers one may score. Higher = more numbers recognized, more noise accepted |
| `ALMATHUS_VOICE_VETO_CONF` | `0.9` | how sure the un-constrained model must be before it overrules the number grammar |
| `ALMATHUS_VOICE_MIN_LEVEL` | `220` | absolute loudness floor for "said out loud" — the whisper gate |
| `ALMATHUS_VOICE_MIN_SNR` | `5.0` | how far a voice must stand above the room — the whisper gate |
| `ALMATHUS_VOICE_MIN_VOICED` | `0.25` | fraction of frames that must carry vocal-fold pitch |

Enable it:

```bash
uv sync --extra voice        # sounddevice + numpy + vosk + parakeet-mlx + webrtcvad
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
- **why an utterance was refused** — how far behind the top hypothesis the
  all-numbers one scored, or what the un-constrained model heard instead;
- model load and warmup failures that are otherwise swallowed silently.

Each capture is also copied to `/tmp/almathus_capture.wav`, so you can listen back
to exactly what the recognizer heard.

### ✅ Tests

```bash
uv run python test_voice.py       # what counts as a spoken answer (no mic, no model)
uv run python test_game_flow.py   # a card leaves the screen only when answered
```

`test_game_flow.py` opens a Tk window briefly and stubs the microphone out. Its
`test_voice_flow` is the regression test for the skipping card: refused
utterances arrive from the recognizer over a long think and not one of them may
move the card, and the mic must never go deaf while it is up.
`test_voice_wrong_answer` holds the other half — a number that *is* recognized
scores the card at once, wrong included, and that card comes back exactly once,
at the end.

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
