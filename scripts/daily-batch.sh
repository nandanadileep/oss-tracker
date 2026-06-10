#!/bin/bash
# Daily OSS Batch Script
# Thin wrapper around the Python Backlog Steward.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BATCH_SIZE="${1:-5}"
DRY_RUN="${2:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "═══════════════════════════════════════════════════════════════"
echo "  Daily OSS Agent Batch"
echo "  Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Batch Size: $BATCH_SIZE"
echo "  Dry Run: $DRY_RUN"
echo "  Runner: $(hostname)"
echo "═══════════════════════════════════════════════════════════════"

# ── Safety Checks ───────────────────────────────────────────────────────────

if ! command -v gh &> /dev/null; then
    echo -e "${RED}✗ gh CLI not found${NC}"
    exit 1
fi

if ! command -v opencode &> /dev/null; then
    echo -e "${RED}✗ opencode not found${NC}"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ python3 not found${NC}"
    exit 1
fi

# Verify gh auth
gh auth status || {
    echo -e "${RED}✗ gh CLI not authenticated${NC}"
    exit 1
}

ARGS=("--batch-size" "$BATCH_SIZE")
if [[ "$DRY_RUN" == "true" ]]; then
    ARGS+=("--dry-run")
fi

python3 "$REPO_DIR/agent/backlog_steward.py" "${ARGS[@]}"
