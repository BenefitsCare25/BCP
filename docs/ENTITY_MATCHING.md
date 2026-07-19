# Insured-entity matching — design reference

How a product's covered legal entities gate employee matching, why the pieces
are shaped the way they are, and what to be careful of when changing them.

Shipped 2026-07-19 (commits `caf616f` → `223a8f0`). Supersedes nothing; the
prior behaviour is described under [History](#history).

---

## 1. The problem

Two independently free-text sources have to agree before an employee can match:

| Side | Source | Stored as |
|---|---|---|
| Config | The entities a product covers — from the placement slip, or picked by the broker | `Product.product_metadata["entities"]`, else `Category.plan_assignments["insured"]` |
| Roster | The employing entity per member | `Employee.attribute_values["entity"]`, parsed from an *Entity / Legal Entity / Employer / Subsidiary / Company Name* column (`roster_parser.py:16-20`) |

When they disagree the employee simply appears **unmatched**, with nothing
saying an entity name caused it. Everything in this module exists to make the
two agree, and to surface it when they don't.

## 2. The gate

`matching_engine._entity_allows`:

```python
return not cat_entities or not emp_entity or emp_entity in cat_entities
```

**Blank on EITHER side is a wildcard.** This is the invariant that keeps
single-entity clients, and rosters with no Entity column, completely
unaffected. Do not "tighten" it — an empty config side means *covers everyone*,
not *covers nobody*.

The gate applies in **every** match tier (exact-name, fuzzy, rule). An
exact-name hit that is gated out falls through to fuzzy, where the sibling
category belonging to the employee's own entity still matches at 1.0.

## 3. Precedence — where the config side comes from

```
Product.product_metadata["entities"]      ← "Entities covered" on the setup header
        │
        ├─ set    ──→ gates EVERY category of that product
        └─ empty  ──→ falls back to each Category.plan_assignments["insured"]
```

One expression, in `_build_product_indices`:

```python
insured_by_category = {
    c.id: prod_entities or category_insured_entities(c, aliases)
    for c in sorted_cats
}
```

**Why the fallback is load-bearing.** A WICA-style slip repeats category names
per subsidiary with a *different* entity per block — a single product-level
field cannot express that. The fallback also means every pre-existing
configuration (e.g. CDL's 131 slip-parsed categories) keeps matching untouched
without a data migration.

**The free-text `insured` field on the setup header is NOT part of this.** It
is slip wording only: it feeds the fact-find and the slip export, and nothing
matches on it. Two fields with similar names, one functional — that is the
single most confusing thing about this module.

## 4. Storage: token list, tolerantly read

`insured` and `entities` are **lists of entity names**, one element each.

```python
insured_names(["Acme Pte Ltd, Singapore Branch"])   # → 1 entity  ✅
insured_names("Acme Pte Ltd, Singapore Branch")     # → 2 entities ⚠ legacy
```

A registered name containing a comma is why. Legacy comma-joined strings still
parse, so there is **no data migration** — but every read must go through
`insured_names()` (`matching_engine.py`) or `insuredNames()`
(`frontend/src/lib/insured.ts`). Both shapes will coexist indefinitely.

## 5. Comparison pipeline

Applied to **both** sides, so either may carry the alias spelling:

```
resolve_entity(name, aliases)
  ├─ normalize_entity(name)      lowercase · split on non-alphanumeric ·
  │                              fold corporate suffixes
  │                              (private→pte, limited→ltd, incorporated→inc,
  │                               corporation→corp, company→co)
  └─ single-hop alias lookup     entity_aliases table, per client
```

```
"CityNexus Pte. Ltd."  → "citynexus pte ltd"          (normalization alone)
"CSO"                  → "cso" → "city serviced offices pte ltd"  (alias)
```

**Single-hop is deliberate.** A map holding A→B and B→C resolves A→B, never
A→C. Cycles are therefore structurally impossible rather than something to
detect. Do not add chaining.

**Gotcha:** `normalize_entity` *tokenizes* on punctuation rather than deleting
it, so `"C.S.O."` → `"c s o"` and `"CSO"` → `"cso"` are **distinct**. A dotted
acronym needs its own alias row (both may point at the same canonical name).

The alias map is loaded **once per run** (`entity_alias_map`) into
`MatchIndex` — never per employee.

### Why the alias table exists at all

`placement_slip_export.py` writes the entity names **verbatim into the slip
sent to the insurer** — a legal document. So the config must keep the
*registered* name while matching needs the *roster's* spelling. "Fixing"
matching by retyping the registered name as the roster's shorthand would
corrupt that document. The alias bridges them without altering either side.

**If you ever remove the export's dependence on these strings, the alias table
becomes optional** — that is the one condition under which this design would
simplify.

## 6. Consumers — all must agree

| Consumer | File | Applies precedence | Applies aliases |
|---|---|---|---|
| Real matching run | `matching_engine.match_policy_year` | ✅ | ✅ |
| Live headcount preview | `member_counts.compute_member_counts` | ✅ | ✅ |
| Vocabulary + reconciliation | `entity_vocab.entity_vocabulary` | ✅ | ✅ |
| Cohort splitting | `cohort_tiers._insured_key` | n/a (category only) | ❌ **by design** |
| Slip export | `placement_slip_export` | ✅ (raw names) | ❌ **by design** |

Two deliberate exclusions:

- **`cohort_tiers`** compares two categories *from the same slip*. Aliases
  bridge slip↔roster, so they would be dead code there. It does share
  `category_insured_entities` so its normalization can't drift from the gate.
- **`placement_slip_export`** must render the stored legal spelling, never a
  normalized or alias-resolved form.

The first three drifting apart is the module's main failure mode — it has
already happened twice (see [History](#history)). **Any change to precedence
must be made in all three.**

## 7. Vocabulary + reconciliation

`GET /policy-years/{id}/entity-vocab` → `services/entity_vocab.py`

```
roster[]  distinct roster entities · headcount · claimed
          → picking one guarantees those employees match
known[]   config entities matching NO roster value · suggestion
          → the reconciliation backlog
```

Both lists cap at `MAX_LIMIT` (200); `roster` is sorted by headcount so the cap
drops the long tail, not the entities that matter.

**Suggestions** use two signals, because the failure modes look nothing alike:

- **Acronym** — the dominant case. `"CSO"` shares *zero* words with
  `"City Serviced Offices Pte Ltd"`; only comparing against the roster name's
  initials finds it.
- **Token overlap** (`jaccard`) — for partial or reordered names.

Corporate suffixes are excluded from **both**. Nearly every Singapore entity
ends in "Pte Ltd", so counting it made unrelated companies look ~40% similar
and produced nonsense suggestions.

## 8. Surfaces

| Screen | Component | Role |
|---|---|---|
| Product Setting → Header & Policy | `InsuredPicker` → `MatchSetPicker` | Pick entities (**the gate**) |
| → Employee Category & Plan Type | `CategoryCard` badge | Read-only *effective* entities |
| Listing Upload | `EntityBreakdownCard` + roster Entity column | Headcount per entity |
| Employee Coverage | `EntityReconciliationPanel` | Unmatched entities + one-click alias |
| Attributes Setting → Entity aliases | `routes/schema/entity-aliases.tsx` | Alias CRUD |

`MatchSetPicker` is shared with flex tiers and is **select-only by default**;
entities opt in via `allowCustom` because product setup routinely happens
*before* the roster upload, so the vocabulary is legitimately empty then and
free entry must stay. That is also why validation re-runs at matching time, not
only at confirm.

The reconciliation panel is a **warning, not a 409 gate** — unlike flex tiers,
where every employee must end up with a wallet. Blank `insured` is legitimate,
so a hard gate would fire constantly on single-entity clients.

## 9. Write paths

| Path | Writes |
|---|---|
| Setup confirm (`_upsert_product`) | `product_metadata["entities"]` from `header.entities` |
| `PATCH /schemas/products/{id}` | same; `[]` **clears** the restriction |
| Slip parse | `Category.plan_assignments["insured"]` per block |

Confirm writes the key on **every** save, not only when non-empty — otherwise
clearing the field would silently keep the old restriction.

## 10. Known limitations

- **Draft saves don't gate.** Entities persist to the product on *Confirm*, not
  *Save draft* — consistent with the rest of the setup form, but it means
  picking entities and saving a draft changes nothing until confirm.
- **No structured unmatch reason.** An employee excluded by the gate is still
  just "unmatched" in match diagnostics; the reconciliation panel is the
  diagnosis instead. Adding one needs a new field on the match result.
- **Dotted acronyms need their own alias row** (§5).
- **Aliases are per-client only** — no shared library tier, unlike insurers.
  Entity names are a client's own subsidiaries, so this is intentional.

## 11. Tests

| Area | File |
|---|---|
| Gate, token list, legacy string, comma-in-name, aliases both sides, single-hop, product precedence | `tests/test_matching_engine.py` |
| Suggestion signals (acronym / overlap / suffix-only → none) | `tests/test_matching_engine.py` |
| Cohort normalization shared with the gate | `tests/test_cohort_tiers.py` |
| Tenant isolation for `entity-vocab` + `entity-aliases` | `tests/test_tenant_isolation.py` |

**Regression guard:** exporting a placement slip for a multi-entity product
must produce a byte-identical Insured column before and after any change here.
Verified against all 131 real CDL categories when the token list landed.

## History

| Commit | Change |
|---|---|
| `caf616f` | Token-list storage; roster vocabulary endpoint; per-category picker |
| `f0bd006` | Alias map (finishes a model started in a prior session) |
| `0dbdebd` | Reconciliation warnings |
| `86fd257` | Entity breakdown + roster Entity column |
| `2721e38` | **Gate moved to product level**; per-category editor removed |
| `223a8f0` | Review fixes — see below |

`2721e38` moved the gate but updated only the *matching* path. Two subsystems
were left reading the per-category field and broke silently:

1. `entity_vocab` reported every roster entity as unclaimed, so the
   reconciliation panel rendered **nothing** exactly when the new path was used.
2. The slip export produced an **empty Insured line** for any product
   configured through the header.

Both fixed in `223a8f0`. This is the failure mode to watch: the gate has three
readers plus the export, and changing one without the others fails quietly
rather than loudly.
