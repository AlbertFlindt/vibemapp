#!/usr/bin/env bash
# Project health check: lint (byte-compile + data validation) and the full
# unit test suite. Exits non-zero on the first failure.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

echo "==> Byte-compiling Python (syntax check)"
python3 -m compileall -q src tests >/dev/null

echo "==> Validating data files"
desktop-file-validate data/io.github.albertflindt.Vibemapp.desktop
# Optional: metainfo only matters for software centres, not the AUR package.
if command -v appstreamcli >/dev/null; then
  appstreamcli validate --no-net --pedantic data/io.github.albertflindt.Vibemapp.metainfo.xml >/dev/null
else
  echo "    appstreamcli not found, skipping metainfo validation"
fi
glib-compile-schemas --strict --dry-run data

echo "==> Unit tests"
# PyGObject 3.58 calls asyncio APIs deprecated in Python 3.14; not our code.
PYTHONPATH=src python3 -W "ignore::DeprecationWarning:gi.events" \
  -m unittest discover -s tests -t .

echo "==> OK"
