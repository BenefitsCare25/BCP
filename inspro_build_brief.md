# Inspro Group Benefits Configuration Platform
## Build Brief for Claude Code

> Paste this entire document into Claude Code as your initial prompt. Claude Code should read it end-to-end, produce a detailed implementation plan with timing and risks for human review, then begin scaffolding once the plan is approved.

---

## 1. Your Role

You are building the production version of the **Inspro Group Benefits Configuration Platform**, a multi-tenant SaaS that replaces a manual placement-slip-to-plan-assignment workflow used by brokers and HR teams in Singapore. A working browser-only prototype validates the core extraction and matching logic; you are porting and hardening that logic into a deployable system, not designing from scratch.

**Before writing any code, produce:**

1. A detailed implementation plan (phased, with concrete milestones) for human approval
2. A list of decisions you need confirmed before starting (database choice details, auth provider, etc.)
3. An identification of risks or assumptions that should be validated

Once the plan is approved, work in small verifiable increments. After each milestone, summarise what was built and what's next.

---

## 2. Product Context

**What it does.** A broker hands HR an Excel "placement slip" listing the insurance plans the company has bought for the year — typically 6–10 product sheets (GTL, GHS, GMM, SP, GPA, GBT, WICA, etc.), each with a "Basis of Cover" table defining categories of employees and which plan code each gets. HR maintains a roster of employees and dependants. Today, an admin manually fills in the plan ID for each employee row. This platform automates that: ingest placement slip + employee roster + dependant roster → produce plan assignments per employee, with auditable derivation.

**Who uses it.**

- Broker admins (one company per broker, many companies per broker firm)
- HR admins (one company)
- Insurer-facing exporters (data flows out to insurers in their expected format)

**Singapore-specific.** The platform handles Singapore PDPA-classed employee PII. All hosting must be in the Singapore Azure region. Audit logging is mandatory. Employee identifiers (NRIC/FIN, salary, DOB) require access control.

**Multi-tenancy.** Clients are isolated at the row level (every table has a `client_id`). Schema-level isolation is overkill for this scale (target: hundreds of clients, hundreds of thousands of employee records total). One client = one company being insured. One broker firm may operate across many clients.

---

## 3. Architectural Principles (Non-Negotiable)

### 3.1 Three-Layer Architecture

| Layer | Lives in | Changes when |
|---|---|---|
| **Layer 1 — Engine** | Code | New feature added (versioning, approval flow, etc.) |
| **Layer 2 — Schema** | DB tables | New attribute, product, or rule operator introduced (admin action, no deploy) |
| **Layer 3 — Instances** | DB tables | Per client, per policy year — everything filled out either manually or by AI |

Layer 1 is what you code. Layer 2 is config that ships with Singapore defaults but is editable by admins per-client. Layer 3 is data.

**Critical implication:** there is no enum in code for products, attributes, or rule operators. Adding a new product like "Pet Insurance" or a new employee attribute like "site_location" is a database insert into Layer 2 by an admin, not a code change.

### 3.2 The Four Form Patterns

The entire admin UI is composed of four reusable form components. Anything that looks like a fifth pattern is a sign of either feature creep or unrecognised reuse.

| Pattern | Renders | Used for |
|---|---|---|
| Attribute Schema Editor | A list of `(field_name, type, constraints)` rows | Defining employee attributes; defining what attributes a plan has per product |
| Instance Editor | A form for one record, with one input per attribute the schema defines | Editing an employee row, a plan instance, a product, client settings |
| Rule Builder | A predicate over attributes — `AND/OR/NOT` with operators allowed by Layer 2 | Category matching rules, validation rules, eligibility rules |
| Mapping Editor | A table mapping items in set A to items in set B | Category → plan assignments per product, employee → category preview |

These four components must be built first. Every other screen composes them.

### 3.3 AI ↔ Manual Symmetry

Every record in Layer 3 carries the same provenance envelope:

```json
{
  "data": { /* the actual fields */ },
  "source": "manual" | "ai_extracted" | "csv_import",
  "confidence": 0.0-1.0,
  "source_ref": "placement_slip://STM/GEL-GHS/row_21",
  "status": "draft" | "needs_review" | "confirmed",
  "human_modified": false,
  "modified_by": "user_id",
  "modified_at": "2026-05-12T08:30:00Z"
}
```

A category extracted from a placement slip and a category typed by hand are **the same record** — only `source` differs. Admin can confirm AI suggestions in bulk, edit any AI-extracted field (which flips `source` to `manual` and sets `human_modified: true`), or start from blank. There is no "AI mode" vs "manual mode" — there is just the workspace.

### 3.4 The Three Workspaces

The whole admin app collapses to three workspaces:

- **Schema** — Layer 2 editing. Rarely visited. Default Singapore schema ships pre-populated.
- **Configuration** — categories, plans, client settings, AI-extraction inbox. 80% of admin time.
- **Operations** — employees, dependants, match results, exceptions, activation history.

---

## 4. Technology Stack

Use these exact choices unless you have a strong reason otherwise (in which case raise it in the plan).

### 4.1 Backend

- **Language:** Python 3.12
- **Framework:** FastAPI (latest stable)
- **ORM:** SQLAlchemy 2.x (async)
- **Validation:** Pydantic v2
- **Migrations:** Alembic
- **Excel parsing:** `openpyxl` for .xlsx, `xlrd==2.0.1` for legacy .xls. Do not use pandas in production code paths — pandas is heavyweight and not needed once the parser is direct.
- **AI:** Anthropic Python SDK (`anthropic` package), model `claude-sonnet-4-20250514` for routine extraction, `claude-opus-4-7` only for ambiguous cases where Sonnet's confidence is below threshold
- **Background jobs:** FastAPI background tasks for file processing < 30s; Azure Container Apps Jobs for longer-running batch operations
- **Auth:** Microsoft Entra ID (Azure AD) using `msal` + custom FastAPI dependency. Multi-tenant — broker firm = tenant.

### 4.2 Frontend

- **Framework:** React 18 + TypeScript + Vite
- **Routing:** TanStack Router
- **Data fetching:** TanStack Query (mandatory; no useEffect+fetch)
- **Forms:** React Hook Form + Zod schemas
- **UI components:** shadcn/ui on top of Radix primitives
- **Styling:** Tailwind CSS v4
- **State:** Zustand for cross-component UI state, never for server state (TanStack Query owns server state)
- **Icons:** Lucide React
- **Excel client preview:** SheetJS (`xlsx`) for showing uploaded file content before sending to backend — actual parsing happens server-side

### 4.3 Data and Infrastructure

- **Database:** Azure Database for PostgreSQL Flexible Server (Singapore region, version 16)
- **Object storage:** Azure Blob Storage (raw uploaded files retained for audit; 7-year retention)
- **Cache:** Azure Cache for Redis (small SKU; sessions + idempotency keys)
- **Hosting:** Azure App Service (Linux) — one App Service Plan with two slots (staging, production) for the backend; Azure Static Web Apps for the frontend
- **Secrets:** Azure Key Vault, referenced by App Service via managed identity
- **Logging:** Azure Application Insights + structured JSON logs
- **CDN:** Azure Front Door if frontend latency outside SG matters

### 4.4 Tooling

- **Repo:** Monorepo. `backend/`, `frontend/`, `infra/`, `prompts/`, `docs/`
- **Package management:** `uv` for Python, `pnpm` for Node
- **Linting:** `ruff` + `mypy --strict` for Python; `eslint` + `typescript --strict` for frontend
- **Testing:** `pytest` + `pytest-asyncio` + `httpx` for backend; `vitest` + Playwright for frontend
- **Pre-commit:** `pre-commit` hooks running ruff, mypy, eslint, prettier
- **CI:** GitHub Actions

---

## 5. Repository Layout

```
inspro/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/                 # config, security, deps
│   │   ├── db/                   # session, base
│   │   ├── models/               # SQLAlchemy models per table
│   │   ├── schemas/              # Pydantic request/response models
│   │   ├── api/v1/               # FastAPI routers
│   │   │   ├── clients.py
│   │   │   ├── policy_years.py
│   │   │   ├── schema_employee.py
│   │   │   ├── schema_products.py
│   │   │   ├── placement_slips.py
│   │   │   ├── categories.py
│   │   │   ├── employees.py
│   │   │   ├── dependants.py
│   │   │   └── activations.py
│   │   ├── services/             # business logic, isolated from web layer
│   │   │   ├── placement_slip_parser.py
│   │   │   ├── rule_generator.py
│   │   │   ├── matching_engine.py
│   │   │   ├── ai_extractor.py
│   │   │   ├── dependant_linker.py
│   │   │   └── diagnostics.py
│   │   └── workers/              # background tasks
│   ├── alembic/
│   ├── tests/
│   │   ├── fixtures/             # real anonymised placement slips
│   │   ├── unit/
│   │   ├── integration/
│   │   └── e2e/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── primitives/       # the four form patterns
│   │   │   │   ├── AttributeSchemaEditor.tsx
│   │   │   │   ├── InstanceEditor.tsx
│   │   │   │   ├── RuleBuilder.tsx
│   │   │   │   └── MappingEditor.tsx
│   │   │   ├── ui/               # shadcn primitives
│   │   │   └── shared/
│   │   ├── routes/
│   │   │   ├── schema/
│   │   │   ├── configuration/
│   │   │   └── operations/
│   │   ├── lib/
│   │   ├── api/                  # TanStack Query hooks
│   │   └── types/                # TypeScript types matching backend schemas
│   ├── package.json
│   └── vite.config.ts
├── infra/
│   ├── bicep/                    # Azure resources as code
│   │   ├── main.bicep
│   │   ├── modules/
│   │   │   ├── postgres.bicep
│   │   │   ├── app-service.bicep
│   │   │   ├── storage.bicep
│   │   │   ├── keyvault.bicep
│   │   │   └── monitoring.bicep
│   │   └── parameters/
│   │       ├── dev.bicepparam
│   │       ├── staging.bicepparam
│   │       └── prod.bicepparam
│   └── github/
│       └── workflows/
│           ├── ci.yml
│           ├── deploy-backend.yml
│           ├── deploy-frontend.yml
│           └── deploy-infra.yml
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── ai-integration.md
│   └── runbook.md
├── prompts/
│   └── system-prompts/           # versioned LLM prompts for extraction
└── README.md
```

---

## 6. Data Model

### 6.1 Tenancy and Versioning

```sql
-- Every operational table has client_id for row-level isolation.
-- Every config table also has policy_year_id for versioning.

clients
  id (uuid pk)
  name (text)
  broker_firm_id (uuid fk)
  created_at, updated_at

policy_years
  id (uuid pk)
  client_id (uuid fk)
  year (int)               -- 2026
  status (enum: draft, active, archived)
  activated_at (timestamptz nullable)
  activated_by (uuid nullable)
  snapshot_json (jsonb)    -- frozen copy at activation time
  UNIQUE (client_id, year)
```

### 6.2 Layer 2 — Schema

```sql
employee_attribute_schemas
  id (uuid pk)
  client_id (uuid fk nullable)   -- null = global default
  attribute_id (text)            -- 'grade', 'pass', 'class'
  display_name (text)
  data_type (enum: string, integer, decimal, boolean, date, enum)
  enum_values (text[] nullable)
  is_required (bool)
  is_pii (bool)                  -- gates access in API
  description (text)
  derived_from (text nullable)   -- 'category_field' for STM-style derived attrs
  derivation_rule (jsonb nullable) -- regex patterns to extract from source field

products
  id (uuid pk)
  client_id (uuid fk nullable)   -- null = catalog item
  code (text)                    -- 'GTL', 'GHS'
  display_name (text)
  insurer (text)
  participation_model (enum: standard, extended, eo_only)
  has_dependants (bool)
  is_outpatient (bool)
  metadata (jsonb)

plan_attribute_schemas
  id (uuid pk)
  product_id (uuid fk)
  attribute_id (text)
  display_name (text)
  data_type (enum)
  is_required (bool)

rule_operators
  id (uuid pk)
  operator (text)                -- '=', '!=', '>=', '<=', '>', '<', 'in', 'not_in', 'between'
  display_name (text)
  applicable_types (text[])      -- which data types it works with
```

### 6.3 Layer 3 — Instances

```sql
placement_slips
  id (uuid pk)
  policy_year_id (uuid fk)
  uploaded_by (uuid)
  blob_url (text)                -- Azure Blob reference
  filename (text)
  uploaded_at (timestamptz)
  parse_status (enum: pending, parsing, parsed, error)
  parse_log (jsonb)              -- diagnostics, AI usage, confidence scores
  raw_text (text nullable)       -- extracted text for audit

product_instances
  id (uuid pk)
  policy_year_id (uuid fk)
  product_id (uuid fk)
  attribute_values (jsonb)
  source (enum: manual, ai_extracted, csv_import)
  source_ref (text nullable)
  confidence (decimal nullable)
  status (enum: draft, needs_review, confirmed)
  human_modified (bool)
  created_at, updated_at

plans
  id (uuid pk)
  policy_year_id (uuid fk)
  product_instance_id (uuid fk)
  code (text)                    -- 'Plan A', 'Plan 1'
  attribute_values (jsonb)
  source, source_ref, confidence, status, human_modified

categories
  id (uuid pk)
  policy_year_id (uuid fk)
  priority (int)                 -- order matters
  display_name (text)
  raw_description (text)         -- original text from placement slip
  matching_rule (jsonb)          -- the predicate
  rule_human_readable (text)
  participation_model (text)
  plan_assignments (jsonb)       -- {"product_id": "plan_id"}
  source, source_ref, confidence, status, human_modified

employees
  id (uuid pk)
  client_id (uuid fk)
  policy_year_id (uuid fk)
  staff_id (text)                -- the external HR identifier
  attribute_values (jsonb)       -- shape determined by employee_attribute_schemas
  derived_attribute_values (jsonb) -- computed from raw, e.g. grade derived from category text
  matched_category_id (uuid fk nullable) -- computed
  match_method (text nullable)   -- 'exact_name', 'fuzzy_name', 'rule'
  match_confidence (decimal nullable)
  source, status

dependants
  id (uuid pk)
  client_id (uuid fk)
  policy_year_id (uuid fk)
  employee_id (uuid fk)
  attribute_values (jsonb)
  link_method (text)             -- 'staff_id', 'id_no', 'name'
  status

audit_log
  id (uuid pk)
  client_id (uuid fk)
  user_id (uuid)
  action (text)
  entity_type (text)
  entity_id (uuid)
  before (jsonb nullable)
  after (jsonb nullable)
  at (timestamptz)
```

### 6.4 Indexing Notes

- `employees(client_id, policy_year_id, staff_id)` — frequent lookup
- `employees(matched_category_id)` — for reverse queries
- `categories(policy_year_id, priority)` — match-order iteration
- `audit_log(client_id, at desc)` — recent activity
- Use partial indexes for `status = 'needs_review'` on every table that has it

---

## 7. Core Features (v1 Scope)

In priority order:

1. **Auth + multi-tenancy** — Entra ID login, broker firm → client scoping, role-based access (admin, viewer, broker, hr).
2. **Schema workspace** — view/edit `employee_attribute_schemas`, `products`, `plan_attribute_schemas`. Ship Singapore default schema seeded via Alembic.
3. **Placement slip ingestion** — upload .xlsx/.xls, parse via deterministic parser (see §8), produce draft categories with auto-generated matching rules, store in `categories` table with `source='ai_extracted'`, `status='needs_review'`.
4. **Categories editor** — list categories grouped by product, edit matching rule via Rule Builder, edit plan assignments via Mapping Editor, confirm/reject.
5. **Employee ingestion** — upload roster, parse with structured-field decoder for known formats (STM, etc.), insert/update employees, run match engine.
6. **Dependant ingestion** — upload, multi-key link to employees (staff_id → id_no → name), compute participation type per employee.
7. **Diagnostics surface** — column mapping (mapped/unmapped), raw vs derived attribute coverage, match-attempt breakdown, why-failed reason aggregation.
8. **Activation** — snapshot the full Layer 3 config into `policy_years.snapshot_json`, mark `status='active'`. Downstream systems read from snapshot.
9. **Audit log viewer** — read-only view of changes per client.

**Out of scope for v1** (build hooks but not the workflow):

- Approval workflows
- E-signatures
- Insurer-format export
- Premium calculation
- Claims integration
- Self-service for HR end users (admin tool only)

---

## 8. Validated Extraction Logic — Port These Patterns

The prototype validated against real STM (4,607 employees, 4,240 dependants) and VDL placement slips. The following logic produced **99% match rate** on STM. Port it directly into `services/placement_slip_parser.py` and `services/rule_generator.py`.

### 8.1 Placement Slip Parser

For each sheet (skip "Billing numbers", "comments", "Setup", "Summary"):

1. Find the row containing "Basis of Cover" within the first 30 rows.
2. Within 1–5 rows below that, find the column-header row containing "Category" AND ("Participation" OR "Plan").
3. Identify column indices for: `Insured`, `Category`, `Participation`, `Plan` (by header name match, lowercased).
4. Walk rows from `header_idx + 1` collecting categories until any stop condition:
   - Row contains "FIGURES ABOVE ARE FOR" or "ACTUAL FIGURES"
   - Row contains "RATE :" or `^\s*rate\s*:?\s*$`
   - Row contains "SCHEDULE OF BENEFITS" or "BENEFITS / INSURER"
   - 3 consecutive blank rows
   - Category cell starts with `*` or contains "FIGURES"
5. For each non-empty row:
   - Track `current_insured` (carries down when blank)
   - Track `last_participation` (carries down when blank)
   - Strip footnote text: split on `\s*\*` and take first portion
   - Strip parenthetical "(premium includes...)" trailers
   - If no Plan column or empty plan cell, try to extract "Plan X:" from category text: `^\s*plan\s+([A-Za-z0-9/ ]+?)\s*[:\-—]\s*(.+)$`
   - Skip if cat length < 6 chars
   - Skip if cat matches `^[A-Z0-9 ,/-]{1,10}$` (e.g. "B1 , B3" rate codes)
   - Skip if cat lowercase contains "premium include", "(premium", "rate :", "subj to gst", "annual premium", "figures above"

### 8.2 Rule Generator

Convert each category description into a JSONLogic-style predicate. Patterns proven to work:

```python
# Grade range
r"(?:hay )?(?:job )?grade?s?\s*0?(\d+)\s*(?:to|-|–|—)\s*0?(\d+)"
  → {"between": ["grade", lo, hi]}

# Grade lower bound
r"(?:hay )?(?:job )?grade?s?\s*0?(\d+)\s*(?:&|and|or)\s*above"
  → {">=": ["grade", lo]}

# Grade upper bound
r"(?:hay )?(?:job )?grade?s?\s*0?(\d+)\s*(?:&|and|or)\s*below"
  → {"<=": ["grade", hi]}

# Salary brackets (used by clients without Hay grades)
r"(?:earning|salary)?\s*(?:less than|<|under)\s*\$?\s*([\d,]+)"
  → {"<": ["salary", v]}

# Employment pass — most specific first
r"work permit\s*(?:&|and|or|\/)\s*s[\- ]?pass" with no "non" → {"in": ["pass", ["WP", "SP"]]}
r"wp\/sp"                                    → {"in": ["pass", ["WP", "SP"]]}
r"non[- ]?wp\/sp"                             → {"not_in": ["pass", ["WP", "SP"]]}
r"s[ -]?pass"                                → {"=": ["pass", "SP"]}
r"work permit"                               → {"=": ["pass", "WP"]}
r"foreign worker"                            → {"in": ["pass", ["WP", "SP", "EP"]]}

# Employment class
"bargainable" + "fire fighter"  → class=BARGAINABLE AND job_function=FIRE_FIGHTER
"bargainable"                   → class=BARGAINABLE
"intern" not "industrial"       → class IN [INTERN, CONTRACT]
"industrial attachment|student" → class=INDUSTRIAL_STUDENT
"board of directors"            → class=BOARD_OF_DIRECTORS
"postee|secondee|seconded overseas" → class=SECONDEE

# Occupation (WICA)
"management" + "admin"          → occupation=MGMT_ADMIN
"manufacturing assistant"        → occupation=MANUFACTURING
"forklift"                      → occupation=FORKLIFT
"all others" + "engineer"       → occupation=ALL_OTHERS
```

### 8.3 AND vs OR Detection (Critical Bug Fix)

The phrase "Hay Job Grade 08 to 15 **and** Bargainable Staff" means UNION (eligible = HJG 8-15 employees OR bargainable employees), NOT intersection.

Detect: a category text matches grade-range pattern AND class-keyword pattern AND has neither "who [are]" nor "with" connecting them → generate OR rule:

```python
{"or": [
  {"and": [grade_condition, ...other_shared_conditions]},
  {"and": [class_condition, ...other_shared_conditions]}
]}
```

When "who/with" appears between them ("Bargainable Staff who are Fire Fighters"), keep AND semantics.

### 8.4 Employee Attribute Derivation

Some clients (STM is the canonical example) encode multiple attributes in a single `Category` text field:

```
"11 Single"                                      → grade=11, family_status=S
"13 Married or Single Parent plus 1 child"       → grade=13, family_status=M1C
"18 and above"                                   → grade=18, grade_modifier=GTE
"Bargainable FW"                                 → class=BARGAINABLE, is_fw=true
"Apprentice"                                     → class=APPRENTICE
"Thailand 11 to 15 Single"                       → unmappable in SG-only schema (correct: should be unmatched)
```

This derivation logic lives in `services/employee_decoder.py` and is **configured per client via the `employee_attribute_schemas.derivation_rule` JSON column** — not hardcoded. STM's derivation rule is a seeded record; other clients can add their own without code changes.

Example derivation_rule JSON:

```json
{
  "source_field": "category",
  "extractors": [
    {"attribute": "grade", "regex": "^(\\d+)\\s+(?:and\\s+above|or\\s+above)", "transform": "int", "modifier": "GTE"},
    {"attribute": "grade", "regex": "^(\\d+)\\b", "transform": "int"},
    {"attribute": "class", "regex": "bargainable", "value": "BARGAINABLE"},
    {"attribute": "is_fw", "regex": "\\bfw\\b", "value": true}
  ]
}
```

### 8.5 Pass Value Normalization

```python
def normalize_pass(raw: str | None) -> str | None:
    if not raw: return None
    s = raw.upper().replace("-", "").replace(" ", "")
    if s in {"SPASS", "S", "SP"} or "SPASS" in s: return "SP"
    if s in {"WP", "WORKPERMIT", "WORKPASS"}: return "WP"
    if s in {"EP", "EMPLOYMENTPASS"}: return "EP"
    return raw  # pass through unchanged for unrecognised
```

### 8.6 Multi-Key Dependant Linker

```python
def link_dependant(dep, employee_index):
    # 1. staff_id (exact match, case-insensitive)
    if dep.staff_id and (e := employee_index.by_staff_id.get(dep.staff_id.lower())):
        return (e, "staff_id")
    # 2. NRIC/FIN
    if dep.employee_id_no and (e := employee_index.by_id_no.get(dep.employee_id_no.lower())):
        return (e, "id_no")
    # 3. Employee name normalized (commas, whitespace collapsed)
    if dep.employee_name:
        key = normalize_name(dep.employee_name)
        if (e := employee_index.by_name.get(key)):
            return (e, "name")
    return (None, None)
```

### 8.7 Matching Engine Priority

```python
def match_employee(employee, categories):
    # 1. Exact name match on category field
    if employee.category:
        for cat in categories:
            if cat.display_name.lower() == employee.category.lower():
                return Match(cat, method="exact_name", score=1.0)

    # 2. Fuzzy Jaccard similarity on tokenised words (numbers kept!)
    best, best_score = None, 0.0
    for cat in categories:
        sim = jaccard_similarity(employee.category, cat.display_name)
        if sim > best_score:
            best, best_score = cat, sim
    if best and best_score >= 0.6:
        return Match(best, method="fuzzy_name", score=best_score)

    # 3. Rule evaluation in priority order
    for cat in categories:
        if cat.matching_rule and evaluate(cat.matching_rule, employee):
            return Match(cat, method="rule", score=cat.confidence)

    return Match(None, method=None, reasons=[...])
```

Critical: tokenisation must keep numeric tokens. `"18 and above"` tokenises to `{"18", "and", "above"}`, not just `{"and", "above"}`.

---

## 9. AI Integration Strategy

Use Claude API for cases the deterministic parser can't handle, never as the default path.

### 9.1 Where to Use AI

1. **Low-confidence rule generation** — when `descriptionToRule()` returns confidence < 0.5, queue the category description for AI extraction.
2. **Unmapped employee category fields** — when the structured decoder produces no attributes, hand the raw category text to Claude with the client's known attribute schema and ask for structured output.
3. **Column-name suggestions** — when an uploaded file has columns not in the mapping dictionary, ask Claude to suggest mappings (admin reviews).
4. **Anomaly detection** — quarterly batch run over employee records to flag categories that no longer match anything (data drift).

### 9.2 Where NOT to Use AI

- Placement slip table extraction (Excel structure is rigid; rule-based is faster and more reliable)
- Common rule patterns (regex handles them)
- Dependant linking (deterministic keys work)
- Anything in the hot path of bulk operations (a 4607-row file would mean 4607 API calls)

### 9.3 Prompt Pattern

Use the Anthropic SDK with structured output via `tools` (forced tool call to a Pydantic-schema-derived JSON schema):

```python
async def ai_generate_rule(category_text: str, schema: ClientSchema) -> RuleGenResult:
    response = await anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        tools=[{
            "name": "emit_rule",
            "description": "Emit the structured matching rule for the given category",
            "input_schema": Rule.model_json_schema(),
        }],
        tool_choice={"type": "tool", "name": "emit_rule"},
        system=load_prompt("rule_generation.md", schema=schema),
        messages=[{"role": "user", "content": category_text}],
    )
    return parse_tool_use(response)
```

Prompts live in `prompts/system-prompts/` and are versioned. Every AI call records:

- Prompt version
- Model
- Token usage
- Latency
- Output confidence (model self-reported)

All AI-generated rules are marked `source: 'ai_extracted'`, `status: 'needs_review'` regardless of confidence. Admin always sees them before they go live.

### 9.4 Caching

AI results are cached by `(prompt_version, model, input_hash)` in Redis with 30-day TTL. Re-processing the same placement slip should not re-spend tokens.

### 9.5 Cost Controls

- Token budget per client per month, enforced before each call
- Circuit breaker: if error rate > 10% in 5min, fall back to "needs admin review" without calling AI
- Surface AI spend per client in the admin dashboard

---

## 10. Deployment

### 10.1 Azure Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       Azure Front Door                       │
└──────────────────────────────────────────────────────────────┘
              │                                  │
              ▼                                  ▼
      ┌──────────────┐                  ┌──────────────────┐
      │ Static Web   │                  │  App Service     │
      │ Apps         │                  │  (Linux, Python) │
      │ (React)      │                  │  Backend API     │
      └──────────────┘                  └─────────┬────────┘
                                                  │
                                ┌─────────────────┼──────────────┐
                                ▼                 ▼              ▼
                      ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
                      │ PostgreSQL   │  │ Blob Storage │  │ Key Vault    │
                      │ Flexible     │  │ (raw files)  │  │ (secrets)    │
                      │ Server       │  │              │  │              │
                      └──────────────┘  └──────────────┘  └──────────────┘
                                                  │
                                                  ▼
                                          ┌──────────────┐
                                          │ Application  │
                                          │ Insights     │
                                          └──────────────┘
```

All resources in **Southeast Asia (Singapore)** region.

### 10.2 GitHub Actions Workflows

Three workflows in `.github/workflows/`:

**`ci.yml`** — runs on every PR
- Lint (ruff, mypy, eslint, prettier)
- Unit tests (pytest, vitest)
- Build (frontend bundle, backend container)
- Upload coverage to PR comment

**`deploy-backend.yml`** — runs on push to `main`
- Build container image, tag with git SHA
- Push to Azure Container Registry
- Deploy to App Service staging slot
- Run smoke tests against staging
- Require manual approval (GitHub environments) for slot swap
- Swap staging → production

**`deploy-frontend.yml`** — runs on push to `main` if `frontend/` changed
- pnpm build
- Deploy to Static Web Apps via official action
- Invalidate Front Door cache

**`deploy-infra.yml`** — manual trigger
- `az deployment group what-if` against the target environment
- Require manual approval
- `az deployment group create`

### 10.3 Environments

| Environment | Purpose | URL |
|---|---|---|
| dev | Each developer's local | localhost |
| staging | Full deployed copy for QA | inspro-staging.azurewebsites.net |
| prod | Live | inspro.com (or chosen domain) |

Database per environment. No shared databases across environments. Production database backups: 7-day point-in-time + 35-day long-term.

### 10.4 Secrets Management

- All secrets in Azure Key Vault
- App Service authenticates to Key Vault via managed identity
- Local dev uses `.env.local` (gitignored) populated from `azd env` or manual copy
- GitHub Actions reads secrets via OIDC federation to Azure (no long-lived service principal credentials)

### 10.5 Infrastructure as Code

Use Bicep (not Terraform — Bicep is Azure-native and simpler for Azure-only deployments). Parameter files per environment. Run via `az deployment group create`.

---

## 11. Compliance, Security, Audit

### 11.1 Singapore PDPA Requirements

- All employee data hosted in Singapore region
- Encryption at rest (Azure default) and in transit (TLS 1.3 only)
- Field-level access control: NRIC, salary, DOB tagged `is_pii=true` in schema; API responses redact these for non-PII-cleared roles
- Right to erasure: implement DELETE endpoint that soft-deletes employee + dependants and writes audit log
- Data export: implement client-data-export endpoint producing a zip of all the client's data in JSON
- Retention: client config snapshots retained 7 years; raw uploaded files retained 7 years

### 11.2 Audit Trail

Every mutation writes to `audit_log` via a SQLAlchemy event listener. Captured automatically. Includes `before` and `after` JSON. UI viewer in Operations workspace.

### 11.3 Authorization Model

| Role | Scope | Permissions |
|---|---|---|
| Broker admin | All clients in broker firm | Read + write all |
| Broker viewer | All clients in broker firm | Read only |
| Client admin | One client | Read + write own client |
| Client HR | One client | Read employees + dependants, edit own roster |
| System admin | Global | Schema management, AI prompt versioning |

Enforce in API via FastAPI dependencies that check `(user_role, target_client_id)`.

### 11.4 Security Practices

- Argon2id for any password-related hashing (Entra ID handles passwords directly but session keys etc. use Argon2)
- CSRF tokens on state-changing endpoints
- Rate limiting per user (Redis-backed)
- SQL injection prevented by SQLAlchemy parameterised queries (never use raw string SQL)
- File upload validation: max 50MB, content-type check, virus scan via Azure Defender for Storage
- CORS: allowlist frontend origins explicitly
- CSP headers: strict default-src 'self'

### 11.5 Test Coverage Targets

- Backend services: 85% line coverage minimum
- Backend API routes: 100% happy path + permission tests
- Frontend components: snapshot + interaction tests for the four form primitives
- E2E: critical user paths (upload → review → activate)

---

## 12. Implementation Sequence

Plan to deliver in phases. After each phase, present working software for review before starting the next.

### Phase 0 — Project Bootstrap (1–2 days)
- Monorepo scaffold with backend, frontend, infra folders
- Backend: FastAPI hello-world with health endpoint
- Frontend: React app routing to placeholder workspaces
- Infra: Bicep modules for all resources (deployable to dev)
- CI: lint + test + build workflows
- Documentation: architecture.md, README

**Done when:** Backend deploys to Azure dev environment via push to main, frontend loads, hello endpoint returns 200, health checks green.

### Phase 1 — Data Model + Migrations (2–3 days)
- All SQLAlchemy models from §6
- Alembic migrations
- Seed scripts for Singapore default Layer 2 schema (the 27 categories from STM placement slip can serve as the validation seed)
- Database connection pooling
- Repository layer for each entity (CRUD operations isolated from API layer)

**Done when:** `alembic upgrade head` produces a working schema. Seed script populates Layer 2 defaults. Unit tests pass for all repositories.

### Phase 2 — Auth + Multi-tenancy (3 days)
- Entra ID integration
- FastAPI auth dependency
- Role-based authorization
- Client/policy_year scoping enforced at API layer
- Frontend login flow + protected routes

**Done when:** Login works, unauthorized requests return 403, broker admin can see only their clients, audit log records actions.

### Phase 3 — Four Form Primitives (5 days)
- AttributeSchemaEditor
- InstanceEditor
- RuleBuilder (visual builder; mode for written expression deferred)
- MappingEditor
- Storybook (or equivalent) showcase for each
- Snapshot + interaction tests

**Done when:** Each primitive renders, accepts data, emits onChange, has a Storybook entry. The Schema workspace uses them to render the Singapore default schema.

### Phase 4 — Placement Slip Pipeline (5 days)
- Upload endpoint → Blob Storage
- Placement slip parser service (port from prototype, port the validated regex patterns)
- Rule generator service
- Background job for parsing
- Diagnostics service producing the column-mapping/coverage/match-reason output
- API to fetch parsed categories + diagnostics

**Done when:** Uploading STM's `STMicroelectronics_-_Placement_Slips_2026_workingfile.xls` produces 27 categories with correctly generated rules and full diagnostics, matching prototype output exactly.

### Phase 5 — Employee + Dependant Pipeline (4 days)
- Employee upload + parser with structured field decoder (driven by `employee_attribute_schemas.derivation_rule`)
- Multi-key dependant linker
- Matching engine
- Participation type derivation
- API to fetch matched employees + per-product plan assignments

**Done when:** Uploading STM employees (4607) and dependants (4240) produces 99% match rate, 99.8% dependant linkage, correct EO/EF/EC breakdown.

### Phase 6 — Configuration Workspace UI (5 days)
- Categories list with filters
- Inline editing using Rule Builder + Mapping Editor
- AI suggestion review queue
- Confirm/reject/edit workflows
- Diagnostics surface

**Done when:** Admin can navigate to a client's policy year, see all categories, edit any rule, accept AI suggestions in bulk, and reject specific ones.

### Phase 7 — Operations Workspace UI (4 days)
- Employee roster with filtering
- Per-employee detail view
- Match exception triage
- Dependant roster

**Done when:** Admin can find any employee, see their attributes (raw + derived), see their matched category and plan assignments, and trigger re-match.

### Phase 8 — AI Integration (3 days)
- `ai_extractor` service with Anthropic SDK
- Prompts versioned in `prompts/system-prompts/`
- Redis caching
- Circuit breaker
- Spend tracking per client

**Done when:** Categories that the deterministic parser flagged low-confidence get queued, processed by Claude, and surface in the review queue with structured output.

### Phase 9 — Activation + Audit Trail (2 days)
- Activation endpoint that snapshots `policy_years.snapshot_json`
- Validation gates (all categories confirmed, all employees matched, etc.)
- Audit log viewer

**Done when:** Admin can activate a policy year and see a frozen snapshot. Audit log shows every mutation since the year was created.

### Phase 10 — Hardening (3 days)
- Load test: simulate 100 concurrent users, 50K-row file uploads
- Security review: OWASP Top 10 checklist
- Performance tuning: query plans, N+1 elimination
- Documentation: runbook, on-call playbook

**Done when:** Load tests pass, security checklist green, runbook complete.

**Estimated total: 6–8 weeks of focused single-developer work.**

---

## 13. Quality Bars

- **No ignored test failures.** A test that flakes gets fixed or deleted; it never gets skipped indefinitely.
- **No "TODO" in committed code without a linked issue.** Every TODO has a tracking reference.
- **All public APIs have docstrings.** Generated docs live in `/docs/api`.
- **Type-checking is strict.** `mypy --strict` passes. No `Any` without justification in a comment.
- **No raw SQL.** Always SQLAlchemy. Exception: read-only reports may use SQL views.
- **Migrations are reversible.** Every `upgrade()` has a corresponding `downgrade()`.
- **Secrets never appear in code.** `git-secrets` pre-commit hook enforces.

---

## 14. Open Questions for Human Approval

Before scaffolding, get clarification on:

1. **Tenancy model.** Confirm: broker firm is a tenant in Entra ID, clients are scoped under broker firms. Is there a need for cross-broker-firm visibility (e.g. a master admin)?
2. **AI cost model.** Who pays for AI calls — the broker firm? Pass-through to client? Define per-client token quotas.
3. **Insurer-side integration.** Does any insurer expect data via a specific API/SFTP/email? If yes, defer to v2 but design exports with their format in mind.
4. **Approval workflow scope.** Is single-approver enough for v1, or do regulated insurers require dual approval before activation?
5. **Default Singapore schema completeness.** Confirm the attribute set: `grade`, `pass`, `class`, `occupation`, `job_function`, `nationality`, `salary`, `family_status`, `is_fw`. Anything missing for VDL or HSBC use cases?
6. **Domain and hosting plan SKU.** What domain? What App Service tier (B1 sufficient for staging, P1v3 recommended for prod)?
7. **Sample data for fixtures.** Can the validated STM placement slip + employee/dependant files be committed (anonymised) as test fixtures? If yes, scrub PII and add to `backend/tests/fixtures/`.

---

## 15. Reference: The Prototype

A working prototype (`inspro_platform.html`) is a single-file browser app that validates the entire extraction pipeline. Treat it as executable documentation:

- All regex patterns in §8 come from it
- The 99% match rate on STM (4607 employees, 4548 matched) was measured against it
- The diagnostics surface design (column mapping + attribute coverage + failure reasons) is implemented there

When in doubt about behaviour, run the prototype against real data and match its output.

---

## 16. How to Start

1. Read this entire brief.
2. Produce a written plan covering: assumptions, phase-by-phase milestones with day estimates, risks, and questions back to the human for §14. Surface anything you'd change.
3. Wait for plan approval.
4. Begin Phase 0.
5. After each phase, summarise what was built, what tests pass, and what's next.

If you encounter a decision the brief doesn't cover, default to the simpler choice and flag it for human review at the end of the phase.

Build small, verify often, keep the diagnostics surface honest. The reason this product exists is to make a manual, error-prone process auditable — every decision the system makes must be explainable to a human admin.
