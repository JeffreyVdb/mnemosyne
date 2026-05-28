#!/usr/bin/env bash
# Mock mnemosyne CLI used by tests. Exits 0 on every invocation, logs args.
case "${1:-}" in
  --version) echo "mnemosyne mock 3.1.0" ;;
  --help)    echo "mnemosyne mock --help" ;;
  *)         printf '[mock-mnemosyne] %s\n' "$*" >>"${MOCK_MNEM_LOG:-/tmp/mock-mnem.log}" ;;
esac
exit 0
