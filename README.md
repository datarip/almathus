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
dark blue palette to a warm pink one, and swaps the boy next to the title for
the girl. It is purely cosmetic — nothing about the game changes — and the choice
is remembered in `prefs.json` between runs.

Both palettes are the `THEMES` dict at the top of `main.py`; add a third one
there and it needs nothing else but a way to select it. Each names its drawing
in `assets/` (`boy.svg`, `girl.svg`) and an emoji to fall back on if that
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
  plateaus are visible. The curve is a monotone cubic through the points: soft,
  but it never dips below a value the kid actually scored. Days that recorded a
  score carry a **second curve of their own** — the grades — on a **right-hand
  axis** headed *Notes* / *Grades* — level with the chart's own title, both
  drawn inside the canvas so they stay aligned however the fonts scale — and
  running from nothing to a full deck
  (`0/20` … `20/20`). The two series share neither scale nor colour: times are
  the theme's accent, grades its `CHART_GRADE` (blue on the dark palette, deep
  teal on the pink one), each axis is written in the colour of the line it
  belongs to, and a **legend** in the header — beside the month filter, and on
  the PDF beside the chart's title — draws each curve small against its name.
  The grade key only appears while the month on show has a grade to draw. A day with no score is simply absent from
  the grade line, so the old paper rows leave a gap rather than reading as a
  zero. Hover either line for that day's date, families, session count, average
  and grade. Like the point it grades, a day's grade **averages** the day rather
  than totalling it: three sessions of 20 cards read `16/20`, not `47/60`. Mixed
  decks average their ratios and are written over the deck played most that day
  (the smaller one on a tie), so a 20- and a 30-card session still read out
  of 20;
- the **table** of every session, newest first, with a *note* column (`18/20`),
  a *temps moyen / carte* column and a *Difficultés* column — the five cards
  that session fought with;
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
date,famille,temps,cartes,note,temps_moyen_par_carte,difficultes
2026-06-16,-1,0:31,,,,
2026-07-28,-2 et -3,1:35.4,20,18,4.8,"17 - 4, 13 - 4, 9 - 4, 6 - 4, 20 - 4"
```

Canonical English header (written when `LANG = "en"`):

```csv
date,family,time,cards,grade,average_time_per_card,difficulty
```

| field | 🇫🇷 header | 🇬🇧 header | meaning |
|---|---|---|---|
| date | `date` | `date` | day the session was played |
| famille | `famille` | `family` | which family was drilled — `-3`, `-1 et -2`, `x7` |
| temps | `temps` | `time` | total time of the whole session |
| cartes | `cartes` | `cards` | how many cards were asked |
| note | `note` | `grade` | cards answered right on the first try |
| temps moyen | `temps_moyen_par_carte` | `average_time_per_card` | `temps / cartes` |
| difficultés | `difficultes` | `difficulty` | the five cards that session fought with |

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
| **difficultés** | the session's hardest cards, hardest first, comma separated: `18 - 4, 11 - 4, 13 - 4`. Same ASCII signs as `famille`. **The next session reads this cell back**, so writing a card in by hand is a way to say *drill this tomorrow*. |

Rows that can't be read at all are skipped, never rewritten.

### Difficulties, and the revision they drive

Two different things make a card hard, and the *Difficultés* column records both:
getting it **wrong**, and **labouring** over it. A finished session ranks its
cards — wrong ones first, then the slowest — and writes down the top five.

The clock behind that ranking counts **both sittings**. A card missed on the
first pass comes back in the reinforcement round at the end of the session, and
the time it really cost is the two goes added together — otherwise a card fumbled
in half a second would read as the fastest answer of the round.

The next session then **deals each of those cards twice**, so a fact that fought
back is met again before it is forgotten. It reads the difficulties of the **last
day that was played**, all of that day's sessions together, and only takes what
the round can actually ask — same operation, and a number that is switched on. So
switching from *−4* to *×2* leaves the *−4* revision waiting for the day you come
back to it, rather than dealing a card that doesn't belong.

Revision never takes more than **half the round** (five carried-over cards need a
deck of twenty), and the shuffle keeps the two copies of a card apart — asked
back to back it would only test the memory of the last answer, which is the
opposite of the point.

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

### 🔊 Dictation — the question read aloud (toggle on the settings screen)

**ON** (the default) each card is read aloud — *“sept fois quatre”* — before the
child answers: the mode the game was built for, for a child who is faster with
numbers than with the signs. The mic opens when the reading ends, so the machine
never records its own voice.

**OFF** the card is only shown, and nothing stands between showing it and playing
it: the mic opens with the card and the child can answer straight away.

**Neither mode is timed for the reading.** The card's clock starts when the
sentence ends and the session total is held across it, so a card is worth the
same time whichever way it was played — what is measured is a child thinking,
not a machine talking. A question costs about 2.2s of system voice, against card
times of 5 to 9, so counting it would have made the log a third a measure of the
speech rate: change the voice or its `rate` and every number would move without
the child doing anything differently. The keypad stays live under a reading, so
a card answered before its clock starts is worth zero, never less.

Sessions logged before 2026-08-19 were timed the old way; `tools/retime_dictation.py`
took the readings back out of them, card by card, so the progress chart is one
curve measured one way. The choice of mode is remembered between runs
(`prefs.json`).

### 🎤 Answer by voice — the two-stage pipeline

When enabled (toggle on the settings screen), voice input runs **continuously and
in parallel with the keypad** — there is no mic button. With dictation on the card
is read aloud first; then the app keeps listening and re-arming until the card
changes. A spoken number is auto-filled and auto-submitted; the keypad stays fully
usable the whole time.

```
                    ┌── the room, measured ──┐
                    ▼                        ▼
mic audio ─▶ [VAD record] ─▶ [is it a voice?] ─▶ [subtract the room] ─▶ Vosk ─▶ [numbers only?] ─▶ submit
             (voice.py)       level + pitch       spectral subtraction     per-language  strict rule
```

Each stage can only ever say "not an answer", in which case the card stays up and
the app keeps listening. **Nothing takes a card off the screen but an answer.**

0. **Listen to the room first.** A few hundred milliseconds after the app opens,
   before there is anything on screen to answer, it opens the mic for about two
   seconds and measures the noise around it — then closes it again. Nothing is
   shown and nothing is asked; it happens on its own thread and the menu does not
   wait for it. It is repeated when a game starts, in case the app has been open
   since this morning, and the measurement is kept current between answers from
   the seconds of silence that sit either side of them.

   This is what the next two steps are measured against. **Where you play changes
   what a spoken answer looks like**: at night in a bedroom the room reads near
   nothing, at noon on a terrace with a road behind it the same mic reads twenty
   times that, and a fixed threshold cannot be right for both — set for the quiet
   room it triggers on traffic, set for the terrace it goes deaf indoors. The
   thresholds are a *factor over the room*, so they move with it by themselves.
   It also fixes the distance problem: loudness at the mic falls with the square
   of the distance, so a child sat two metres back arrives ~15 dB down on the
   same child at arm's length, and a bar set from a close recording simply
   refuses them. Measured against a quiet room, that same voice stands well
   clear.
1. **Record.** Voice-activity detection records from first speech until ~0.35 s of
   silence (or a ~6 s cap), so you don't press anything. Waiting for speech has no
   deadline — think for as long as you like.
2. **Was that a voice, said out loud?** The captured audio is judged on *voicing*
   — the vocal-fold pitch that speech has and noise, taps and unvoiced whispers do
   not — and then on level and signal-to-noise, both measured against the room
   from step 0. The first keeps a chair or a cough out of the recognizer; the
   second two are the **whisper gate**. A murmured guess is a real voice by every
   acoustic measure there is, so only loudness separates it from an answer, and a
   whisper must never score a card: the child would lose the question without
   having decided to answer it. What "said out loud" means is *clearly above this
   room* — a murmur does not stand three times over the room at any distance,
   while a normal voice two metres away does. A voice that was heard but was too
   quiet is told so — *“🔊 Plus fort !”* — rather than silently dropped. See what
   your own mic and room measure with `uv run python mic_check.py`, which prints
   the room, the threshold it implies, and the verdict.
2b. **Take the room back out.** The room's average spectrum, measured in the
   silence just before the child spoke, is subtracted from the utterance bin by
   bin before it reaches the recognizer. A fan, a road or a café does not make
   speech quieter — it fills the gaps between the formants, which is what wrecks
   recognition and what a band-pass cannot touch. Deliberately mild (no bin is
   taken down more than ~18 dB) because over-subtracting leaves warbling
   artefacts that cost more than the noise did, and nothing is staked on it: if
   the cleaned audio says nothing, the untouched capture gets its own decode. In
   a room already quiet enough, it is skipped entirely.
3. **Recognize.** **Vosk** with a vocabulary restricted to number words is the
   recognizer (~100 ms, and the accurate one for isolated digits like the French
   "un"). There is one small model per language; the setup screen asks for the
   language on first run and downloads just that model (see *Languages* below).
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
33 of 38 spoken numbers are still recognized. English is handled the same way,
with its own Vosk model and its own number grammar.

**Languages and models.** On first run the app shows a language picker and
downloads only that language's small Vosk model (30–160 MB) into a per-user data
directory — `~/Library/Application Support/Almathus/models` on macOS,
`%LOCALAPPDATA%\Almathus\models` on Windows, `~/.local/share/almathus/models` on
Linux. Add or switch languages later from the settings screen or the language
chip in the top-right corner; an already-downloaded model is reused, and
switching takes effect at once. The registry (`languages.py`) lists 32 Vosk
languages; the six with no number spellings in `num2words` (Hindi, Gujarati,
Chinese, Georgian, Kyrgyz, Uzbek) run keypad-only. `ALMATHUS_VOSK_MODEL=/path/to/model`
bypasses the store; `ALMATHUS_MODELS_DIR` moves it.

Tuning, if a room needs it — all optional, all with measured defaults:

| Variable | Default | What it does |
|---|---|---|
| `ALMATHUS_VOICE_ALT_GAP` | `0.5` | how far behind the top hypothesis an all-numbers one may score. Higher = more numbers recognized, more noise accepted |
| `ALMATHUS_VOICE_VETO_CONF` | `0.9` | how sure the un-constrained model must be before it overrules the number grammar |
| `ALMATHUS_VOICE_MIN_SNR` | `3.0` | how far a voice must stand above the room — the whisper gate |
| `ALMATHUS_VOICE_MIN_VOICED` | `0.25` | fraction of frames that must carry vocal-fold pitch |
| `ALMATHUS_VOICE_LEVEL_OVER_ROOM` | `3.0` | how far over the *measured* room "said out loud" is. The whisper gate proper — raise it if noise scores cards, lower it if a voice from across the room is refused |
| `ALMATHUS_VOICE_MIN_LEVEL_FLOOR` | `45` | ...but never ask for less than this, so a near-silent room cannot leave the gate at nothing |
| `ALMATHUS_VOICE_MIN_LEVEL` | *(unset)* | a hard, absolute gate. Set it and nothing is measured — the escape hatch for a room where the automatic one reads wrong |
| `ALMATHUS_VOICE_CALIB_SECS` | `1.5` | how long the startup listen to the room lasts |
| `ALMATHUS_VOICE_DENOISE` | `auto` | `1` always subtract the room, `0` never, `auto` only when there is measurable noise to subtract |
| `ALMATHUS_VOICE_DENOISE_ALPHA` | `1.5` | how hard to subtract it. Higher removes more noise and risks warbling artefacts |

Enable it:

```bash
uv sync --extra voice --group dev   # sounddevice + numpy + num2words + vosk + webrtcvad
                                    # keep --extra voice on every sync: without it
                                    # uv prunes the audio libs and the mic goes dead
```

- First run asks for a language and downloads one small Vosk model (30–160 MB).
  **macOS microphone permission is required** — grant your terminal app access
  under *System Settings → Privacy & Security → Microphone*. If capture is
  silent, the card shows a permission hint.
- No model for a language you can read but not answer in yet? Keep the keypad:
  the game is fully playable without voice.

Both TTS and STT degrade to no-ops if unavailable — the game stays fully playable
with the keypad, and voice input layers on top when it's ready.

### 🌍 Supported languages

On first run you pick one of these, and only that language's model is downloaded
(it is reused on later launches). Add or switch languages any time from the
settings screen or the chip in the top-right corner.

| Language | English | Vosk model | Size | Voice |
|---|---|---|---|---|
| العربية | Arabic | `vosk-model-small-ar-0.3` | 104 MB | 🔊 Voice |
| العربية (تونس) | Arabic (Tunisian) | `vosk-model-small-ar-tn-0.1-linto` | 166 MB | 🔊 Voice |
| Català | Catalan | `vosk-model-small-ca-0.4` | 43 MB | 🔊 Voice |
| 中文 | Chinese | `vosk-model-small-cn-0.22` | 44 MB | ⌨️ Keypad only |
| Čeština | Czech | `vosk-model-small-cs-0.4-rhasspy` | 46 MB | 🔊 Voice |
| Nederlands | Dutch | `vosk-model-small-nl-0.22` | 40 MB | 🔊 Voice |
| Esperanto | Esperanto | `vosk-model-small-eo-0.42` | 44 MB | 🔊 Voice |
| فارسی | Farsi | `vosk-model-small-fa-0.42` | 53 MB | 🔊 Voice |
| Français | French | `vosk-model-small-fr-0.22` | 42 MB | ✅ Tested |
| ქართული | Georgian | `vosk-model-small-ka-0.42` | 46 MB | ⌨️ Keypad only |
| Deutsch | German | `vosk-model-small-de-0.15` | 46 MB | 🔊 Voice |
| ગુજરાતી | Gujarati | `vosk-model-small-gu-0.42` | 108 MB | ⌨️ Keypad only |
| हिन्दी | Hindi | `vosk-model-small-hi-0.22` | 44 MB | ⌨️ Keypad only |
| English (India) | Indian English | `vosk-model-small-en-in-0.4` | 38 MB | ✅ Tested |
| Italiano | Italian | `vosk-model-small-it-0.22` | 50 MB | 🔊 Voice |
| 日本語 | Japanese | `vosk-model-small-ja-0.22` | 50 MB | 🔊 Voice |
| Қазақша | Kazakh | `vosk-model-small-kz-0.42` | 60 MB | 🔊 Voice |
| 한국어 | Korean | `vosk-model-small-ko-0.22` | 87 MB | 🔊 Voice |
| Кыргызча | Kyrgyz | `vosk-model-small-ky-0.42` | 51 MB | ⌨️ Keypad only |
| Polski | Polish | `vosk-model-small-pl-0.22` | 53 MB | 🔊 Voice |
| Português | Portuguese | `vosk-model-small-pt-0.3` | 32 MB | 🔊 Voice |
| Русский | Russian | `vosk-model-small-ru-0.22` | 46 MB | 🔊 Voice |
| Español | Spanish | `vosk-model-small-es-0.42` | 40 MB | 🔊 Voice |
| Svenska | Swedish | `vosk-model-small-sv-rhasspy-0.15` | 304 MB | 🔊 Voice |
| Тоҷикӣ | Tajik | `vosk-model-small-tg-0.22` | 52 MB | 🔊 Voice |
| తెలుగు | Telugu | `vosk-model-small-te-0.42` | 61 MB | 🔊 Voice |
| Türkçe | Turkish | `vosk-model-small-tr-0.3` | 37 MB | 🔊 Voice |
| English (UK) | UK English | `vosk-model-small-en-gb-0.15` | 43 MB | ✅ Tested |
| Українська | Ukrainian | `vosk-model-small-uk-v3-small` | 144 MB | 🔊 Voice |
| English (US) | US English | `vosk-model-small-en-us-0.15` | 41 MB | ✅ Tested |
| Oʻzbekcha | Uzbek | `vosk-model-small-uz-0.22` | 51 MB | ⌨️ Keypad only |
| Tiếng Việt | Vietnamese | `vosk-model-small-vn-0.4` | 34 MB | 🔊 Voice |

**Voice status.** ✅ **Tested** — French and English (US/UK/India) are the only
languages tested end to end, on a real microphone. 🔊 **Voice** — the number
words are generated automatically and the language *should* work, but it has not
been tried yet. ⌨️ **Keypad only** — no number words exist for this language, so
voice answering stays off and the game is played on the keypad. The interface is
only translated for French and English; every other language falls back to
English labels until a translation is added.

**Feedback is very welcome.** Real-mic reports are the only way the untested
languages get verified, and the quickest way to fix one. If a spoken number is
misheard or refused, please open an issue saying which language you picked, what
you said, and what happened — `ALMATHUS_VOICE_DEBUG=1` (below) prints the
recognizer's raw output to include.

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
- recognition results — the raw Vosk alternatives, the parsed number and Vosk's
  elapsed time in ms;
- **why an utterance was refused** — how far behind the top hypothesis the
  all-numbers one scored, or what the un-constrained model heard instead;
- model load and warmup failures that are otherwise swallowed silently.

Each capture is also copied to `/tmp/almathus_capture.wav`, so you can listen back
to exactly what the recognizer heard.

### ✅ Tests

```bash
uv run python test_voice.py       # what counts as a spoken answer (no mic, no model)
uv run python test_room.py        # the acoustic gates, against a synthesised room
uv run python test_game_flow.py   # a card leaves the screen only when answered
```

`test_room.py` covers the acoustic gates with a synthesised room and a
synthesised voice (no microphone): the bar has to move with the noise, a voice two
metres out in a quiet room has to be accepted, and noise, silence and an unvoiced
whisper still have to be refused.

`test_game_flow.py` opens a Tk window briefly and stubs the microphone out. Its
`test_voice_flow` is the regression test for the skipping card: refused
utterances arrive from the recognizer over a long think and not one of them may
move the card, and the mic must never go deaf while it is up.
`test_voice_wrong_answer` holds the other half — a number that *is* recognized
scores the card at once, wrong included, and that card comes back exactly once,
at the end. `test_dictation_modes` pins the two openings of a card: with
dictation on the mic stays shut and both clocks are held until the reading is
over, with it off the mic and the clocks start with the card.

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
