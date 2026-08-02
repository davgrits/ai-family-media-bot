"""Prometheus metrics. Includes the contract's per-job cost metric (sum of the
text call and the image call) — the FinOps story at request level."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

WEBHOOK_UPDATES = Counter(
    "fmb_webhook_updates_total",
    "Telegram updates received, by handling result.",
    ["result"],  # accepted | ignored | invalid
)

JOBS_ENQUEUED = Counter(
    "fmb_jobs_enqueued_total",
    "Generation jobs enqueued from the webhook.",
    ["mode"],
)

JOBS_PROCESSED = Counter(
    "fmb_jobs_processed_total",
    "Generation jobs processed by the worker.",
    ["mode", "status"],  # success | error
)

JOB_DURATION = Histogram(
    "fmb_job_processing_seconds",
    "Pipeline processing time in the worker, excluding time waiting in the queue.",
    ["mode"],
)

QUEUE_WAIT_DURATION = Histogram(
    "fmb_queue_wait_seconds",
    "Time from job creation until delivery to a worker.",
    ["mode"],
)

# Per-job cost — observed once per job (sum of text + image calls).
JOB_COST = Histogram(
    "fmb_job_cost_usd",
    "Per-job Bedrock cost in USD (text call + image call).",
    ["mode"],
    buckets=(0, 0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0),
)

# Distinct base name from the histogram above to avoid a timeseries collision
# (prometheus_client appends _total → fmb_cost_usd_total).
JOB_COST_TOTAL = Counter(
    "fmb_cost_usd",
    "Cumulative Bedrock cost in USD.",
    ["mode"],
)

QUEUE_DEPTH = Gauge(
    "fmb_queue_depth",
    "Approximate number of jobs waiting in the queue.",
)
