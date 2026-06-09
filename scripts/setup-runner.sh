#!/bin/bash
# Setup script for registering a self-hosted GitHub Actions runner
# Run this ONCE on the machine where opencode is already configured

set -euo pipefail

REPO="Mr-Neutr0n/oss-tracker"
RUNNER_NAME="oss-agent-$(hostname)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "═══════════════════════════════════════════════════════════════"
echo "  OSS Agent - Self-Hosted Runner Setup"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "This will register your machine as a GitHub Actions runner"
echo "for the private repo: $REPO"
echo ""
echo "Prerequisites:"
echo "  1. gh CLI installed and authenticated"
echo "  2. opencode installed and configured (~/.opencode/bin/opencode)"
echo "  3. git installed"
echo "  4. python3 installed"
echo ""

# ── Verify Prerequisites ────────────────────────────────────────────────────

echo "→ Checking prerequisites..."

if ! command -v gh &> /dev/null; then
    echo -e "${RED}✗ gh CLI not found. Install: https://cli.github.com${NC}"
    exit 1
fi

if ! command -v opencode &> /dev/null; then
    # Check if it's in the default location
    if [[ -f "$HOME/.opencode/bin/opencode" ]]; then
        export PATH="$HOME/.opencode/bin:$PATH"
        echo -e "${GREEN}✓ Found opencode in ~/.opencode/bin${NC}"
    else
        echo -e "${RED}✗ opencode not found. Install: https://opencode.ai${NC}"
        exit 1
    fi
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ python3 not found. Install python3."${NC}"
    exit 1
fi

if ! command -v git &> /dev/null; then
    echo -e "${RED}✗ git not found. Install git."${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All prerequisites met${NC}"

# ── Verify GitHub Auth ──────────────────────────────────────────────────────

echo "→ Verifying GitHub authentication..."
gh auth status || {
    echo -e "${RED}✗ Not authenticated. Run: gh auth login${NC}"
    exit 1
}

echo -e "${GREEN}✓ GitHub authenticated${NC}"

# ── Verify opencode Config ──────────────────────────────────────────────────

echo "→ Verifying opencode configuration..."

if [[ -f "$HOME/.config/opencode/opencode.json" ]]; then
    echo -e "${GREEN}✓ opencode config found${NC}"
    # Extract model name for display
    MODEL=$(python3 -c "
import json
with open('$HOME/.config/opencode/opencode.json') as f:
    config = json.load(f)
    print(config.get('model', 'unknown'))
" 2>/dev/null || echo "unknown")
    echo "  Model: $MODEL"
else
    echo -e "${YELLOW}⚠ opencode config not found at ~/.config/opencode/opencode.json${NC}"
    echo "  You may need to run 'opencode' interactively first to configure."
fi

# ── Create Runner Directory ─────────────────────────────────────────────────

RUNNER_DIR="$HOME/oss-runner"
mkdir -p "$RUNNER_DIR"

echo "→ Runner directory: $RUNNER_DIR"

# ── Download GitHub Actions Runner ──────────────────────────────────────────

echo "→ Downloading GitHub Actions runner..."

cd "$RUNNER_DIR"

# Detect architecture
ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

if [[ "$ARCH" == "x86_64" ]]; then
    ARCH="x64"
elif [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
    ARCH="arm64"
else
    echo -e "${YELLOW}⚠ Unknown architecture: $ARCH, trying x64${NC}"
    ARCH="x64"
fi

if [[ "$OS" == "darwin" ]]; then
    RUNNER_OS="osx"
else
    RUNNER_OS="linux"
fi

RUNNER_VERSION="2.321.0"  # Latest stable as of early 2026
RUNNER_PACKAGE="actions-runner-${RUNNER_OS}-${ARCH}-${RUNNER_VERSION}.tar.gz"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_PACKAGE}"

if [[ ! -f "$RUNNER_PACKAGE" ]]; then
    echo "  Downloading: $RUNNER_URL"
    curl -o "$RUNNER_PACKAGE" -L "$RUNNER_URL" || {
        echo -e "${RED}✗ Failed to download runner${NC}"
        exit 1
    }
    
    echo "  Extracting..."
    tar xzf "$RUNNER_PACKAGE" || {
        echo -e "${RED}✗ Failed to extract runner${NC}"
        exit 1
    }
else
    echo "  Runner package already downloaded"
fi

# ── Get Runner Token ────────────────────────────────────────────────────────

echo "→ Getting runner registration token..."

# Get token via gh API
TOKEN_RESPONSE=$(gh api --method POST "repos/$REPO/actions/runners/registration-token" 2>/dev/null) || {
    echo -e "${RED}✗ Failed to get runner token. Make sure you have admin access to $REPO${NC}"
    exit 1
}

RUNNER_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('token', ''))")

if [[ -z "$RUNNER_TOKEN" ]]; then
    echo -e "${RED}✗ Could not extract runner token${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Got runner token${NC}"

# ── Configure Runner ────────────────────────────────────────────────────────

echo "→ Configuring runner..."

./config.sh \
    --url "https://github.com/$REPO" \
    --token "$RUNNER_TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "self-hosted,macOS,oss-agent" \
    --work "_work" \
    --unattended \
    || {
    echo -e "${RED}✗ Runner configuration failed${NC}"
    exit 1
}

echo -e "${GREEN}✓ Runner configured: $RUNNER_NAME${NC}"

# ── Create Launch Script ────────────────────────────────────────────────────

cat > "$RUNNER_DIR/start-runner.sh" << 'EOF'
#!/bin/bash
# Start the GitHub Actions runner
# Run this to start the runner as a background service

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure opencode is in PATH
export PATH="$HOME/.opencode/bin:$PATH"

# Verify opencode is available
which opencode > /dev/null || {
    echo "Error: opencode not found in PATH"
    echo "Add ~/.opencode/bin to your PATH or run setup again"
    exit 1
}

# Verify gh is available
which gh > /dev/null || {
    echo "Error: gh CLI not found"
    exit 1
}

# Start the runner
./run.sh "$@"
EOF

chmod +x "$RUNNER_DIR/start-runner.sh"

# ── Create LaunchAgent (macOS) ─────────────────────────────────────────────

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "→ Creating macOS LaunchAgent..."
    
    PLIST_DIR="$HOME/Library/LaunchAgents"
    mkdir -p "$PLIST_DIR"
    
    cat > "$PLIST_DIR/com.github.oss-agent.runner.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.github.oss-agent.runner</string>
    <key>ProgramArguments</key>
    <array>
        <string>$RUNNER_DIR/start-runner.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$RUNNER_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.opencode/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$RUNNER_DIR/runner.log</string>
    <key>StandardErrorPath</key>
    <string>$RUNNER_DIR/runner.error.log</string>
</dict>
</plist>
EOF
    
    echo "  LaunchAgent created: com.github.oss-agent.runner.plist"
    echo "  To start: launchctl load ~/Library/LaunchAgents/com.github.oss-agent.runner.plist"
    echo "  To stop: launchctl unload ~/Library/LaunchAgents/com.github.oss-agent.runner.plist"
    echo "  Logs: $RUNNER_DIR/runner.log"
fi

# ── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Setup Complete!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Runner directory: $RUNNER_DIR"
echo "Runner name: $RUNNER_NAME"
echo ""
echo "Next steps:"
echo ""
echo "1. Start the runner manually (to test):"
echo "   cd $RUNNER_DIR"
echo "   ./start-runner.sh"
echo ""
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "2. Or start as a background service:"
    echo "   launchctl load ~/Library/LaunchAgents/com.github.oss-agent.runner.plist"
    echo ""
fi
echo "3. In GitHub, go to:"
echo "   https://github.com/$REPO/settings/actions/runners"
echo "   You should see '$RUNNER_NAME' as an idle runner."
echo ""
echo "4. Trigger the workflow:"
echo "   Go to https://github.com/$REPO/actions/workflows/daily-oss-agent.yml"
echo "   Click 'Run workflow'"
echo ""
echo "═══════════════════════════════════════════════════════════════"
