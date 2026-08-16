#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
scanner="$repo_root/scripts/secret-scan.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

make_repo() {
  local dir="$1"
  mkdir -p "$dir/services" "$dir/infra" "$dir/.github"
  git -C "$dir" init -q
  git -C "$dir" config user.email order-001@example.invalid
  git -C "$dir" config user.name ORDER-001
  printf 'safe=true\n' > "$dir/services/config.txt"
  git -C "$dir" add .
  git -C "$dir" commit -qm init
}

clean="$work/clean"
make_repo "$clean"
(
  cd "$clean"
  bash "$scanner" >/dev/null
)

secret="$work/secret"
make_repo "$secret"
printf '%s\n' '-----BEGIN PRIVATE KEY-----' > "$secret/services/leak.txt"
git -C "$secret" add services/leak.txt
git -C "$secret" commit -qm leak
if (
  cd "$secret"
  bash "$scanner" >/dev/null 2>&1
); then
  echo "secret scanner failed to reject synthetic private key" >&2
  exit 1
fi

api="$work/api"
make_repo "$api"
printf '%s\n' 'sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX' > "$api/services/leak.txt"
git -C "$api" add services/leak.txt
git -C "$api" commit -qm leak
if (
  cd "$api"
  bash "$scanner" >/dev/null 2>&1
); then
  echo "secret scanner failed to reject synthetic API key" >&2
  exit 1
fi

broken="$work/broken"
make_repo "$broken"
mkdir -p "$broken/fakebin"
cat > "$broken/fakebin/git" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "grep" ]]; then
  echo "synthetic git grep failure" >&2
  exit 2
fi
exec /usr/bin/git "$@"
SH
chmod +x "$broken/fakebin/git"
if (
  cd "$broken"
  PATH="$broken/fakebin:$PATH" bash "$scanner" >/dev/null 2>&1
); then
  echo "secret scanner converted git grep error into PASS" >&2
  exit 1
fi

echo "Secret scanner adversarial fixtures PASS"
