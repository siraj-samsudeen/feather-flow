# Development workflow

How work flows from idea to merged code in this repo.

Companion to `docs/testing.md` (how we test) and the `openspec-siraj-flow`
skill (how we spec). This doc covers the layer above both: where work is
tracked, how it branches, and how it lands.

## Two tracking systems, two jobs

- **GitHub issues** are the backlog inbox — un-specced intent: dogfooding
  bugs, ideas, RFCs, "we should look at X someday."
- **OpenSpec change folders** (`openspec/changes/<change>/`) are specced,
  in-build work. The proposal / spec / design / tasks / plans inside are the
  source of truth for that work.

They overlap nowhere except at graduation (below). Neither duplicates the
other: an issue is never a second spec, and a change folder never needs an
issue to exist.

## The rules

- **Backlog → issues.** Anything not yet specced lives as a GitHub issue.
  Keep issues as captured intent, not as specifications.

- **Specced work → one change = one branch = one PR.** Specced work does not
  need a GitHub issue; the change folder is its handle.

- **Graduation.** When a backlog issue gets pulled into a spec, the change's
  PR closes it with `Closes #N`. This is the only point where issues and
  changes meet.

- **Traceability.** The change slug (e.g. `add-source-add`) appears in the
  branch name, every commit subject, and the PR title. The slug — not an
  issue number — is how code traces back to its rationale.

## Branch and PR shape

- **Branch per change**, named for the slug (`add-source-add`), cut from
  `main`.

- The whole spec→build sequence lives on that branch — proposal, spec,
  design, tasks, plans, and the per-section implementation commits.

- **One PR per change.** The per-section commits from `execute-plan` all land
  inside that single PR.

- Merging to `main` closes any graduated issue via `Closes #N`.

## Why no issue per change

- An issue's unique job is to be a stable handle that exists *before* work is
  specified. Once specified, the change folder is the better handle —
  versioned, reviewed, and carrying the actual contract.

- Confining issues to the un-specced zone avoids two-sources-of-truth drift:
  an issue checklist silently disagreeing with `tasks.md`.

## Note: add-feather-init is the bootstrap exception

The first change, `add-feather-init`, landed directly on `main` while this
workflow was being established — it predates these rules and is not worth
retrofitting into a branch/PR. Branch-per-change starts with the next verb
(`add-source-add`).
