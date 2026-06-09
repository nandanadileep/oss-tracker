#!/bin/bash
# Daily OSS Batch Script
# Orchestrates the daily PR processing batch.
# Runs on a self-hosted GitHub Actions runner where opencode and gh are configured.

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

# ── Read Queue ──────────────────────────────────────────────────────────────

QUEUE_FILE="$REPO_DIR/.oss-harness/state/queue.json"
PROGRESS_FILE="$REPO_DIR/.oss-harness/state/progress.md"

if [[ ! -f "$QUEUE_FILE" ]]; then
    echo -e "${RED}✗ Queue file not found: $QUEUE_FILE${NC}"
    exit 1
fi

# Extract top N PRs from queue
# Queue format: ["owner/repo#number", "owner/repo#number", ...]
# We read the first BATCH_SIZE items
mapfile -t PR_LIST < <(python3 -c "
import json
with open('$QUEUE_FILE') as f:
    queue = json.load(f)
    # Filter out done/deferred items if the queue has structure
    # For now, assume simple list of 'owner/repo#number' strings
    for item in queue[:$BATCH_SIZE]:
        if isinstance(item, str) and '#' in item:
            print(item)
        elif isinstance(item, dict) and 'pr' in item:
            print(item['pr'])
")

if [[ ${#PR_LIST[@]} -eq 0 ]]; then
    echo -e "${YELLOW}⚠ Queue is empty or no actionable items${NC}"
    exit 0
fi

# ── Process Batch ───────────────────────────────────────────────────────────

PROCESSED=0
SKIPPED=0
NUDGED=0
CLOSED=0
FIXED=0
FAILED=0

# Start opencode server in background for reuse
echo "→ Starting opencode server..."
opencode serve --port 4096 &
SERVER_PID=$!

# Wait for server to be ready
for i in {1..30}; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:4096/health 2>/dev/null | grep -q "200"; then
        echo -e "${GREEN}✓ opencode server ready${NC}"
        break
    fi
    sleep 1
    if [[ $i -eq 30 ]]; then
        echo -e "${YELLOW}⚠ opencode server not ready, continuing anyway${NC}"
    fi
done

cleanup() {
    echo "→ Stopping opencode server..."
    kill $SERVER_PID 2>/dev/null || true
    wait $SERVER_PID 2>/dev/null || true
}
trap cleanup EXIT

# Process each PR
for PR_ITEM in "${PR_LIST[@]}"; do
    echo ""
    echo "───────────────────────────────────────────────────────────────"
    echo "Processing: $PR_ITEM"
    echo "───────────────────────────────────────────────────────────────"
    
    # Parse owner/repo#number
    if [[ "$PR_ITEM" =~ ^([^/]+)/([^#]+)#([0-9]+)$ ]]; then
        OWNER="${BASH_REMATCH[1]}"
        REPO="${BASH_REMATCH[2]}"
        NUMBER="${BASH_REMATCH[3]}"
    else
        echo -e "${YELLOW}⚠ Invalid format: $PR_ITEM, skipping${NC}"
        ((SKIPPED++)) || true
        continue
    fi
    
    FULL_REPO="$OWNER/$REPO"
    
    # Run analysis
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "[DRY RUN] Analyzing $FULL_REPO#$NUMBER"
        python3 "$REPO_DIR/agent/opencode-runner.py" "$FULL_REPO" "$NUMBER" --dry-run || {
            echo -e "${YELLOW}⚠ Analysis failed for $FULL_REPO#$NUMBER${NC}"
            ((FAILED++)) || true
            continue
        }
    else
        echo "→ Analyzing $FULL_REPO#$NUMBER..."
        
        # Run the analysis
        DECISION_OUTPUT=$(python3 "$REPO_DIR/agent/opencode-runner.py" "$FULL_REPO" "$NUMBER" 2>&1) || {
            echo -e "${YELLOW}⚠ Analysis failed for $FULL_REPO#$NUMBER${NC}"
            echo "$DECISION_OUTPUT"
            ((FAILED++)) || true
            continue
        }
        
        echo "$DECISION_OUTPUT"
        
        # Parse decision from output (last JSON block)
        DECISION_JSON=$(echo "$DECISION_OUTPUT" | python3 -c "
import sys, json, re
output = sys.stdin.read()
# Find the last JSON block
matches = re.findall(r'\{.*\}', output, re.DOTALL)
if matches:
    try:
        data = json.loads(matches[-1])
        print(json.dumps(data))
    except:
        pass
")
        
        if [[ -z "$DECISION_JSON" ]]; then
            echo -e "${YELLOW}⚠ Could not parse decision, skipping${NC}"
            ((SKIPPED++)) || true
            continue
        fi
        
        ACTION=$(echo "$DECISION_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin).get('action', 'skip'))")
        REASON=$(echo "$DECISION_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin).get('reason', ''))")
        CONFIDENCE=$(echo "$DECISION_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin).get('confidence', 0))")
        COMMENT_BODY=$(echo "$DECISION_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin).get('comment_body', ''))")
        REQUIRES_HUMAN=$(echo "$DECISION_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin).get('requires_human', False))")
        
        echo "→ Decision: $ACTION (confidence: $CONFIDENCE/10)"
        echo "→ Reason: $REASON"
        
        # Safety: never execute if confidence < 5 and action is not skip
        if [[ "$CONFIDENCE" -lt 5 && "$ACTION" != "skip" && "$ACTION" != "defer" ]]; then
            echo -e "${YELLOW}⚠ Low confidence ($CONFIDENCE), converting to defer${NC}"
            ACTION="defer"
        fi
        
        # Safety: never close without explicit reason
        if [[ "$ACTION" == "close" && -z "$COMMENT_BODY" ]]; then
            echo -e "${YELLOW}⚠ Close action without comment body, skipping${NC}"
            ACTION="skip"
        fi
        
        # Execute action
        case "$ACTION" in
            nudge)
                if [[ -n "$COMMENT_BODY" ]]; then
                    echo "→ Posting comment..."
                    gh pr comment "$NUMBER" -R "$FULL_REPO" --body "$COMMENT_BODY" || {
                        echo -e "${RED}✗ Failed to comment${NC}"
                        ((FAILED++)) || true
                        continue
                    }
                    echo -e "${GREEN}✓ Nudged $FULL_REPO#$NUMBER${NC}"
                    ((NUDGED++)) || true
                else
                    echo -e "${YELLOW}⚠ No comment body, skipping${NC}"
                    ((SKIPPED++)) || true
                fi
                ;;
            
            close)
                if [[ -n "$COMMENT_BODY" ]]; then
                    echo "→ Posting comment before close..."
                    gh pr comment "$NUMBER" -R "$FULL_REPO" --body "$COMMENT_BODY" || true
                fi
                echo "→ Closing PR..."
                gh pr close "$NUMBER" -R "$FULL_REPO" || {
                    echo -e "${RED}✗ Failed to close${NC}"
                    ((FAILED++)) || true
                    continue
                }
                echo -e "${GREEN}✓ Closed $FULL_REPO#$NUMBER${NC}"
                ((CLOSED++)) || true
                ;;
            
            fix)
                echo -e "${YELLOW}⚠ Fix action requires manual implementation, deferring${NC}"
                ((SKIPPED++)) || true
                ;;
            
            skip)
                echo -e "${YELLOW}→ Skipped${NC}"
                ((SKIPPED++)) || true
                ;;
            
            defer)
                echo -e "${YELLOW}→ Deferred to human${NC}"
                ((SKIPPED++)) || true
                ;;
            
            *)
                echo -e "${YELLOW}⚠ Unknown action: $ACTION, skipping${NC}"
                ((SKIPPED++)) || true
                ;;
        esac
    fi
    
    ((PROCESSED++)) || true
    
    # Rate limiting: sleep between PRs
    if [[ $PROCESSED -lt ${#PR_LIST[@]} ]]; then
        echo "→ Sleeping 5s between PRs..."
        sleep 5
    fi
done

# ── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Batch Summary"
echo "═══════════════════════════════════════════════════════════════"
echo "  Processed: $PROCESSED"
echo "  Nudged:    $NUDGED"
echo "  Closed:    $CLOSED"
echo "  Fixed:     $FIXED"
echo "  Skipped:   $SKIPPED"
echo "  Failed:    $FAILED"
echo "═══════════════════════════════════════════════════════════════"

# Append to progress.md
if [[ "$DRY_RUN" != "true" ]]; then
    echo "" >> "$PROGRESS_FILE"
    echo "## $(date -u +%Y-%m-%d) — Daily Automation Batch" >> "$PROGRESS_FILE"
    echo "" >> "$PROGRESS_FILE"
    echo "- Batch size: $BATCH_SIZE" >> "$PROGRESS_FILE"
    echo "- Processed: $PROCESSED" >> "$PROGRESS_FILE"
    echo "- Nudged: $NUDGED" >> "$PROGRESS_FILE"
    echo "- Closed: $CLOSED" >> "$PROGRESS_FILE"
    echo "- Fixed: $FIXED" >> "$PROGRESS_FILE"
    echo "- Skipped/Deferred: $SKIPPED" >> "$PROGRESS_FILE"
    echo "- Failed: $FAILED" >> "$PROGRESS_FILE"
    echo "" >> "$PROGRESS_FILE"
    echo "PRs touched:" >> "$PROGRESS_FILE"
    for PR_ITEM in "${PR_LIST[@]}"; do
        echo "  - $PR_ITEM" >> "$PROGRESS_FILE"
    done
fi

exit 0
