# DL-003: Bun for Claude Code Hooks

**Date**: 2025-02-15
**Status**: Accepted

## Context

Claude Code hooks need to parse JSON from subprocess output. In bash, this requires `jq` (external dependency) with verbose quoting. We need a lightweight runtime for hook scripts.

## Options Considered

### Option A: Bun

TypeScript-native runtime. Built-in JSON parsing, no compilation step.

- **Pro**: `JSON.parse()` just works. TypeScript out of the box. Fast startup.
- **Con**: Less mature ecosystem. Another runtime to install.

### Option B: Bash + jq

Shell scripts with `jq` for JSON parsing.

- **Pro**: No additional runtime. Universal on Linux/macOS.
- **Con**: Brittle quoting. Hard to maintain. No type safety.

## Decision

**Chosen option: Option A — Bun**

The simplified JSON parsing alone justifies the switch. Hook scripts are small utilities where Bun's startup speed and TypeScript support make them easier to write and maintain than bash equivalents.

## Consequences

**Positive:**
- Hook scripts are readable TypeScript instead of bash.
- JSON parsing is native — no `jq` dependency or quoting issues.

**Negative:**
- Bun must be installed alongside Node.js in the dev environment.

**Action items:**
- Add Bun to the project's dev setup instructions.
