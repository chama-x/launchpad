#!/usr/bin/env bash
# ==============================================================================
# Launchpad v2 — 1-Line Installer for macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/chama-x/launchpad/main/install.sh | bash
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}${BLUE}==>${NC} ${BOLD}Installing Launchpad v2 for macOS...${NC}"

# 1. Platform check
if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}Error: Launchpad v2 is designed specifically for macOS (Finder, Spotlight, Quick Look, Automator).${NC}"
    exit 1
fi

# 2. Python 3 check
if ! command -v python3 >/dev/null 2>&1; then
    echo -e "${RED}Error: python3 is required. Please install Xcode Command Line Tools: xcode-select --install${NC}"
    exit 1
fi

# 3. Determine target workspace
WORKSPACE_ROOT="${LAUNCHPAD_HOME:-$HOME/Projects}"
mkdir -p "$WORKSPACE_ROOT"

ENGINE_DIR="$WORKSPACE_ROOT/.launchpad/engine"
mkdir -p "$ENGINE_DIR"

echo -e "${BLUE}==>${NC} Installing Launchpad engine into ${BOLD}$WORKSPACE_ROOT${NC}..."

# 4. Download latest engine script
REPO_URL="https://raw.githubusercontent.com/chama-x/launchpad/main/engine/launchpad.py"
TEMP_SCRIPT=$(mktemp /tmp/launchpad_install.XXXXXX)

if curl -fsSL "$REPO_URL" -o "$TEMP_SCRIPT" 2>/dev/null; then
    mv "$TEMP_SCRIPT" "$ENGINE_DIR/launchpad.py"
elif [[ -f "engine/launchpad.py" ]]; then
    # Local fallback
    cp "engine/launchpad.py" "$ENGINE_DIR/launchpad.py"
else
    echo -e "${RED}Failed to download launchpad.py. Check your internet connection.${NC}"
    rm -f "$TEMP_SCRIPT"
    exit 1
fi

chmod +x "$ENGINE_DIR/launchpad.py"

# 5. Run bootstrap
export LAUNCHPAD_HOME="$WORKSPACE_ROOT"
python3 "$ENGINE_DIR/launchpad.py" bootstrap --scan-local

echo ""
echo -e "${GREEN}${BOLD}✨ Launchpad v2 installed successfully!${NC}"
echo -e "  • Root Workspace: ${BOLD}$WORKSPACE_ROOT${NC}"
echo -e "  • CLI Executable: ${BOLD}~/.local/bin/launchpad${NC}"
echo -e "  • Quick Actions:  ${BOLD}Installed in Finder${NC}"
echo ""
echo -e "Run ${BOLD}launchpad status${NC} to check your workspace."
