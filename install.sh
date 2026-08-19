#!/usr/bin/env bash
# ==============================================================================
# Launchpad v2 — 1-Line Interactive Smart Installer for macOS
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/chama-x/launchpad/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/chama-x/launchpad/main/install.sh | bash -s -- /custom/path
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
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

# 3. Interactive Workspace Directory Resolution
# Priority:
#   1. Explicit Argument ($1)
#   2. Environment variable ($LAUNCHPAD_HOME)
#   3. Interactive Prompt with Finder Drag-and-Drop & Smart Default
#   4. Default fallback (~/Projects)

TARGET_DIR="${1:-${LAUNCHPAD_HOME:-}}"

if [[ -z "$TARGET_DIR" ]]; then
    # Detect best default candidate
    DEFAULT_WORKSPACE="$HOME/Projects"
    CANDIDATES=(
        "$PWD"
        "$HOME/Desktop/0/Projects"
        "$HOME/Developer"
        "$HOME/Development"
        "$HOME/Code"
        "$HOME/Workspace"
        "$HOME/workspace"
        "$HOME/src"
        "$HOME/Projects"
    )
    for cand in "${CANDIDATES[@]}"; do
        if [[ -d "$cand" && "$cand" != "$HOME" && "$cand" != "/" && "$cand" != "/tmp"* ]]; then
            DEFAULT_WORKSPACE="$cand"
            break
        fi
    done

    # Check if terminal is interactive (/dev/tty available)
    if [[ -c /dev/tty ]] 2>/dev/null; then
        echo ""
        echo -e "${PURPLE}==>${NC} ${BOLD}Select your Projects directory:${NC}"
        echo -e "    ${CYAN}• Press ${BOLD}Return${NC}${CYAN} to accept default${NC}"
        echo -e "    ${CYAN}• Or ${BOLD}drag & drop${NC}${CYAN} your project folder from Finder into this terminal${NC}"
        echo ""
        echo -ne "    ${BOLD}Workspace Path${NC} [${GREEN}${DEFAULT_WORKSPACE}${NC}]: "
        
        # Read from /dev/tty to support curl | bash pipe
        if read -r USER_INPUT < /dev/tty 2>/dev/null; then
            # Strip quotes and whitespace (added by macOS Finder drag & drop)
            USER_INPUT="$(echo "$USER_INPUT" | sed -e "s/^['\"]//" -e "s/['\"]$//" | xargs 2>/dev/null || echo "$USER_INPUT")"
            
            # Resolve tilde
            if [[ "$USER_INPUT" == "~"* ]]; then
                USER_INPUT="${USER_INPUT/#\~/$HOME}"
            fi
            
            if [[ -n "$USER_INPUT" ]]; then
                TARGET_DIR="$USER_INPUT"
            else
                TARGET_DIR="$DEFAULT_WORKSPACE"
            fi
        else
            TARGET_DIR="$DEFAULT_WORKSPACE"
        fi
    else
        TARGET_DIR="$DEFAULT_WORKSPACE"
    fi
fi

WORKSPACE_ROOT="${TARGET_DIR:-$HOME/Projects}"
mkdir -p "$WORKSPACE_ROOT"

ENGINE_DIR="$WORKSPACE_ROOT/.launchpad/engine"
mkdir -p "$ENGINE_DIR"

echo ""
echo -e "${BLUE}==>${NC} Installing Launchpad engine into ${BOLD}$WORKSPACE_ROOT${NC}..."

# 4. Download latest engine script
REPO_URL="https://raw.githubusercontent.com/chama-x/launchpad/main/engine/launchpad.py"
TEMP_SCRIPT=$(mktemp /tmp/launchpad_install.XXXXXX)

if curl -fsSL "$REPO_URL" -o "$TEMP_SCRIPT" 2>/dev/null; then
    mv "$TEMP_SCRIPT" "$ENGINE_DIR/launchpad.py"
elif [[ -f "engine/launchpad.py" ]]; then
    # Local repo fallback
    cp "engine/launchpad.py" "$ENGINE_DIR/launchpad.py"
    rm -f "$TEMP_SCRIPT"
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
