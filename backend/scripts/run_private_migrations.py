"""One-shot private-network release migrations; never starts the HTTP app."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from azure.identity import ManagedIdentityCredential
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def validate_release(actual: str, expected: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", expected) or actual != expected:
        raise RuntimeError("Migration image does not match the expected release")


def validate_database(url: str, host: str, network: str) -> None:
    parsed = make_url(url)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.host != host
        or parsed.database != "inspro"
        or any(key in parsed.query for key in ("host", "hostaddr", "dbname", "service"))
        or parsed.query.get("sslmode") not in {"require", "verify-full"}
    ):
        raise RuntimeError("Migration database target or TLS configuration is invalid")
    allowed = ipaddress.ip_network(network)
    addresses = socket.getaddrinfo(host, parsed.port or 5432, type=socket.SOCK_STREAM)
    if not addresses or any(ipaddress.ip_address(a[4][0]) not in allowed for a in addresses):
        raise RuntimeError(
            "Database DNS did not resolve exclusively to the production private network"
        )


def read_secret(credential: ManagedIdentityCredential, vault: str, name: str) -> str:
    parsed = urlparse(vault)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".vault.azure.net"):
        raise RuntimeError("Invalid migration vault URL")
    token = credential.get_token("https://vault.azure.net/.default").token
    request = Request(
        f"{vault.rstrip('/')}/secrets/{name}?api-version=7.4",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=30) as response:
        value = json.load(response)["value"]
    if not isinstance(value, str) or not value:
        raise RuntimeError("Required migration secret is empty")
    return value


def redact(output: str, secrets: list[str]) -> str:
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        output = output.replace(secret, "[redacted]")
    return output


def main() -> None:
    validate_release(
        os.environ.get("INSPRO_GIT_SHA", ""), os.environ.get("INSPRO_EXPECTED_GIT_SHA", "")
    )
    with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
        vault = os.environ["INSPRO_MIGRATION_VAULT_URL"]
        database = read_secret(credential, vault, "database-url")
        encryption_key = read_secret(credential, vault, "ai-key-encryption-key")
    validate_database(
        database,
        os.environ["INSPRO_MIGRATION_DATABASE_HOST"],
        os.environ["INSPRO_MIGRATION_DATABASE_NETWORK"],
    )
    os.environ["INSPRO_DATABASE_URL"] = database
    os.environ["INSPRO_AI_KEY_ENCRYPTION_KEY"] = encryption_key
    password = make_url(database).password or ""
    secrets = [database, encryption_key, password, unquote(password), quote(password, safe="")]
    engine = create_engine(
        database, connect_args={"connect_timeout": 10}, pool_size=1, max_overflow=0
    )
    try:
        with engine.connect() as lock:
            if not lock.scalar(text("SELECT pg_try_advisory_lock(710219, 1)")):
                raise RuntimeError("Another migration execution holds the release lock")
            try:
                print(
                    "Private database connectivity verified; migration lock acquired.", flush=True
                )
                for args in (["alembic", "upgrade", "head"], ["scripts.provision_tenants"]):
                    result = subprocess.run(
                        [sys.executable, "-m", *args],
                        capture_output=True,
                        text=True,
                        timeout=720,
                        check=False,
                    )
                    print(redact(result.stdout + result.stderr, secrets), flush=True)
                    if result.returncode:
                        raise RuntimeError(f"Migration stage {args[0]} failed")
                print("Release migrations and tenant provisioning succeeded.", flush=True)
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(710219, 1)"))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not emit credential-bearing DSNs, request headers or DB tracebacks.
        print(
            f"Private migration failed ({type(exc).__name__}). Deployment must stop.",
            file=sys.stderr,
        )
        sys.exit(1)
