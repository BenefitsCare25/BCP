"""Render the local design handoff; no application or database changes."""
from html import escape
from pathlib import Path
import json
import shutil

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
DOCS = {
    'AI_GOVERNANCE_IMPLEMENTATION_PLAN.md': ('implementation-plan', 'Implementation plan'),
    'AI_GOVERNANCE_UX_DESIGN.md': ('ux-specification', 'UX specification'),
    'AI_COMPLIANCE_READINESS_2026-09-28.md': ('readiness-assessment', 'Readiness assessment'),
}
md = MarkdownIt('commonmark', {'html': False}).enable('table')


def link_open(tokens, idx, options, env):
    token = tokens[idx]
    href = token.attrGet('href') or ''
    basename = href.rsplit('/', 1)[-1]
    if basename in DOCS:
        token.attrSet('href', DOCS[basename][0] + '.html')
    elif href.startswith(('https://', 'http://', '#')):
        pass
    elif href.startswith('../output/ai-governance-design-2026-09-28/'):
        token.attrSet('href', basename)
    else:
        # Source-code references remain useful, but the preview never serves the repo.
        token.tag = 'span'
        token.attrs = {'class': 'source-reference', 'title': 'Repository reference: ' + href}
        depth = 1
        for sibling in tokens[idx + 1:]:
            if sibling.type == 'link_open':
                depth += 1
            elif sibling.type == 'link_close':
                depth -= 1
                if depth == 0:
                    sibling.tag = 'span'
                    break
    return md.renderer.renderToken(tokens, idx, options, env)


def image_rule(tokens, idx, options, env):
    token = tokens[idx]
    src = token.attrGet('src') or ''
    if src.startswith('../output/ai-governance-design-2026-09-28/'):
        token.attrSet('src', src.rsplit('/', 1)[-1])
    return md.renderer.image(tokens, idx, options, env)


md.renderer.rules['link_open'] = link_open
md.renderer.rules['image'] = image_rule
md.renderer.rules['table_open'] = lambda *_: '<div class="table-wrap" tabindex="0" role="region" aria-label="Scrollable reference table"><table>\n'
md.renderer.rules['table_close'] = lambda *_: '</table></div>\n'

for source, (slug, title) in DOCS.items():
    content = (REPO / 'docs' / source).read_text(encoding='utf-8')
    (ROOT / (slug + '.md')).write_text(content, encoding='utf-8')
    rendered = md.render(content).replace('<pre>', '<pre tabindex="0" role="region" aria-label="Scrollable code or diagram">')
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Inspro design review</title><link rel="icon" href="data:,"><link rel="stylesheet" href="review.css"></head>
<body><a class="skip-link" href="#document">Skip to document</a>
<header class="topbar"><a class="brand" href="index.html" aria-label="Inspro design review home"><span class="brand-mark" aria-hidden="true">i</span><strong>INSPRO</strong><span class="brand-divider"></span><span>Design review</span></a><a class="button compact" href="Inspro-AI-Governance-Design.pdf" download>Download design pack ↓</a></header>
<main class="document-page"><nav class="document-toolbar" aria-label="Document navigation"><a href="index.html">← Back to annotated screens</a><a class="button" href="{slug}.md" download>Download Markdown ↓</a></nav>
<p class="document-meta">28 September 2026 · {escape(title)} · Local review copy</p>
<article class="document-content" id="document" tabindex="-1">{rendered}</article>
<footer class="page-footer"><a href="implementation-plan.html">Implementation plan</a><a href="ux-specification.html">UX specification</a><a href="readiness-assessment.html">Readiness assessment</a></footer></main></body></html>'''
    (ROOT / (slug + '.html')).write_text(html, encoding='utf-8')

font_folder = REPO / 'frontend/node_modules/@fontsource-variable/inter'
font_source = font_folder / 'files/inter-latin-wght-normal.woff2'
if font_source.is_file():
    shutil.copy2(font_source, ROOT)
elif not (ROOT / 'inter-latin-wght-normal.woff2').is_file():
    raise FileNotFoundError('Install frontend dependencies or restore the bundled Inter font.')
if (font_folder / 'LICENSE').exists():
    shutil.copy2(font_folder / 'LICENSE', ROOT / 'Inter-LICENSE.txt')

prompts = json.loads((ROOT / 'generation-prompts.json').read_text(encoding='utf-8-sig'))
prompts['corrections'] = [{
    'applies_to': ['01-firm-ai-oversight.png', '03-release-validation.png'],
    'prompt': (ROOT / 'correction-prompt.txt').read_text(encoding='utf-8').strip(),
    'method': 'Built-in image_gen edit; original generated files retained',
}]
(ROOT / 'generation-prompts.json').write_text(json.dumps(prompts, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print('Created 3 readable document pages, 3 Markdown copies, and the local font.')
