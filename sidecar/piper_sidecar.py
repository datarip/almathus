#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Datarip
"""Almathus voice sidecar — a separate, GPL-licensed program.

The game never imports Piper. This process owns the GPL stack (piper-tts and
espeak-ng) and talks to the game over stdin/stdout, which keeps the two
programs at arm's length: the app can be distributed under its own licence,
while this sidecar and the engine it links travel under GPL-3.0.

Protocol (UTF-8 JSON lines in, JSON header + raw PCM out):

    {"cmd": "ping"}                        -> {"pong": true}
    {"cmd": "quit"}                        -> (exit)
    {"text": "...", "length_scale": 1.0}   -> {"sample_rate": 22050, "n": N}\n
                                              followed by exactly N bytes of
                                              little-endian int16 mono PCM

Anything that goes wrong is answered with {"error": "..."} and keeps the
process alive, so the game can fall back for that phrase instead of losing the
warm model. Logs go to stderr only; stdout is the audio channel.

Placement of the model and speaker is decided by the game: it passes
``--model`` and, for a multi-speaker voice, ``--speaker``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys

from piper import PiperVoice
from piper.config import SynthesisConfig


def send(out, payload: dict) -> None:
    out.write((json.dumps(payload) + "\n").encode("utf-8"))
    out.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="Almathus Piper sidecar")
    ap.add_argument("--model", required=True, help="path to the .onnx voice")
    ap.add_argument("--speaker", default=None, help="speaker name, if multi-speaker")
    args = ap.parse_args()

    voice = PiperVoice.load(args.model)
    speaker_id = None
    if args.speaker:
        speaker_id = voice.config.speaker_id_map.get(args.speaker)

    # Warm the phonemizer and the first inference: both build caches, and on a
    # cold process the first real phrase otherwise waits ~0.6 s. The game is
    # already waiting for the ready line, so this costs nothing visible.
    with contextlib.suppress(Exception):  # a failed warm-up is not fatal
        list(voice.synthesize("a", SynthesisConfig(length_scale=1.0)))

    out = sys.stdout.buffer
    send(out, {"ready": True})

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError as error:
            send(out, {"error": f"bad request: {error}"})
            continue

        if request.get("cmd") == "ping":
            send(out, {"pong": True})
            continue
        if request.get("cmd") == "quit":
            break

        text = request.get("text")
        if not isinstance(text, str) or not text:
            send(out, {"error": "no text"})
            continue
        try:
            config = SynthesisConfig(
                length_scale=float(request.get("length_scale", 1.0)),
                speaker_id=speaker_id,
            )
            chunks = list(voice.synthesize(text, config))
            pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
            if not pcm:
                send(out, {"error": "nothing synthesized"})
                continue
            send(out, {"sample_rate": chunks[0].sample_rate, "n": len(pcm)})
            out.write(pcm)
            out.flush()
        except Exception as error:  # noqa: BLE001 - answer, never take the game down
            send(out, {"error": f"{type(error).__name__}: {error}"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
