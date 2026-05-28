#!/usr/bin/env bash
# Shared helpers for Claude Code hooks scripts. Not directly executable; source me.

# Sentinel — refuse to run as a script.
[ "${BASH_SOURCE[0]}" = "$0" ] && { echo "claude-code-hooks-common.sh: must be sourced, not executed" >&2; exit 1; }

# Color helpers — degrade to plain when stdout is not a tty.
if [ -t 1 ]; then
  CC_RED=$'\033[31m'; CC_GREEN=$'\033[32m'; CC_YELLOW=$'\033[33m'; CC_DIM=$'\033[2m'; CC_RESET=$'\033[0m'
else
  CC_RED=''; CC_GREEN=''; CC_YELLOW=''; CC_DIM=''; CC_RESET=''
fi

cc_info()  { printf '%s%s%s\n' "$CC_DIM" "$*" "$CC_RESET" >&2; }
cc_ok()    { printf '%s[ ok ]%s %s\n' "$CC_GREEN" "$CC_RESET" "$*" >&2; }
cc_warn()  { printf '%s[warn]%s %s\n' "$CC_YELLOW" "$CC_RESET" "$*" >&2; }
cc_err()   { printf '%s[err ]%s %s\n' "$CC_RED" "$CC_RESET" "$*" >&2; }
cc_die()   { cc_err "$*"; exit "${CC_EXIT_CODE:-1}"; }

# Resolve the mnemosyne CLI the same way the hooks do.
cc_resolve_mnem() {
  if [ -n "${MNEMOSYNE_BIN:-}" ] && [ -x "$MNEMOSYNE_BIN" ]; then
    printf '%s' "$MNEMOSYNE_BIN"; return 0
  fi
  if command -v mnemosyne >/dev/null 2>&1; then
    command -v mnemosyne; return 0
  fi
  for cand in "$HOME/.local/bin/mnemosyne" \
              "$HOME/.local/venvs/mnemosyne/bin/mnemosyne" \
              "/usr/local/bin/mnemosyne" \
              "/opt/homebrew/bin/mnemosyne"; do
    [ -x "$cand" ] && { printf '%s' "$cand"; return 0; }
  done
  return 1
}

# Compute a unix timestamp for backup file naming.
cc_ts() { date +%s; }

# Compare two files structurally as JSON — exit 0 if equal, 1 if differ.
cc_json_equal() {
  local a=$1 b=$2
  jq -S . "$a" >/dev/null 2>&1 || return 2
  jq -S . "$b" >/dev/null 2>&1 || return 2
  diff <(jq -S . "$a") <(jq -S . "$b") >/dev/null 2>&1
}

# Default hook destination paths (relative to $HOME).
CC_HOOK_DIR="$HOME/.claude/hooks"
CC_SETTINGS="$HOME/.claude/settings.json"
CC_IGNORE="$HOME/.claude/mnemosyne-ignore-patterns"
CC_HOOK_USERPROMPT="$CC_HOOK_DIR/mnemosyne-user-prompt"
CC_HOOK_STOP="$CC_HOOK_DIR/mnemosyne-stop"
