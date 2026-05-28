#!/usr/bin/env bash
# Verify a Mnemosyne Claude Code hooks install. Re-runnable any time.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/claude-code-hooks-common.sh
source "$SCRIPT_DIR/lib/claude-code-hooks-common.sh"

FAIL=0

cc_info "Verify Mnemosyne <-> Claude Code integration"

# 1. CLI alive
if mnem=$(cc_resolve_mnem) && "$mnem" --help >/dev/null 2>&1; then
  cc_ok "1/5 mnemosyne CLI: $mnem"
else
  cc_err "1/5 mnemosyne CLI: not found or not responding"
  FAIL=1
fi

# 2. Hook bash syntax
syntax_ok=1
for f in "$CC_HOOK_USERPROMPT" "$CC_HOOK_STOP"; do
  if [ ! -x "$f" ]; then
    cc_err "2/5 hook not executable: $f"
    syntax_ok=0; FAIL=1
  elif ! bash -n "$f" 2>/dev/null; then
    cc_err "2/5 hook has bash syntax error: $f"
    syntax_ok=0; FAIL=1
  fi
done
[ "$syntax_ok" -eq 1 ] && cc_ok "2/5 hook scripts syntax-clean"

# 3. Round-trip UserPromptSubmit hook
SMOKE_JSON='{"prompt":"verify smoke ping","session_id":"x","transcript_path":"/tmp/none","cwd":"/tmp"}'
if [ -x "$CC_HOOK_USERPROMPT" ]; then
  out=$(printf '%s' "$SMOKE_JSON" | "$CC_HOOK_USERPROMPT" 2>/dev/null || true)
  if [ -z "$out" ] || printf '%s' "$out" | jq -e . >/dev/null 2>&1; then
    cc_ok "3/5 UserPromptSubmit hook round-trip OK (empty or valid JSON)"
  else
    cc_err "3/5 UserPromptSubmit hook produced invalid JSON: $out"
    FAIL=1
  fi
else
  cc_err "3/5 UserPromptSubmit hook missing"
  FAIL=1
fi

# 4. settings.json shape
shape_ok=1
if [ ! -f "$CC_SETTINGS" ]; then
  cc_err "4/5 $CC_SETTINGS missing"
  shape_ok=0; FAIL=1
else
  up_len=$(jq '.hooks.UserPromptSubmit // [] | length' "$CC_SETTINGS")
  stop_len=$(jq '.hooks.Stop // [] | length' "$CC_SETTINGS")
  if [ "$up_len" -lt 1 ] || [ "$stop_len" -lt 1 ]; then
    cc_err "4/5 settings.json missing hook entries (UserPromptSubmit=$up_len, Stop=$stop_len)"
    shape_ok=0; FAIL=1
  fi
fi
[ "$shape_ok" -eq 1 ] && cc_ok "4/5 settings.json shape OK"

# 5. Log clean (or absent)
LOG="$HOME/.claude/mnemosyne-hook.log"
if [ -f "$LOG" ]; then
  recent_errors=$(tail -50 "$LOG" 2>/dev/null | grep -ciE 'error|fail' || true)
  if [ "$recent_errors" -gt 0 ]; then
    cc_warn "5/5 log has $recent_errors recent error/fail lines — review $LOG"
  else
    cc_ok "5/5 log clean ($LOG)"
  fi
else
  cc_ok "5/5 log absent (no hook invocations yet — expected on fresh install)"
fi

if [ "$FAIL" -ne 0 ]; then
  cc_err "verification failed"
  exit 1
fi
cc_ok "all checks passed"
exit 0
