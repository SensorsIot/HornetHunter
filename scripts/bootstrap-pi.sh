#!/usr/bin/env bash
# Set up one Raspberry Pi to work on its own slice of this repo.
#
# The repo is shared, but each Pi uses a git sparse-checkout so only its own
# target directory (plus shared/ and docs/) ever lands on disk. Pull and push
# behave normally; commits made on a Pi only ever touch files it can see.
#
#   ./scripts/bootstrap-pi.sh kraken
#   ./scripts/bootstrap-pi.sh management
#
# Re-running is safe: it re-applies the sparse set and reinstalls the venv.

set -euo pipefail

REPO_URL="${HORNETHUNTER_REPO:-https://github.com/SensorsIot/HornetHunter.git}"
BRANCH="${HORNETHUNTER_BRANCH:-main}"

usage() {
    cat >&2 <<'EOF'
usage: bootstrap-pi.sh <kraken|management> [checkout-dir]

  kraken       KrakenSDR ground station  -> kraken_pi/
  management   aggregator / operator UI  -> management_pi/

Run inside an existing clone, or pass a checkout-dir to clone into it.
Env: HORNETHUNTER_REPO, HORNETHUNTER_BRANCH override the remote and branch.
EOF
    exit 2
}

[ $# -ge 1 ] || usage

ROLE="$1"
case "$ROLE" in
    kraken)     TARGET_DIR="kraken_pi" ;;
    management) TARGET_DIR="management_pi" ;;
    *)          echo "error: unknown role '$ROLE'" >&2; usage ;;
esac

CHECKOUT="${2:-}"
if [ -n "$CHECKOUT" ]; then
    if [ ! -d "$CHECKOUT/.git" ]; then
        echo "==> cloning $REPO_URL into $CHECKOUT (no checkout yet)"
        git clone --filter=blob:none --no-checkout --branch "$BRANCH" "$REPO_URL" "$CHECKOUT"
    fi
    cd "$CHECKOUT"
elif ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "error: not inside a git clone, and no checkout-dir given" >&2
    usage
else
    cd "$(git rev-parse --show-toplevel)"
fi

echo "==> restricting checkout to: $TARGET_DIR shared docs scripts"
git sparse-checkout init --cone
# Cone mode always keeps root-level files (README, LICENSE, pyproject.toml).
git sparse-checkout set "$TARGET_DIR" shared docs scripts
git checkout "$BRANCH"

echo "==> creating .venv"
python3 -m venv --upgrade-deps .venv

# shared must go in first: the target package depends on it by name, and pip
# would otherwise go looking for hornethunter-shared on PyPI.
echo "==> installing hornethunter-shared, then hornethunter-$ROLE"
./.venv/bin/pip install --quiet -e shared
./.venv/bin/pip install --quiet -e "$TARGET_DIR"

CONFIG_NAME="$ROLE.toml"
cat <<EOF

Done. This clone now contains only $TARGET_DIR/, shared/, docs/ and scripts/.

  1. Configure:
       sudo install -d /etc/hornethunter
       sudo cp $TARGET_DIR/config.example.toml /etc/hornethunter/$CONFIG_NAME
       sudo \${EDITOR:-nano} /etc/hornethunter/$CONFIG_NAME

  2. Try it:
       ./.venv/bin/hornethunter-$ROLE --config /etc/hornethunter/$CONFIG_NAME --help

  3. Install the service (see docs/deployment.md for the full walk-through):
       sudo cp $TARGET_DIR/systemd/hornethunter-$ROLE.service /etc/systemd/system/
       sudo systemctl daemon-reload
       sudo systemctl enable --now hornethunter-$ROLE

  Updating later:  git pull && ./.venv/bin/pip install -e shared -e $TARGET_DIR
EOF
