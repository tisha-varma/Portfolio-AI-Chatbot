#!/usr/bin/env bash
# Pre-commit secret scan.
#
# Scans the staged index for common credential patterns and blocks the commit
# when a match is found. Only file names are printed - never the matched text.
#
# Install as a pre-commit hook (Git Bash, macOS, or Linux):
#   cp scripts/check_secrets.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit

set -euo pipefail

patterns=(
  'gsk_[A-Za-z0-9]{20,}'
  'AIza[0-9A-Za-z_-]{30,}'
  'sk-[A-Za-z0-9]{20,}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
)

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "check_secrets: not inside a git repository." >&2
  exit 1
fi

matched=()
for pattern in "${patterns[@]}"; do
  while IFS= read -r file; do
    if [ -n "$file" ]; then
      matched+=("$file")
    fi
  done < <(git grep --cached -l -I -E -e "$pattern" -- . 2>/dev/null || true)
done

if [ "${#matched[@]}" -eq 0 ]; then
  exit 0
fi

echo "Secret scan blocked this commit: potential credentials found in:" >&2
printf '%s\n' "${matched[@]}" | sort -u | while IFS= read -r file; do
  echo "  $file" >&2
done
echo "Remove the secret from the staged files and rotate the key with the provider." >&2
exit 1
