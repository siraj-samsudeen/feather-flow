# Spec conventions

How requirements and scenarios are numbered in `openspec/changes/<change>/specs/<capability>/spec.md`. Locked 2026-05-18.

## The rule

Every requirement and every scenario carries a position number inside its heading name.

```markdown
### Requirement: 1. <Requirement name>

<description>

#### Scenario: 1a. <Scenario name>
- **WHEN** ...
- **THEN** ...

#### Scenario: 1b. <Scenario name>
...

### Requirement: 2. <Requirement name>

#### Scenario: 2a. <Scenario name>
...
```

- **Requirements:** integers, starting at `1`, monotonically within the spec file.
- **Scenarios:** `<requirement-number><letter>`, lowercase, starting at `a`.
- **Separator:** a period (`.`) between the number and the requirement/scenario name.
- **Scope:** numbers are local to each `spec.md` file (per capability). Numbers reset to `1` in every new spec file.

Cross-file references use the form `<capability>.<N><letter>`, e.g. `init.1a`, `source-add.3b`. In-file references just use the position (`1a`, `3b`).

## Why this scheme

- **Numbers are inside the OpenSpec-parsed name.** OpenSpec's sync and verify skills extract requirements by matching `### Requirement: <name>` and use `<name>` as the canonical identifier. Putting numbers in the heading keeps the numbering visible in any markdown render AND honours the parsing contract.
- **Local scope minimises blast radius.** Inserting a new requirement at `init.3` shifts only `init`'s subsequent numbers. Other capabilities are untouched. Global numbering would propagate that edit across every later spec file in the project.
- **Short references match the workflow.** "Fix 3b" is the natural sentence when you're looking at a spec file. The qualifier `init.3b` only shows up when crossing files, which is rare.

See `cross-project/principles/core-earns-complexity.md` for the broader pattern of preferring locality and simplicity unless complexity earns its keep.

## Renumbering protocol

When you insert, remove, or reorder requirements:

1. **Edit the spec.md** with the new numbering applied to every affected requirement and its scenarios.
2. **Update any references** in the same change folder — `tasks.md`, `design.md`, sibling spec files if you cross-reference them, test names that embed numbers.
3. **Commit the renumber as one atomic change** with a message like `spec(init): renumber after inserting req 3`. Reviewers see one consistent diff.
4. **OpenSpec sync treats renumbers as renames.** When you eventually `opsx:sync` the change into main specs, expect a RENAME block for every renumbered requirement. That's the cost; it's manageable.

In practice early-stage specs renumber often; mature specs almost never. The renumber cost is concentrated in the design window where churn is expected.

## Cross-file references

Use the form `<capability>.<N><letter>`:

- `init.1a` — scenario `1a` in `specs/init/spec.md`
- `source-add.3` — whole requirement `3` in `specs/source-add/spec.md`

The capability is the directory name under `openspec/changes/<change>/specs/`. Cross-references should be rare; if you find yourself reaching for them often, consider whether the requirements belong in the same spec.

## What this convention does NOT cover

- **Stable IDs across changes.** Numbers are local-and-positional, not globally stable. Old chat references to `init.1a` may point to a different scenario six months from now if `init` got reordered. We accept this — specs evolve with the code, and "the current spec" is always the source of truth.
- **Tasks.md numbering.** `tasks.md` uses its own existing numbering (`1.1`, `1.2`, …) for build steps. That's independent of spec numbering.
- **PRD section numbering.** PRDs use their own `§N.M` section scheme. Don't conflate.

## Quick reference

| You want to... | Reference looks like |
|---|---|
| Point at requirement 1 in the current spec file | `1` |
| Point at the second scenario of requirement 1 | `1b` |
| Point at the same in a different spec file | `init.1b` |
| Point at the whole spec | `init` (the capability directory name) |
