# Inspro AI compliance readiness assessment

> Historical reference, archived 2 October 2026. Dates, verification results and open items below describe the original review, not the current deployment. See the current [AI Settings guide](../AI_SETTINGS.md).

Assessment date: 28 September 2026. Repository baseline: `d29a0b66ee9a45a40a116a380a69967e9f1a053b`.

**Conclusion:** Inspro has useful AI safeguards, but the reviewed evidence does not establish ISO/IEC 42001 conformity or certification readiness. The EU AI Act appears outside the current operating scope based on the owner's confirmation of no EU customers, EU use, or decisions affecting people in the EU. That is an applicability conclusion, not an EU compliance certificate.

The requested outcome was a readiness and gap assessment. The assessment itself changed no application behavior; subsequent implementation is recorded separately below.

## Implementation update: 28 September 2026

[AI Settings](../AI_SETTINGS.md) has since been deployed, consolidating provider administration, a persistent platform-wide policy library and guidance for existing roles. `system_admin` maintains policy PDFs; `broker_admin` and `broker_viewer` read published versions. Files, history and publication actions now have an implemented storage workflow. Actual approved policy content and organizational evidence still need to be supplied.

The gaps and verification results below refer to the stated baseline. This update does not rerun the legal assessment, close organizational evidence gaps or establish certification readiness. Prototype owners and separate oversight screens are retired; use the [implementation record](../AI_GOVERNANCE_IMPLEMENTATION_PLAN.md) and [UI specification](../AI_GOVERNANCE_UX_DESIGN.md) for current product behavior.

## Scope and evidence limits

- Reviewed source code, migrations, tests, configuration artifacts, product documentation and operational runbooks in this checkout. Ran a focused local test selection.
- The owner confirmed **no** EU customers, users or affected EU population. This assessment assumes no EU establishment/deployer or other EU placing-on-the-market arrangement inconsistent with that answer.
- Did not inspect production cloud configuration, actual decision records, customer contracts, supplier agreements, staff records or organizational documents held outside the repository. Did not send customer documents to an AI provider.
- “Not evidenced” means not located in the reviewed material; it does not establish that an organizational practice does not exist.
- This is an engineering readiness assessment, not a certification audit or formal legal opinion. ISO mapping uses public ISO material and a published standard preview; a full conformity audit needs the licensed standard and organizational evidence.
- Public legal sources were checked on the assessment date. Some Commission article pages explicitly retain pre-amendment wording; current Commission timelines and indexed consolidated legislation were used for the July 2026 changes.

## Two different questions

| Framework | What it evaluates | Current assessment |
| --- | --- | --- |
| ISO/IEC 42001:2023 | The organization's AI management system within a defined scope, including how it develops, supplies or uses AI. | **Conformity not demonstrated.** Technical controls provide a foundation; governance and operating evidence remain to be supplied or established. No certificate was provided or found. |
| EU AI Act | Applicable obligations for particular operators and AI uses with an EU connection. | **Appears outside current scope**, based on owner-provided facts. Reassess before EU expansion or a change in intended use. |

ISO describes 42001 as an organizational management-system standard. Passing software tests alone cannot establish conformity, and an ISO certificate would not itself settle the EU legal analysis. [ISO overview](https://www.iso.org/standard/42001).

The EU scope includes supplying AI into the EU, EU deployers, and certain non-EU providers/deployers whose AI outputs are used in the EU. Singapore hosting does not by itself determine applicability. [Article 2](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-2).

## AI uses found

| Use | Evidence and observed behavior | Assessment implication |
| --- | --- | --- |
| Placement-slip and flexible-benefit extraction | [AI gateway](../../backend/app/services/ai_gateway.py), [slip extractor](../../backend/app/services/ai_slip_extractor.py), and locally held `PRODUCT.md` assessment context. Converts supplied insurance terms into structured configuration. | Inventory the effects of an incorrect extracted limit, rate or eligibility term. Extracting an insurer's existing price is distinct from predicting an individual's insurance risk. |
| Eligibility-rule suggestions and roster/schema assistance | [Category API](../../backend/app/api/v1/categories.py), [eligibility mapping](../../backend/app/services/eligibility_mapping.py), [AI gateway](../../backend/app/services/ai_gateway.py). Suggestions, validation and broker confirmation are present. | Assess the complete route from a suggested rule to member coverage. A confirmation button alone does not prove effective oversight. |
| Member claim intake | [Intake suggestions](../../backend/app/services/claim_intake_suggest.py), [autofill UI](../../frontend/src/components/portal/claims/AutofillCard.tsx). Documents populate editable fields; uncertain readings are identified. | Assess extraction errors, sensitive medical information, user understanding and corrections. |
| Claim review and document verification | [Pipeline](../../backend/app/services/claims_review/pipeline.py), [verdict](../../backend/app/services/claims_review/verdict.py), [claim AI](../../backend/app/services/claim_ai.py). AI produces comparisons, rules, confidence and a clean/flagged result. | AI affects review handling, so accuracy, oversight and adverse-impact evidence matter even without automatic final decisions. |
| Underwriting administration | Locally held `UNDERWRITING.md` assessment notes; the [underwriting implementation](../../backend/app/services/underwriting.py) is retained in the repository. Records insurer/broker decisions and applies configured non-evidence-limit triggers. | No AI mortality/risk prediction was identified in the reviewed underwriting material. Confirm intended use before classifying a future AI underwriting feature. |

The configured AI provider is Vertex/Gemini; the code's default model is `gemini-3.5-flash`. This identifies the checkout configuration, not a verified live deployment. [Provider configuration](../../backend/app/core/ai_config.py).

## Safeguards already evidenced

1. **Human claim decisions.** The pipeline changes claims to `ai_verified` or `ai_flagged`; it does not approve or reject them. The broker decision endpoint records the decision maker, time and audit event. See `pipeline.py:141` and [claims API](../../backend/app/api/v1/claims.py), `decide_claim` at line 560.
2. **Manual fallback.** Provider failures return claims to a state available for manual review. Stale pipeline output is guarded against overwriting a broker decision or amended claim. These behaviors were covered by the passing tests.
3. **Review evidence.** [ClaimAIReview](../../backend/app/models/claim_ai_review.py) retains extraction results, comparisons, rule evidence, confidence, model name, timestamps and a review-configuration snapshot/fingerprint. Re-runs supersede earlier reviews.
4. **Access and audit controls.** Claim access is role-gated; viewer mutations are tested. Audit payloads redact credential-like fields. A PostgreSQL migration implements an append-only audit trigger. The trigger was inspected, but its production installation and PostgreSQL behavior were not tested in this run. [Migration](../../backend/alembic/versions/a4c6e8f0b2d4_claims_production_integrity.py).
5. **Provider and resilience controls.** Singapore-region restrictions, encrypted stored credentials, configuration probes, caching, budgets, circuit breakers and durable worker recovery exist. These are implementation controls; supplier terms and actual deployment settings remain separate evidence.
6. **Initial evaluation controls.** Versioned confidence thresholds, a dataset hash, low-confidence handling and output validation are present. [Threshold profile](../../backend/config/claims_ai_thresholds.json).
7. **Prompt boundaries and operational monitoring.** Prompts treat documents as untrusted evidence. Queue age, failures, latency and provider usage have telemetry hooks. Prompt instructions are a defense, not proof of resistance to adversarial documents. [Claim prompts](../../backend/app/services/claim_ai.py), [metrics](../../backend/app/services/claims_review/metrics.py).

## ISO/IEC 42001 readiness map

Clause references below are orientation points, not a complete normative checklist. The standard preview confirms the structure covering scope, leadership, planning, support, operation, evaluation and improvement. [Published ISO/IEC 42001 preview](https://cdn.standards.iteh.ai/samples/81230/4c1911ebc9a641fcb6ee21aa09c28ad3/ISO-IEC-42001-2023.pdf).

| Area | Evidence found | Evidence still needed |
| --- | --- | --- |
| Scope and accountability (4–5) | Product purpose, roles and technical access controls. | Approved AIMS scope, accountable executive, AI policy, responsibility assignments and stakeholder/legal obligations register. |
| Risk and impact management (6, 8) | Engineering failure controls and documented remediation work. | Recorded AI risk and impact assessments covering members, dependants, employers and brokers; risk owners, treatment decisions and acceptance evidence; Statement of Applicability. |
| Objectives and controlled change (6, 8) | Versioned code, prompts and threshold artifacts. | Approved quality objectives, release criteria, independent validation and model/prompt/configuration change approvals. |
| Competence and documentation (7) | Technical runbooks. | Role-specific AI training records, assessor competence checks and controlled governance documents. |
| Operational control and supplier assurance (8; applicable Annex A controls) | Provider gateway, human decisions, review history and regional restrictions. | Data inventory, supplier assessment, approved processing purposes, retention rules and evidence of operating these controls. |
| Measurement and assurance (9) | Regression tests, small calibration dataset and reliability telemetry. | Representative validation, outcome monitoring, internal AI-management-system audit and management-review records. |
| Improvement (10) | Engineering fixes and incident/recovery guidance. | AI issue/nonconformity register, corrective-action owners and evidence that actions were effective. |

No AI management policy, formal AI impact assessment, Statement of Applicability, AI literacy record, AIMS internal audit or management review was located in the searched repository material. Existing security and resilience documents do not substitute for these records. This finding should be checked against the organization's external document store before creating duplicate processes.

## Material gaps and recommended acceptance evidence

Priority indicates recommended remediation order, not a legal severity rating. Named owners below are proposed roles, not assignments already accepted.

| Priority | Finding | Recommended owner | Evidence that would close it |
| --- | --- | --- | --- |
| P1 | AIMS governance is not evidenced. | Executive sponsor + compliance owner | Approved scope and policy; AI inventory; named owners; risk/impact assessments; control applicability and treatment decisions. |
| P1 | Existing calibration cannot establish production accuracy. | AI/engineering lead + claims assessor | Representative adjudicated corpus, independently held-out evaluation, source/output/label lineage, documented acceptance thresholds and error analysis. |
| P1 | Human-decision evidence and member explanations are incomplete. | Claims operations + product | Oversight procedure, assessor training, sampled decision-quality review, required rationale for adverse decisions and material AI overrides, and a documented reconsideration process. |
| P1 | Data and supplier governance are not evidenced end to end. | Privacy/security owner | Medical-data flow inventory; appropriate processing basis and notices; supplier agreements and retention/training-use terms; deletion/retention and incident evidence across database, documents, cache, logs and backups. |
| P2 | Model change can reuse thresholds without model-specific evaluation. | AI/engineering lead | Explicit release gate tying each approved model/prompt/configuration to an evaluated profile; documented fallback policy for unassessed combinations. |
| P2 | Historical review records do not capture a complete execution manifest. | Engineering lead | Per-stage model identifier, prompt version, threshold/profile hash, relevant input references/hashes, application revision and human-decision linkage retained with each review. |
| P2 | Member-facing AI disclosure is inconsistent across the inspected intake paths. | Product + privacy owner | Clear first-use explanation of AI assistance and limits; tested correction and human-review paths; consistent notices for single- and multiple-invoice intake. |
| P2 | Monitoring primarily measures system operation. | AI/claims operations | Outcomes sampled against assessor ground truth; false-clear/false-flag rates; override and complaint trends; justified subgroup/language/document-quality analysis; triggers and owners for corrective action. |
| P2 | Independent AIMS assurance is not evidenced. | Compliance owner + independent auditor | Internal audit, recorded management review and completed corrective-action follow-up before a certification-readiness decision. |

**Evaluation detail.** The checked-in profile contains 24 intake and 16 review cases. Both flows have stored overall correctness of 62.5%; reported false acceptance is 0% on the subsets accepted by their selected thresholds. These are properties of this small stored dataset, not measured production accuracy. The evaluator selects thresholds and reports metrics on the same cases. The JSONL contains confidence/correctness labels, but does not itself contain source documents, model outputs or adjudicator provenance. No independently held-out validation was found in the reviewed evaluation path. [Evaluator](../../backend/app/services/claims_ai_evaluation.py), [dataset](../../backend/evals/claims_ai/gold.jsonl), [existing caveat](AI_CLAIM_REVIEW_PRODUCTION_REMEDIATION.md).

**Model-change detail.** `confidence_threshold` falls back to a document-type or default threshold when a model-specific combination is absent. A successful provider structured-output probe does not validate claims accuracy for that model. [Runtime thresholds](../../backend/app/services/claims_ai_confidence.py), [queue activation checks](../../backend/app/services/claims_review/queue.py).

**Human oversight detail.** The decision schema allows a missing note for ordinary rejection and approval. The API separately requires a note for rejection after sending to an insurer, and the schema requires a note for requests for information. Thus, a decision can be attributed to a person without retaining a substantive explanation. This is a readiness gap, not a finding that all decisions lack explanations. Member messaging exists, but a formal appeal/reconsideration procedure was not found. [Decision schema](../../backend/app/schemas/claims.py), [decision API](../../backend/app/api/v1/claims.py).

**Transparency detail.** The principal intake control says “Autofill from your documents”; a later expandable section explains model confidence. A multiple-invoice notice mentions AI review. This does not establish consistent disclosure before the first AI-assisted upload. A clear notice is a recommended governance improvement now; EU Article 50 applicability is a separate future legal question. [Autofill UI](../../frontend/src/components/portal/claims/AutofillCard.tsx), [multiple-invoice notice](../../frontend/src/components/portal/claims/PendingClaimsNotice.tsx).

## EU expansion: conditions to reassess

The present result is **apparently outside scope**, not “failed compliance.” Reopen this assessment before supplying the platform into the EU, serving an EU deployer, or arranging for its AI outputs to be used there.

If that changes, classify each intended use and each party's role:

- **Platform role:** Inspro would likely be the downstream provider of its branded AI system; customers may be deployers. Using Google's model does not transfer all system-level responsibilities to Google. No general-purpose foundation-model training or distribution was identified here. The role conclusion is provisional and needs the actual supply arrangement. [Consolidated Act, definitions](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02024R1689-20260727).
- **Claims processing:** The Commission's draft classification examples distinguish health-insurance claims administration from insurance risk assessment/pricing. This supports a provisional view that the reviewed claims workflow is outside Annex III 5(c), provided that remains its intended use. The examples are interpretive draft guidance, not a blanket exemption from the whole Act. [Commission examples](https://ai-act-service-desk.ec.europa.eu/en/essential-services), [draft status](https://digital-strategy.ec.europa.eu/en/library/draft-commission-guidelines-classification-high-risk-ai-systems).
- **Underwriting and pricing:** AI that assesses natural persons' life/health insurance risk or determines premiums is a high-risk classification candidate under Annex III 5(c). Extracting pre-agreed rate tables does not, by itself, establish that function. [Annex III](https://ai-act-service-desk.ec.europa.eu/en/ai-act/annex-3).
- **Employment and benefit eligibility:** Examine whether AI-generated eligibility rules materially determine employment-related entitlements or terms. A benefits-administration product is not automatically an employment high-risk system, but this route needs its own assessment under Annex III 4(b). Public-assistance deployments would require separate consideration under 5(a). Human confirmation alone does not resolve classification. [Annex III](https://ai-act-service-desk.ec.europa.eu/en/ai-act/annex-3).
- **Transparency and literacy:** Map direct AI interactions and generated outputs individually. Article 50 is not a universal requirement to label every internal calculation. The amended Article 4 requires measures supporting staff AI literacy, without guaranteeing a specific individual proficiency level. Training remains useful evidence for both oversight and ISO readiness. [Article 50 guidance](https://digital-strategy.ec.europa.eu/en/library/guidelines-transparency-obligations-providers-and-deployers-ai-systems), [consolidated Article 4](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02024R1689-20260727).

If a use is classified as high-risk, prepare the applicable provider/deployer evidence: risk and quality management, technical documentation, validation, logs, instructions, oversight, monitoring and incident handling, plus the relevant conformity/registration steps. Assess the need for a fundamental-rights impact assessment based on the specific deployer/use; it is not required of every ordinary SaaS deployment. No such EU assurance package was found here. The detailed requirements should be mapped against the amended legislation before EU launch. [Commission overview](https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai), [impact-assessment provision](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-27).

**Timing checked on 28 September 2026.** The Commission reports that the AI Omnibus entered into force on 27 July 2026. Its updated timeline places Annex III high-risk rules on 2 December 2027 and Annex I product-related high-risk rules on 2 August 2028. Article 50 generally applies from 2 August 2026, with a 2 December 2026 transition for certain pre-existing systems under Article 50(2). The high-risk extension is not a postponement of every obligation. [Commission announcement](https://digital-strategy.ec.europa.eu/en/news/ai-omnibus-enters-force), [current implementation timeline](https://ai-act-service-desk.ec.europa.eu/en/ai-act/timeline/timeline-implementation-eu-ai-act).

## Practical next steps

1. **Confirm existing organizational evidence.** Collect any AI policy, training, supplier assessments, data-processing agreements, risk assessments and audit records stored outside this repository. Confirm the no-EU scope in a short signed applicability record.
2. **Establish the AIMS foundation.** Name a sponsor and operational owner. Approve the AI inventory, scope, policy, risk/impact assessment and control applicability. Keep this proportional to Inspro's actual uses.
3. **Close the evidence gaps in the product.** Expand and independently evaluate the claims corpus; gate model changes; improve decision rationale, AI disclosure and review provenance; document data retention and supplier controls.
4. **Demonstrate operation.** Record assessor training, monitoring reviews, incidents/corrections and control checks over an appropriate operating period. Conduct an independent internal audit and management review, then decide whether to pursue certification.

Suggested external statement at present: “Inspro uses human-reviewed AI assistance with access controls, retained review evidence and manual fallback. We are assessing readiness for ISO/IEC 42001. We have not established certification or conformity through this review. Based on our current operating scope, the EU AI Act does not appear applicable.”

## Verification performed

Executed from `backend/`:

```text
uv run pytest tests/test_claims_review_pipeline.py tests/test_claims_ai_reliability.py tests/test_ai_gateway_claims.py tests/test_claims_rbac.py tests/test_audit_sanitization.py tests/test_ai_config_vertex.py -q
```

Result: **117 passed, 82 deprecation warnings, 49.97 seconds**. The warnings originated from dependencies. These tests exercise local application behavior and mocked provider paths; they do not establish live model accuracy, production controls, organizational compliance or certification. No production deployment or external audit was performed.
