#!/usr/bin/env bash
# Forge installer entry point: checks for python3 3.11 or newer, then hands every argument
# to install.py beside this script.
# Design: docs/intent/installer/installer-design.md

# @spec INST-CLI-001, INST-CLI-002
here=${BASH_SOURCE[0]%/*}
[ "$here" = "${BASH_SOURCE[0]}" ] && here=.
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "forge-install: PYTHON_TOO_OLD: Forge needs python3 3.11 or newer on PATH" >&2
  exit 1
fi
exec python3 "$here/install.py" "$@"
