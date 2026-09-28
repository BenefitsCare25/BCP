"""Package proposed design sheets without modifying their pixels."""
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent
PDF_PATH = ROOT / "Inspro-AI-Governance-Design.pdf"
SHEETS = [
    ("Firm AI oversight", "01-firm-ai-oversight.png"),
    ("Explained claim decision", "02-claim-decision.png"),
    ("Platform release validation", "03-release-validation.png"),
    ("Member claim journey", "04-member-claim-journey.png"),
]


def text_block(page, y, text, size=15, font="helv", width=990):
    rect = fitz.Rect(72, y, 72 + width, 740)
    height = page.insert_textbox(
        rect, text, fontsize=size, fontname=font, color=(0.11, 0.10, 0.10)
    )
    if height < 0:
        raise RuntimeError("PDF text exceeds its page")


doc = fitz.open()
page = doc.new_page(width=1152, height=768)
text_block(page, 62, "Inspro AI governance", size=32, font="hebo")
text_block(page, 112, "Implementation plan and annotated UI/UX designs", size=20)
text_block(page, 164, "Proposed design handoff | 28 September 2026 | Synthetic examples", size=12)
text_block(
    page,
    219,
    "First delivery: explain adverse claim decisions, disclose AI before autofill, "
    "and retain the exact AI review used by the assessor. Establish named owners "
    "and the initial governance records alongside this work.",
    size=18,
)
milestones = [
    (326, "M0  Accountability", "Approved scope, policy, owners, risks and supplier evidence requests."),
    (392, "M1  Claim safeguards", "Decision reasons, member disclosure and review provenance."),
    (458, "M2  Oversight and validation", "Firm/platform records, independent evaluation and approved releases."),
    (524, "M3  Ongoing oversight", "Reconsideration requests, outcome monitoring and corrective actions."),
    (590, "M4  Audit readiness", "Internal audit, management review and resolution of findings."),
]
for y, title, detail in milestones:
    text_block(page, y, title, size=17, font="hebo")
    text_block(page, y + 24, detail, size=14)
text_block(
    page,
    686,
    "Read the companion implementation plan and UX specification for exact requirements. "
    "These designs do not represent certification, completed features or production changes.",
    size=11,
)

toc = [[1, "Plan and delivery sequence", 1]]
for title, filename in SHEETS:
    path = ROOT / filename
    if not path.is_file():
        raise FileNotFoundError(path)
    page = doc.new_page(width=1152, height=768)
    page.insert_image(page.rect, filename=str(path), keep_proportion=True)
    toc.append([1, title, len(doc)])

page = doc.new_page(width=1152, height=768)
text_block(page, 56, "Companion screen: evidence and policy", size=27, font="hebo")
text_block(page, 105, "M2 | Firm administrator | Same register and detail pattern as the use inventory", size=13)
text_block(page, 161,
    "Evidence                 Version       Review state          Owner\n"
    "AI policy                Draft 1       Action required       Firm admin\n"
    "Assessor training        Not recorded  Action required       Claims lead\n"
    "Data processing          v1            Review due            Privacy owner\n\n"
    "Selected: AI policy\n"
    "Scope      This broker firm's use of AI\n"
    "Document   AI-policy-draft.pdf                       [View document]\n"
    "Version    Draft 1\n"
    "Reviewer   Unassigned                               [Assign reviewer]\n"
    "Next step  Assign a reviewer before requesting approval.\n\n"
    "[Save draft]   [Request approval - unavailable until complete]",
    size=15, font="cour")
text_block(page, 483,
    "1  Approval records a person, date and exact document version.\n\n"
    "2  Editing an approved document creates a draft; prior evidence remains available.\n\n"
    "3  Staff competence records and restricted attachments have narrower access.\n\n"
    "4  Reviewed evidence never becomes an automatic ISO compliance badge.", size=16)
text_block(page, 700, "Proposed layout | Synthetic records | See UX specification, screen E", size=11)
toc.append([1, "Evidence and policy wireframe", len(doc)])

page = doc.new_page(width=1152, height=768)
text_block(page, 56, "Companion screen: data and suppliers", size=27, font="hebo")
text_block(page, 105, "M2 | Platform operator | Firm-specific evidence stays in its own firm scope", size=13)
text_block(page, 163,
    "Supplier: Google Vertex AI                  Review: Action required\n"
    "Applies to    Claim intake, claim review, configured extraction\n"
    "Processing    Configured Singapore endpoint  [View configuration]\n"
    "Data          Claim documents; health information; identifiers\n\n"
    "Evidence                                     State\n"
    "Processing agreement                         Not linked\n"
    "Retention and training-use terms             Needs verification\n"
    "Inspro retention schedule                    Awaiting approval\n"
    "Deletion / backup procedure                  Not linked\n\n"
    "Next action   Verify provider terms | Owner: Privacy owner\n"
    "[Add evidence]    [Create action]",
    size=15, font="cour")
text_block(page, 483,
    "1  A configured endpoint is separate from a verified supplier commitment.\n\n"
    "2  Unknown retention or training terms remain unknown, with an owner and action.\n\n"
    "3  Credentials remain on the authorized AI Provider settings surface.\n\n"
    "4  Evidence exports identify scope and omit raw medical documents by default.", size=16)
text_block(page, 700, "Proposed layout | Synthetic records | See UX specification, screen F", size=11)
toc.append([1, "Data and supplier wireframe", len(doc)])

doc.set_toc(toc)
doc.set_metadata({
    "title": "Inspro AI Governance - Plan and Annotated Designs",
    "subject": "Proposed design handoff; synthetic examples; no production changes",
    "author": "Inspro planning workspace",
})
doc.save(PDF_PATH, deflate=True)
doc.close()

with fitz.open(PDF_PATH) as check:
    if len(check) != 7:
        raise RuntimeError("Unexpected PDF page count")
    for page_index in [0, 5, 6]:
        check[page_index].get_pixmap(matrix=fitz.Matrix(1, 1)).save(
            ROOT / f"qa-pdf-page-{page_index + 1}.png"
        )
    print(f"Created {PDF_PATH.name}: {len(check)} pages, {PDF_PATH.stat().st_size:,} bytes")
