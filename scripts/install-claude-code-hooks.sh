#!/usr/bin/env bash
# Install Mnemosyne Claude Code hooks into ~/.claude/.
# See docs/claude-code-integration.md.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/claude-code-hooks-common.sh
source "$SCRIPT_DIR/lib/claude-code-hooks-common.sh"

REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
FRAGMENT="$REPO_ROOT/claude_code_hooks/settings.fragment.json"
HOOK_SRC_UP="$REPO_ROOT/claude_code_hooks/hooks/mnemosyne-user-prompt"
HOOK_SRC_STOP="$REPO_ROOT/claude_code_hooks/hooks/mnemosyne-stop"
IGNORE_SRC="$REPO_ROOT/claude_code_hooks/mnemosyne-ignore-patterns"
MIN_MNEMOSYNE_VERSION="3.1.0"

FLAG_DRY_RUN=0
FLAG_YES=0
FLAG_FORCE_REPLACE=0
FLAG_RESET_IGNORE=0
OPT_MNEM_BIN=""

usage() {
  cat <<EOF
Usage: install-claude-code-hooks.sh [options]

Options:
  --dry-run                  Print diff and exit, never modify anything.
  --yes                      Skip interactive confirmation (required when stdin is not a tty).
  --force-replace            Overwrite existing entries with the same command but different shape.
  --reset-ignore-patterns    Replace ~/.claude/mnemosyne-ignore-patterns (backup-then-replace).
  --mnemosyne-bin=<path>     Use this mnemosyne CLI instead of PATH lookup.
  -h, --help                 Show this help.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) FLAG_DRY_RUN=1 ;;
    --yes|-y) FLAG_YES=1 ;;
    --force-replace) FLAG_FORCE_REPLACE=1 ;;
    --reset-ignore-patterns) FLAG_RESET_IGNORE=1 ;;
    --mnemosyne-bin=*) OPT_MNEM_BIN="${1#*=}" ;;
    -h|--help) usage; exit 0 ;;
    *) cc_err "unknown argument: $1"; usage >&2; exit 64 ;;
  esac
  shift
done

[ -n "$OPT_MNEM_BIN" ] && export MNEMOSYNE_BIN="$OPT_MNEM_BIN"

# --- Preflight ---
preflight() {
  cc_info "Preflight checks"

  # 1/5 bash version (just informational, 3.2 OK)
  cc_ok "bash ${BASH_VERSION%%(*}"

  # 2/5 jq
  command -v jq >/dev/null 2>&1 || cc_die "jq missing — install with: brew install jq  # or: apt install jq"
  cc_ok "jq $(jq --version 2>/dev/null | sed 's/^jq-//')"

  # 3/5 mnemosyne CLI
  local mnem; mnem=$(cc_resolve_mnem) || cc_die "mnemosyne CLI not found — install with: pip install mnemosyne-memory  # then re-run, or pass --mnemosyne-bin=<path>"
  if ! timeout 5 "$mnem" --help >/dev/null 2>&1; then
    cc_die "mnemosyne CLI at $mnem failed smoke test ('mnemosyne --help' did not exit 0 in 5s)"
  fi
  local ver; ver=$("$mnem" --version 2>/dev/null | head -1 || true)
  cc_ok "mnemosyne CLI ($mnem)${ver:+ — $ver}"

  # 4/5 ~/.claude writable
  mkdir -p "$HOME/.claude" || cc_die "cannot create $HOME/.claude"
  [ -w "$HOME/.claude" ] || cc_die "$HOME/.claude is not writable"
  cc_ok "$HOME/.claude writable"

  # 5/5 hook source files in repo
  for f in "$HOOK_SRC_UP" "$HOOK_SRC_STOP" "$IGNORE_SRC" "$FRAGMENT"; do
    [ -r "$f" ] || cc_die "missing repo file: $f"
  done
  cc_ok "hook source files present"
}

preflight

# --- Confirmation gate (stub, implemented in Task 8) ---
if [ "$FLAG_DRY_RUN" -eq 0 ] && [ "$FLAG_YES" -eq 0 ] && [ ! -t 0 ]; then
  cc_die "non-tty run requires --yes or --dry-run"
fi

# Task 8 implements the mutation flow below this line.
cc_warn "mutation flow not yet implemented — preflight passed"
exit 0
