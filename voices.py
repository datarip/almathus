"""Spoken voice models — one Piper voice per language, on every OS.

Every operating system ships a different speech synthesizer, and none of them
sounds like the others: the same app spoken by Amélie on a Mac, espeak-ng on
Linux and a SAPI voice on Windows is three different games. Piper is a small
neural synthesizer that runs the same way on all three, so the voice becomes a
property of the app rather than of the machine it happens to run on.

The model is a plain ONNX file beside a JSON config, fetched like the Vosk
models and pinned by the sha256 Hugging Face publishes for each file. It lives
under its own directory, not beside the recognizer models, so the two can be
installed and removed independently.

Piper has no voice for eo, gu, ky, tg or uz; those, and every language on the
first run before the model is downloaded, fall back to the operating system's
own voice (see :mod:`voice`).

Names are Piper's own catalog keys (rhasspy/piper-voices). A multi-speaker
model names the speaker we want, and the default is a woman's voice because
that is the voice the game was built around.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import languages

_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


@dataclass(frozen=True)
class Voice:
    model: str  # catalog key, also the file stem, e.g. "fr_FR-siwis-medium"
    path: str  # its directory in the catalog, e.g. "fr/fr_FR/siwis/medium"
    sha256: str  # of the .onnx, as published by Hugging Face
    size: int  # .onnx size in bytes, for the progress bar
    config_size: int  # .onnx.json size in bytes
    speaker: str | None = None  # speaker name in a multi-speaker model

    @property
    def onnx_url(self) -> str:
        return f"{_BASE_URL}/{quote(f'{self.path}/{self.model}.onnx')}"

    @property
    def config_url(self) -> str:
        return f"{_BASE_URL}/{quote(f'{self.path}/{self.model}.onnx.json')}"


# The British voice is also what Indian English is read with: the catalog has no
# Indian English model, and a UK voice is the closest thing to one.
_EN_GB = Voice(
    "en_GB-jenny_dioco-medium",
    "en/en_GB/jenny_dioco/medium",
    "62ba183a504cfcd8816ec1b50ee856ed318ccc9dd22fd0bedd7d839c95dfa20e",
    63221984,
    5478,
)

VOICES: dict[str, Voice] = {
    "ar": Voice(
        "ar_JO-kareem-medium",
        "ar/ar_JO/kareem/medium",
        "9e95cab07b679da603bba17c4dec7ab3111320571964ee95c0379603c086491e",
        63201294,
        5024,
    ),
    "ca": Voice(
        "ca_ES-upc_ona-medium",
        "ca/ca_ES/upc_ona/medium",
        "fdb652db8c11a4475527346cf3241cb064d1ba393cf370f3f2ec09a872d118fd",
        63201294,
        4875,
    ),
    "cn": Voice(
        "zh_CN-huayan-medium",
        "zh/zh_CN/huayan/medium",
        "9929917bf8cabb26fd528ea44d3a6699c11e87317a14765312420be230be0f3d",
        63201294,
        4822,
    ),
    "cs": Voice(
        "cs_CZ-kasandra-medium",
        "cs/cs_CZ/kasandra/medium",
        "09d08aa23691ed2d53fb3db242cfe82b189adf9c1812dd5fe928b5789ed76df7",
        63511038,
        3232,
    ),
    "de": Voice(
        "de_DE-kerstin-medium",
        "de/de_DE/kerstin/medium",
        "c56d70475fb99248a20e60dafbcd107475e60d7e01deec18ba65201d288d015d",
        63221984,
        5459,
    ),
    "en-us": Voice(
        "en_US-lessac-medium",
        "en/en_US/lessac/medium",
        "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        63201294,
        4885,
    ),
    "en-gb": _EN_GB,
    "en-in": _EN_GB,
    "es": Voice(
        "es_ES-sharvard-medium",
        "es/es_ES/sharvard/medium",
        "234dc4cf0fdb53ccb76b9b2c4d7ef1330364762674c1b78c1b62d8608c528a54",
        76753841,
        5486,
        speaker="F",
    ),
    "fa": Voice(
        "fa_IR-ganji-medium",
        "fa/fa_IR/ganji/medium",
        "6a98504bb77dc2fd3a863c977d37e67a6a525fdf661917385d569a3ff78e6cae",
        63516050,
        4958,
    ),
    "fr": Voice(
        "fr_FR-siwis-medium",
        "fr/fr_FR/siwis/medium",
        "b1d946fd68d0347155fa59bce0a018924abbef3c1561181db45ca463d83963ef",
        63221984,
        5458,
    ),
    "hi": Voice(
        "hi_IN-priyamvada-medium",
        "hi/hi_IN/priyamvada/medium",
        "aa63bcf2cd493b55a450f280e23cf77f03afc9af7015e6e5acd43b652f166c88",
        63516050,
        4973,
    ),
    "it": Voice(
        "it_IT-paola-medium",
        "it/it_IT/paola/medium",
        "6fc918b5a0ea6137382833dddfa567bffbe6a5060c02043c87192ee59c04210c",
        63511038,
        7099,
    ),
    "ja": Voice(
        "ja_JP-hi_fi_captain-medium",
        "ja/ja_JP/hi_fi_captain/medium",
        "5eafa1610fc7a0ff2e7fde9cbe0972d876266e23d8db331727eb2466f19460eb",
        76753841,
        5301,
        speaker="female",
    ),
    "ka": Voice(
        "ka_GE-natia-medium",
        "ka/ka_GE/natia/medium",
        "04bdacf188fa24499885f9109b395fe8561a05ec2cd90d55453ec5beed7af460",
        63201294,
        4842,
    ),
    "ko": Voice(
        "ko_KR-kss-medium",
        "ko/ko_KR/kss/medium",
        "624fd774e26895f24bebae1bd9a3379e3394baeade4b584924f83e414096e2c9",
        63221984,
        5232,
    ),
    "kz": Voice(
        "kk_KZ-issai-high",
        "kk/kk_KZ/issai/high",
        "4dee767c893e8535da821447d12cb030e3569e11254c14030a1da5d8b2222c16",
        127864258,
        4358,
        speaker="ISSAI_KazakhTTS2_F1",
    ),
    "nl": Voice(
        "nl_NL-ronnie-medium",
        "nl/nl_NL/ronnie/medium",
        "c6b69203247c9ff9aaadf59ec51147fffb16bb2c2892eb92d5b75d2f9d096622",
        63221984,
        5464,
    ),
    "pl": Voice(
        "pl_PL-gosia-medium",
        "pl/pl_PL/gosia/medium",
        "38f66464240ed74f186e6b7dc13c6e3b22e023426299f25c2b3cc9dfa9373fbc",
        63201294,
        4814,
    ),
    "pt": Voice(
        "pt_PT-tugão-medium",
        "pt/pt_PT/tugão/medium",
        "223a7aaca69a155c61897e8ada7c3b13bc306e16c72dbb9c2fed733e2b0927d4",
        63201294,
        5026,
    ),
    "ru": Voice(
        "ru_RU-irina-medium",
        "ru/ru_RU/irina/medium",
        "8ff38212d23da300bbe3705c645e6e5b9475f0bfde01558eb17813e22acaaaaa",
        63201294,
        4765,
    ),
    "sv": Voice(
        "sv_SE-alma-medium",
        "sv/sv_SE/alma/medium",
        "f75455eb6afeb1db9e2905d5bd7e4115afcd3710e0d399b6a40981766d32fedc",
        63434611,
        6059,
    ),
    "te": Voice(
        "te_IN-padmavathi-medium",
        "te/te_IN/padmavathi/medium",
        "414aa5960d91ceb6e45bbdf8c27fdc71af09f205130d7be4e99470f3c2cfa57d",
        63516050,
        4974,
    ),
    "tr": Voice(
        "tr_TR-dfki-medium",
        "tr/tr_TR/dfki/medium",
        "2844717f524ab965d3fe86e60562cbb601d3e456836efcc2196cc3a14112a8fb",
        63201294,
        4960,
    ),
    "uk": Voice(
        "uk_UA-tetiana-high",
        "uk/uk_UA/tetiana/high",
        "1206d8447b99632badeb63d6132241f76cf551874e5254eb8836db36aed7c85c",
        114204024,
        4905,
    ),
    "vi": Voice(
        "vi_VN-vais1000-medium",
        "vi/vi_VN/vais1000/medium",
        "ec7c89e2c85f4d1edc24b6120c18aaf1bda614f06b511567eb9c7c0de15e2dab",
        63201294,
        4860,
    ),
}

# The Tunisian recognizer speaks the same Arabic as the others.
VOICES["ar-tn"] = VOICES["ar"]


def voices_dir() -> Path:
    """Where downloaded Piper voices live (overridable for tests and packaging)."""
    override = os.environ.get("ALMATHUS_VOICES_DIR")
    if override:
        return Path(override)
    return languages.app_dir() / "voices"


def voice_path(code: str) -> Path:
    """The .onnx file a language's voice installs to."""
    return voices_dir() / f"{VOICES[code].model}.onnx"


def is_installed(code: str) -> bool:
    """Is this language's voice on disk, model and config both non-empty?"""
    if code not in VOICES:
        return False
    onnx = voice_path(code)
    config = onnx.parent / (onnx.name + ".json")
    try:
        return onnx.stat().st_size > 0 and config.stat().st_size > 0
    except OSError:
        return False


def installed() -> list[str]:
    """Codes whose Piper voice is already on disk."""
    return [code for code in VOICES if is_installed(code)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(code: str, progress: languages.Progress | None = None) -> Path:
    """Download and verify one language's voice. Returns the .onnx path.

    Idempotent, and atomic in the same way as the Vosk download: both files are
    fetched into a temp directory beside their destination and moved into place
    only after the model matches the published sha256, so an interrupted
    download never leaves a half voice for the next run to load.
    """
    voice = VOICES.get(code)
    if voice is None:
        raise KeyError(code)  # a voice-less language is a caller bug, not a UI path
    dest = voice_path(code)
    if is_installed(code):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(dest.parent)) as tmp:
        onnx = Path(tmp) / f"{voice.model}.onnx"
        config = Path(tmp) / f"{voice.model}.onnx.json"
        languages.fetch(voice.onnx_url, onnx, voice.size, progress)
        languages.fetch(voice.config_url, config, voice.config_size, None)
        got = _sha256(onnx)
        if got != voice.sha256:
            raise RuntimeError(  # noqa: TRY003 - carries the two hashes to compare
                f"checksum mismatch for {voice.model}: {got}"
            )
        onnx.replace(dest)
        config.replace(dest.parent / (dest.name + ".json"))
    return dest
