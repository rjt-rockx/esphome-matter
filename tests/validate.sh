#!/usr/bin/env bash
# Run `esphome config` on every YAML in tests/configs/ and examples/.
# Used by CI and runnable locally: tests/validate.sh [esphome-binary]
# Prepared copies (ci_tmp_*.yaml, local components, dummy secrets) are removed on exit.
set -u
cd "$(dirname "$0")/.."
ESPHOME="${1:-${ESPHOME:-esphome}}"
# VALIDATE_DIRS (space separated) selects directories; default: tests/configs examples
# The component needs the native ESP-IDF toolchain on ESPHome >= 2026.9.0 (and dev)
# and `toolchain: platformio` on older releases; configs omit the line.
toolchain=$("$ESPHOME" version 2>/dev/null | python3 -c '
import re, sys
m = re.search(r"(\d+)\.(\d+)", sys.stdin.read())
print("platformio" if m and (int(m[1]), int(m[2])) < (2026, 9) else "esp-idf")
')
echo "Toolchain: $toolchain"
failed=()
passed=0
cleanup() {
  rm -f tests/configs/ci_tmp_*.yaml examples/ci_tmp_*.yaml
  for s in tests/configs/secrets.yaml examples/secrets.yaml; do
    [ -f "$s" ] && ! git ls-files --error-unmatch "$s" >/dev/null 2>&1 && rm -f "$s"
  done
}
trap cleanup EXIT

shopt -s nullglob
for dir in ${VALIDATE_DIRS:-tests/configs examples}; do
for f in "$dir"/*.yaml; do
  case "$f" in */ci_tmp_*|*/secrets.yaml) continue ;; esac
  echo "::group::esphome config $f"
  prepared=$(python3 .github/scripts/ci_prepare.py "$f" --toolchain "$toolchain")
  if out=$("$ESPHOME" config "$prepared" 2>&1); then
    echo "OK   $f"
    passed=$((passed + 1))
  else
    echo "$out" | tail -40
    echo "::error file=$f::esphome config failed"
    failed+=("$f")
  fi
  echo "::endgroup::"
done
done

echo "Validated: $passed passed, ${#failed[@]} failed"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Config validation ($("$ESPHOME" version))"
    echo "Passed: $passed, failed: ${#failed[@]}"
    for f in "${failed[@]}"; do echo "- FAILED \`$f\`"; done
  } >> "$GITHUB_STEP_SUMMARY"
fi
[ ${#failed[@]} -eq 0 ]
