"""Diagnostic micro pour Almathus.

    uv run python mic_check.py

Enregistre 4 secondes SANS détection de voix (capture brute), affiche les
niveaux mesurés, puis transcrit avec parakeet — pour voir précisément où ça
coince : la capture (niveaux trop bas ?), ou la transcription.

Parle fort un nombre (ex: "quatre") quand il affiche PARLE MAINTENANT.
"""

import wave

import numpy as np
import sounddevice as sd

import voice

SECS = 4


def main():
    s = voice.SpeechInput("fr")
    if not (s._sd and s._np):
        print("sounddevice/numpy manquants — installe le groupe voice.")
        return

    sr, blk = s._pick_stream_params()
    if sr is None:
        print("❌ Aucun périphérique d'entrée utilisable (permission micro ?)")
        return
    s._rec_sr = sr

    dev = sd.query_devices(kind="input")
    print(f"Micro         : {dev['name']}")
    print(f"Samplerate    : {sr} Hz   (canaux max: {dev['max_input_channels']})")
    print(f"\n>>> PARLE MAINTENANT un nombre, fort, pendant {SECS} secondes...\n")

    audio = sd.rec(int(SECS * sr), samplerate=sr, channels=1, dtype="int16")
    sd.wait()
    audio = audio[:, 0]

    peak = int(np.abs(audio).max())
    step = int(0.25 * sr)
    timeline = [
        int(np.sqrt((audio[i:i + step].astype("float32") ** 2).mean() + 1))
        for i in range(0, len(audio), step)
    ]
    print(f"Amplitude max : {peak}   (une voix normale dépasse ~2000–15000)")
    print(f"Niveaux RMS   : {timeline}")
    if peak < 500:
        print("  ⚠️  Niveau TRÈS bas — monte le volume d'entrée dans")
        print("      Réglages Système ▸ Son ▸ Entrée (et choisis le bon micro).")

    norm = s._normalize(audio)
    print(f"Après normalisation, amplitude max : {int(np.abs(norm).max())}")

    with wave.open("/tmp/almathus_mic.wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(norm.tobytes())
    print("Fichier enregistré : /tmp/almathus_mic.wav")

    # Vosk d'abord : c'est le reconnaisseur principal en français (~100 ms).
    print("\nReconnaissance Vosk (grammaire de nombres)…")
    if s._load_vosk():
        nums = s._vosk_numbers(audio)
        print(f"  nombres     : {nums or '— rien retenu'}")
        if not nums:
            print("      Un énoncé n'est accepté que s'il ne contient QUE des")
            print("      nombres. Relance avec ALMATHUS_VOICE_DEBUG=1 pour voir")
            print("      les hypothèses de Vosk et la raison du refus.")
    else:
        print("  modèle Vosk introuvable — voir le README (dossier ./models)")

    print("\nTranscription parakeet (premier appel = chargement du modèle ~60s)…")
    raw = s._transcribe(audio)
    normd = s._transcribe(norm)
    print(f"  brut        : {raw!r}  -> {voice.parse_number(raw, 'fr')}")
    print(f"  normalisé   : {normd!r}  -> {voice.parse_number(normd, 'fr')}")
    print("\n  (parakeet transcrit le français vers l'anglais : en français il ne")
    print("   sert que de secours, et la règle « que des nombres » le filtre.)")


if __name__ == "__main__":
    main()
