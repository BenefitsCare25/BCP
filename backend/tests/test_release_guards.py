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


# ── Developer tooling stays out of the production image ──────────────────────

# Seed/reset tooling that must never ship. Each also refuses to run unless
# INSPRO_ENV=dev against SQLite (`assert_local_dev_database`).
DEV_ONLY_SCRIPTS = (
    "dev.ps1",
    "local_dev.py",
    "reset_data.py",
    "seed_cdl_portal_accounts.py",
    "seed_claims_demo.py",
    "seed_portal_ui_review.py",
)
# Operational entry points that run from the image, plus seed_demo.py: app
# startup (app/core/drift_checks.py) and seed_firm_library.py import its catalogs.
SHIPPED_SCRIPTS = (
    "calibrate_claims_ai.py",
    "create_system_admin.py",
    "provision_tenants.py",
    "reconcile_product_identity.py",
    "relocate_to_firm_schemas.py",
    "report_leaver_access.py",
    "run_private_migrations.py",
    "seed_demo.py",
    "seed_firm_library.py",
    "seed_insurers.py",
)


def _dockerignored(path: str) -> bool:
    """Whether the root .dockerignore keeps `path` out of the build context.

    Mirrors Docker's matching: patterns are anchored at the context root, `*`
    stays inside one path segment, `**` spans any number of segments, a pattern
    that matches a directory covers everything below it, and the last matching
    line wins (`!` re-includes).
    """
    excluded = False
    for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        body = re.escape(pattern.lstrip("!").strip("/"))
        body = body.replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
        body = body.replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        if re.fullmatch(f"{body}(?:/.*)?", path):
            excluded = not pattern.startswith("!")
    return excluded


def _imported_scripts(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "scripts":
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("scripts."):
            names.add((node.module or "").split(".")[1])
        elif isinstance(node, ast.Import):
            names.update(
                alias.name.split(".")[1]
                for alias in node.names
                if alias.name.startswith("scripts.")
            )
    return names


def test_dev_only_scripts_are_excluded_from_the_production_image():
    """The Dockerfile copies backend/scripts wholesale, so .dockerignore is the gate."""
    assert _dockerignored("backend/inspro.db")
    assert not _dockerignored("backend/app/main.py")
    assert "COPY backend/scripts ./scripts" in (ROOT / "backend/Dockerfile").read_text(
        encoding="utf-8"
    )
    for name in DEV_ONLY_SCRIPTS:
        assert (ROOT / "backend/scripts" / name).is_file(), name
        assert _dockerignored(f"backend/scripts/{name}"), name
    for name in SHIPPED_SCRIPTS:
        assert (ROOT / "backend/scripts" / name).is_file(), name
        assert not _dockerignored(f"backend/scripts/{name}"), name


def test_shipped_code_never_imports_an_excluded_script():
    """An import of a script the image omits would only fail in production."""
    backend = ROOT / "backend"
    shipped = [
        path
        for path in [*(backend / "app").rglob("*.py"), *(backend / "scripts").glob("*.py")]
        if not _dockerignored(path.relative_to(ROOT).as_posix())
    ]
    assert shipped
    for path in shipped:
        for name in _imported_scripts(path):
            assert not _dockerignored(f"backend/scripts/{name}.py"), (path.name, name)


@pytest.mark.parametrize(
    ("module_name", "entry", "args"),
    [
        ("scripts.reset_data", "reset", ()),
        ("scripts.seed_demo", "seed", ()),
        ("scripts.seed_claims_demo", "seed_claims_demo", ()),
        ("scripts.seed_cdl_portal_accounts", "main", (1,)),
        ("scripts.seed_portal_ui_review", "main", ()),
    ],
)
@pytest.mark.parametrize(
    ("env", "database_url"),
    [
        ("prod", "sqlite:///inspro.db"),
        ("dev", "postgresql+psycopg://inspro@db.example.com:5432/inspro"),
    ],
)
def test_dev_scripts_refuse_before_opening_a_session(
    monkeypatch, module_name, entry, args, env, database_url
):
    """Seed/reset tooling needs INSPRO_ENV=dev AND SQLite, checked before any
    session exists — wherever the script is run from."""
    import importlib

    module = importlib.import_module(module_name)

    def _no_session():
        raise AssertionError(f"{module_name} opened a session before its guard")

    monkeypatch.setattr(module, "SessionLocal", _no_session)
    monkeypatch.setattr(module, "DATABASE_URL", database_url)
    monkeypatch.setenv("INSPRO_ENV", env)
    with pytest.raises(RuntimeError, match="INSPRO_ENV=dev"):
        getattr(module, entry)(*args)
