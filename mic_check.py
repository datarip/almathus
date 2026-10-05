"""Diagnostic micro pour Almathus.

    uv run python mic_check.py

Enregistre 4 secondes SANS détection de voix (capture brute), affiche les
niveaux mesurés, puis reconnaît le nombre avec Vosk — pour voir précisément où
ça coince : la capture (niveaux trop bas ?), ou la reconnaissance.

Parle fort un nombre (ex: "quatre") quand il affiche PARLE MAINTENANT.

Affiche aussi les mesures du juge acoustique du jeu (voice._judge) et
dit si cet enregistrement aurait été accepté.

Le seuil « parlé à voix haute » n'est plus une constante : il est mesuré à partir
du bruit de la pièce, exactement comme le fait le jeu au démarrage. Cet outil
affiche donc le bruit mesuré ici et le seuil qui en découle. Pour vérifier le
réglage : enregistre une fois en parlant normalement (à la distance où l'enfant
joue), une fois en chuchotant — le seuil doit tomber entre les deux.
"""

import tempfile
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

import voice

SECS = 4


def show_judge(s, audio, sr):
    """Les mesures sur lesquelles le jeu décide « c'est une réponse » ou non."""
    blk = int(sr * 0.02)  # mêmes blocs de 20 ms que la capture
    nb = len(audio) // blk
    if nb < 2:
        return
    blocks = list(audio[: nb * blk].reshape(nb, blk))
    rms = s._band_levels(blocks, sr)
    peak = float(rms.max())
    # Le bruit de la pièce et son spectre, mesurés comme le jeu les mesure.
    s._learn_room(blocks, sr)
    floor = s.room_level() or 0.0
    gate = voice.level_gate(floor)
    snr = peak / max(floor, 1.0)
    # La voix seule, sans le silence autour : c'est ce que le jeu mesure.
    loud = rms > max(peak * 0.12, floor * 1.5, voice._OFFSET_FLOOR)
    span = audio[: nb * blk].reshape(nb, blk)[loud].reshape(-1) if loud.any() else audio
    voiced = s._voiced_fraction(span.astype(np.float32), sr)

    print("\nJuge acoustique (ce que le jeu mesure) :")
    print(f"  bruit pièce   : {floor:8.2f}   -> seuil « à voix haute » {gate:.0f}")
    ok = True
    for label, got, mini in (
        ("niveau crête ", peak, gate),
        ("rapport S/B  ", snr, voice._MIN_SNR),
        ("voisement    ", voiced, voice._MIN_VOICED),
    ):
        ok = ok and got >= mini
        print(
            f"  {label} : {got:8.2f}   (minimum {mini:g})  "
            f"{'✅' if got >= mini else '❌'}"
        )
    if ok:
        print("  -> accepté : le jeu aurait écouté cet énoncé.")
    elif voiced < voice._MIN_VOICED:
        print("  -> refusé : pas une voix (bruit, choc, ou chuchotement non voisé).")
    else:
        print("  -> refusé : voix trop faible pour cette pièce — « Plus fort ! ».")
        print("     Le seuil suit le bruit ambiant : dans une pièce bruyante il")
        print("     monte tout seul, dans une chambre calme il descend.")


def main():
    s = voice.SpeechInput("fr")
    if not (s._sd and s._np):
        print("sounddevice/numpy manquants — installe le groupe voice.")
        return

    sr, _blk = s._pick_stream_params()
    if sr is None:
        print("❌ Aucun périphérique d'entrée utilisable (permission micro ?)")
        return
    s._rec_sr = sr

    dev = sd.query_devices(kind="input")
    print(f"Micro         : {dev['name']}")
    print(f"Samplerate    : {sr} Hz   (canaux max: {dev['max_input_channels']})")
    print(f"\n>>> PARLE MAINTENANT un nombre, fort, pendant {SECS} secondes...\n")

    # Même gain d'entrée que pendant une partie : sans ça les niveaux mesurés
    # ici ne sont pas ceux que le juge voit en jeu, et régler un seuil dessus
    # donne un réglage faux.
    s.boost_input_volume()
    try:
        audio = sd.rec(int(SECS * sr), samplerate=sr, channels=1, dtype="int16")
        sd.wait()
    finally:
        s.restore_input_volume()
    audio = audio[:, 0]

    peak = int(np.abs(audio).max())
    step = int(0.25 * sr)
    timeline = [
        int(np.sqrt((audio[i : i + step].astype("float32") ** 2).mean() + 1))
        for i in range(0, len(audio), step)
    ]
    print(f"Amplitude max : {peak}   (une voix normale dépasse ~2000–15000)")
    print(f"Niveaux RMS   : {timeline}")
    if peak < 500:
        print("  ⚠️  Niveau TRÈS bas — monte le volume d'entrée dans")
        print("      Réglages Système ▸ Son ▸ Entrée (et choisis le bon micro).")

    show_judge(s, audio, sr)

    clean = s._denoise(audio.astype(np.float32), sr)
    if clean is not audio:
        removed = float(np.sqrt(np.mean((audio - clean) ** 2)))
        print(f"\nDébruitage    : bruit retiré, RMS {removed:.0f}")

    norm = s._normalize(audio)
    print(f"Après normalisation, amplitude max : {int(np.abs(norm).max())}")

    dump = Path(tempfile.gettempdir()) / "almathus_mic.wav"
    with wave.open(str(dump), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(norm.tobytes())
    print(f"Fichier enregistré : {dump}")

    print("\nReconnaissance Vosk (grammaire de nombres, ~100 ms)…")
    if s._load_vosk():
        nums = s._vosk_numbers(audio)
        print(f"  nombres     : {nums or '— rien retenu'}")
        if not nums:
            print("      Un énoncé n'est accepté que s'il ne contient QUE des")
            print("      nombres. Relance avec ALMATHUS_VOICE_DEBUG=1 pour voir")
            print("      les hypothèses de Vosk et la raison du refus.")
    else:
        print("  modèle Vosk introuvable — lance l'appli pour choisir une langue,")
        print("  ou pose un modèle dans ./models (voir le README).")


if __name__ == "__main__":
    main()
