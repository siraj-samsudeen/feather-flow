#!/bin/bash
set -e

echo "This will uninstall feather-flow by removing:"
echo "  - ~/.claude/feather-flow/"
echo "  - feather:* symlinks from ~/.claude/skills/"
echo ""
read -p "Continue? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

# Remove feather:* symlinks from ~/.claude/skills/
removed=0
for link in "$HOME/.claude/skills"/feather:*; do
  if [ -L "$link" ]; then
    rm "$link"
    echo "Removed symlink: $(basename "$link")"
    removed=$((removed + 1))
  elif [ -d "$link" ]; then
    rm -rf "$link"
    echo "Removed directory: $(basename "$link")"
    removed=$((removed + 1))
  fi
done
if [ "$removed" -eq 0 ]; then
  echo "No feather skills found in ~/.claude/skills/."
else
  echo "Removed $removed feather skills."
fi

# Remove feather-flow directory
if [ -d "$HOME/.claude/feather-flow" ]; then
  rm -rf "$HOME/.claude/feather-flow"
  echo "Removed ~/.claude/feather-flow/"
else
  echo "~/.claude/feather-flow/ not found, skipping."
fi

echo ""
echo "feather-flow has been uninstalled."
