#!/usr/bin/env bash
# Run Vibemapp from the source tree, with the GSettings schema and
# icons from data/. Usage: ./run-dev.sh [--background] ...
#   VIBEMAPP_MOCK=1 ./run-dev.sh      use the simulated keyboard
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
schemas="$root/build-dev/schemas"

mkdir -p "$schemas"
rm -f "$schemas/io.github.albertflindt.KeyMappForLinux.gschema.xml"   # pre-rename leftover
cp "$root/data/io.github.albertflindt.Vibemapp.gschema.xml" "$schemas/"
glib-compile-schemas --strict "$schemas"

export GSETTINGS_SCHEMA_DIR="$schemas${GSETTINGS_SCHEMA_DIR:+:$GSETTINGS_SCHEMA_DIR}"
export XDG_DATA_DIRS="$root/data:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export VIBEMAPP_DEVEL=1   # separate app ID (…Vibemapp.Devel), so the installed app can keep running
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m vibemapp "$@"
