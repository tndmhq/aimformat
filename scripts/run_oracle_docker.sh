#!/usr/bin/env bash
# Run the LibreOffice oracle (scripts/libreoffice_oracle.py) in a container
# that matches CI's tracked-oracle job.
#
# usage: scripts/run_oracle_docker.sh <aimformat checkout> <output dir>
#
# Writes the tracked-change shapes with the checkout's own code on the host,
# then resolves and compares them inside the container, with no network and
# the checkout mounted read-only. The exit code is the oracle's (0: no
# disagreement). Several runs can go at once.
#
# Needs the image built once:
#   docker build -t aim-oracle:24.2.7 scripts/oracle-docker
# PYTHON names the host interpreter (default: python3); it needs the
# package's dependencies (pip install -e '.[dev]').
#
# On macOS, use this rather than LibreOffice's bundled Python: macOS stops
# that Python when a shell starts it (LibreOffice 25.8, a launch constraint
# in its code signature).
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: $0 <aimformat checkout> <output dir>" >&2; exit 2; }
SRC=$(cd "$1" && pwd)
mkdir -p "$2"
OUT=$(cd "$2" && pwd)
IMAGE=${AIM_ORACLE_IMAGE:-aim-oracle:24.2.7}
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "image $IMAGE not found; build it with:" >&2
  echo "  docker build -t $IMAGE $SRC/scripts/oracle-docker" >&2
  exit 2
fi
rm -rf "$OUT/shapes"
mkdir -p "$OUT/shapes"
PYTHONPATH="$SRC/src" "${PYTHON:-python3}" "$SRC/scripts/gen_docx_tracked.py" "$OUT/shapes"
exec docker run --rm --network none \
  -v "$SRC":/src:ro -v "$OUT/shapes":/shapes \
  "$IMAGE" /usr/bin/python3 /src/scripts/libreoffice_oracle.py /shapes
