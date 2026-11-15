#!/usr/bin/env bash
# Build the GPL-3.0 source tarball that install.txt points at.
#
#   packaging/gpl-source.sh 0.1.0 [outdir]
#
# The sidecar is a separate GPL-3.0 program linked against piper-tts and
# espeak-ng. Distributing the frozen binary obliges us to offer its exact
# corresponding source, and that is this file: our sidecar, the engine's own
# sdist as pinned in uv.lock, the spec and driver used to freeze it, and the
# licence text.
set -euo pipefail

VERSION="${1:?usage: gpl-source.sh <version> [outdir]}"
OUT="${2:-dist}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$OUT"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PIPER_VERSION="$(uv run --directory "$ROOT" python -c \
  'import importlib.metadata as m; print(m.version("piper-tts"))')"

NAME="almathus-voice-${VERSION}"
STAGE="${WORK}/${NAME}"
mkdir -p "${STAGE}/packaging/pyinstaller" "${STAGE}/packaging/linux" "${STAGE}/vendored"

# 1. Our sidecar, verbatim.
cp -R "${ROOT}/sidecar" "${STAGE}/sidecar"

# 2. The build inputs that produced the frozen binary.
cp "${ROOT}/packaging/build.py" "${STAGE}/packaging/"
cp "${ROOT}/packaging/pyinstaller/piper-sidecar.spec" "${STAGE}/packaging/pyinstaller/"
cp -R "${ROOT}/packaging/pyinstaller/hooks" "${STAGE}/packaging/pyinstaller/"
cp "${ROOT}/sidecar/LICENSE" "${STAGE}/GPL-3.0.txt"

# 3. The engine's own source archive, fetched at the version uv.lock pins.
uv run --directory "$ROOT" python - "$PIPER_VERSION" "${STAGE}/vendored" <<'PY'
import json
import sys
import urllib.request

version, dest = sys.argv[1], sys.argv[2]
meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/piper-tts/{version}/json"))
sdist = next(f for f in meta["urls"] if f["packagetype"] == "sdist")
target = f"{dest}/{sdist['filename']}"
urllib.request.urlretrieve(sdist["url"], target)
print(f"vendored {sdist['filename']} ({sdist['size'] // 1000} kB)")
PY

cat > "${STAGE}/BUILDING.md" <<EOF
# Almathus voice engine — corresponding source

Version: ${VERSION}
piper-tts: ${PIPER_VERSION} (vendored sdist, plus its upstream URL below)

This archive contains everything needed to rebuild the \`piper-sidecar\` binary
that ships with Almathus ${VERSION}:

| Path | What it is |
|---|---|
| \`sidecar/\` | our sidecar program, GPL-3.0-or-later, unmodified |
| \`vendored/piper_tts-${PIPER_VERSION}.tar.gz\` | the engine's own source |
| \`packaging/pyinstaller/piper-sidecar.spec\` | the freeze recipe |
| \`packaging/pyinstaller/hooks/\` | one hook override (see below) |
| \`packaging/build.py\` | the driver that runs PyInstaller and the acceptance test |

espeak-ng ships inside the \`piper\` package (\`piper/espeak-ng-data\`) and comes
from the same vendored sdist.

## Rebuild

\`\`\`sh
python3.12 -m venv venv && . venv/bin/activate
pip install "piper-tts==${PIPER_VERSION}" pyinstaller
pyinstaller --noconfirm --clean --onedir --name piper-sidecar \\
    --collect-data piper sidecar/piper_sidecar.py
printf '{"cmd":"ping"}\\n{"cmd":"quit"}\\n' | ./dist/piper-sidecar/piper-sidecar --model <voice.onnx>
\`\`\`

The spec in \`packaging/pyinstaller/\` is what the shipped binary was actually
built with; it also collects \`piper/espeak-ng-data\`, without which the frozen
program starts, answers \`ping\`, and then produces silence.
EOF

tar -czf "${OUT}/${NAME}.tar.gz" -C "${WORK}" "${NAME}"
echo "wrote ${OUT}/${NAME}.tar.gz"
