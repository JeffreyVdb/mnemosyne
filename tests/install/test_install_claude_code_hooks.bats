#!/usr/bin/env bats
# Tests for scripts/install-claude-code-hooks.sh.

setup() {
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  export REPO_ROOT
  TEST_HOME="$(mktemp -d)"
  export HOME="$TEST_HOME"   # sandbox ~/.claude/
  export MOCK_MNEM_LOG="$TEST_HOME/mock-mnem.log"
  mkdir -p "$TEST_HOME/.claude"
  # Wire mock mnemosyne onto PATH so preflight passes.
  MOCK_DIR="$(mktemp -d)"
  install -m 755 "$BATS_TEST_DIRNAME/helpers/mock-mnemosyne.sh" "$MOCK_DIR/mnemosyne"
  export PATH="$MOCK_DIR:$PATH"
  export INSTALLER="$REPO_ROOT/scripts/install-claude-code-hooks.sh"
}

teardown() {
  rm -rf "$TEST_HOME" "$MOCK_DIR"
}

@test "installer file exists and is executable" {
  [ -x "$INSTALLER" ]
}

@test "fresh install with --yes creates hook files and patches settings.json" {
  cp "$BATS_TEST_DIRNAME/fixtures/empty-settings.json" "$HOME/.claude/settings.json"
  run "$INSTALLER" --yes
  [ "$status" -eq 0 ]
  [ -x "$HOME/.claude/hooks/mnemosyne-user-prompt" ]
  [ -x "$HOME/.claude/hooks/mnemosyne-stop" ]
  [ -f "$HOME/.claude/mnemosyne-ignore-patterns" ]
  # UserPromptSubmit + Stop both wired.
  jq -e '.hooks.UserPromptSubmit[0].hooks[0].command' "$HOME/.claude/settings.json" >/dev/null
  jq -e '.hooks.Stop[0].hooks[0].command' "$HOME/.claude/settings.json" >/dev/null
}

@test "fresh install without settings.json seeds it" {
  rm -f "$HOME/.claude/settings.json"
  run "$INSTALLER" --yes
  [ "$status" -eq 0 ]
  [ -f "$HOME/.claude/settings.json" ]
  jq -e '.hooks.UserPromptSubmit' "$HOME/.claude/settings.json" >/dev/null
}

@test "install preserves pre-existing unrelated hooks" {
  cp "$BATS_TEST_DIRNAME/fixtures/only-other-hooks-settings.json" "$HOME/.claude/settings.json"
  run "$INSTALLER" --yes
  [ "$status" -eq 0 ]
  jq -e '.hooks.PreToolUse[0].hooks[0].command == "~/.claude/hooks/some-other-hook"' "$HOME/.claude/settings.json" >/dev/null
}

@test "second install on already-installed system is a no-op" {
  cp "$BATS_TEST_DIRNAME/fixtures/already-installed-settings.json" "$HOME/.claude/settings.json"
  cksum_before=$(jq -S . "$HOME/.claude/settings.json" | shasum | awk '{print $1}')
  run "$INSTALLER" --yes
  [ "$status" -eq 0 ]
  cksum_after=$(jq -S . "$HOME/.claude/settings.json" | shasum | awk '{print $1}')
  [ "$cksum_before" = "$cksum_after" ]
}

@test "install refuses conflicting entry without --force-replace" {
  cp "$BATS_TEST_DIRNAME/fixtures/conflicting-entry-settings.json" "$HOME/.claude/settings.json"
  run "$INSTALLER" --yes
  [ "$status" -eq 2 ]
}

@test "install with --force-replace overwrites conflicting entry" {
  cp "$BATS_TEST_DIRNAME/fixtures/conflicting-entry-settings.json" "$HOME/.claude/settings.json"
  run "$INSTALLER" --yes --force-replace
  [ "$status" -eq 0 ]
  jq -e '.hooks.UserPromptSubmit[0].hooks[0].timeout == 15' "$HOME/.claude/settings.json" >/dev/null
}

@test "--dry-run prints diff and writes nothing" {
  cp "$BATS_TEST_DIRNAME/fixtures/empty-settings.json" "$HOME/.claude/settings.json"
  before_mtime=$(stat -f %m "$HOME/.claude/settings.json" 2>/dev/null || stat -c %Y "$HOME/.claude/settings.json")
  run "$INSTALLER" --dry-run
  [ "$status" -eq 0 ]
  after_mtime=$(stat -f %m "$HOME/.claude/settings.json" 2>/dev/null || stat -c %Y "$HOME/.claude/settings.json")
  [ "$before_mtime" = "$after_mtime" ]
  # No hook files written either.
  [ ! -e "$HOME/.claude/hooks/mnemosyne-user-prompt" ]
}

@test "non-tty run without --yes refuses to proceed" {
  cp "$BATS_TEST_DIRNAME/fixtures/empty-settings.json" "$HOME/.claude/settings.json"
  run bash -c "$INSTALLER </dev/null"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "non-tty" ]]
}

@test "install creates timestamped backup" {
  cp "$BATS_TEST_DIRNAME/fixtures/only-other-hooks-settings.json" "$HOME/.claude/settings.json"
  run "$INSTALLER" --yes
  [ "$status" -eq 0 ]
  # Exactly one .bak.<ts> file produced.
  count=$(ls "$HOME/.claude/settings.json.bak."* 2>/dev/null | wc -l | tr -d ' ')
  [ "$count" -eq 1 ]
}

@test "preflight aborts when jq is missing" {
  # Strip jq from PATH only.
  noJqDir=$(mktemp -d)
  for util in bash mnemosyne diff cat grep sed awk stat date readlink mktemp install printf cp mv rm ls; do
    src=$(command -v "$util" 2>/dev/null) && ln -s "$src" "$noJqDir/$util"
  done
  run env PATH="$noJqDir" "$INSTALLER" --yes
  rm -rf "$noJqDir"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "jq" ]]
}

@test "preflight aborts when mnemosyne CLI is missing" {
  # Use a PATH with no mnemosyne, no fallback locations populated either.
  minimalDir=$(mktemp -d)
  for util in bash jq diff cat grep sed awk stat date readlink mktemp install printf cp mv rm ls; do
    src=$(command -v "$util" 2>/dev/null) && ln -s "$src" "$minimalDir/$util"
  done
  run env -i HOME="$HOME" PATH="$minimalDir" "$INSTALLER" --yes
  rm -rf "$minimalDir"
  [ "$status" -ne 0 ]
  [[ "$output" =~ "mnemosyne" ]]
}
