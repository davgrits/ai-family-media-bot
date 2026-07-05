# AI Family Media Bot — API & Job Contract

The **frozen contract**. Endpoints and the job shape stay stable so the thin
stub and the later full app both satisfy them. You build the infra once and
swap the brains in later without touching Terraform.

---

## Design constraints (read first)

- **Provider: AWS Bedrock** — a text model for the story and an image model
  for the illustration. Both authenticated by IAM role / workload identity —
  no API keys stored in secrets.
- **MVP output per request: a short bedtime story (text) + one illustration
  (image).** The story is the basis for your evening read-aloud; the image
  illustrates it. Audio and video are future `mode` values behind the same
  contract.

---

## Architecture (async)

Media generation is slow (seconds–minutes), so it **cannot** run inside the
webhook handler — Telegram would time out. Flow:

```
Telegram ──► POST /webhook ──► enqueue job ──► 200 OK (fast)
                                   │
                                   ▼
                            queue  (SQS in prod / in-memory in dev)
                                   │
                                   ▼
   worker: compose prompt ─► Bedrock (story) ─► Bedrock (illustration) ─► S3
                          ─► send story + image to Telegram
```

Two independently-scaling tiers:

- **web tier** — always warm: receives webhooks, enqueues, serves health/metrics
- **worker tier** — scales 0→N on queue depth: does the generation

---

## HTTP endpoints

| Method | Path        | Purpose |
|--------|-------------|---------|
| GET    | `/healthz`  | Liveness — process is alive. **MUST NOT** check Bedrock/SQS (avoids restart loops). |
| GET    | `/readyz`   | Readiness — checks downstream deps (SQS, S3, Bedrock) reachable. |
| GET    | `/metrics`  | Prometheus scrape. |
| POST   | `/webhook`  | Telegram update entry point. Validate → enqueue → return 200 fast. |

---

## Bot commands (MVP)

| Command          | Mode        | Meaning |
|------------------|-------------|---------|
| `/fairytale`     | `fairytale` | Guided scenario; assign family roles as text ("mom = king", "me = sleepy queen"). |
| `/custom <text>` | `custom`    | Free-text scene prompt. |
| `/surprise`      | `random`    | Random scenario — surprise me. |

Each maps internally to one generation job.

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

---

## Result

The worker generates the **story** first (text model), then derives a one-line
illustration prompt from it and generates **one image** (image model) — so the
picture matches the story. It writes the PNG to S3
(`s3://<bucket>/generated/<job_id>.png`) and sends the **story text + image**
back to `chat_id` via the Telegram Bot API (image with the story as caption, or
as a follow-up message if the story is long).

It emits **per-job cost** as a custom metric — now the sum of the text call and
the image call (Bedrock price × tokens + price × images) — the FinOps story at
request level.

---

## Swap points (interfaces, not hardcoded)

| Concern   | Interface       | Dev impl                          | Prod impl            |
|-----------|-----------------|-----------------------------------|----------------------|
| Queue     | `QueuePort`     | InMemoryQueue                     | SqsQueue             |
| Story     | `StoryProvider` | FakeStoryProvider (canned text)   | BedrockStoryProvider |
| Image     | `ImageProvider` | FakeImageProvider (placeholder)   | BedrockImageProvider |
| Storage   | `StoragePort`   | LocalDir                          | S3                   |

Keeping these behind interfaces is what lets the stub run on a laptop with **no
AWS**, and lets the infra slot in later without touching app logic.
