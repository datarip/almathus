<img src="assets/icon.svg" alt="Almathus app icon" width="128" height="128">

# 🧮 Almathus

A very simple game I made for a child so we could stop arguing about math practice 😉.

The exercises are the same ones speech and language therapists give, to build
automatic recall of basic operations. The game is aimed at children aged 6 to 8,
and for now covers addition, subtraction, multiplication and division; harder
levels are planned.

A session is short: the child sees a two-number card, answers it, and the time it
took is recorded. The faster the answer, the better the result.

The interesting part of this MVP is the voice loop: TTS reads the question aloud,
STT takes the answer, and the game validates it. The keyboard and the on-screen
keypad also work, but the aim was to keep the child's hands off the pad so the
timing stays honest.

Built with Python and Tkinter, with no third-party packages: the base game runs on
the standard library alone. Voice is an optional extra (`--extra voice`), and on
Linux a system Python may still need `python3-tk` for Tk itself.

## Screenshots

**Girly mode**

![Almathus in girly mode: settings, progress chart, a 4 × 7 card, and the language picker in the pink palette](screenshots/girly-mode.png)

**Dark — the default palette**

![Almathus in the dark palette: the same four screens](screenshots/boy-mode.png)

## Run

```bash
uv run almathus
# or
uv run python main.py
# or, without uv:
python3 main.py
```

## How it plays

1. **Settings** — how many cards (default **20**), and one of four modes:
   addition, subtraction, multiplication, division.
2. **3-2-1-GO** countdown.
3. **Cards** — an operation in big numbers (`8 − 2`). Answer on the keypad, with
   the keyboard (Enter), or by voice.
4. **Reinforcement** — every card missed on the first try comes back once at the
   end. 🔁
5. **Recap** — score, total and average time, an encouraging emoji, and the five
   slowest operations.

## Progress screen

Settings → *My progress*, or the recap after a game. It reads the results file.

- **Chart** — average time per day on a real calendar axis, one point per day,
  with that day's grade on a right-hand axis (`0/20` … `20/20`). A month filter,
  and a hover that names the date, the families, the session count, the average
  and the grade. A day with no score leaves a gap in the grade line instead of
  reading as a zero.
- **Table** — every session, newest first: grade, average time per card, and the
  five cards that session fought with.
- **📄 PDF** — an A4 report with the same chart drawn as vectors and the table
  flowing over as many pages as it needs, in the palette currently on screen.
  Written by `pdfreport.py`, with no print dependency.

A day's grade averages that day rather than totalling it, and is written over the
deck played most that day: three 20-card sessions read `16/20`, not `47/60`.

## What it records

A total stopwatch runs bottom-right, a per-card timer bottom-left. Each session is
written twice under **`Documents/Almathus/`** — `ALMATHUS_RESULTS_DIR` overrides
the folder:

| File | Contents |
|---|---|
| `results.csv` | one line per session; hand-editable, and what the progress screen reads |
| `stats/<timestamp>-<mode>.json` | per-card detail: time, attempts, first-try correctness |

A log an older build wrote inside the checkout is copied there the first time the
app starts and finds the folder empty.

## The results log — `results.csv`

The digital continuation of the paper *Calcul mental* sheet (Date / Famille /
Temps), plus the average column. **It is the database**: every finished session is
appended, and anything you edit by hand is what the app shows next time. The app
only ever appends — except the header line, which gains a column the app knows
about when your file lacks it.

### Header

```csv
date,famille,temps,cartes,note,temps_moyen_par_carte,difficultes
2026-06-16,-1,0:31,,,,
2026-07-28,-2 et -3,1:35.4,20,18,4.8,"17 - 4, 13 - 4, 9 - 4, 6 - 4, 20 - 4"
```

The English header (`LANG = "en"`) is:

```csv
date,family,time,cards,grade,average_time_per_card,difficulty
```

| Field | 🇫🇷 | 🇬🇧 | Meaning |
|---|---|---|---|
| date | `date` | `date` | day the session was played |
| famille | `famille` | `family` | which family was drilled — `-3`, `-1 et -2`, `x7` |
| temps | `temps` | `time` | total time of the session |
| cartes | `cartes` | `cards` | how many cards were asked |
| note | `note` | `grade` | cards right on the first try |
| temps moyen | `temps_moyen_par_carte` | `average_time_per_card` | `temps / cartes` |
| difficultés | `difficultes` | `difficulty` | the five cards that session fought with |

**Either language is read**, in any column order. Header matching ignores case,
accents, spaces and underscores (`Temps`, `temps moyen par carte` and `temps_moyen`
all work), and a few synonyms are accepted — see `ALIASES` in `results.py`.
Columns the app doesn't know are left untouched when it appends.

### Editing it by hand

| Column | What you can write |
|---|---|
| **date** | `2026-06-16`, `16/06/2026`, or `16/06`. **Empty repeats the row above**, like the blank cells on the paper sheet. |
| **famille** | free text. The app writes the operator sign then the numbers switched on: `-3`, `-1 et -2`, `x7`, `+1–10`. ASCII signs (`+ - x /`) so the column stays easy to type. |
| **temps** | `1:23` · `1:23.4` · `1'23` · `1'23"4` · `83"` · `83` · `83.4` · `1,23`. A comma followed by exactly two digits below 60 is the minute separator (`1,29` = 1 min 29 s); otherwise it is a decimal point (`32,60` = 32.6 s). **Use a dot for a decimal.** |
| **cartes** | an integer, or empty when unknown (the old paper rows). |
| **note** | cards right on the first try, as a bare number (`15`) — the denominator is the `cartes` cell beside it. A hand-written `15/20` keeps only the numerator. |
| **temps moyen** | plain seconds. Empty means it is computed as `temps / cartes`. |
| **difficultés** | the hardest cards, hardest first: `18 - 4, 11 - 4, 13 - 4`. **The next session reads this cell back**, so writing a card in is a way to say *drill this tomorrow*. |

Rows that can't be read are skipped, never rewritten. The file ships seeded with
the sessions from the paper sheet (16/06 → 12/07).

### Difficulties, and the revision they drive

A card is difficult if it was answered **wrong** or **slowly**. A finished session
ranks its cards — wrong first, then the slowest — and writes down the top five.
A card's time counts both sittings: one missed on the first pass comes back in the
reinforcement round, and the two goes are added, or a card fumbled in half a second
would read as the fastest answer of the round.

The next session deals each listed card **twice**, the copies kept apart in the
shuffle — asked back to back, the second would only test the memory of the first.
It reads the last day that was played, all of that day's sessions together, and
only deals what the round can ask: the same operation, from a number that is
switched on. So switching from *−4* to *×2* leaves the *−4* revision waiting rather
than dealing a card that doesn't belong. Revision never takes more than **half the
deck** — five carried-over cards need a deck of twenty.

> The *temps moyen / carte* column is **not** the average on the recap screen. The
> recap averages the time spent thinking on each card; this column divides the
> whole session — answer pauses and the reinforcement round included — by the card
> count, so it always matches the two columns beside it.

## Configuration

| What | Where |
|---|---|
| Number ranges | `RANGES` in `main.py`: addition and subtraction use operands **0–20** (never negative); multiplication and division use factors **0–10** (division always divides evenly) |
| Number words | generated from `num2words`, except Chinese, Hindi, Gujarati, Georgian, Kyrgyz and Uzbek — written out in `_TABLE_NUMBERS` (`voice.py`) |
| Language | `LANG = "fr"` or `"en"` in `main.py`; the spoken voice, the spoken phrases and the number parser all follow it |
| Remembered choices | `prefs.json` (dictation, theme, voice) |
| Results folder | `ALMATHUS_RESULTS_DIR` |

## 🎙️ Voice

`voice.py`, in two independent halves — speech out and speech in — each a no-op
when its dependencies are missing. The keypad always works.

### Speech out

One voice on every OS: **Piper**, one model per language (~60 MB), downloaded at
setup. The game never imports it — it starts `sidecar/piper_sidecar.py` and
exchanges text and PCM over stdin/stdout, which keeps the GPL engine out of the MIT
app. Source runs need `--extra sidecar`; packaged builds ship a frozen helper, and
`ALMATHUS_PIPER_SIDECAR` points at one explicitly.

- **The voice is a file, not the machine's synthesizer**, so a card sounds the same
  on a Mac and on a PC: nothing to enable, no language pack, nothing online.
- **No Piper model** for Esperanto, Gujarati, Kyrgyz, Tajik or Uzbek, and none
  until a download finishes: the OS voice takes over (macOS `say`, Linux
  `espeak-ng`, Windows `pyttsx3`). Fallbacks come from a curated list of that
  language's adult voices, women first — English gets Samantha, not a 1994
  MacinTalk novelty voice.
- `ALMATHUS_TTS_VOICE` picks another voice (a name for the OS backends, an `.onnx`
  path for Piper); `ALMATHUS_TTS_BACKEND=say|espeak|piper` forces a backend.

**French numbers are said the way a teacher says them.** Conversation drops the
final consonant of *six*, *huit* and *dix* and reads *plus* as /ply/, which is
right at a dinner table and wrong at a math table — "dix fois 4" came out as "di
fwa katr". The text is respelled before it is voiced (`dix` → *disse*, `huit` →
*huite*, `plus` → *plusse*), including at the tail of compounds (*dix-huit* →
*dix-huite*). This is a policy, applied only where phonemes were measured to
change: French is the only one of the 23 languages with number words where a
number loses a consonant in context. Portuguese voices the final *s* before a
voiced consonant, which is correct sandhi; German, Italian, Catalan, Turkish,
Persian and Kazakh differ only in stress.

### Dictation

Each card is read aloud before the child answers — *"sept fois quatre"* — the mode
the game was built for. **On by default**, toggled in settings. The mic opens when
the reading ends, so the machine never records its own voice, and neither clock
runs during the reading: what is measured is a child thinking, not a machine
talking. A question costs about 2.2s of system voice against card times of 5 to 9,
so counting it would have made the log a measure of the speech rate. The keypad
stays live under a reading, so a card answered before its clock starts is worth
zero, never less. With dictation off, the mic and the clocks start with the card.

Sessions logged before 2026-08-19 were timed the old way; a one-off correction took
the readings back out of them, card by card, so the chart is one curve measured one
way.

### Answer by voice

Voice input runs **continuously, in parallel with the keypad** — there is no mic
button. A spoken number is auto-filled and auto-submitted; the keypad stays usable
throughout.

```
                    ┌── the room, measured ──┐
                    ▼                        ▼
mic audio ─▶ [VAD record] ─▶ [is it a voice?] ─▶ [subtract the room] ─▶ Vosk ─▶ [numbers only?] ─▶ submit
             (voice.py)       level + pitch       spectral subtraction     per-language  strict rule
```

Every stage can only ever say *not an answer*, and then the card stays up.
**Nothing takes a card off the screen but an answer.**

0. **Listen to the room.** A few hundred milliseconds after the app opens, it
   opens the mic for ~2s and measures the noise, then closes it — on its own
   thread, nothing shown. It repeats at the start of a game, and stays current
   between answers from the silence either side of them. Where you play changes
   what a spoken answer looks like: a bedroom reads near nothing, a terrace with a
   road behind it twenty times that, and a fixed threshold cannot serve both. The
   thresholds are a *factor over the room*, so they move with it, and a child two
   meters back — ~15 dB down on the same child at arm's length — still clears a bar
   measured against a quiet room.
1. **Record.** From first speech until ~0.35s of silence (or a ~6s cap). No button,
   and no deadline on thinking.
2. **Was that a voice, said out loud?** Judged on *voicing* — the vocal-fold pitch
   speech has and noise, taps and unvoiced whispers do not — then on level and
   signal-to-noise against the room. The first keeps a chair or a cough out of the
   recognizer; the last two are the **whisper gate**. A murmur is a real voice by
   every acoustic measure, so only loudness separates it from an answer, and a
   whisper must never score a card: the child would lose the question without
   deciding to answer. A voice too quiet is told so — *"🔊 Plus fort !"* — not
   dropped. `uv run python mic_check.py` prints your room, the threshold it
   implies, and the verdict.
3. **Take the room back out.** The spectrum measured in the silence before the
   child spoke is subtracted bin by bin. A fan or a road does not make speech
   quieter — it fills the gaps between the formants, which wrecks recognition and
   which a band-pass cannot touch. Deliberately mild (~18 dB at most), because
   over-subtracting warbles; if the cleaned audio says nothing, the untouched
   capture gets its own decode, and in a quiet room it is skipped.
4. **Recognize.** **Vosk**, vocabulary restricted to number words (~100 ms, and
   accurate on isolated digits like the French "un"). One small model per language,
   downloaded on first run.
5. **Numbers only.** Accepted **only if every word is a number**
   (`voice.strict_numbers`). One stray word — a filler, an operator, `[unk]` — and
   the utterance is refused. Vosk's winning hypothesis must also score within
   `ALMATHUS_VOICE_ALT_GAP` of its best overall, and a second decode by the
   un-constrained model can veto it. This fixed real failures: the parser used to
   keep the numbers out of anything, so a child reading "quinze moins quatre"
   answered `4` and "je sais pas" answered `16`. Over a 68-clip French corpus,
   19 of 30 non-number utterances were scored as answers; now 3 are, and 33 of 38
   spoken numbers are still recognized.
6. **Answer.** A number that gets this far *is* the answer, exactly as if ✓ had
   been pressed — right or wrong. A wrong one shows the correct result and puts the
   question in the reinforcement queue for one more ask at the end. Nothing is ever
   repeated to confirm it.

### Models and languages

The language picker on first run downloads only that language's Vosk model
(30–160 MB) into a per-user folder: `~/Library/Application Support/Almathus/models`
on macOS, `%LOCALAPPDATA%\Almathus\models` on Windows,
`~/.local/share/almathus/models` on Linux. Switch any time from settings or the
chip in the corner; a downloaded model is reused. `ALMATHUS_VOSK_MODEL=/path`
bypasses the store, `ALMATHUS_MODELS_DIR` moves it.

| Language | English | Vosk model | Size | Voice |
|---|---|---|---|---|
| العربية | Arabic | `vosk-model-small-ar-0.3` | 104 MB | 🔊 Voice |
| العربية (تونس) | Arabic (Tunisian) | `vosk-model-small-ar-tn-0.1-linto` | 166 MB | 🔊 Voice |
| Català | Catalan | `vosk-model-small-ca-0.4` | 43 MB | 🔊 Voice |
| 中文 | Chinese | `vosk-model-small-cn-0.22` | 44 MB | 🔊 Voice |
| Čeština | Czech | `vosk-model-small-cs-0.4-rhasspy` | 46 MB | 🔊 Voice |
| Nederlands | Dutch | `vosk-model-small-nl-0.22` | 40 MB | 🔊 Voice |
| Esperanto | Esperanto | `vosk-model-small-eo-0.42` | 44 MB | 🔊 Voice |
| فارسی | Farsi | `vosk-model-small-fa-0.42` | 53 MB | 🔊 Voice |
| Français | French | `vosk-model-small-fr-0.22` | 42 MB | ✅ Tested |
| ქართული | Georgian | `vosk-model-small-ka-0.42` | 46 MB | 🔊 Voice |
| Deutsch | German | `vosk-model-small-de-0.15` | 46 MB | 🔊 Voice |
| ગુજરાતી | Gujarati | `vosk-model-small-gu-0.42` | 108 MB | 🔊 Voice |
| हिन्दी | Hindi | `vosk-model-small-hi-0.22` | 44 MB | 🔊 Voice |
| English (India) | Indian English | `vosk-model-small-en-in-0.4` | 38 MB | ✅ Tested |
| Italiano | Italian | `vosk-model-small-it-0.22` | 50 MB | 🔊 Voice |
| 日本語 | Japanese | `vosk-model-small-ja-0.22` | 50 MB | 🔊 Voice |
| Қазақша | Kazakh | `vosk-model-small-kz-0.42` | 60 MB | 🔊 Voice |
| 한국어 | Korean | `vosk-model-small-ko-0.22` | 87 MB | 🔊 Voice |
| Кыргызча | Kyrgyz | `vosk-model-small-ky-0.42` | 51 MB | 🔊 Voice |
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
| Oʻzbekcha | Uzbek | `vosk-model-small-uz-0.22` | 51 MB | 🔊 Voice |
| Tiếng Việt | Vietnamese | `vosk-model-small-vn-0.4` | 34 MB | 🔊 Voice |

The registry (`languages.py`) lists 32 Vosk languages, and every one of them
answers by voice. Thirty get their number words from `num2words`; the six it
cannot spell — Chinese, Hindi, Gujarati, Georgian, Kyrgyz and Uzbek — are written
out in `voice.py` (`_TABLE_NUMBERS`), which feeds both the Vosk grammar and the
strict parser. Five languages have no Piper model (Esperanto, Gujarati, Kyrgyz,
Tajik, Uzbek), so *speaking* falls back to the OS voice there.

**Status.** ✅ **Tested** — French and English (US/UK/India) end to end on a real
microphone. 🔊 **Voice** — the words are there and the grammar is built, but
nobody has tried it on a real mic yet; the hand-written tables are the least
proven of the lot. Labels, month names, the operator word ("7 fois 4", "7 times
4") and the recap sentence are translated for all 32 languages; a missing label
falls back to English, and the PDF prints English where its built-in Latin font
cannot draw the script.

Reports from a real mic are the only way the untested languages get verified. If a
spoken number is misheard or refused, say which language, what you said and what
happened — `ALMATHUS_VOICE_DEBUG=1` prints the raw recognizer output to include.

### Tuning

All optional, all with measured defaults:

| Variable | Default | What it does |
|---|---|---|
| `ALMATHUS_VOICE_ALT_GAP` | `0.5` | how far behind the top hypothesis an all-numbers one may score. Higher = more numbers recognized, more noise accepted |
| `ALMATHUS_VOICE_VETO_CONF` | `0.9` | how sure the un-constrained model must be before it overrules the number grammar |
| `ALMATHUS_VOICE_MIN_SNR` | `3.0` | how far a voice must stand above the room |
| `ALMATHUS_VOICE_MIN_VOICED` | `0.25` | fraction of frames that must carry vocal-fold pitch |
| `ALMATHUS_VOICE_LEVEL_OVER_ROOM` | `3.0` | how far over the measured room "said out loud" is. Raise it if noise scores cards, lower it if a voice across the room is refused |
| `ALMATHUS_VOICE_MIN_LEVEL_FLOOR` | `45` | never ask for less than this, so a near-silent room cannot leave the gate at nothing |
| `ALMATHUS_VOICE_MIN_LEVEL` | *(unset)* | a hard, absolute gate; set it and nothing is measured |
| `ALMATHUS_VOICE_CALIB_SECS` | `1.5` | how long the startup listen to the room lasts |
| `ALMATHUS_VOICE_DENOISE` | `auto` | `1` always subtract the room, `0` never, `auto` only when there is noise to subtract |
| `ALMATHUS_VOICE_DENOISE_ALPHA` | `1.5` | how hard to subtract it. Higher removes more noise and risks warbling artifacts |

### Enabling it

```bash
uv sync --extra voice --extra sidecar --group dev
# voice   : sounddevice + numpy + num2words + vosk + webrtcvad (the mic)
# sidecar : piper-tts, the GPL engine the separate voice process runs
# keep --extra voice on every sync: without it uv prunes the audio libs and
# the mic goes dead
```

First run asks for a language and downloads its Vosk model plus its Piper voice
(~60 MB; 109 MB for Ukrainian, 122 MB for Kazakh). **macOS needs microphone
permission** for your terminal app — *System Settings → Privacy & Security →
Microphone*; if capture is silent, the card shows a permission hint.

### Debugging

`ALMATHUS_VOICE_DEBUG` is an **environment variable**, not a flag (the app takes no
arguments). Set it to anything but empty or `0` and `voice.py` traces the pipeline
to stderr, every line prefixed `[voice]`:

```bash
ALMATHUS_VOICE_DEBUG=1 uv run almathus
```

You get capture setup (rate, block size, which detector), why a recording ended,
the raw Vosk alternatives with the parsed number and elapsed ms, **why an utterance
was refused**, and model load failures that are otherwise swallowed. Each capture
is copied to `/tmp/almathus_capture.wav`, so you can hear what the recognizer
heard. Nothing outside `voice.py` reads the variable.

### Tests

```bash
uv run python test_voice.py       # what counts as a spoken answer (no mic, no model)
uv run python test_room.py        # the acoustic gates, against a synthesized room
uv run python test_game_flow.py   # a card leaves the screen only when answered
```

`test_room.py` puts a synthesized voice in a synthesized room: the bar has to move
with the noise, a voice two meters out in a quiet room has to be accepted, and
noise, silence and an unvoiced whisper still have to be refused.

`test_game_flow.py` opens a Tk window briefly and stubs the mic. `test_voice_flow`
is the regression test for the skipping card — refused utterances over a long think
may not move the card, and the mic may never go deaf while it is up.
`test_voice_wrong_answer` holds the other half: a recognized number scores at once,
wrong included, and comes back exactly once. `test_dictation_modes` pins the two
openings of a card.

## Licensing

- The game's code and its wheel are **MIT** — see [`LICENSE`](LICENSE).
- [`sidecar/`](sidecar/) is a separate **GPL-3.0** program — see
  [`sidecar/LICENSE`](sidecar/LICENSE). It owns Piper and espeak-ng so the game
  does not have to; an installer that bundles it is a GPL-3.0 combined work and
  must carry its source and license. The wheel never contains it.
- Voice models are downloaded at setup and keep their own licenses; the sounds
  are CC0 / CC BY ([`assets/README.md`](assets/README.md)); the flags are MIT.
- The app icon shown at the top (`assets/icon.svg`) is VistaCreate stock artwork:
  allowed inside the app, but its license does not clearly permit using it as a
  logo or trademark — see [`assets/README.md`](assets/README.md#licence).
- The shipping matrix and the per-OS packaging steps are in
  [`docs/shipping.md`](docs/shipping.md); [`NOTICE`](NOTICE) records the MIT/GPL
  split.

## Roadmap

- Language toggle on the settings screen.
- Per-operation breakdown on the progress screen, from `stats/*.json` — which
  facts are still slow, not just the session total.
- VAD thresholds and timeouts in `voice.py`, if a room is noisy.
