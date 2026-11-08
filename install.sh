#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -ne 1 ]; then
    printf 'Usage: %s "/absolute/path/to/Obsidian Vault"\nClose Obsidian before installing.\n' "$0" >&2
    exit 1
fi
for command in python3 node npm; do
    command -v "$command" >/dev/null || { printf 'Required command: %s\n' "$command" >&2; exit 1; }
done
python3 -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); sys.exit(0 if p.is_absolute() and p.is_dir() else "Vault must be an existing absolute directory")' "$1"
printf '%s\n' 'Close Obsidian before installing. Preparing local installation tools…'
(cd "$project_dir" && npm ci --ignore-scripts --no-audit --no-fund --engine-strict)
exec python3 "$project_dir/scripts/install.py" "$1"
