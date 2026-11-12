"""Supported languages, and the on-disk store of their Vosk models.

One entry per language the app can run end to end. The setup and settings
pickers read this registry, so adding a language is a data change here plus its
number words in :mod:`voice`; nothing else has to learn a new code.

Only "small" Vosk models are listed. They are the CPU-friendly ones — 30 MB to
160 MB — and every language below has one, which is what keeps the app on an
ordinary laptop. The names, hashes and sizes mirror Vosk's published catalog
(https://alphacephei.com/vosk/models/model-list.json); pinning the md5 lets a
download be verified before it is ever unpacked.

Nothing here is loaded at import on the hot path: the registry is a plain tuple
and the model directory is only touched when the app asks for it.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Every small model lives at the same path on the same host, so the url is not
# stored per entry — one typo'd host cannot hide among thirty correct ones.
_BASE_URL = "https://alphacephei.com/vosk/models/"
_APP_DIR = "Almathus"

# How each language is spelled out by num2words, where the library can do it.
# Two names differ from ours (en-in -> en_IN; Chinese has no converter). A
# language absent here runs keypad-only for voice, which is the documented
# degradation for the six num2words cannot spell: hi, gu, cn, ka, ky, uz.
_NUM2WORDS: dict[str, str] = {
    "fr": "fr",
    "en-us": "en",
    "en-gb": "en",
    "en-in": "en_IN",
    "es": "es",
    "de": "de",
    "it": "it",
    "pt": "pt",
    "nl": "nl",
    "pl": "pl",
    "ru": "ru",
    "uk": "uk",
    "cs": "cs",
    "sv": "sv",
    "tr": "tr",
    "ar": "ar",
    "ar-tn": "ar",
    "fa": "fa",
    "te": "te",
    "ja": "ja",
    "ko": "ko",
    "vi": "vi",
    "ca": "ca",
    "eo": "eo",
    "kz": "kz",
    "tg": "tg",
}

Progress = Callable[[int, int], None]
"""Called as (bytes_done, bytes_total) while a model downloads."""


@dataclass(frozen=True)
class Language:
    code: str  # app/B CP-47-ish code, e.g. "fr", "en-us", "ar-tn"
    endonym: str  # the language's own name for itself, shown in the pickers
    english: str  # English name, for logs and the setup screen
    flag: str  # flag-icons stem (e.g. "gb"); "" falls back to a globe
    model: str  # Vosk model directory name
    md5: str  # Vosk's published hash for the archive
    size: int  # archive size in bytes, so a progress bar has a total

    @property
    def url(self) -> str:
        return f"{_BASE_URL}{self.model}.zip"

    @property
    def num2words(self) -> str | None:
        """num2words' code for this language, or None when it has no converter."""
        return _NUM2WORDS.get(self.code)


# Frozen copy of Vosk's small-model catalog. Ordered roughly by speaker count,
# but the pickers sort by endonym; the order here is only for readability.
LANGUAGES: tuple[Language, ...] = (
    Language(
        "fr",
        "Français",
        "French",
        "fr",
        "vosk-model-small-fr-0.22",
        "8873b1234503f6edd55f54bfff31cf3e",
        42233323,
    ),
    Language(
        "en-us",
        "English (US)",
        "US English",
        "us",
        "vosk-model-small-en-us-0.15",
        "09ab50ccd62b674cbaa231b825f9c1cb",
        41205931,
    ),
    Language(
        "en-gb",
        "English (UK)",
        "UK English",
        "gb",
        "vosk-model-small-en-gb-0.15",
        "6afd611b04b2b47c129c3615dc502383",
        42757500,
    ),
    Language(
        "en-in",
        "English (India)",
        "Indian English",
        "in",
        "vosk-model-small-en-in-0.4",
        "62fd085f33c8c5fc01a235030e2a1fe3",
        37573330,
    ),
    Language(
        "es",
        "Español",
        "Spanish",
        "es",
        "vosk-model-small-es-0.42",
        "2d5c94f9859a84881a0ef744738ebd31",
        39817833,
    ),
    Language(
        "de",
        "Deutsch",
        "German",
        "de",
        "vosk-model-small-de-0.15",
        "4f21f92c0897b48287ef8839420608eb",
        46499967,
    ),
    Language(
        "it",
        "Italiano",
        "Italian",
        "it",
        "vosk-model-small-it-0.22",
        "fbd8f9c72cbb8c3dfa3e4581bd3585f4",
        49665141,
    ),
    Language(
        "pt",
        "Português",
        "Portuguese",
        "pt",
        "vosk-model-small-pt-0.3",
        "458c69371c5a0b9ab6ee8fa417bf89da",
        32453112,
    ),
    Language(
        "nl",
        "Nederlands",
        "Dutch",
        "nl",
        "vosk-model-small-nl-0.22",
        "4582e1f20d6849099da08511f9797017",
        40441176,
    ),
    Language(
        "pl",
        "Polski",
        "Polish",
        "pl",
        "vosk-model-small-pl-0.22",
        "91cbbd6231320467da672be31827b6ac",
        52979372,
    ),
    Language(
        "ru",
        "Русский",
        "Russian",
        "ru",
        "vosk-model-small-ru-0.22",
        "d1759dc83eb8fd87850129afbd9f4b7b",
        46236750,
    ),
    Language(
        "uk",
        "Українська",
        "Ukrainian",
        "ua",
        "vosk-model-small-uk-v3-small",
        "3188e41b4a38369d8b9527d0149616ab",
        143914407,
    ),
    Language(
        "cs",
        "Čeština",
        "Czech",
        "cz",
        "vosk-model-small-cs-0.4-rhasspy",
        "df5ddac5ee8632f6b46a6fc751953286",
        46088666,
    ),
    Language(
        "sv",
        "Svenska",
        "Swedish",
        "se",
        "vosk-model-small-sv-rhasspy-0.15",
        "5ae431c65fe8636692118792daa22ecc",
        303504931,
    ),
    Language(
        "tr",
        "Türkçe",
        "Turkish",
        "tr",
        "vosk-model-small-tr-0.3",
        "198511631860597639a0ebf779263fcf",
        36855784,
    ),
    Language(
        "ar",
        "العربية",
        "Arabic",
        "sa",
        "vosk-model-small-ar-0.3",
        "0eb578c0779f85ef039b7ee5bc1b64ed",
        104351896,
    ),
    Language(
        "ar-tn",
        "العربية (تونس)",
        "Arabic (Tunisian)",
        "tn",
        "vosk-model-small-ar-tn-0.1-linto",
        "67bbe16beaa17e6cf14f8c295ee5ddd1",
        165703754,
    ),
    Language(
        "fa",
        "فارسی",
        "Farsi",
        "ir",
        "vosk-model-small-fa-0.42",
        "cc2b18af256ffab2c44055f6a02ecb3d",
        53431220,
    ),
    Language(
        "hi",
        "हिन्दी",
        "Hindi",
        "in",
        "vosk-model-small-hi-0.22",
        "80f1265262c9a8a515f2707498e1b485",
        44458845,
    ),
    Language(
        "gu",
        "ગુજરાતી",
        "Gujarati",
        "in",
        "vosk-model-small-gu-0.42",
        "4595a6f0cc0c88fee6eec7d80496fc10",
        108054987,
    ),
    Language(
        "te",
        "తెలుగు",
        "Telugu",
        "in",
        "vosk-model-small-te-0.42",
        "ae3c093fd7dd883d462983b15900ca61",
        60544249,
    ),
    Language(
        "ja",
        "日本語",
        "Japanese",
        "jp",
        "vosk-model-small-ja-0.22",
        "0e3163dd62dfb0d823353718ac3cbf79",
        49704573,
    ),
    Language(
        "ko",
        "한국어",
        "Korean",
        "kr",
        "vosk-model-small-ko-0.22",
        "fa8029a173787a159e0e72fe6135f890",
        86914329,
    ),
    Language(
        "cn",
        "中文",
        "Chinese",
        "cn",
        "vosk-model-small-cn-0.22",
        "d1a8e82933dcc7632667c036b0bb3dbb",
        43898754,
    ),
    Language(
        "vi",
        "Tiếng Việt",
        "Vietnamese",
        "vn",
        "vosk-model-small-vn-0.4",
        "b31b474a1ef75488c5fa575f8e2a1269",
        33656337,
    ),
    Language(
        "ca",
        "Català",
        "Catalan",
        "es-ct",
        "vosk-model-small-ca-0.4",
        "5dc7901815fef3dc8b784b31f309385c",
        43405881,
    ),
    Language(
        "eo",
        "Esperanto",
        "Esperanto",
        "",
        "vosk-model-small-eo-0.42",
        "3e9319ca789fa06fd3efa68d51c85d6b",
        43839401,
    ),
    Language(
        "ka",
        "ქართული",
        "Georgian",
        "ge",
        "vosk-model-small-ka-0.42",
        "7e95b6a69b60a5f1c45e29d1cd2ac353",
        45682310,
    ),
    Language(
        "ky",
        "Кыргызча",
        "Kyrgyz",
        "kg",
        "vosk-model-small-ky-0.42",
        "541eb45589c2b6f8350905086a76f072",
        51041096,
    ),
    Language(
        "kz",
        "Қазақша",
        "Kazakh",
        "kz",
        "vosk-model-small-kz-0.42",
        "1c7dc487ac5e0c24acfa7566c106ba2d",
        59697294,
    ),
    Language(
        "tg",
        "Тоҷикӣ",
        "Tajik",
        "tj",
        "vosk-model-small-tg-0.22",
        "fca4e40de915a5bd2f30e32d60af45c0",
        51879043,
    ),
    Language(
        "uz",
        "Oʻzbekcha",
        "Uzbek",
        "uz",
        "vosk-model-small-uz-0.22",
        "4d75acfef76fe919c8fb68cd1179c182",
        51061189,
    ),
)

BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}


def _base_dir() -> Path:
    """The per-user application-data directory for this OS.

    A model is user data, not app data: it is downloaded after install into a
    place the current user can write, on an app that may itself live in a
    read-only location. ``platformdirs`` would say the same thing; the three
    lines are kept here so the voice extra stays dependency-free.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        return Path(local) if local else Path.home() / "AppData" / "Local"
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def app_dir() -> Path:
    """The app's own per-user directory; models and voices live under it."""
    return _base_dir() / _APP_DIR


def models_dir() -> Path:
    """Where installed Vosk models live (overridable for tests and packaging)."""
    override = os.environ.get("ALMATHUS_MODELS_DIR")
    if override:
        return Path(override)
    return app_dir() / "models"


def model_path(code: str) -> Path:
    """The directory a language's Vosk model is (or would be) unpacked into."""
    return models_dir() / BY_CODE[code].model


def is_installed(code: str) -> bool:
    """Is this language's model present and non-empty on disk?

    A directory that exists but is empty is a half-finished install, not an
    install: the check is "has files", so a crashed download never counts.
    """
    lang = BY_CODE.get(code)
    if lang is None:
        return False
    d = models_dir() / lang.model
    try:
        return any(d.iterdir())
    except OSError:
        return False


def installed() -> list[str]:
    """Codes of every language whose model is already on disk."""
    return [lang.code for lang in LANGUAGES if is_installed(lang.code)]


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)  # integrity, not security
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, dest: Path, expected: int, progress: Progress | None) -> None:
    """Stream a model archive to ``dest``, reporting progress as it goes.

    The url always comes from this module's pinned registry (never from the
    user or the network), which is why opening it is safe.
    """
    req = urllib.request.Request(  # noqa: S310 - url is a pinned constant
        url, headers={"User-Agent": "Almathus"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - pinned url
        total = int(resp.headers.get("Content-Length") or expected)
        done = 0
        with dest.open("wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)


def _safe_extract(archive: zipfile.ZipFile, where: Path) -> str:
    """Extract a Vosk archive and return its single top-level directory name.

    Vosk ships one directory inside the zip. Members are checked to stay under
    ``where`` so a crafted archive cannot write outside the temp directory.
    """
    names = archive.namelist()
    roots = {name.split("/", 1)[0] for name in names}
    if len(roots) != 1:
        raise RuntimeError(  # noqa: TRY003 - internal invariant, not user input
            "model archive has an unexpected layout"
        )
    root = roots.pop()
    target = (where / root).resolve()
    for name in names:
        member = (where / name).resolve()
        if member != target and target not in member.parents:
            raise RuntimeError(  # noqa: TRY003 - craftable input, plain message
                f"unsafe path in model archive: {name!r}"
            )
    archive.extractall(where)
    return root


def download(code: str, progress: Progress | None = None) -> Path:
    """Download, verify and unpack one language's model. Returns its directory.

    Idempotent: an already-installed model is returned untouched. The archive is
    fetched to a temp file, checked against the published md5, then unpacked
    beside it and moved into place, so a failure at any step leaves no
    half-installed model behind for the next launch to trip over.
    """
    lang = BY_CODE.get(code)
    if lang is None:
        raise KeyError(code)  # an unregistered code is a caller bug, not a UI path
    dest = model_path(code)
    if is_installed(code):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(dest.parent)) as tmp:
        tmp_path = Path(tmp)
        archive_path = tmp_path / f"{lang.model}.zip"
        fetch(lang.url, archive_path, lang.size, progress)
        got = _md5(archive_path)
        if got != lang.md5:
            raise RuntimeError(  # noqa: TRY003 - carries the two hashes to compare
                f"checksum mismatch for {lang.model}: {got}"
            )
        with zipfile.ZipFile(archive_path) as archive:
            root = _safe_extract(archive, tmp_path)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(tmp_path / root), str(dest))
    return dest
