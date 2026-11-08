#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 || ! "$1" =~ ^(MT4|MT5)$ ]]; then
  printf '%s\n' 'Usage: bash install-terminal.sh MT5 "/path/from/MetaTrader/Open Data Folder"' >&2
  exit 2
fi
platform=$1
data_folder=$2
if [[ "$platform" == MT5 ]]; then lang=MQL5; ext=mq5; else lang=MQL4; ext=mq4; fi
kit_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_file="$kit_dir/COREIOSMiniTerminalCheck.$ext"
experts="$data_folder/$lang/Experts"
[[ -f "$source_file" && -d "$experts" ]] || { printf '%s\n' 'Source or Experts folder missing. Use File > Open Data Folder in the correct terminal.' >&2; exit 1; }
[[ ! -L "$experts/COREIOSMini" ]] || { printf '%s\n' 'Installation directory must not be a symbolic link.' >&2; exit 1; }
install -d -- "$experts/COREIOSMini"
target="$experts/COREIOSMini/COREIOSMiniTerminalCheck.$ext"
if [[ -e "$target" || -L "$target" ]]; then
  [[ -f "$target" && ! -L "$target" ]] || { printf '%s\n' 'Existing target is not a regular file.' >&2; exit 1; }
  cp -p -- "$target" "$target.backup.$(date -u +%Y%m%dT%H%M%S).$$"
fi
stage=$(mktemp "$experts/COREIOSMini/.install.XXXXXX")
trap 'rm -f -- "$stage"' EXIT
cp -- "$source_file" "$stage"
chmod 644 "$stage"
mv -- "$stage" "$target"
printf 'Installed source: %s\n' "$target"
printf '%s\n' 'Next: open source in MetaEditor and compile (F7). Require 0 errors.' 'Then attach COREIOSMiniTerminalCheck to a chart. VPS connection remains unavailable.'
