import random

SCENARIOS = [
    {
        "source": "kubernetes",
        "severity": "critical",
        "title": "Database pool exhaustion",
        "detail": "Postgres connection pool at 100% for 4 minutes on service `orders-api`.",
    },
    {
        "source": "istio",
        "severity": "high",
        "title": "Auth latency spike",
        "detail": "p99 latency for /auth/token climbed to 2400ms from a 220ms baseline.",
    },
    {
        "source": "cloudwatch",
        "severity": "medium",
        "title": "S3 403 burst",
        "detail": "Burst of 403 responses from `s3://prod-artifacts` indicates likely IAM drift.",
    },
    {
        "source": "prometheus",
        "severity": "high",
        "title": "Worker memory pressure",
        "detail": "Node `worker-7` memory is at 94% and OOMKilled events are increasing.",
    },
]


def scenario_names() -> list[str]:
    return [scenario["title"] for scenario in SCENARIOS]


def make_alert(scenario: str | None = None) -> dict[str, str]:
    if scenario:
        selected = next((item for item in SCENARIOS if item["title"] == scenario), None)
        if selected:
            return dict(selected)
    return dict(random.choice(SCENARIOS))
