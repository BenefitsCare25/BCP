"""Serve only the design review artifacts on this computer's loopback interface."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse
import json
import os

ROOT = Path(__file__).resolve().parent
PUBLIC = {
    'index.html', 'review.css', 'review.js', 'inter-latin-wght-normal.woff2',
    'Inter-LICENSE.txt', 'Inspro-AI-Governance-Design.pdf',
    '01-firm-ai-oversight.png', '02-claim-decision.png',
    '03-release-validation.png', '04-member-claim-journey.png',
    'qa-pdf-page-6.png', 'qa-pdf-page-7.png',
    'implementation-plan.html', 'ux-specification.html', 'readiness-assessment.html',
    'implementation-plan.md', 'ux-specification.md', 'readiness-assessment.md',
}


class ReviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == '/health':
            body = json.dumps({'service': 'inspro-ai-governance-design-review', 'status': 'ok'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path not in {'/'} | {'/' + name for name in PUBLIC}:
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        path = unquote(urlsplit(self.path).path)
        if path not in {'/'} | {'/' + name for name in PUBLIC}:
            self.send_error(404)
            return
        super().do_HEAD()

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=4178)
    args = parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1', args.port), partial(ReviewHandler, directory=str(ROOT))) as server:
        info = {'pid': os.getpid(), 'url': f'http://127.0.0.1:{server.server_port}', 'directory': str(ROOT)}
        (ROOT / 'server-info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
        print(json.dumps(info), flush=True)
        server.serve_forever()
