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
  local _t=""
  if command -v timeout >/dev/null 2>&1; then _t="timeout 5"
  elif command -v gtimeout >/dev/null 2>&1; then _t="gtimeout 5"
  fi
  # shellcheck disable=SC2086
  if ! $_t "$mnem" --help >/dev/null 2>&1; then
    cc_die "mnemosyne CLI at $mnem failed smoke test ('mnemosyne --help' did not exit 0 quickly)"
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

# --- Materialize hook artifacts (into tmp staging first if dry-run) ---
TS=$(cc_ts)
STAGING=$(mktemp -d)
TMP_SETTINGS=""
trap 'rm -rf "$STAGING"; rm -f "$TMP_SETTINGS" "$TMP_SETTINGS.next"' EXIT

stage_file() {
  local src=$1 dest=$2
  install -m 0755 "$src" "$STAGING/$(basename "$dest")"
}
stage_text_file() {
  local src=$1 dest=$2
  install -m 0644 "$src" "$STAGING/$(basename "$dest")"
}

stage_file "$HOOK_SRC_UP"   "$CC_HOOK_USERPROMPT"
stage_file "$HOOK_SRC_STOP" "$CC_HOOK_STOP"
stage_text_file "$IGNORE_SRC" "$CC_IGNORE"

# --- Build target settings.json in tmp ---
TMP_SETTINGS=$(mktemp)
if [ -f "$CC_SETTINGS" ]; then
  jq -e . "$CC_SETTINGS" >/dev/null 2>&1 || cc_die "$CC_SETTINGS is not valid JSON; refusing to touch it"
  cp "$CC_SETTINGS" "$TMP_SETTINGS"
else
  printf '%s\n' '{}' >"$TMP_SETTINGS"
fi

# Idempotency / conflict detection.
# For each event in fragment, walk existing entries: skip if exact match; refuse if same command differs in shape.
EXIT_CONFLICT=0
for event in UserPromptSubmit Stop; do
  frag_entry=$(jq -c --arg ev "$event" '.hooks[$ev][0]' "$FRAGMENT")
  frag_cmd=$(jq -r --arg ev "$event" '.hooks[$ev][0].hooks[0].command' "$FRAGMENT")
  # Does the existing settings.json already have any entry with this command?
  matching=$(jq -c --arg ev "$event" --arg cmd "$frag_cmd" \
    '[.hooks[$ev] // [] | .[] | select(.hooks[]?.command == $cmd)]' "$TMP_SETTINGS")
  count=$(printf '%s' "$matching" | jq 'length')
  if [ "$count" -gt 0 ]; then
    # Same command exists. Is the entire entry shape identical to fragment?
    same=$(jq --argjson m "$matching" --argjson f "$frag_entry" -n '$m | any(. == $f)')
    if [ "$same" = "true" ]; then
      cc_info "  $event: already installed"
      continue
    fi
    # Same command, different shape.
    if [ "$FLAG_FORCE_REPLACE" -ne 1 ]; then
      cc_err "$event: existing entry has same command but different shape:"
      printf '%s\n' "$matching" | jq -C . >&2 || printf '%s\n' "$matching" >&2
      cc_err "Use --force-replace to overwrite."
      EXIT_CONFLICT=1
      continue
    fi
    # Force-replace: drop all entries with this command, then append.
    jq --arg ev "$event" --arg cmd "$frag_cmd" --argjson f "$frag_entry" \
      '.hooks //= {} |
       .hooks[$ev] = (((.hooks[$ev] // []) | map(select((.hooks // [] | any(.command == $cmd)) | not))) + [$f])' \
      "$TMP_SETTINGS" > "$TMP_SETTINGS.next" \
      && mv "$TMP_SETTINGS.next" "$TMP_SETTINGS" \
      || cc_die "jq merge failed for $event (force-replace)"
  else
    # No matching entry — append.
    jq --arg ev "$event" --argjson f "$frag_entry" \
      '.hooks //= {} | .hooks[$ev] = ((.hooks[$ev] // []) + [$f])' \
      "$TMP_SETTINGS" > "$TMP_SETTINGS.next" \
      && mv "$TMP_SETTINGS.next" "$TMP_SETTINGS" \
      || cc_die "jq merge failed for $event (append)"
  fi
done

if [ "$EXIT_CONFLICT" -eq 1 ]; then
  exit 2
fi

# --- Diff + confirm ---
EXISTING_FOR_DIFF=$(mktemp)
if [ -f "$CC_SETTINGS" ]; then
  jq -S . "$CC_SETTINGS" >"$EXISTING_FOR_DIFF"
else
  printf '%s\n' '{}' >"$EXISTING_FOR_DIFF"
fi
TMP_FOR_DIFF=$(mktemp)
jq -S . "$TMP_SETTINGS" >"$TMP_FOR_DIFF"

if cc_json_equal "$EXISTING_FOR_DIFF" "$TMP_FOR_DIFF"; then
  cc_ok "settings.json already in target shape — nothing to do"
  rm -f "$EXISTING_FOR_DIFF" "$TMP_FOR_DIFF"
  if [ "$FLAG_DRY_RUN" -eq 0 ]; then
    # Still copy hook scripts if missing / outdated.
    mkdir -p "$CC_HOOK_DIR"
    install -m 0755 "$STAGING/mnemosyne-user-prompt" "$CC_HOOK_USERPROMPT"
    install -m 0755 "$STAGING/mnemosyne-stop"        "$CC_HOOK_STOP"
    if [ ! -f "$CC_IGNORE" ] || [ "$FLAG_RESET_IGNORE" -eq 1 ]; then
      [ -f "$CC_IGNORE" ] && cp "$CC_IGNORE" "$CC_IGNORE.bak.$TS"
      install -m 0644 "$STAGING/mnemosyne-ignore-patterns" "$CC_IGNORE"
    fi
  fi
  exit 0
fi

cc_info ""
cc_info "┌──────────────────────────────────────────────────────────┐"
cc_info "│ The following changes will be made to $CC_SETTINGS"
cc_info "│ Backup will be saved to: $CC_SETTINGS.bak.$TS"
cc_info "└──────────────────────────────────────────────────────────┘"
if command -v colordiff >/dev/null 2>&1 && [ -t 1 ]; then
  diff -u "$EXISTING_FOR_DIFF" "$TMP_FOR_DIFF" | colordiff >&2 || true
else
  diff -u "$EXISTING_FOR_DIFF" "$TMP_FOR_DIFF" >&2 || true
fi
cc_info ""
rm -f "$EXISTING_FOR_DIFF" "$TMP_FOR_DIFF"

if [ "$FLAG_DRY_RUN" -eq 1 ]; then
  cc_ok "dry-run: no files were modified"
  exit 0
fi

if [ "$FLAG_YES" -ne 1 ]; then
  # Interactive prompt — tty-only path, non-tty caught in preflight.
  read -r -p "Apply changes? [y/N] " reply </dev/tty
  case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) cc_warn "aborted by user"; exit 0 ;;
  esac
fi

# --- Commit: backup + atomic mv ---
if [ -f "$CC_SETTINGS" ]; then
  cp "$CC_SETTINGS" "$CC_SETTINGS.bak.$TS"
  cc_info "backup: $CC_SETTINGS.bak.$TS"
fi

mv "$TMP_SETTINGS" "$CC_SETTINGS"
if ! jq -e . "$CC_SETTINGS" >/dev/null 2>&1; then
  cc_err "post-commit JSON sanity check failed — rolling back"
  [ -f "$CC_SETTINGS.bak.$TS" ] && mv "$CC_SETTINGS.bak.$TS" "$CC_SETTINGS"
  exit 1
fi

# Place hook + ignore files.
mkdir -p "$CC_HOOK_DIR"
for dst in "$CC_HOOK_USERPROMPT" "$CC_HOOK_STOP"; do
  if [ -f "$dst" ] && ! cmp -s "$STAGING/$(basename "$dst")" "$dst"; then
    cp "$dst" "$dst.bak.$TS"
    cc_info "hook backup: $dst.bak.$TS"
  fi
  install -m 0755 "$STAGING/$(basename "$dst")" "$dst"
done
if [ ! -f "$CC_IGNORE" ] || [ "$FLAG_RESET_IGNORE" -eq 1 ]; then
  [ -f "$CC_IGNORE" ] && cp "$CC_IGNORE" "$CC_IGNORE.bak.$TS"
  install -m 0644 "$STAGING/mnemosyne-ignore-patterns" "$CC_IGNORE"
  cc_info "ignore-patterns installed at $CC_IGNORE"
else
  cc_info "existing $CC_IGNORE kept (repo default at $IGNORE_SRC)"
fi

cc_ok "installed"
if [ -f "$CC_SETTINGS.bak.$TS" ]; then
  cc_info "Rollback: mv $CC_SETTINGS.bak.$TS $CC_SETTINGS"
else
  cc_info "Fresh install — no settings.json backup needed (rm $CC_SETTINGS to undo)"
fi

# --- Post-install smoke (non-fatal) ---
SMOKE_JSON='{"prompt":"installer smoke ping","session_id":"x","transcript_path":"/tmp/none","cwd":"/tmp"}'
if printf '%s' "$SMOKE_JSON" | "$CC_HOOK_USERPROMPT" | jq -e . >/dev/null 2>&1; then
  cc_ok "smoke: UserPromptSubmit hook round-trip OK"
else
  cc_warn "smoke: hook returned non-JSON or non-zero exit (may be fine on first run — DB cold start)"
fi
