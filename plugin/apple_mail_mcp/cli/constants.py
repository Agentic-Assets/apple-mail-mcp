"""Shared module-level constants for the Apple Mail CLI perf and smoke surfaces."""

from __future__ import annotations

NO_HIT_SUBJECT = "NO_SUCH_SUBJECT_APPLE_MAIL_CLI_SMOKE_20991231"
INVALID_ACCOUNT = "__INVALID_APPLE_MAIL_CLI_ACCOUNT__"

PERF_THRESHOLDS_MS: dict[str, int] = {
    "metadata": 2000,
    "no_hit_search": 3000,
    "inbox": 5000,
    "dry_run": 5000,
    "overview": 10000,
    "bad_account": 2000,
    "dashboard": 5000,
    "needs_response": 8000,
    "awaiting_reply": 5000,
    "top_senders": 5000,
    "statistics_overview": 12000,
}

PERF_PROFILES: dict[str, dict[str, int]] = {
    "light": {"overview": 10000},
    "production": {"overview": 15000, "no_hit_search": 4500},
}
DEFAULT_PERF_PROFILE = "production"
