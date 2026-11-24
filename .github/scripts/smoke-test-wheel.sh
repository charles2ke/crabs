#!/usr/bin/env bash
# Install a built wheel into a throwaway virtualenv and run the CLI from
# outside the checkout, so only the packaged files are exercised.
#
# Usage: .github/scripts/smoke-test-wheel.sh dist/openclaw-X.Y.Z-py3-none-any.whl
set -euo pipefail

wheel=$(realpath "${1:?usage: smoke-test-wheel.sh path/to/openclaw-*.whl}")
config=$(realpath "${2:-examples/dublin.json}")
python=${PYTHON:-python}
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

"$python" -m venv "$workdir/venv"
"$workdir/venv/bin/python" -m pip install --quiet --disable-pip-version-check "$wheel"
cd "$workdir"
openclaw="$workdir/venv/bin/openclaw"

expected="openclaw $(basename "$wheel" | cut -d- -f2)"
actual=$("$openclaw" --version)
if [[ "$actual" != "$expected" ]]; then
  echo "::error::--version printed '$actual', expected '$expected'"
  exit 1
fi

"$openclaw" --config "$config" --validate-config
"$openclaw" --config "$config" --list-slots --state "$workdir/state.json"

# The bundled mock example always offers slots, so one cycle must alert (10).
code=0
"$openclaw" --config "$config" --once --state "$workdir/state.json" || code=$?
if [[ "$code" != 10 ]]; then
  echo "::error::--once exited $code, expected 10 (alerts dispatched)"
  exit 1
fi
echo "wheel smoke test passed: $actual"
