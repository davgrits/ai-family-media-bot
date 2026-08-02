# AI Family Media Bot — API & Job Contract

## What is frozen and what is not

This distinction matters, because the document is edited while claiming to be
frozen:

- **Frozen:** the HTTP endpoints, the job shape, and the port interfaces. Code on
  both sides of the queue depends on these, and a message published by an older
  web tier must still parse in a newer worker. Changes here are additive, with a
  default, and called out in the commit.
- **Not frozen:** which provider implements each port. That is a deployment
  choice recorded in the [swap points](#swap-points) table, and it changes freely.

The contract is deliberately provider-neutral. Naming a vendor in the contract
itself would defeat the point of having the interfaces at all.

---

## Design constraints (read first)

- **Two model calls per request** — a text model for the story, an image model for
  the illustration. Both authenticated by **workload identity**: no API key, no
  service-account key file, no static credential in a secret.
- **One request produces a short bedtime story (text) plus one illustration
  (image).** The story is the basis for the evening read-aloud; the image
  illustrates it. Audio and video are future `mode` values behind the same
  contract.
- **Characters are text descriptions only.** Never a photograph of a real child.
- **An adapter never substitutes a fabricated value for a provider failure.** A
  placeholder image, a truncated story, or a `$0` cost is a failure disguised as
  a success: the job acks, `job completed` is logged, and nobody notices. Adapters
  raise; the queue retries.

---

## Architecture (async)

Media generation is slow (seconds–minutes), so it **cannot** run inside the
intake path — Telegram would time out. There are two entry points, and both only
enqueue:

```
Telegram ──► POST /webhook ──────┐
         └─► getUpdates polling ─┴─► enqueue job ──► 200 OK / ack (fast)
                                          │
                                          ▼
                              managed queue (in-memory in dev)
                                          │
                                          ▼
   worker: story model ─► image prompt ─► image model ─► object storage
                       ─► send story + image to Telegram
```

Two tiers, separated because their latency and failure profiles differ:

- **web tier** — answers Telegram in milliseconds: receives updates, enqueues,
  serves health and metrics.
- **worker tier** — does the generation, ~14 s per job.

Both run warm. The worker's replica count is fixed rather than scaled from queue
depth: activation from zero measured 4m43s, because the queue-backlog metric
samples on a ~60s interval and can gap for minutes. See the
[runbook](runbook.md).

---

## HTTP endpoints

| Method | Path        | Purpose |
|--------|-------------|---------|
| GET    | `/healthz`  | Liveness — process is alive. **MUST NOT** call any cloud dependency; a provider outage must not cause a restart loop. |
| GET    | `/readyz`   | Readiness — checks the configured dependencies are reachable. Must not bill a model call. |
| GET    | `/metrics`  | Prometheus scrape. |
| POST   | `/webhook`  | Telegram update entry point. Validate → enqueue → return 200 fast. |

---

## Bot commands

| Command          | Mode        | Meaning |
|------------------|-------------|---------|
| `/fairytale`     | `fairytale` | A tale starring the family's registered characters. |
| `/custom <text>` | `custom`    | Free-text scene prompt. |
| `/surprise`      | `random`    | Random scenario — surprise me. |
| `/family`        | —           | Lists the registered characters. A query, not a generation mode: it enqueues nothing. |

Each generation command maps to exactly one job.

---

## Job shape (frozen)

```json
{
  "job_id": "uuid",
  "chat_id": 123456,
  "mode": "fairytale | custom | random",
  "prompt": "composed scene description (text)",
  "created_at": "iso8601"
}
```

`prompt` carries the family's request, framed by the bedtime guardrails. It does
**not** carry character descriptions: those are deployment configuration resolved
in the worker, so a queued job cannot be generated against a stale cast.

---

## Result

The worker generates the **story** first (text model), then builds the
illustration prompt and generates **one image** (image model), so the picture
matches the words. It writes the story text and the PNG to object storage under
the key layout `generated/<job_id>.txt|.png` — the URI scheme is the storage
adapter's business, not the contract's — and sends the **story text plus image**
back to `chat_id` through the Telegram Bot API (image with the story as caption,
or as a preceding message when the story exceeds the caption limit).

A story is delivered only if the model stopped cleanly. A response truncated at
the token ceiling, or stopped for safety or recitation, fails the job so the queue
retries: a fragment reaching a child mid-sentence is worse than a delayed story.

It emits **per-job cost** as a custom metric — the sum of the text call and the
image call. Reasoning tokens are included: they are billed at the output rate but
excluded from the visible output count, so omitting them understates every job.

On failure the chat is told, but only once the retries are exhausted. Apologising
on every delivery attempt would send the same message once per attempt.

---

## Swap points

Interfaces, not hardcoded choices. The `Prod` column is a deployment fact and is
free to change; the interfaces are not.

| Concern   | Interface       | Dev impl                        | Prod impl (GCP)      |
|-----------|-----------------|---------------------------------|----------------------|
| Queue     | `QueuePort`     | InMemoryQueue                   | PubSubQueue          |
| Story     | `StoryProvider` | FakeStoryProvider (canned text) | VertexStoryProvider  |
| Image     | `ImageProvider` | FakeImageProvider (placeholder) | VertexImageProvider  |
| Storage   | `StoragePort`   | LocalDirStorage                 | GcsStorage           |

Keeping these behind interfaces is what lets the app run on a laptop with **no
cloud account**, and lets the infrastructure change without touching the story
pipeline. Selection is by environment variable and is validated at startup — an
unrecognised value fails fast rather than being silently ignored.

`QueuePort` additionally exposes `start(concurrency)` and `close()` so an adapter
can own a long-lived consumer without leaking provider details into the worker,
and carries a delivery attempt counter so the pipeline can tell "will be retried"
from "this was the last attempt".
