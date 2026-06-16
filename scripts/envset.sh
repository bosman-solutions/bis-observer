#!/usr/bin/env sh
# envset.sh — idempotent upsert of KEY=VALUE in an env file.
#
# Replaces the key's line in place if it already exists (commented-out
# documentation lines beginning with '#' are left untouched), otherwise
# appends it exactly once. This is the cure for .env accretion: make
# targets must call this instead of `>>` appending.
#
# Usage: scripts/envset.sh FILE KEY [VALUE]
#
# Notes:
#   - VALUE may be empty (clears the key but keeps it present).
#   - Output is intentionally silent so callers can use it for secrets.
#     Callers that want a confirmation line should print their own
#     (and must NOT print the value for sensitive keys).
set -eu

file="${1:?usage: envset.sh FILE KEY [VALUE]}"
key="${2:?usage: envset.sh FILE KEY [VALUE]}"
val="${3-}"

# Reject anything that isn't a plausible env key, so a bad arg can't
# turn into a sed expression that mangles the file.
case "$key" in
  *[!A-Za-z0-9_]* | "" ) echo "envset: invalid key '$key'" >&2; exit 2 ;;
esac

touch "$file"

if grep -qE "^${key}=" "$file"; then
  # Escape the replacement side for sed (delimiter '|', plus & and \).
  esc=$(printf '%s' "$val" | sed -e 's/[\\&|]/\\&/g')
  sed -i "s|^${key}=.*|${key}=${esc}|" "$file"
else
  printf '%s=%s\n' "$key" "$val" >> "$file"
fi
