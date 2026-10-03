"""Static checks for production startup and local fixture privacy."""
import ast
import json
import re
import shlex
from pathlib import Path

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

ROOT = Path(__file__).resolve().parents[2]


def test_production_cmd_is_exec_form_with_one_quoted_proxy_argument():
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    command = dockerfile[dockerfile.rindex("\nCMD ") + len("\nCMD "):]
    command = re.sub(r"\\\r?\n\s*", "", command)
    argv = json.loads(command)
    assert argv[:2] == ["sh", "-c"]
    assert len(argv) == 3
    assert argv[2].startswith("exec gunicorn ")
    shell_args = shlex.split(argv[2])
    proxy_value = shell_args[shell_args.index("--forwarded-allow-ips") + 1]
    assert proxy_value == "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
    assert "*" not in shell_args


def test_fixture_script_does_not_copy_working_database():
    source = (ROOT / "backend/scripts/seed_portal_ui_review.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {"backup", "copy", "copyfile", "copy2"}
                   for node in ast.walk(tree))
    assert "before-seed.db" not in source
    assert "before-cleanup.db" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,expected", [
    ("169.254.129.1", "198.51.100.25"),
    ("203.0.113.10", "203.0.113.10"),
])
async def test_production_proxy_trust_ignores_spoofed_forwarded_prefix(peer, expected):
    observed = {}

    async def capture(scope, receive, send):
        observed.update(scope)

    middleware = ProxyHeadersMiddleware(capture, trusted_hosts="127.0.0.1,169.254.129.1")
    await middleware({
        "type": "http", "client": (peer, 80), "scheme": "http",
        "headers": [(b"x-forwarded-for", b"192.0.2.66, 198.51.100.25"),
                    (b"x-forwarded-proto", b"https")],
    }, None, None)
    assert observed["client"][0] == expected
    assert observed["scheme"] == ("https" if peer == "169.254.129.1" else "http")
