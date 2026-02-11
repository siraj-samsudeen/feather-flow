# Section Guidelines

## Workflow Context (Optional)

Include when discovery was done. Helps AI understand the "why."

```markdown
Workflow Context:
> "I log hours at end of day but often forget. Have to guess when invoicing."
Pain points: Forgetting to log, guessing hours
Opportunity: Reduce friction between working and logging
```

## Goal

One sentence. User benefit, not technical outcome.

- ❌ "Store task data in database"
- ✅ "Allow users to track personal tasks without losing items"

## Scope

**In:** Capabilities being built
**Out:** Explicitly excluded (prevents scope creep)

## Dependencies

- **Requires:** Must exist before this works
- **Triggers:** Side effects when this runs
- **Blocked by:** Build order dependencies

## Acceptance Criteria (EARS)

Use EARS syntax for all criteria. Organize by pattern:

**WHEN (Event-driven)** - Start with happy path user actions
```
WHEN user submits valid data THE SYSTEM SHALL save and confirm
```

**WHILE (State-driven)** - Behavior during states
```
WHILE session active THE SYSTEM SHALL refresh token every 15min
```

**IF/THEN (Unwanted)** - All error cases
```
IF validation fails THEN THE SYSTEM SHALL display specific error
```

**Ubiquitous** - Always-true constraints
```
THE SYSTEM SHALL encrypt passwords (ubiquitous)
```

**WHERE (Optional)** - Feature flags
```
WHERE premium enabled THE SYSTEM SHALL allow unlimited exports
```

### Tips for Good Criteria

- Be specific: "within 2s", "every 15min", "after 3 attempts"
- One behavior per line
- Cover all user actions with WHEN
- Cover all failure modes with IF/THEN
- Don't state obvious (auth required, record must exist)

## Business Validation

Rules requiring business decisions. Mark critical with `!`.

**Don't include:**
- "Record must exist" - obvious
- "Must be authenticated" - covered by Permissions

Omit section if no business rules beyond acceptance criteria.

## Calculations

Explicit formulas. Mark financial/critical with `!`.

```
C1!: discount = (subtotal × percentage)
     capped at max_discount if specified
```

Omit section if no calculations.

## Permissions

**Simple case:** `Standard ownership (user can only access own [resources])`

**Complex case:** Expand to P1, P2 when:
- Role-based access (admin, member)
- Shared resources
- Public endpoints
- View vs edit differences

Don't state "must be authenticated" - assume auth by default.

## Input Validation

Format and constraint rules.

```
IV1: Title is required
IV2: Title maximum 500 characters
```

## State Rules

**Reactive behavior only:**
- "When cart changes, totals recalculate"
- "Deleting project deletes its tasks"
- "Order: pending → confirmed → shipped"

**Don't include:**
- Default values ("new tasks default to incomplete")
- UI behavior ("completed tasks stay in list")

Omit section if no reactive behavior.

## Data Model

Client-friendly types. Only fields this feature touches.

**Tables created/modified:** Full field list
**Related tables:** Only fields this feature reads/writes
