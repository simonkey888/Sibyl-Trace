#!/usr/bin/env bash
set -euo pipefail

fail=0
patterns=(
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'
  'sk-proj-[A-Za-z0-9_-]{16,}'
  'AKIA[0-9A-Z]{16}'
  'ghp_[A-Za-z0-9]{30,}'
  'github_pat_[A-Za-z0-9_]{30,}'
)

for pattern in "${patterns[@]}"; do
  if git grep -n -E "$pattern" -- . \
    ':(exclude)scripts/secret-scan.sh' \
    ':(exclude)docs/**' \
    ':(exclude)**/*.md'; then
    echo "Potential secret material matched pattern: $pattern" >&2
    fail=1
  fi
done

if git grep -n -E 'LIVE_TRADING_ENABLED[=:][[:space:]]*(true|TRUE|1)' -- \
  '.github/**' 'infra/**' 'services/**'; then
  echo "LIVE trading enablement detected." >&2
  fail=1
fi

if git grep -n -E 'COST_AUTHORIZED_USD[=:][[:space:]]*[1-9]' -- \
  '.github/**' 'infra/**' 'services/**'; then
  echo "Non-zero cost authorization detected." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "Secret/live/cost boundary scan PASS"
