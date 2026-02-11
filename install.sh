#!/usr/bin/env bash
set -e

# ─────────────────────────────────────────────────────────────────────────────
# feather-flow installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/sirajraval/feather-flow/main/install.sh | bash
#   -- or --
#   git clone https://github.com/sirajraval/feather-flow.git && cd feather-flow && bash install.sh
# ─────────────────────────────────────────────────────────────────────────────

REPO="sirajraval/feather-flow"
INSTALL_DIR="$HOME/.claude/feather-flow"
SKILLS_DIR="$INSTALL_DIR/skills"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

info()  { printf "\033[1;34m==>\033[0m %s\n" "$1"; }
warn()  { printf "\033[1;33mwarning:\033[0m %s\n" "$1"; }
error() { printf "\033[1;31merror:\033[0m %s\n" "$1" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Obtain the source files
#
# Two modes:
#   - Local clone: ./VERSION and ./skills/ exist in the current directory
#   - Remote:      Download the repo tarball from GitHub to a temp directory
# ─────────────────────────────────────────────────────────────────────────────

if [ -f "./VERSION" ] && [ -d "./skills" ]; then
    # Local clone — use the current directory as the source
    SOURCE_DIR="$(pwd)"
    info "Detected local clone at $SOURCE_DIR"
    CLEANUP_TEMP=false
else
    # Remote — download tarball from GitHub
    info "Downloading feather-flow from GitHub..."
    TEMP_DIR="$(mktemp -d)"
    CLEANUP_TEMP=true

    curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" \
        -o "$TEMP_DIR/feather-flow.tar.gz"

    tar -xzf "$TEMP_DIR/feather-flow.tar.gz" -C "$TEMP_DIR"
    SOURCE_DIR="$TEMP_DIR/feather-flow-main"

    if [ ! -f "$SOURCE_DIR/VERSION" ]; then
        error "Download succeeded but VERSION file not found — archive layout may have changed."
    fi
fi

NEW_VERSION="$(cat "$SOURCE_DIR/VERSION" | tr -d '[:space:]')"

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Handle upgrades vs fresh installs
# ─────────────────────────────────────────────────────────────────────────────

if [ -f "$INSTALL_DIR/VERSION" ]; then
    OLD_VERSION="$(cat "$INSTALL_DIR/VERSION" | tr -d '[:space:]')"
    info "Upgrading from v$OLD_VERSION to v$NEW_VERSION"

    # Remove old install contents so we get a clean replacement
    rm -rf "$INSTALL_DIR"
else
    info "Installing feather-flow v$NEW_VERSION"

    # ─────────────────────────────────────────────────────────────────────────
    # First install: check for legacy feather skills in ~/.claude/skills/
    # ─────────────────────────────────────────────────────────────────────────
    OLD_SKILLS_DIR="$HOME/.claude/skills"
    if [ -d "$OLD_SKILLS_DIR" ]; then
        OLD_FEATHER_DIRS=$(find "$OLD_SKILLS_DIR" -maxdepth 1 -type d -name "feather:*" 2>/dev/null || true)
        if [ -n "$OLD_FEATHER_DIRS" ]; then
            echo ""
            warn "Found old feather skills in $OLD_SKILLS_DIR:"
            echo "$OLD_FEATHER_DIRS" | while read -r d; do
                echo "    $(basename "$d")"
            done
            echo ""
            printf "    Remove old feather skills? [y/N] "

            # When piped through curl | bash, stdin is the script itself.
            # Re-attach stdin to the terminal so we can read user input.
            if [ -t 0 ]; then
                read -r REPLY
            else
                read -r REPLY < /dev/tty || REPLY="n"
            fi

            if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
                echo "$OLD_FEATHER_DIRS" | while read -r d; do
                    rm -rf "$d"
                done
                info "Removed old feather skills"
            else
                info "Keeping old skills — you can remove them manually later"
            fi
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Copy files to the install directory
# ─────────────────────────────────────────────────────────────────────────────

mkdir -p "$INSTALL_DIR"

# Copy everything from source into the install directory
cp -R "$SOURCE_DIR/." "$INSTALL_DIR/"

# Remove the install script itself from the installed copy (not needed there)
rm -f "$INSTALL_DIR/install.sh"

info "Installed to $INSTALL_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Register skills directory in ~/.claude/settings.json
#
# Uses python3 for safe JSON manipulation — available on macOS and most Linux.
# ─────────────────────────────────────────────────────────────────────────────

info "Registering skills in Claude settings..."

python3 -c "
import json, pathlib
p = pathlib.Path.home() / '.claude' / 'settings.json'
s = json.loads(p.read_text()) if p.exists() else {}
dirs = s.setdefault('additionalDirectories', [])
entry = str(pathlib.Path.home() / '.claude' / 'feather-flow' / 'skills')
if entry not in dirs: dirs.append(entry)
p.write_text(json.dumps(s, indent=2) + '\n')
"

info "Skills directory registered in ~/.claude/settings.json"

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Clean up temp files (remote install only)
# ─────────────────────────────────────────────────────────────────────────────

if [ "$CLEANUP_TEMP" = true ] && [ -n "$TEMP_DIR" ]; then
    rm -rf "$TEMP_DIR"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Done — print getting-started instructions
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "───────────────────────────────────────────────────"
echo "  feather-flow v$NEW_VERSION installed successfully"
echo "───────────────────────────────────────────────────"
echo ""
echo "  Getting started:"
echo ""
echo "    1. Open Claude Code in any project"
echo "    2. Type /feather:help to see all commands"
echo "    3. Type /feather:workflow to start a guided workflow"
echo ""
echo "  Installed to:  $INSTALL_DIR"
echo "  Skills dir:    $SKILLS_DIR"
echo ""
echo "  To upgrade later, re-run this script."
echo ""
