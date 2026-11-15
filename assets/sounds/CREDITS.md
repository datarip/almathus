# Sound credits

The four samples in this directory are **not** MIT: they keep the licences they
came with. Each one was trimmed of leading and trailing silence, loudness-
normalised (the one-shots to -18 LUFS, the four-second countdown to -22 LUFS,
4 dB lower because a dense run of tones reads louder than its integrated
loudness suggests) and re-encoded to mp3. The CC BY licences require us to say
so.

| File | Source | Author | Licence |
|---|---|---|---|
| `countdown.mp3` | [Countdown for race, game start](https://freesound.org/people/Pastew/sounds/813525/) | Pastew | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `correct.mp3` | [success.wav](https://freesound.org/people/grunz/sounds/109662/) | grunz | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `wrong.mp3` | [Error.wav](https://freesound.org/people/Autistic%20Lucario/sounds/142608/) | Autistic Lucario | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `victory.mp3` | [venusalternate.wav](https://freesound.org/people/haxekhaex2/sounds/663923/) | haekhaex2 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

**No non-commercial sample is used**, so a paid build is allowed. The audio
stays under these licences even though the rest of the project is MIT: the
project licence does not relicense the files. This file is packaged inside the
wheel, so the credit travels with the audio.

A shipped app should show the same credits in an About or Licences screen,
because a README inside a bundle is not visible to the player.

## Replacing a sample

The files are named after the moment they mark, not after the tune they were cut
from, so any mp3 dropped in under the same name is used instead. Run it through
the same trim and loudness step, or it will sit at a different volume from the
rest.

`countdown.mp3` drives the count rather than following it: three beeps exactly
one second apart and then the launch tone, with the numbers on screen hung off
those instants. Four constants in `main.py` describe it — `COUNTDOWN_TICK`,
`COUNTDOWN_GO_AT`, `COUNTDOWN_GO_LEN` and `COUNTDOWN_STEPS` — so swapping in a
sample of your own means re-measuring them.

Playing needs a command-line player on PATH: `afplay` on macOS, or `ffplay` /
`mpg123` / `mpv` / `cvlc`. With none installed, or with a file missing, the game
runs silently.
