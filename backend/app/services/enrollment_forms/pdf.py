"""Render a signed e-form snapshot to PDF (PyMuPDF ``Story`` — HTML in, real
text out, no browser or sidecar).

The layout follows the paper forms brokers used to collect, section for
section, so HR and insurers read it the same way: A personal particulars,
B benefit selection, C family members, D notes and documents, E declarations,
then the electronic signature block. It prints ONLY what the member chose and
the plan their grade is on — the paper form's every-grade premium table is not
reproduced.

MuPDF's HTML engine does not resolve CSS custom properties, so the colour
tokens live in ``PDF_TOKENS`` and are substituted into the stylesheet once.
Text stays in the Latin-1 range: any glyph outside the base-14 fonts pulls a
fallback font into the file (a single tick mark costs ~500 KB).
"""
from __future__ import annotations

import io
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

import fitz

SGT = ZoneInfo("Asia/Singapore")

PDF_TOKENS: dict[str, str] = {
    "foreground": "#1f2933",
    "muted": "#5f6b7a",
    "border": "#c9d1db",
    "accent": "#1d4f91",
}

_CSS = """
body { font-family: sans-serif; font-size: 9pt; color: {foreground}; }
h1 { font-size: 15pt; color: {accent}; margin: 0 0 2pt 0; }
h2 { font-size: 10pt; color: {accent}; padding: 0 0 2pt 0; margin: 12pt 0 5pt 0;
     border-bottom: 0.75pt solid {accent}; }
p { margin: 0 0 4pt 0; }
.muted { color: {muted}; }
.small { font-size: 8pt; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; font-size: 8pt; font-weight: bold; color: {muted};
     border: 0.5pt solid {border}; padding: 3pt; }
td { border: 0.5pt solid {border}; padding: 3pt; vertical-align: top; }
td.k { color: {muted}; font-size: 8pt; }
.right { text-align: right; }
.sub { font-weight: bold; margin: 10pt 0 4pt 0; }
ul { margin: 0 0 4pt 12pt; padding: 0; }
li { margin: 0 0 2pt 0; }
"""

_PAGE = fitz.paper_rect("a4")
_MARGIN = 40


def _css() -> str:
    css = _CSS
    for key, value in PDF_TOKENS.items():
        css = css.replace("{" + key + "}", value)
    return css


def _e(value: object) -> str:
    if value is None or value == "":
        return "-"
    return escape(str(value))


def _money(value: object, currency: str | None = None) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    symbol = "S$" if currency in (None, "", "SGD") else f"{currency} "
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{abs(value):,.2f}"


def _day(value: str | None) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y")
    except ValueError:
        return escape(value)


def _stamp(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return escape(value)
    return dt.astimezone(SGT).strftime("%d %b %Y, %H:%M SGT")


def _rows(pairs: list[tuple[str, object]]) -> str:
    """Label/value pairs, two to a row. MuPDF shares table width equally between
    columns regardless of CSS widths, so four equal columns is the layout that
    reads cleanly (and matches the paper form's "Name | Sex" rows)."""
    cells = [f"<td class='k'>{escape(k)}</td><td>{_e(v)}</td>" for k, v in pairs]
    if len(cells) % 2:
        cells.append("<td class='k'></td><td></td>")
    return "".join(f"<tr>{cells[i]}{cells[i + 1]}</tr>" for i in range(0, len(cells), 2))


def _header(s: dict[str, Any]) -> str:
    form = s["form"]
    return (
        f"<p class='muted small'>{_e(form['company_name'])}</p>"
        f"<h1>{_e(form['title'])}</h1>"
        f"<p>Policy period: {_day(form['policy_start'])} to {_day(form['policy_end'])}"
        f" &nbsp;|&nbsp; Enrolment closes: {_stamp(form['closes_at'])}</p>"
        f"<p class='muted small'>Reference {_e(form['reference_no'])} &nbsp;|&nbsp; "
        f"Version {_e(form['version'])} &nbsp;|&nbsp; Submitted {_stamp(form['submitted_at'])}</p>"
    )


def _with_flag(value: object, updated: object) -> str:
    text = str(value) if value else "-"
    return f"{text} (updated on this form)" if updated else text


def _section_a(s: dict[str, Any]) -> str:
    p = s["particulars"]
    return "<h2>Section A - Personal particulars</h2><table>" + _rows([
        ("Name of employee", p.get("name")),
        ("NRIC / FIN", p.get("id_no")),
        ("Staff ID", p.get("staff_id")),
        ("Sex", p.get("gender")),
        ("Date of birth", _day(p.get("dob"))),
        ("Job grade", p.get("job_grade")),
        ("Date of hire", _day(p.get("date_of_hire"))),
        ("Contact number", _with_flag(p.get("contact_no"), p.get("contact_updated"))),
        ("Email", _with_flag(p.get("email"), p.get("email_updated"))),
    ]) + "</table>"


def _cover(sel: dict[str, Any]) -> str:
    parts: list[str] = []
    if sel.get("highlight"):
        parts.append(escape(str(sel["highlight"])))
    if isinstance(sel.get("sum_insured"), (int, float)):
        parts.append(f"Sum insured {_money(sel['sum_insured'])}")
    return "<br/>".join(parts) or "-"


def _selection_table(s: dict[str, Any]) -> str:
    flex = s.get("flex")
    currency = flex.get("currency") if flex else None
    flex_head = "<th class='right'>Flex price tag</th>" if flex else ""
    out = [
        "<table><tr><th>Benefit</th><th>Before</th><th>Your choice</th><th>Cover</th>"
        f"<th>Family covered</th>{flex_head}<th class='right'>You pay (annual)</th></tr>"
    ]
    for sel in s.get("selections", []):
        choice = "Declined" if sel.get("declined") else (sel.get("elected_plan") or "Included")
        family = escape(", ".join(sel.get("covered") or [])) or "-"
        if sel.get("withdrawn"):
            gone = escape(", ".join(sel["withdrawn"]))
            family += f"<br/><span class='muted'>Withdrawn: {gone}</span>"
        benefit = _e(sel.get("product_name") or sel.get("product_code"))
        if sel.get("insurer"):
            benefit += f"<br/><span class='muted small'>{_e(sel['insurer'])}</span>"
        out.append(
            f"<tr><td>{benefit}</td>"
            f"<td>{_e(sel.get('previous_plan'))}</td><td>{_e(choice)}</td>"
            f"<td>{'-' if sel.get('declined') else _cover(sel)}</td><td>{family}</td>"
            + (
                f"<td class='right'>{_money(sel.get('price_tag'), currency)}</td>"
                if flex else ""
            )
            + f"<td class='right'>{_money(sel.get('you_pay'))}</td></tr>"
        )
    out.append("</table>")
    return "".join(out)


def _share_text(sel: dict[str, Any]) -> str:
    parts = []
    own, family = sel.get("employee_pct"), sel.get("dependant_pct")
    upgrade = sel.get("upgrade_pct")
    if own == 0 or (own is None and upgrade is not None):
        parts.append("Own cover paid by the company")
        if own is None and upgrade:
            parts.append(f"you pay {upgrade:g}% of the extra for a higher plan")
    elif own is not None:
        parts.append(f"You pay {own:g}% of your own cover")
    if family == 0:
        parts.append("family cover paid by the company")
    elif family is not None:
        parts.append(f"{'you pay ' if own in (None, 0) else ''}{family:g}% of family cover")
    text = "; ".join(parts)
    return text[:1].upper() + text[1:] if text else "-"


def _premium_table(s: dict[str, Any]) -> str:
    """The paper form's premium table, for the member's OWN plans only: the
    annual premium per family composition, and the share they bear."""
    # Only plans with money in play for the member: something to pay, a plan
    # change, or family on cover. A company-paid plan they kept is not listed.
    rows = [
        sel for sel in s.get("selections", [])
        if sel.get("premium") and not sel.get("declined")
        and (sel.get("you_pay") or sel.get("premium_change") or sel.get("covered"))
    ]
    if not rows:
        return ""
    out = [
        "<p class='sub'>Annual premium for your plans</p>"
        "<table><tr><th>Benefit and plan</th><th class='right'>Employee only</th>"
        "<th class='right'>Employee and spouse</th><th class='right'>Employee and child(ren)</th>"
        "<th class='right'>Family</th><th class='right'>Change vs current plan</th>"
        "<th>Your share</th></tr>"
    ]
    for sel in rows:
        prem = sel["premium"]
        per_dep = sel.get("premium_per_dependant")
        if per_dep is not None and len(prem) == 1:
            extra = f"+ {_money(per_dep)} each"
            cells = [_money(prem.get("EO")), extra, extra, extra]
        else:
            cells = [_money(prem.get(k)) for k in ("EO", "ES", "EC", "EF")]
        name = f"{sel.get('product_name') or sel.get('product_code')} - {sel.get('elected_plan')}"
        change = sel.get("premium_change")
        change_cell = (
            "No change" if not change
            else ("+" if change > 0 else "") + _money(change)
        )
        out.append(
            f"<tr><td>{_e(name)}</td>"
            + "".join(f"<td class='right'>{c}</td>" for c in cells)
            + f"<td class='right'>{change_cell}</td>"
            + f"<td>{_e(_share_text(sel))}</td></tr>"
        )
    out.append("</table>")
    return "".join(out)


def _flex_table(s: dict[str, Any]) -> str:
    """The flex wallet ledger: allowance, price tags drawn, leave traded, left."""
    flex = s.get("flex")
    if not flex:
        return ""
    cur = flex.get("currency")
    allowance = _money(flex.get("allowance"), cur)
    if flex.get("proration_note"):
        allowance += f" <span class='muted'>({_e(flex['proration_note'])})</span>"
    pairs: list[tuple[str, str]] = [
        ("Your flex allowance", allowance),
        ("Price tags for your choices", _money(-(flex.get("price_tags_used") or 0.0), cur)),
    ]
    if flex.get("leave_amount"):
        label = "Leave sold back" if flex["leave_amount"] > 0 else "Leave bought"
        pairs.append((label, _money(flex["leave_amount"], cur)))
    pairs.append(("Flex balance", f"<b>{_money(flex.get('balance'), cur)}</b>"))
    body = "".join(
        f"<tr><td class='k'>{escape(k)}</td><td class='right'>{v}</td></tr>" for k, v in pairs
    )
    note = ""
    if flex.get("unpriced"):
        note = (
            "<p class='muted small'>No flex price set yet for: "
            f"{_e(', '.join(flex['unpriced']))}.</p>"
        )
    return f"<p class='sub'>Flex dollars</p><table>{body}</table>{note}"


def _section_b(s: dict[str, Any]) -> str:
    out = ["<h2>Section B - Benefit selection</h2>"]
    for line in s.get("intro_lines", []):
        out.append(f"<p class='small'>{_e(line)}</p>")
    out.append(_selection_table(s))
    out.append(_premium_table(s))
    out.append(_flex_table(s))
    leave = s.get("leave")
    if leave and leave.get("action") in ("buy", "sell"):
        out.append(f"<p>Leave: {_e(leave['action'].title())} {_e(leave['days'])} day(s).</p>")
    note = s.get("premium_note") or s.get("pricing_note")
    if note:
        out.append(f"<p class='muted small'>{_e(note)}</p>")
    return "".join(out)


def _section_c(s: dict[str, Any]) -> str:
    deps = s.get("dependants") or []
    out = ["<h2>Section C - Family members</h2>"]
    if not deps:
        out.append("<p class='muted'>No family members named.</p>")
        return "".join(out)
    out.append(
        "<table><tr><th>Relationship</th><th>Full name</th><th>Sex</th><th>NRIC / BC / FIN</th>"
        "<th>Occupation</th><th>Date of birth</th><th>Covered on</th></tr>"
    )
    for d in deps:
        covered = escape(", ".join(d.get("covered_on") or [])) or "-"
        if d.get("status") == "pending":
            covered += "<br/><span class='muted'>New - pending verification</span>"
        out.append(
            f"<tr><td>{_e(d.get('relationship'))}</td><td>{_e(d.get('name'))}</td>"
            f"<td>{_e(d.get('gender'))}</td><td>{_e(d.get('id_no'))}</td>"
            f"<td>{_e(d.get('occupation'))}</td><td>{_day(d.get('dob'))}</td><td>{covered}</td></tr>"
        )
    out.append("</table>")
    return "".join(out)


def _section_d(s: dict[str, Any]) -> str:
    out = ["<h2>Section D - Eligibility and documents</h2>"]
    notes = s.get("eligibility_notes") or []
    if notes:
        out.append("<ul>" + "".join(f"<li>{_e(n)}</li>" for n in notes) + "</ul>")
    docs = s.get("documents") or []
    if docs:
        out.append("<p>Documents made available to the employee before signing:</p><ul>")
        out.extend(f"<li>{_e(d.get('label'))}</li>" for d in docs)
        out.append("</ul>")
    return "".join(out)


def _section_e(s: dict[str, Any]) -> str:
    out = ["<h2>Section E - Declarations</h2><table>"]
    for clause in s.get("declarations") or []:
        mark = "Agreed" if clause.get("accepted") else "Not agreed"
        out.append(f"<tr><td>{_e(clause.get('text'))}</td><td class='right'>{mark}</td></tr>")
    out.append("</table>")
    sig = s.get("signature") or {}
    signature_rows = _rows([
        ("Signed by (typed name)", sig.get("name")),
        ("Signed at", _stamp(sig.get("signed_at"))),
        ("Method", "Typed name and declaration confirmed in the employee portal"),
    ])
    out.append(f"<h2>Electronic signature</h2><table>{signature_rows}</table>")
    return "".join(out)


def _footer_block(s: dict[str, Any], sha256: str) -> str:
    form = s["form"]
    lines = [f"<p class='muted small'>Content fingerprint (SHA-256): {escape(sha256)}</p>"]
    if form.get("submission_note"):
        lines.append(f"<p class='small'>{_e(form['submission_note'])}</p>")
    if form.get("helpline"):
        lines.append(f"<p class='small'>{_e(form['helpline'])}</p>")
    return "".join(lines)


def render_form_html(snapshot: dict[str, Any], sha256: str) -> str:
    return "".join([
        _header(snapshot),
        _section_a(snapshot),
        _section_b(snapshot),
        _section_c(snapshot),
        _section_d(snapshot),
        _section_e(snapshot),
        _footer_block(snapshot, sha256),
    ])


def _stamp_footers(pdf: bytes, reference: str) -> bytes:
    doc = fitz.open(stream=pdf, filetype="pdf")
    total = doc.page_count
    for index, page in enumerate(doc, start=1):
        page.insert_text(
            (_MARGIN, _PAGE.height - 20),
            f"{reference}  |  Page {index} of {total}",
            fontsize=7,
            color=(0.37, 0.42, 0.48),
        )
    out: bytes = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def render_form_pdf(snapshot: dict[str, Any], sha256: str) -> bytes:
    story = fitz.Story(html=render_form_html(snapshot, sha256), user_css=_css())
    buffer = io.BytesIO()
    writer = fitz.DocumentWriter(buffer)
    where = fitz.Rect(_MARGIN, _MARGIN, _PAGE.width - _MARGIN, _PAGE.height - _MARGIN - 10)
    more = 1
    while more:
        device = writer.begin_page(_PAGE)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return _stamp_footers(buffer.getvalue(), str(snapshot["form"]["reference_no"]))
