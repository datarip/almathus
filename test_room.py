"""Regression tests for the acoustic gates — the ones that decide "that was an
answer" before a single word is recognized.

Run with:  uv run python test_room.py   (needs the voice extra: numpy)

The text layer has ``test_voice.py``; this is the layer in front of it, and it is
the one that went deaf. Its thresholds used to be constants measured once, in one
quiet room, at arm's length from one mic — so the same child, sat back from the
laptop, was refused as whisper-quiet, and the same room with a window open
triggered on traffic. They are measured from the room now, and these cases pin
that down: the bar has to *move* with the noise, and it has to keep saying no to
the things it was always right to refuse.

No microphone: the room and the voice are synthesised, so this runs anywhere.
Synthetic speech is a harmonic stack (which is what "voiced" means to the judge)
and a synthetic whisper is the same turbulence as the room, only louder — which
is what an unvoiced whisper is, and why level alone can never separate the two.
"""

import sys
import threading
import time

import numpy as np

import voice

SR, BLK = 16000, 320


def room(rng, n, amp):
    """Room tone: white noise plus a low-frequency drift, like a fan or a road."""
    w = rng.normal(0, amp, n)
    b = np.cumsum(w)
    return w * 0.6 + (b - b.mean()) / np.sqrt(max(n, 1)) * 3


def spoken(n, f0=230.0, amp=1.0):
    """A voice: harmonics of a pitch, faded in and out like a word."""
    t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3, 4, 5, 6, 8, 11, 15))
    return (
        x
        * np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.05 * SR))
        * amp
    )


def whispered(rng, n, amp):
    return room(rng, n, amp) * 3  # turbulence, no vocal folds — no pitch


def new_input() -> voice.SpeechInput:
    """A SpeechInput whose numpy is loaded even without an audio device.

    ``voice._probe`` imports numpy and sounddevice in one try block, so a machine
    with no sound server (a CI runner, a container) ends up with neither — and the
    room maths this file exercises needs numpy. Everything here is synthesised,
    so the device is irrelevant and only the maths has to be present.
    """
    s = voice.SpeechInput("fr")
    if getattr(s, "_np", None) is None:
        import numpy

        s._np = numpy
    return s


def capture(noise_amp, body, calibrated=True):
    """Feed the recorder a card's worth of audio: room, then this, then room."""
    rng = np.random.default_rng(3)
    s = new_input()
    s._active_stream, s._rec_sr, s._blk = object(), SR, BLK
    if calibrated:
        pre = room(rng, SR * 2, noise_amp)
        s._learn_room([pre[i : i + BLK] for i in range(0, SR * 2 - BLK, BLK)], SR)
    said = body(rng)
    seq = np.concatenate(
        [
            room(rng, SR, noise_amp),
            said + room(rng, len(said), noise_amp),
            room(rng, SR, noise_amp),
        ]
    )
    blocks = [
        seq[i : i + BLK].clip(-32768, 32767).astype(np.int16)
        for i in range(0, len(seq) - BLK, BLK)
    ]

    def feed() -> None:
        # _record drains the queue the moment it starts, to throw away audio the
        # stream gathered while nothing was listening. So the capture has to
        # arrive *after* it is listening, exactly as a real stream callback would.
        time.sleep(0.3)
        for block in blocks:
            s._audio_q.put(block)

    producer = threading.Thread(target=feed, daemon=True)
    producer.start()
    heard = s._record(None) is not None
    producer.join(timeout=2.0)
    return s, heard


def check(failures, name, cond):
    if not cond:
        failures.append(f"  {name}")


def main() -> int:
    f: list[str] = []
    rng = np.random.default_rng(11)

    # -- the bar moves with the room ---------------------------------------- #
    check(
        f,
        "a louder room must ask for a louder voice",
        voice.level_gate(200.0) > voice.level_gate(20.0),
    )
    check(
        f,
        "a silent room must not end up with a gate of nothing",
        voice.level_gate(0.0) >= voice._MIN_LEVEL_FLOOR,
    )
    check(
        f,
        "an unmeasured room must still have a bar",
        voice.level_gate(None) == voice._MIN_LEVEL_BLIND,
    )

    # -- the room is the quiet part of the window, not its average ---------- #
    # A word, a door or a cough in the run-up must not be taken for the room and
    # take the gate up with it — that is how a session goes progressively deaf.
    s = new_input()
    quiet = list(room(rng, SR, 60).reshape(-1, BLK))
    loud = list((spoken(BLK * 10, amp=4000) + room(rng, BLK * 10, 60)).reshape(-1, BLK))
    s._learn_room(quiet, SR)
    calm = s.room_level()
    s._learn_room(quiet + loud, SR)
    noisy = s.room_level()
    check(
        f,
        "a word in the run-up must not become 'the room'",
        calm is not None and noisy is not None and noisy < calm * 1.5,
    )

    # -- subtracting the room ------------------------------------------------ #
    s = new_input()
    n = int(SR * 0.6)
    noise = room(rng, SR * 2, 300)
    s._learn_room([noise[i : i + BLK] for i in range(0, SR * 2 - BLK, BLK)], SR)
    sig = spoken(n, amp=600)
    dirty = (sig + room(rng, n, 300)).astype(np.float32)
    clean = s._denoise(dirty, SR)

    def snr(est):
        a = float(np.dot(est, sig) / np.dot(sig, sig))  # best-fit scale
        return 10 * np.log10(
            np.dot(sig * a, sig * a) / max(np.sum((est - sig * a) ** 2), 1e-9)
        )

    check(f, "cleaning must leave the clip the same length", len(clean) == len(dirty))
    check(
        f, "cleaning must raise the signal-to-noise ratio", snr(clean) > snr(dirty) + 3
    )
    check(
        f,
        "cleaning must not flatten the voice out of it",
        s._voiced_fraction(clean, SR) >= voice._MIN_VOICED,
    )
    s._room = 1.0  # a room with nothing in it: there is nothing to subtract
    check(f, "a silent room must be left alone", s._denoise(dirty, SR) is dirty)

    # -- what is, and is not, an answer -------------------------------------- #
    # The middle two are the regression: a fixed gate of 220 refused both, and
    # the child was told to speak up while sitting a normal distance away.
    cases = [
        (
            "close voice, quiet room",
            60,
            lambda _r: spoken(int(SR * 0.7), amp=3000),
            True,
        ),
        ("2 m voice, quiet room", 60, lambda _r: spoken(int(SR * 0.7), amp=250), True),
        ("2 m voice, noisy room", 400, lambda _r: spoken(int(SR * 0.7), amp=900), True),
        ("2 m voice, loud room", 900, lambda _r: spoken(int(SR * 0.7), amp=1800), True),
        ("nobody talking, noisy room", 400, lambda _r: np.zeros(int(SR * 0.7)), False),
        (
            "a whisper, quiet room",
            60,
            lambda r: whispered(r, int(SR * 0.7), 260),
            False,
        ),
    ]
    for name, noise_amp, body, want in cases:
        for calibrated in (True, False):
            _s, got = capture(noise_amp, body, calibrated)
            how = "calibrated" if calibrated else "uncalibrated"
            check(
                f, f"{name} ({how}): {'refused' if want else 'accepted'}", got == want
            )

    for line in f:
        print(line)
    print(f"{'FAILED' if f else 'passed'} — {len(f)} failing checks")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
