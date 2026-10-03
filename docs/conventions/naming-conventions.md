# Naming conventions — self-evident names, no house codes

**Shared convention — kept byte-identical across repos.** Edit it in one repo and copy it verbatim to the others. Do not fork it with repo-specific detail: no local links, no issue numbers, no ADR numbers. Anything that would only make sense in one repo belongs in that repo's `CONTEXT.md` instead.

## The rule

Any name we coin for a value, column, config key, store format, category, status, or flag — anything a human or an AI agent will read — must be **self-evident**:

1. **No acronyms or house codes.** A name must not need a lookup table to decode. `SC-M-LWGF-N`, `BB`, `FAS`, `OTB`, `LFL` fail this; `Super Centre`, `Fashion Store`, `remaining buy budget`, `comparable store sales` pass.
2. **Readable by an industry veteran.** Use the word the industry already uses, in its plain form. A ten-year hand should be willing to say it out loud in a design review without feeling talked down to.
3. **A newcomer understands it on first explanation and remembers it easily.** One sentence should be enough, and it should still be enough next week. A name sticks when the reader can **rebuild** the meaning from the words, and fails when the mapping is arbitrary and must be **memorised**. *Estate* → everything one party owns → everything one customer runs: rebuildable, so one sentence holds. *Plane* offers no route to "deciding": memorised, and asked about again a week later.
4. **Terminology an AI agent is well-trained on.** Our repos are worked on heavily by agents, so a word that misleads a model costs as much as one that misleads a person. Prefer the globally-standard word over a locally-invented label, and avoid words whose training-data meaning outweighs ours: `environment` (dev/staging/prod, env vars), `setup` (`setup.py`, test setup), `site` (`site-packages`), `layer` (OSI, Docker, neural nets), `range` (`range()`, date ranges), `markdown` (the text format), `account` (billing, cloud), and above all **`agent`** — which now means an LLM agent.
5. **No clever special-cases that become footguns.** Avoid names that smuggle in a second dimension: "Big Box" encodes *size*, "City Center" encodes *location*, and neither says what the store *sells*, which is what a format is. Avoid magic catch-alls (`SPECIAL`) that mean two unrelated things at once. A name that answers a different question than its column asks reads fine and quietly classifies on the wrong axis — the error surfaces only when someone filters on it.

**The grep test for rule 4:** search the candidate in a normal repo. Unrelated hits mean an agent carries the same competing readings. This splits by where the word lives — **prose takes the plain phrase; identifiers take the distinctive one**, because filenames, functions, tables and columns are what agents grep.

## When to use it

Apply it whenever you **introduce or rename** anything a reader will meet:

- dimension and column names, and **enumerated values** (formats, categories, statuses, modes, flags),
- config keys and CLI flags,
- glossary terms in [`CONTEXT.md`](../../CONTEXT.md),
- seed values and `accepted_values` sets,
- anything surfaced on a dashboard, in a report, in a log line or an error message.

It does **not** apply to:

- **Source-faithful ingestion names** — the raw/Bronze layer mirrors the source system verbatim (SAP, GoFrugal, Zakya) so rows can be traced back. Fidelity beats friendliness there; see this repo's warehouse-layer doc.
- **Established external standards** — SAP `MATKL`, GST HSN chapters, ISO codes.
- **Product names** — *Dagster*, *DuckDB*, *Azure Lighthouse*. Explain them; never translate them.

## Three verdicts, not one

Renaming is one outcome of three, and reaching for it every time is the usual mistake:

- **Replace it** — the original earns nothing. *hybrid hub-and-spoke* → *central control + customer-side collectors*.
- **Add a word to it** — keep the original, add the word that kills the wrong reading. *estate* → **customer estate**. The veteran's word survives and the newcomer stops guessing.
- **Keep it** — the jargon is the best name available and any replacement loses information. *multi-tenant* beat the plainer *multi-client* because tenants share a building **and have walls between them**, and that isolation was the whole architectural claim; *client* carries no wall.

Where the original is strong industry jargon that practitioners say out loud, lead with the plain phrase and keep the original in brackets **permanently** — *shelf layout (planogram)*, *product selection (assortment)*, *data pipeline scheduler (orchestrator)*. Two names is the right answer there, not a compromise.

A dropped implication can also be carried by the **explanation** rather than the name, and the sharpest explanations contrast with the term next door: *"a scheduler decides when; an orchestrator also handles dependencies, retries and ordering."*

## Identifiers: the system's own noun, always text, `_code` for our handles, `_number` for sequences

1. **An identifier takes the system of record's own noun.** SAP's plant is `plant_code` (WERKS, '1501'), its merchandise leaf is `material_group_code` (MATKL, '010505003'); its documents are `document_number`, `purchase_order_number`. Do not coin a second name for a value another system already names.
2. **Every identifier is text, never cast**, and a fixed-width code is stored **zero-padded to its width** (SAP: line of business 2, main category 4, category 6, material group 9, plant 4). '1501' has no leading zero only because it fills its width; there is no exception to document, only the width.
3. **`_code` is a human-readable business handle when it is ours**: `store_code` = 'ATK', `region_code` = 'KL'.
4. **`_number` only for sequence and document numbers a person quotes** (invoice, purchase order, revision). Never for a location or a classification — a digit string that acts as a code is a code.
5. **Acronyms in CAPS** inside column names when they are established and known to the people who read them: `LOB_SAP`, `category_SAP`; a house acronym nobody outside can rebuild still fails rule 1.

## Hierarchy and classification names: unique at every level, title case, one spelling

A name in a hierarchy (division, subdivision, category, subcategory; any classification) must be **unique at its level without walking the path**, so a full-name search returns one row and a `group by name` never merges two nodes: a bare name may not hide inside an audience-qualified sibling ("Kurti" beside "Girls Kurti" becomes "Women Kurti"). Names are **title case**, spelled once, `&` never `N`, abbreviations expanded, no source-system shouting; the code (not the name) is the join key, so a rename is a data edit. Enforced by a build test wherever the tree lives.

## Infrastructure names: the customer slug first, lowercase-hyphen, `-db` for databases

Every **Railway** project and service (and any cloud resource that cannot carry a customer column) is named
`<customer>-<system>[-<part>]` (Siraj, 03-Oct-2026, data-warehouse #4085):

1. **The customer slug comes first, on projects *and* services**, so a search across workspaces shows
   `jeyarama-cdc-log-db` and `acme-cdc-log-db`, never two bare `cdc-log-db`s. The slug is defined once, in the
   system registry (feather-flow #77). Jeyarama's is `jeyarama`; no acronyms such as JRBG.
2. **All lowercase, hyphens only.** Railway puts service names in private network addresses, which allow
   letters, digits and hyphens. A mix of `Jeyarama-ETL`, `control_plane_db` and `featherbase-cdc` is what
   confuses agents.
3. **A database ends in `-db`**: `jeyarama-featherbase-db`, `jeyarama-cdc-log-db`.
4. **A CDC worker is named after its source**: `<customer>-cdc-<source>`, e.g. `jeyarama-cdc-featherbase`.
5. **One Railway workspace per customer**, named after the customer: the walled-off boundary.
6. **Existing names change only when touched**, and every rename comes with a sweep of name-based references
   (CLI `-s <name>`, skills, runbooks). A reference variable follows the service id; a name lookup silently
   returns nothing (the stale `-s Postgres` lookup, fixed in data-warehouse #4083).

Packages follow the product family: `feather-cdc`, `feather-control-plane` (feather-flow #80).

## The local-vs-global tension — flag it

Sometimes the local term is *less* clear than the global one, and the writer cannot see it because it is their own usage. In India "department store" reads as a glorified kirana, so we use the precise format word instead. An internal name-prefix like `CC` / `HM` should be replaced by the plain term it stands for, not preserved. **Watch especially for vocabulary carried in from a previous industry or employer** — *client* is standard in consulting and reads as jargon in a product context, which is why **customer** is the word in these repos.

## One thing, one word

Sweep every document for **one concept appearing under two names**. This is a defect on its own, independent of whether either name is good, because a reader cannot tell whether two words mean two things and often guesses wrong. Found twice in a single report: *heartbeat* / *liveness signal* for the same message, and *client* / *customer* for the same entity.

Check first whether it is genuinely **two concepts, both badly named**. Otherwise pick one, sweep the whole document, and report which earlier decisions the sweep changes.

## How to settle a name

Run the `/convert-jargon-to-beginner-friendly-terms` skill. It proposes lettered candidates, scores each against the three readers in rules 2–4, names what the losing options cost, and writes the decision back into `CONTEXT.md` in house format (`**Term**:` / definition / `_Avoid_:`). Grill the result if the name is load-bearing: propose the industry-standard candidate, state what it must be distinguished *from*, and confirm it reads correctly to both audiences before committing.

Prefer **deriving** a classification from objective inputs over hand-keying a label that will drift.

## Worked example — the store dimension

Redesigned 2026-06-30 with the CEO under this rule:

- **Rejected:** `SC-M` / `BB` / `FS` / `SS` (size codes), `LWGF` / `FLD` / `GH` (line-of-business codes), `N` / `G` / `M` (lifecycle codes), `Big Box` (a size word), `City Center` (a location word), `Department Store` (ambiguous in India).
- **Adopted:** `store_format` ∈ {Supermarket, Fashion Store, Family Store, Super Centre, Hypermarket, Gold House}; `lifecycle` ∈ {Pre-opening, New, Maturing, Mature, …} via the Walmart 13-month like-for-like rule — all **derived** from objective line-of-business sq-ft, not hand-typed.

## Worked example — the monitoring vocabulary

Settled 2026-08-17 under this rule:

- **Rejected:** `control plane` (*plane* gives no route to "deciding" — re-explained every time), `agent` for a collector (means an LLM agent), `ledger` alone (pulls toward blockchain and accounting), `findings` (audit vocabulary, vague about what was found), `register` (reads as the CPU sense), `isolated` as an identifier (means transaction isolation levels), `client` (consulting vocabulary), `liveness signal` (a second name for *heartbeat*), `markdown` as an identifier (means the text format).
- **Adopted:** `customer estate`, `central monitoring service`, `customer-side collector`, `heartbeat`, `heartbeat history ledger`, `system registry`, `detected issues`, `walled off`, `data pipeline scheduler (orchestrator)`, and `multi-tenant` kept deliberately.
- **Ruling, 03-Oct-2026 (Siraj, data-warehouse #4085): `control plane` and `client` are kept.** Their rejection above was idealistic: both are prevalent in the code and docs, and agents understand them. Use them freely; do not rename existing uses. The rule they were judged by still stands for new coinages.
