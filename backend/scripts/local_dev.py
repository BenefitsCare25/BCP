"""Run the local frontend and API against the one persistent development database."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DATABASE = BACKEND / "inspro.db"
LOGS = BACKEND / "var/local-dev"


def local_environment() -> dict[str, str]:
    configured = {k: v for k, v in dotenv_values(BACKEND / ".env").items() if v is not None}
    if any(values.get("INSPRO_ENV", "dev").lower() != "dev" for values in (os.environ, configured)):
        raise RuntimeError("The local launcher cannot run in a staging or production environment.")
    env = {**configured, **os.environ}
    env.update(
        {
            "INSPRO_ENV": "dev",
            "INSPRO_DATABASE_URL": f"sqlite:///{DATABASE.as_posix()}",
            "INSPRO_STORAGE_MODE": "local",
            "INSPRO_STORAGE_DIR": str(BACKEND / "var/uploads"),
            "INSPRO_DEV_API_TARGET": "http://127.0.0.1:8000",
            "INSPRO_FRONTEND_ORIGIN": "http://localhost:5173",
            "VITE_API_BASE_URL": "/api/v1",
        }
    )
    env.setdefault("INSPRO_AUTH_MODE", "mock")
    return env


def check_port(port: int) -> None:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"Port {port} is already in use. Stop the existing local server first."
            ) from exc


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def wait_ready(url: str, processes: list[subprocess.Popen[bytes]]) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise RuntimeError(f"A local server exited. See logs in {LOGS}.")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Server did not become ready at {url}. See logs in {LOGS}.")


def main() -> None:
    if not DATABASE.is_file():
        raise RuntimeError(
            f"The shared local database is missing: {DATABASE}. "
            "Refusing to create an empty replacement."
        )
    vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
    if not vite.is_file():
        raise RuntimeError(
            "Install frontend dependencies with pnpm.cmd install --frozen-lockfile first."
        )
    for port in (8000, 5173):
        check_port(port)
    env = local_environment()
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if migration.returncode:
        raise RuntimeError(f"Local migration failed:\n{migration.stderr}")
    LOGS.mkdir(parents=True, exist_ok=True)
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for name, command, cwd in [
            (
                "api",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                    "--reload",
                ],
                BACKEND,
            ),
            (
                "web",
                ["node", str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
                ROOT / "frontend",
            ),
        ]:
            with (LOGS / f"{name}.log").open("w", encoding="utf-8") as log:
                processes.append(
                    subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        start_new_session=os.name != "nt",
                    )
                )
        wait_ready("http://127.0.0.1:8000/readiness", processes)
        wait_ready("http://localhost:5173/", processes)
        print(f"Database: {DATABASE}", flush=True)
        print("Frontend: http://localhost:5173/", flush=True)
        print("Backend:  http://127.0.0.1:8000/docs", flush=True)
        print("Press Ctrl+C to stop both servers.", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
        raise RuntimeError(f"A local server exited. See logs in {LOGS}.")
    except KeyboardInterrupt:
        print("Stopping local servers.", flush=True)
    finally:
        for process in reversed(processes):
            stop(process)


if __name__ == "__main__":
    main()
