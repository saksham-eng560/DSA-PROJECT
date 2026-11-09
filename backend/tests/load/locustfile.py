"""Load test: dashboard traffic from many concurrent sessions (plan target: 100 users, p95 < 500 ms).

    python scripts/seed_db.py                       # demo@example.com / demo-password-123
    cd backend && locust -f tests/load/locustfile.py --host http://localhost:8000 \
        --headless -u 100 -r 20 -t 2m --only-summary

Logs in once and shares the access token (login is rate-limited per IP), then every simulated user
browses the dashboard: overview, applications, jobs, analytics, e-mails, interviews, notifications.
Override the account with LOCUST_EMAIL / LOCUST_PASSWORD.
"""

from __future__ import annotations

import os
import random

import httpx
from locust import HttpUser, between, events, task

API = "/api/v1"
_auth: dict[str, str] = {}


@events.test_start.add_listener
def login_once(environment, **_kwargs) -> None:  # type: ignore[no-untyped-def]
    response = httpx.post(
        f"{environment.host}{API}/auth/login",
        json={"email": os.getenv("LOCUST_EMAIL", "demo@example.com"),
              "password": os.getenv("LOCUST_PASSWORD", "demo-password-123")},
        timeout=30,
    )
    response.raise_for_status()
    _auth["Authorization"] = f"Bearer {response.json()['access_token']}"


class DashboardUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.client.headers.update(_auth)
        self.app_ids: list[str] = [a["id"] for a in self.client.get(f"{API}/applications?page_size=50").json().get("items", [])]

    @task(5)
    def overview(self) -> None:
        self.client.get(f"{API}/agent/status")
        self.client.get(f"{API}/applications?status=pending_approval")
        self.client.get(f"{API}/notifications")

    @task(4)
    def applications(self) -> None:
        self.client.get(f"{API}/applications?page=1&page_size=25")
        if self.app_ids:
            self.client.get(f"{API}/applications/{random.choice(self.app_ids)}", name=f"{API}/applications/[id]")

    @task(3)
    def jobs(self) -> None:
        self.client.get(f"{API}/jobs?page=1&page_size=25")

    @task(2)
    def analytics(self) -> None:
        self.client.get(f"{API}/analytics/overview?days=30")

    @task(2)
    def inbox_and_interviews(self) -> None:
        self.client.get(f"{API}/communications")
        self.client.get(f"{API}/interviews")

    @task(1)
    def agent_logs(self) -> None:
        self.client.get(f"{API}/agent/runs")

    @task(1)
    def health(self) -> None:
        self.client.get("/health")
