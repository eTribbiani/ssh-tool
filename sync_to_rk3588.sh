#!/usr/bin/env bash
#
# Incremental sync through two-hop SSH tunnel: local ↔ A733 ↔ RK3588
#
# Topology:
#   local ──ssh──► A733 (192.168.1.241:60000) ──ssh──► RK3588 (10.21.20.200:22)
#
# Method: Local port forwarding (no rsync/sshpass needed on A733).
# rsync runs entirely on local machine, through the tunnel.
#
# Push (local → remote, default):
#   ./scripts/sync_to_rk3588.sh                                        # sync cwd → /home/BAOBAO/MM/
#   ./scripts/sync_to_rk3588.sh src/MM-Nav                              # sync src/MM-Nav → /home/BAOBAO/MM/
#   ./scripts/sync_to_rk3588.sh src/MM-Nav workspace/mm_nav_ws/src      # → /home/BAOBAO/MM/workspace/mm_nav_ws/src
#   ./scripts/sync_to_rk3588.sh . workspace/mm_nav_ws --dry-run         # preview only
#
# Pull (remote → local):
#   ./scripts/sync_to_rk3588.sh --reverse                               # /home/BAOBAO/MM/ → cwd
#   ./scripts/sync_to_rk3588.sh --reverse ./lib                          # /home/BAOBAO/MM/ → ./lib
#   ./scripts/sync_to_rk3588.sh --reverse ./lib workspace/mm_nav_ws/src # /home/BAOBAO/MM/workspace/mm_nav_ws/src → ./lib
#   ./scripts/sync_to_rk3588.sh --reverse . workspace/mm_nav_ws --dry-run
#
# Remote path = /home/BAOBAO/MM/ + remote-sub-path (if provided)
#

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────

GW_HOST="192.168.1.214"
GW_PORT=60000
GW_USER="bpx"
GW_PASS="bpx1024"

RK_HOST="10.21.20.200"
RK_USER="BAOBAO"
RK_PASS="BAOBAO2024"
RK_DEFAULT_PATH="/home/BAOBAO/MM/"

TUNNEL_PORT=22358  # Local forwarded port

# ─── Parse arguments ────────────────────────────────────────────────

REVERSE=""
DRY_RUN=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reverse)
      REVERSE="1"
      shift
      ;;
    --dry-run)
      DRY_RUN="--dry-run"
      shift
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ -n "$REVERSE" ]]; then
  # Pull: arg1 = local dest (default .), arg2 = remote sub-path (optional)
  LOCAL_DST="${POSITIONAL[0]:-.}"
  REMOTE_SUB="${POSITIONAL[1]:-}"
else
  # Push: arg1 = local src (default .), arg2 = remote sub-path (optional)
  LOCAL_SRC="${POSITIONAL[0]:-.}"
  REMOTE_SUB="${POSITIONAL[1]:-}"
fi

# Build full remote path: base + sub-path (strip leading / from sub-path)
REMOTE_PATH="${RK_DEFAULT_PATH}${REMOTE_SUB#/}"

# ─── Cleanup on exit ─────────────────────────────────────────────────

TUNNEL_PID=""
cleanup() {
  if [[ -n "$TUNNEL_PID" ]]; then
    kill "$TUNNEL_PID" 2>/dev/null || true
    echo "  Tunnel closed."
  fi
}
trap cleanup EXIT

# ─── Validation ──────────────────────────────────────────────────────

if [[ -n "$REVERSE" ]]; then
  [[ ! -d "$LOCAL_DST" ]] && echo "ERROR: local dest '$LOCAL_DST' not found." && exit 1
else
  [[ ! -d "$LOCAL_SRC" ]] && echo "ERROR: '$LOCAL_SRC' not found." && exit 1
fi

# ─── Display plan ────────────────────────────────────────────────────

if [[ -n "$REVERSE" ]]; then
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║           Incremental Sync: RK3588 → A733 → local            ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║                                                               ║"
printf "║  Source:  %-52s ║\n" "$RK_USER@$RK_HOST:$REMOTE_PATH"
printf "║  Target:  %-52s ║\n" "$(realpath "$LOCAL_DST")"
printf "║  Gateway: %-52s ║\n" "$GW_USER@$GW_HOST:$GW_PORT"
echo "║                                                               ║"
else
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║           Incremental Sync: local → A733 → RK3588            ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║                                                               ║"
printf "║  Source:  %-52s ║\n" "$(realpath "$LOCAL_SRC")"
printf "║  Target:  %-52s ║\n" "$RK_USER@$RK_HOST:$REMOTE_PATH"
printf "║  Gateway: %-52s ║\n" "$GW_USER@$GW_HOST:$GW_PORT"
echo "║                                                               ║"
fi
if [[ -n "$DRY_RUN" ]]; then
echo "║  Mode:    DRY-RUN (no files transferred)                      ║"
else
echo "║  Mode:    INCREMENTAL (rsync --delete)                        ║"
fi
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# ─── Step 1: Create SSH tunnel ───────────────────────────────────────

echo "[1/3] Creating SSH tunnel (local:$TUNNEL_PORT → A733 → RK3588:22)..."

sshpass -p "$GW_PASS" ssh -p "$GW_PORT" -o StrictHostKeyChecking=no \
  -f -N -L "$TUNNEL_PORT:$RK_HOST:22" \
  "$GW_USER@$GW_HOST"

# Get tunnel PID (the ssh process we just started)
TUNNEL_PID=$(pgrep -f "ssh.*-L.*$TUNNEL_PORT.*$GW_HOST" | head -1)
echo "  ✓ Tunnel established (PID=$TUNNEL_PID)"

# Give tunnel a moment to stabilize
sleep 1

# ─── Step 2: Verify tunnel ───────────────────────────────────────────

echo "[2/3] Verifying tunnel..."

sshpass -p "$RK_PASS" ssh -p "$TUNNEL_PORT" -o StrictHostKeyChecking=no \
  "$RK_USER@127.0.0.1" "echo OK" 2>/dev/null | grep -q "OK"

if [[ $? -ne 0 ]]; then
  echo "ERROR: Cannot reach RK3588 through tunnel. Check credentials."
  exit 1
fi
echo "  ✓ RK3588 reachable via tunnel"

# ─── Step 3: rsync through tunnel ────────────────────────────────────

echo "[3/3] Syncing..."

RSYNC_OPTS=(
  -avz --delete --compress
  --info=progress2
  --exclude='.git/'
  --exclude='build/'
  --exclude='install/'
  --exclude='.cache/'
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='*.o'
  --exclude='*.a'
  --exclude='*.deb'
  --exclude='*.zip'
  --exclude='*.tar.gz'
  --exclude='compile_commands.json'
  -e "sshpass -p '$RK_PASS' ssh -p $TUNNEL_PORT -o StrictHostKeyChecking=no"
)

if [[ -n "$REVERSE" ]]; then
  rsync "${RSYNC_OPTS[@]}" \
    "$RK_USER@127.0.0.1:$REMOTE_PATH" \
    "$LOCAL_DST" \
    $DRY_RUN
else
  rsync "${RSYNC_OPTS[@]}" \
    "$LOCAL_SRC" \
    "$RK_USER@127.0.0.1:$REMOTE_PATH" \
    $DRY_RUN
fi

echo ""
echo "✓ Sync complete."
