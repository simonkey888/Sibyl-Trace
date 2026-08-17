#!/usr/bin/env bash
set -euo pipefail

scan_pattern() {
  local label="$1"
  local pattern="$2"
  shift 2
  local output
  local rc

  set +e
  output=$(git grep -n -E -e "$pattern" -- "$@" 2>&1)
  rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    printf '%s\n' "$output"
    echo "$label" >&2
    return 1
  fi
  if [[ $rc -eq 1 ]]; then
    return 0
  fi

  printf '%s\n' "$output" >&2
  echo "Secret/live/cost scanner execution failure" >&2
  return 2
}

secret_paths=(
  .
  ':(exclude)scripts/secret-scan.sh'
  ':(exclude)scripts/test-secret-scan.sh'
  ':(exclude)docs/**'
  ':(exclude)**/*.md'
)
runtime_paths=(
  '.github/**'
  'infra/**'
  'services/**'
)

patterns=(
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'
  'sk-proj-[A-Za-z0-9_-]{16,}'
  'AKIA[0-9A-Z]{16}'
  'ghp_[A-Za-z0-9]{30,}'
  'github_pat_[A-Za-z0-9_]{30,}'
)

for pattern in "${patterns[@]}"; do
  scan_pattern \
    "Potential secret material matched pattern: $pattern" \
    "$pattern" \
    "${secret_paths[@]}"
done

scan_pattern \
  "LIVE trading enablement detected." \
  'LIVE_TRADING_ENABLED[=:][[:space:]]*(true|TRUE|1)' \
  "${runtime_paths[@]}"

scan_pattern \
  "Non-zero cost authorization detected." \
  'COST_AUTHORIZED_USD[=:][[:space:]]*[1-9]' \
  "${runtime_paths[@]}"

echo "Secret/live/cost boundary scan PASS"
