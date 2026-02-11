#!/bin/bash
set -e

echo "This will uninstall feather-flow by removing:"
echo "  - ~/.claude/feather-flow/"
echo "  - ~/.claude/feather-flow/skills from settings.json additionalDirectories"
echo "  - Any old feather skills from ~/.claude/skills/ (directories starting with 'feather:')"
echo ""
read -p "Continue? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

# Remove feather-flow directory
if [ -d "$HOME/.claude/feather-flow" ]; then
  rm -rf "$HOME/.claude/feather-flow"
  echo "Removed ~/.claude/feather-flow/"
else
  echo "~/.claude/feather-flow/ not found, skipping."
fi

# Remove feather-flow/skills from additionalDirectories in settings.json
python3 -c "
import json, pathlib
p = pathlib.Path.home() / '.claude' / 'settings.json'
if not p.exists(): exit()
s = json.loads(p.read_text())
dirs = s.get('additionalDirectories', [])
entry = str(pathlib.Path.home() / '.claude' / 'feather-flow' / 'skills')
if entry in dirs: dirs.remove(entry)
p.write_text(json.dumps(s, indent=2) + '\n')
"
echo "Cleaned up settings.json."

# Remove old feather skills from ~/.claude/skills/
found_old=false
for dir in "$HOME/.claude/skills"/feather:*; do
  if [ -d "$dir" ]; then
    rm -rf "$dir"
    echo "Removed old skill: $(basename "$dir")"
    found_old=true
  fi
done
if [ "$found_old" = false ]; then
  echo "No old feather skills found in ~/.claude/skills/."
fi

echo ""
echo "feather-flow has been uninstalled."
