#!/usr/bin/env bash
# Run every test. Needs ffmpeg/ffprobe; the suites stand up their own stub servers.
cd "$(dirname "$0")"
fail=0
for t in tests/test_*.py; do
  name=$(basename "$t" .py)
  out=$(timeout 250 python3 "$t" 2>&1 | grep -E "passed|Error|assert" | tail -1)
  printf "%-18s %s\n" "$name" "$out"
  echo "$out" | grep -q passed || fail=1
done
[ $fail -eq 0 ] && echo "all green" || echo "FAILURES above"
exit $fail
