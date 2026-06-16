#!/usr/bin/env sh
# checkenv.sh — warn if required keys from an .env.example are missing
# from the corresponding .env. Drift detector: surfaces missing config
# loudly instead of letting a stack come up half-configured.
#
# Only *uncommented* keys in the example are treated as required —
# commented lines (#KEY=...) are optional/documentation by convention.
#
# Usage: scripts/checkenv.sh EXAMPLE_FILE ENV_FILE
# Exit:  0 always (advisory, never blocks a deploy). Prints warnings.
set -eu

example="${1:?usage: checkenv.sh EXAMPLE ENV}"
envf="${2:?usage: checkenv.sh EXAMPLE ENV}"

if [ ! -f "$envf" ]; then
  echo "  check-env: $envf not found (copy from $example)"
  exit 0
fi

missing=""
# Required = uncommented KEY= lines in the example.
keys=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$example" | sed 's/=.*//')
for k in $keys; do
  if ! grep -qE "^${k}=[^[:space:]]" "$envf"; then
    missing="${missing} ${k}"
  fi
done

if [ -n "$missing" ]; then
  echo "  check-env: WARNING — required keys unset in $envf:${missing}"
else
  echo "  check-env: ok ($envf)"
fi
exit 0
