"""Locust load profile for Inspro hot endpoints.

Run against localhost:

    uv run locust -f tests/load/locustfile.py --host http://127.0.0.1:8000 \
        --headless -u 100 -r 10 -t 60s

Acceptance: p95 < 500ms on GET endpoints at 100 concurrent users (per
Phase 10 verification in the build plan).
"""
from __future__ import annotations

from locust import HttpUser, between, task


class InsproUser(HttpUser):
    wait_time = between(0.5, 2.0)
    policy_year_id: str | None = None

    def on_start(self) -> None:
        res = self.client.get("/api/v1/policy-years")
        if res.status_code == 200 and res.json():
            self.policy_year_id = res.json()[0]["id"]

    @task(5)
    def list_policy_years(self) -> None:
        self.client.get("/api/v1/policy-years", name="GET /policy-years")

    @task(8)
    def list_categories(self) -> None:
        if not self.policy_year_id:
            return
        self.client.get(
            f"/api/v1/categories?policy_year_id={self.policy_year_id}",
            name="GET /categories",
        )

    @task(6)
    def list_employees(self) -> None:
        if not self.policy_year_id:
            return
        self.client.get(
            f"/api/v1/employees?policy_year_id={self.policy_year_id}&limit=50",
            name="GET /employees",
        )

    @task(4)
    def match_results(self) -> None:
        if not self.policy_year_id:
            return
        self.client.get(
            f"/api/v1/match-results?policy_year_id={self.policy_year_id}",
            name="GET /match-results",
        )

    @task(2)
    def ai_status(self) -> None:
        self.client.get("/api/v1/system/ai-status", name="GET /system/ai-status")

    @task(1)
    def audit_log(self) -> None:
        self.client.get("/api/v1/audit-log?limit=20", name="GET /audit-log")
