#!/usr/bin/env bash
# Uninstall Mnemosyne Claude Code hooks from ~/.claude/.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/claude-code-hooks-common.sh
source "$SCRIPT_DIR/lib/claude-code-hooks-common.sh"

REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
FRAGMENT="$REPO_ROOT/claude_code_hooks/settings.fragment.json"

FLAG_DRY_RUN=0
FLAG_YES=0
FLAG_PURGE=0

usage() {
  cat <<EOF
Usage: uninstall-claude-code-hooks.sh [options]

Options:
  --dry-run        Print diff and exit.
  --yes            Skip interactive confirmation.
  --purge-files    Also delete the hook scripts and ignore-patterns file.
  -h, --help       Show this help.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) FLAG_DRY_RUN=1 ;;
    --yes|-y) FLAG_YES=1 ;;
    --purge-files) FLAG_PURGE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) cc_err "unknown argument: $1"; usage >&2; exit 64 ;;
  esac
  shift
done

command -v jq >/dev/null 2>&1 || cc_die "jq missing"
[ -r "$FRAGMENT" ] || cc_die "missing repo file: $FRAGMENT"

if [ ! -f "$CC_SETTINGS" ]; then
  cc_ok "$CC_SETTINGS not present — nothing to uninstall"
  exit 0
fi
jq -e . "$CC_SETTINGS" >/dev/null 2>&1 || cc_die "$CC_SETTINGS is not valid JSON"

if [ "$FLAG_DRY_RUN" -eq 0 ] && [ "$FLAG_YES" -eq 0 ] && [ ! -t 0 ]; then
  cc_die "non-tty run requires --yes or --dry-run"
fi

TS=$(cc_ts)
TMP=$(mktemp)
trap 'rm -f "$TMP" "$TMP.next"' EXIT
cp "$CC_SETTINGS" "$TMP"

# For each event in the fragment, strip any entry whose hooks include the fragment command.
for event in UserPromptSubmit Stop; do
  frag_cmd=$(jq -r --arg ev "$event" '.hooks[$ev][0].hooks[0].command' "$FRAGMENT")
  jq --arg ev "$event" --arg cmd "$frag_cmd" \
    '.hooks //= {} |
     .hooks[$ev] = ((.hooks[$ev] // []) | map(select((.hooks // [] | any(.command == $cmd)) | not))) |
     if (.hooks[$ev] | length) == 0 then del(.hooks[$ev]) else . end' \
    "$TMP" > "$TMP.next" \
    && mv "$TMP.next" "$TMP" \
    || cc_die "jq strip failed for $event"
done

# Drop empty .hooks object if nothing left.
jq 'if (.hooks // {} | length) == 0 then del(.hooks) else . end' "$TMP" > "$TMP.next" \
  && mv "$TMP.next" "$TMP" \
  || cc_die "jq cleanup failed"

EXIST_DIFF=$(mktemp); jq -S . "$CC_SETTINGS" >"$EXIST_DIFF"
TMP_DIFF=$(mktemp);  jq -S . "$TMP" >"$TMP_DIFF"

if cc_json_equal "$EXIST_DIFF" "$TMP_DIFF"; then
  cc_ok "no mnemosyne entries found in $CC_SETTINGS — nothing to remove"
  rm -f "$EXIST_DIFF" "$TMP_DIFF"
else
  cc_info ""
  cc_info "┌──────────────────────────────────────────────────────────┐"
  cc_info "│ The following changes will be made to $CC_SETTINGS"
  cc_info "│ Backup will be saved to: $CC_SETTINGS.bak.$TS"
  cc_info "└──────────────────────────────────────────────────────────┘"
  diff -u "$EXIST_DIFF" "$TMP_DIFF" >&2 || true
  rm -f "$EXIST_DIFF" "$TMP_DIFF"

  if [ "$FLAG_DRY_RUN" -eq 1 ]; then
    cc_ok "dry-run: no files were modified"
    exit 0
  fi

  if [ "$FLAG_YES" -ne 1 ]; then
    read -r -p "Apply changes? [y/N] " reply </dev/tty
    case "$reply" in [yY]|[yY][eE][sS]) ;; *) cc_warn "aborted by user"; exit 0 ;; esac
  fi

  cp "$CC_SETTINGS" "$CC_SETTINGS.bak.$TS"
  cc_info "backup: $CC_SETTINGS.bak.$TS"
  mv "$TMP" "$CC_SETTINGS"
  cc_ok "uninstalled (settings.json)"
  cc_info "Rollback: mv $CC_SETTINGS.bak.$TS $CC_SETTINGS"
fi

if [ "$FLAG_PURGE" -eq 1 ]; then
  for f in "$CC_HOOK_USERPROMPT" "$CC_HOOK_STOP" "$CC_IGNORE"; do
    if [ -f "$f" ]; then
      cp "$f" "$f.bak.$TS"
      rm -f "$f"
      cc_info "removed $f (backup: $f.bak.$TS)"
    fi
  done
fi

cc_info "DB at \$MNEMOSYNE_DATA_DIR (default ~/.hermes/mnemosyne/data) was not touched."
