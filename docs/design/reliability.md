# Reliability review — warm worker, Pub/Sub streaming pull, single replica

**Stream:** reliability-engineer · **Owns:** RR-1 … RR-7
**Baseline:** `/Users/davgrits/repos/ai-family-media-bot` @ `a7c2f29`, clean tree. Read-only review; no repo file was modified.
**Inputs:** `PRODUCT-DIRECTION.md` §3.2/§4, `SPEC.md` §6.
**Library versions verified against the venv:** `google-cloud-pubsub 2.39.0`, runtime image `python:3.11-slim` (`app/Dockerfile:2`).

---

## 0. Verdict in one paragraph

The warm-worker decision (D2) is **correct and delivers RR-1**: enqueue→reply drops from ~342 s to ~14 s. But it is **not reliable as currently coded**, for one reason that has nothing to do with latency: the Pub/Sub streaming subscriber can terminate permanently, and when it does, the pod stays `Running`, `Ready`, and `Live` forever while consuming nothing. Under KEDA that fault self-healed by accident — the cooldown scaled the deployment to zero and the next backlog spike created a *fresh* pod. Fixing `replicas: 1` removes that accidental watchdog and converts a self-healing transient into an indefinite silent outage. Nothing in the cluster or in Terraform would detect it, because nothing scrapes `/metrics` and there is no Pub/Sub backlog alert. **Keep D2. Add a health signal and one alert.** Details in F-1 and F-2.

Severity scale: **S1** = silent total outage or data/UX loss with no detection · **S2** = user-visible failure or deploy hazard · **S3** = degraded correctness/observability · **S4** = hygiene.

---

## 1. Findings, ranked

| # | Sev | Finding | Req |
|---|---|---|---|
| F-1 | S1 | Dead streaming subscriber → permanent silent outage; both probes stay green; `replicas: 1` removes the accidental KEDA self-heal | RR-1, RR-5 |
| F-2 | S1 | No metrics collection path at all (no Service, no `PodMonitoring`, GKE `WORKLOADS` metrics off) and no Pub/Sub backlog/DLQ alert — every signal below is currently unobservable | obs |
| F-3 | S2 | ConfigMap edits never reach running pods (no `checksum/config`); `helm --atomic --wait` reports success anyway — breaks the IR-4 acceptance criterion | RR-6 |
| F-4 | S2 | Pipeline has no upper time bound (no Vertex timeout, no job timeout). Hung job blocks the *only* worker, holds the lease up to `max_lease_duration` = 1 h, and prevents clean shutdown → SIGKILL at 120 s | RR-3, RR-4 |
| F-5 | S2 | `Worker.stop()` cancels in-flight jobs with **zero** drain window; `send_story_and_image` has a cancellation window that delivers a partial reply and then nacks → duplicate/partial story on every rolling update | RR-3, RR-7 |
| F-6 | S3 | No `retry_policy` on the subscription + apology sent on every attempt → 5 apology messages in seconds, then a DLQ entry nobody reads | RR-4 |
| F-7 | S3 | Image failure falls back to a placeholder PNG and **acks as success** — contradicts the FR-5 "a truncated story is worse than no story" argument, and is invisible | RR-2 |
| F-8 | S3 | Worker readiness makes two live cloud calls every 15 s, gates nothing (no Service), and can roll back a good release; `story`/`image` checks only resolve ADC, so `/readyz: true` is a false assurance | RR-5, RR-6 |
| F-9 | S3 | Worker deployment has no `strategy`; default `maxSurge: 1` needs headroom on the collapsed single node pool (IR-3) or `--atomic` rolls back a healthy release | RR-6 |
| F-10 | S4 | `depth()` hardcodes `0`; `fmb_queue_depth` is a constant-0 gauge — worse than absent | obs |
| F-11 | S4 | Web `Recreate` + non-durable poller offset → duplicate jobs on every web restart | RR-7 |

---

## 2. RR-1 — does the warm worker actually deliver seconds, not minutes?

### 2.1 Yes, and the arithmetic is clean

| Path | Enqueue → reply |
|---|---|
| KEDA 0→N (measured) | 282.7 s activation + 46 s node + 13.7 s pipeline ≈ **342 s** |
| Warm `replicas: 1` | ~0.1–1 s streaming-pull delivery + 13.7 s pipeline ≈ **14 s** |

The 282.7 s is structural, not tunable: it is Cloud Monitoring's ~60 s sampling of `subscription/num_undelivered_messages` plus visibility delay plus KEDA's `pollingInterval: 15` (`deploy/charts/family-media-bot/values.yaml:43`). Nothing on the KEDA side removes it.

The warm path has no metric in the loop at all. The subscriber is already open (`queue_pubsub.py:76-81`) before the first message exists, so the server *pushes* on the established stream. **RR-1 is satisfied by D2, and it is satisfied for the right reason.**

Worth noting for the interview: `fmb_queue_wait_seconds` (`metrics.py:32`, observed at `queue_pubsub.py:155` from `Job.created_at`) is *already* the exact SLI for RR-1 — it would have shown p50 ≈ 283–342 s from day one. It was emitted and never collected. See F-2.

### 2.2 New failure modes that 0→N did not have

**(a) F-1 — the accidental watchdog is gone. This is the real hole.**

`google-cloud-pubsub` classifies stream errors into recoverable and terminating (`streaming_pull_manager.py:81-97`). `_should_terminate` returns `True` for `Cancelled`, `InvalidArgument`, `NotFound`, `PermissionDenied`, `Unauthenticated`, `Unauthorized` — **and for any non-`GoogleAPICallError` exception at all** (`streaming_pull_manager.py:1380-1396`). A Workload Identity token-refresh failure, a GKE metadata-server hiccup surfacing as a plain exception, an IAM propagation blip, or a transient `Unauthenticated` during credential rotation all land in that bucket. The stream shuts down and never reopens.

What the application does about it:

```python
# queue_pubsub.py:246-258
def _on_stream_done(self, future: StreamingPullFuture) -> None:
    if self._closing or future.cancelled():
        return
    ...
    if exception is not None:
        logger.error("Pub/Sub streaming subscriber stopped unexpectedly", ...)
```

It logs one ERROR line. `_streaming_future` is left dangling, `start()` is never re-invoked, and there is no supervisor. After that:

- `/healthz` returns `{"status": "ok"}` unconditionally (`app.py:89-92`) — correct per contract #3, but it means liveness will never restart the pod.
- `/readyz` calls `queue.check_ready()`, which is `get_subscription` (`queue_pubsub.py:265-275`) — a **control-plane admin RPC that succeeds whether or not the data-plane stream is alive**. Readiness stays green.
- `Worker._run` keeps calling `dequeue(timeout=1.0)`, which keeps returning `None` forever (`worker.py:35-36`). No log, no metric, no restart.

Net: the pod is `1/1 Running`, `READY 1/1`, zero restarts, and the bot is dead. Under KEDA this healed in ≤ 300 s + activation because the cooldown deleted the pod. Under `replicas: 1`, nothing ever replaces it.

**Mitigation (keeps D2 fully intact) — three parts, all small:**

1. **Make the adapter own its liveness.** Add `PubSubQueue.is_streaming() -> bool` returning `self._streaming_future is not None and not self._streaming_future.done()`, and set `self._streaming_future = None` inside `_on_stream_done` so the state is honest.
2. **Self-heal first.** In `_on_stream_done`, schedule a bounded restart on the loop (`loop.call_soon_threadsafe`) that clears `_streaming_future` and re-runs the `subscribe()` call with exponential backoff (1 s → 60 s, jittered). Most terminating errors are transient credential/IAM blips and a reconnect fixes them without a pod restart. Guard with `self._closing`.
3. **Then fail loudly.** Add a *worker-only* liveness endpoint `/livez` that returns 503 when the stream has been down for > N consecutive seconds (e.g. 120 s), and point the worker's `livenessProbe` at it. This stays inside contract #3 — no cloud call is made; it reads a local boolean the adapter already maintains. `/healthz` keeps its current semantics for the web tier and for `helm test`. Kubernetes then restarts the pod, which is exactly the recovery KEDA used to provide for free.

Also add the cheap external detector — see F-2/§8: `oldest_unacked_message_age > 120 s` catches this even if all three code changes are skipped.

**(b) Throughput ceiling / head-of-line blocking.**

`WORKER_CONCURRENCY: 1` (`values.yaml:16`) → `Worker(concurrency=1)` → `FlowControl(max_messages=1)` and `asyncio.Queue(maxsize=1)` (`queue_pubsub.py:72-75`). The system is strictly serial: **~4.4 jobs/minute**, and job N+1 waits the full 13.7 s behind job N. Two children pressing `/fairytale` at the same time means the second waits ~28 s. Under KEDA `maxReplicaCount: 10` these fanned out (after 4m43s, so it was never actually better in practice).

This is an acceptable trade for a family bot, but it should be a stated number, not an accident. Two knobs, in order of preference:

- Raise `WORKER_CONCURRENCY` to **3**. The pipeline is almost entirely I/O-wait on Vertex, running in `asyncio.to_thread` (`story_vertex.py:47`, `image_vertex.py:78`), so three concurrent jobs cost ~3 extra threads and ~3× peak memory on the PNG buffers, not 3× CPU. `FlowControl(max_messages=3)` and the local queue follow automatically from `Worker.start()` → `queue.start(concurrency)` (`worker.py:27`). Bump the memory limit from `1Gi` if you do this.
- Leave replicas at 1. See below.

**(c) Single point of failure.** OOMKill, node preemption, node auto-upgrade (release channel `REGULAR`, `gke.tf:28-30`), or a CrashLoop now takes 100 % of processing capacity with it. There is **no PodDisruptionBudget anywhere** in `deploy/` (verified) — a node drain evicts the only worker immediately.

### 2.3 Is `replicas: 1` the right number?

**Yes — keep it, and say why out loud.** The honest reasoning:

- `replicas: 2` would remove the SPOF and double throughput for ~$0 if both fit on the one collapsed pool. It is genuinely tempting.
- But it does **not** fix F-1. Two independently-dying streams still both die; you just take longer to notice, and you notice *less* because the queue keeps draining at half rate. The health signal is the required fix either way.
- `replicas: 2` also makes a `PodDisruptionBudget{minAvailable: 1}` meaningful, which `replicas: 1` cannot support (see §6.3 — a single-replica PDB that guarantees availability makes the node **undrainable**).
- The worker does **not** poll Telegram (`RUN_MODE: worker`, `TELEGRAM_POLLING` absent from the ConfigMap → `False` per `config.py:43`), so unlike the web tier there is no 409 constraint blocking a second replica.

Recommendation: **`replicas: 1` with `WORKER_CONCURRENCY: 3`, plus F-1's liveness fix.** Note in the runbook that `replicas: 2` + `minAvailable: 1` is the single change that makes this HA, and that it was deliberately not taken because a portfolio bot with one family does not need it. That is a stronger answer than either extreme.

---

## 3. RR-2 / RR-7 — delivery semantics audit

I traced every ack/nack path in `adapters/queue_pubsub.py` and `worker.py` against the library source in the venv. **At-least-once holds.** The adapter is more careful than it first looks; several things I expected to be broken are correct, and I want to record why, because the non-obvious ones are the ones that regress later.

### 3.1 What is correct (verified, not assumed)

**Callback → asyncio handoff is race-free.** The window I went looking for — a message accepted onto the loop *after* shutdown started — is closed by a real lock, not by luck:

```python
# queue_pubsub.py:121-131
with self._callback_lock:
    loop = self._loop
    if self._closing or loop is None:
        message.nack(); return
    try:
        loop.call_soon_threadsafe(self._accept_message, message, job, queue_wait)
    except RuntimeError:
        message.nack()
```

`close()` takes the same `threading.Lock` to set `_closing` (`queue_pubsub.py:197-198`), so once it releases, no new `call_soon_threadsafe` can be scheduled. Handoffs already sitting in the loop's ready queue are flushed by the `await asyncio.sleep(0)` at `queue_pubsub.py:201`, and `_accept_message` re-checks `_closing` and nacks (`queue_pubsub.py:134-136`). Between the `sleep(0)` and the drain at line 205 there is no `await`, so it is atomic with respect to the loop. This is correct.

**Nacks issued during shutdown are actually transmitted.** This was my main suspicion — `close()` nacks everything at `queue_pubsub.py:208-209` and then immediately calls `future.cancel()` at line 214, which looks like it should drop the batched modacks. It does not. `StreamingPullFuture.cancel()` → `manager.close()` → `_shutdown()` → `self._dispatcher.stop()` (`streaming_pull_manager.py:1057`), and `Dispatcher.stop()` posts a poison pill and **joins** the worker thread (`dispatcher.py:109-115`); `QueueCallbackWorker.__call__` processes every item queued ahead of `STOP` before exiting (`helper_threads.py:100-118`). The nacks go out. Good — and this is worth a code comment, because it is entirely non-obvious and a future refactor that reorders `cancel()` before the nacks would silently break it.

**The library also nacks what the app never saw.** `_shutdown()` nacks `dropped_messages` plus `_messages_on_hold` (`streaming_pull_manager.py:1046-1053`), so messages leased by the client but never dispatched to `_on_message` are returned too.

**"Buffered but never dequeued" is handled.** `close()` clears `_buffered` and nacks its contents (`queue_pubsub.py:205-209`), leaving stale `QueueDelivery` objects in the `asyncio.Queue`. `dequeue()` looks the receipt up and returns `None` when it is gone (`queue_pubsub.py:176-179`), which the worker treats as a timeout. No double-nack, no phantom delivery.

**No double-ack.** `ack()` and `nack()` both `pop()` from `_in_flight` (`queue_pubsub.py:184`, `189`) and no-op when absent. Both run on the loop thread, as does `close()`, so the dict is not concurrently mutated.

**Ack cannot be interrupted mid-flight.** `ack()`/`nack()` are `async def` with no internal `await`, so `await self._queue.ack(delivery)` (`worker.py:43`) is not a cancellation point. A cancel landing "between process and ack" is not reachable.

**`close()` is idempotent**, which matters because `app.py` calls it twice — once via `worker.stop()` (`worker.py:74`) and again at `app.py:84`. The `_closed` guard (`queue_pubsub.py:195-196`) makes the second a no-op.

**Malformed messages are handled without leaking PII.** `_on_message` nacks and logs only `message_id`, deliberately excluding exception text because pydantic embeds the rejected input (`queue_pubsub.py:109-117`). That upholds contract #5.

### 3.2 F-5 (S2) — the real delivery defect: no drain, and a partial-reply window

`Worker.stop()` gives in-flight work **zero** time to finish:

```python
# worker.py:65-74
async def stop(self) -> None:
    self._stop.set()
    for task in self._tasks:
        task.cancel()          # immediate, no grace
    ...
    await self._queue.close()
```

`_stop.set()` is pointless here — the loop's `while not self._stop.is_set()` check at `worker.py:32` never gets a chance to run, because `cancel()` fires on the same tick. Every rolling update, every node drain, every config change therefore throws away whatever job is in flight and re-runs it from scratch. For a 13.7 s pipeline this is avoidable waste.

Worse, it opens a **partial-delivery window**. `send_story_and_image` has two sequential awaits on the long-story branch:

```python
# telegram.py:79-83
if len(story_text) <= _CAPTION_LIMIT:
    await self._send_photo(chat_id, png_bytes, filename, caption=story_text)
else:
    await self._send_message(chat_id, story_text)   # <-- cancel here
    await self._send_photo(chat_id, png_bytes, filename)
```

A cancel between those two lines, or inside `_send_photo` after the HTTP request has reached Telegram but before the response is read, means: **the child has the message, the job is nacked, and the retry sends a second, different story.** `asyncio.CancelledError` derives from `BaseException`, so `pipeline.process`'s `except Exception` (`pipeline.py:100`) correctly does *not* swallow it — the cancel propagates to `worker.py:47-50`, which nacks. Correct for at-least-once, wrong for the user.

RR-7 says "must not drop a job." It does not drop one. It can **duplicate** one, and it can deliver half a reply.

**Fix — drain before cancel (~8 lines):**

```python
async def stop(self, drain_timeout: float = 30.0) -> None:
    self._stop.set()
    done, pending = await asyncio.wait(self._tasks, timeout=drain_timeout)
    for task in pending:
        task.cancel()
    for task in pending:
        try: await task
        except asyncio.CancelledError: pass
    await self._queue.close()
```

`_run` already exits its loop cleanly once `_stop` is set (`worker.py:32`) — it finishes the current job, acks it, then falls out on the next iteration within ≤ 1 s of `dequeue` timeout. A 30 s drain covers a 13.7 s job with 2× headroom and still leaves 90 s of the 120 s grace period. Pair with F-4's job timeout so `drain_timeout` has a principled value rather than a guessed one.

Note the existing test `test_worker_starts_queue_once_with_configured_concurrency_and_closes` (`tests/test_worker_delivery.py:56-73`) asserts the *current* cancel-immediately behaviour indirectly; it will need updating. There is currently **no test covering shutdown with a job in flight** — that is the QA-stream gap this finding implies.

### 3.3 Duplicate delivery has no idempotency guard

At-least-once means duplicates are expected, and there is no defence anywhere:

- `storage.save` keys on `generated/{job_id}.{ext}` (`storage_gcs.py:26`) — idempotent overwrite. Fine.
- `telegram.send_story_and_image` is **not** idempotent — every redelivery is another photo in the chat.
- `message.ack()` is fire-and-forget in `google-cloud-pubsub` 2.x (no future, no confirmation, since exactly-once is not enabled on the subscription). If the ack RPC fails, or the pod dies in the ~10 ms window between `ack()` and the dispatcher flushing it, the message redelivers and the child gets a second story.

With `max_delivery_attempts = 5` and **no `retry_policy`** (F-6), the theoretical worst case is 5 stories in a few seconds. Given the traffic profile (one family), I do not recommend building a dedup store — that reintroduces state, which §2 rules out. I recommend instead: (a) fix F-6 so retries are spaced, (b) fix F-5 so the common cause disappears, and (c) note the residual honestly. If dedup ever becomes necessary, the natural place is a GCS conditional-create on `generated/{job_id}.sent` with `if_generation_match=0` — no new service, uses the bucket that already exists.

### 3.4 Flow control vs the Pub/Sub lease

`FlowControl(max_messages=max_messages)` (`queue_pubsub.py:75`) sets only that one field. Everything else takes library defaults (`pubsub_v1/types.py:247-272`), and one of them matters:

- `max_lease_duration = 1 * 60 * 60` — **1 hour**.

Under streaming pull the leaser automatically modacks every outstanding message on a timer, so **`ack_deadline_seconds = 300` on the subscription is largely not the operative limit** — see §5. The operative limit is `max_lease_duration`. Combined with F-4 (no timeout on the model calls), a hung Vertex request holds its lease for a full hour, then the leaser stops extending, the message redelivers **while the original attempt is still running in an orphaned thread**, and you get duplicate work.

Local flow control and the lease are otherwise consistent: `_buffered` + `_in_flight` can never exceed `max_messages`, because a message stays "outstanding" from the client's perspective until acked, whether it sits in `_buffered` or `_in_flight`. The `deliveries.full()` guard at `queue_pubsub.py:143` is therefore defensive-only and should never fire; if it ever logs `"releasing Pub/Sub message outside local flow-control capacity"` (`queue_pubsub.py:145-148`), that is a genuine bug signal and deserves an alert (§7).

Recommended once F-4 lands: `FlowControl(max_messages=n, max_lease_duration=JOB_TIMEOUT + 30)` so the lease and the application's own timeout agree instead of differing by 40×.

---

## 4. RR-3 — graceful shutdown

### 4.1 The full path, with the time budget

SIGTERM to the worker pod:

| Step | Where | Bounded? |
|---|---|---|
| 1. kubelet SIGTERM. No `preStop` hook; no Service, so no endpoint-removal wait | `deployment-worker.yaml` (verified absent) | 0 s |
| 2. uvicorn `should_exit`, finishes in-flight HTTP (probe requests only) | `__main__.py:17-22` | ms |
| 3. lifespan `finally` → `poller.stop()` (None on worker) → `worker.stop()` | `app.py:79-85` | — |
| 4. `_stop.set()`, `task.cancel()`, `await task` — cancel is immediate | `worker.py:66-73` | ms (**F-5**) |
| 5. `_release` → `nack` → queued to dispatcher | `worker.py:56-63` | ms |
| 6. `queue.close()`: sleep(0) → nack buffered+in-flight → `future.cancel()` → `to_thread(future.result, timeout=10)` | `queue_pubsub.py:193-228` | ≤ 11 s |
| 7. `subscriber.close()` | `queue_pubsub.py:229-234` | ≤ 2 s |
| 8. `publisher.stop()`, second `queue.close()` (no-op), `telegram.aclose()` | `queue_pubsub.py:236-239`, `app.py:84-85` | ~0 |
| 9. `asyncio.run` teardown → `loop.shutdown_default_executor()` → `executor.shutdown(wait=True)` | CPython 3.11 | **UNBOUNDED** |

Steps 1–8 are bounded at roughly **13–15 s**, comfortably inside `terminationGracePeriodSeconds: 120` (`deployment-worker.yaml:30`).

### 4.2 F-4 (S2) — step 9 is the problem

`story_vertex.py:47` and `image_vertex.py:78` both run the blocking SDK call through `asyncio.to_thread`. Cancelling the awaiting coroutine **cannot cancel a thread that has already started** — the `concurrent.futures.Future` is un-cancellable once running. So after step 4 the Vertex call is still executing in an orphaned default-executor thread.

On **Python 3.11** (`app/Dockerfile:2`), `loop.shutdown_default_executor()` takes no timeout parameter — it waits forever. (3.12 added one; `asyncio.Runner` defaults it to 300 s there, which would still exceed the 120 s grace period.) And there is **no client-side timeout on any Vertex call**: `story_vertex.py:36-44` and `image_vertex.py:42-69` pass no `http_options`, and I confirmed by grep that neither file mentions `timeout` at all. `storage_gcs.py` likewise.

So: a healthy job (≤ ~12 s of model time remaining) exits well inside the grace period. **A hung model call means the pod never exits and is SIGKILLed at 120 s.**

The saving grace is that the nack was already flushed at step 6, so **the message is safely returned before the hang** — RR-3's *correctness* property holds even in the SIGKILL case. It is the *timeliness* property that fails, and the operator sees an ungraceful pod termination with no explanation.

**Fix (three coordinated pieces — all needed, they don't substitute for each other):**

1. **Bound the pipeline.** In `worker.py`, `await asyncio.wait_for(self._pipeline.process(delivery.job), timeout=JOB_TIMEOUT)` with `JOB_TIMEOUT = 90` (6.5× the measured 13.7 s), treating `TimeoutError` as a failure → nack. This unblocks the worker slot.
2. **Bound the HTTP call**, so the thread actually unwinds. `google-genai` accepts `HttpOptions(timeout=...)` (milliseconds) on the client or per-request; set it to `JOB_TIMEOUT` for the story call and something similar for the image call. Without this, (1) alone leaves the orphan thread and step 9 still hangs.
3. **Bound the executor teardown.** Either pin the image to `python:3.12-slim` (which gives `shutdown_default_executor` a timeout) or, more directly, give the worker its own `ThreadPoolExecutor` and call `executor.shutdown(wait=False, cancel_futures=True)` in `Worker.stop()` instead of relying on the default executor's atexit semantics.

With (1)–(3), `terminationGracePeriodSeconds: 120` stops being an arbitrary number and becomes `JOB_TIMEOUT (90) + drain slack (30)` — a value you can defend.

### 4.3 What happens to a job mid-generation during a rolling update

Today: cancelled instantly, nacked, redelivered immediately (no backoff — F-6), regenerated from scratch by whichever pod picks it up, with the F-5 partial-reply risk. With the drain fix, the common case becomes: old pod stops dequeuing, finishes and acks its current job in ≤ 13.7 s, exits cleanly; the new pod (already up, see §6.2) picks up everything after. **No visible effect on the child.** That is the outcome RR-3 and RR-7 are asking for.

---

## 5. RR-4 — ack deadline vs processing time

### 5.1 Headroom is fine, but the framing in the requirement is misleading

`ack_deadline_seconds = 300` (`infra/gcp/pubsub.tf:23`) against a measured 13.7 s is 21× headroom. But under **streaming pull** that number is mostly not what governs redelivery:

- The subscription's `ack_deadline_seconds` is the default the server applies; the streaming client sends its own `stream_ack_deadline_seconds` on the initial request and then the **leaser continuously modacks** every outstanding message, extending the deadline on a timer derived from observed ack latency, bounded by `min_duration_per_lease_extension` / `max_duration_per_lease_extension` (both `0` = library-chosen) and hard-capped by `max_lease_duration = 1 hour`.
- Because `FlowControl` is constructed with only `max_messages` (`queue_pubsub.py:75`), all of those take defaults. **The real worst case is 1 hour, not 300 s.**

So the answer to "can a slow model call cause redelivery and duplicate work?" is: **yes, but only past one hour** — and because there is no client-side timeout (F-4), one hour is reachable. Duplicate work in that scenario is genuinely bad: two threads generating, two Telegram sends.

Where `ack_deadline_seconds` *does* matter is **pod death**. When the pod is SIGKILLed (F-4) or OOMKilled, modacks stop. Redelivery then waits out the remaining deadline — up to 300 s with the current setting. With `replicas: 1` that is 5 minutes of a child waiting with nothing happening.

**Recommendation:** lower `ack_deadline_seconds` to **60 s**. The leaser will extend it automatically for jobs that need longer, so a 90 s job is unaffected; but a crashed pod's messages come back in ≤ 60 s instead of ≤ 300 s. Set `max_lease_duration = JOB_TIMEOUT + 30` explicitly so the client can never hold a lease for an hour. This is a pure Terraform + one-line adapter change and does not touch D2.

### 5.2 F-6 (S3) — DLQ policy and the 5-apology problem

`google_pubsub_subscription.jobs` has `max_delivery_attempts = 5` and **no `retry_policy` block** (`pubsub.tf:20-30`). Without one, Pub/Sub redelivers as fast as it can. Combined with:

```python
# pipeline.py:100-115
except Exception as exc:
    ...
    await self._telegram.send_text(job.chat_id, "Простите, сказка сейчас не получилась 😔 ...")
    return False
```

…a deterministically failing job (bad model ID, revoked `aiplatform.user`, a prompt that trips a safety filter) sends **five apology messages within seconds** and then lands in the DLQ. That is the worst possible UX for the failure case, and it is the failure case a live demo is most likely to hit.

Two fixes, both small:

1. Add to `pubsub.tf`:
   ```hcl
   retry_policy {
     minimum_backoff = "10s"
     maximum_backoff = "600s"
   }
   ```
   Exponential backoff over 5 attempts spreads them across ~20 minutes.
2. Send the apology **only on the final attempt.** Pub/Sub populates `message.delivery_attempt` whenever a dead-letter policy exists; the adapter currently discards it. Add `attempt: int = 1` to `QueueDelivery` (`ports/queue.py:15-20`) — contract #1 freezes `Job`, not `QueueDelivery`, so this is in bounds — populate it from `message.delivery_attempt` in `_accept_message`, and gate the apology on `attempt >= 5`. The child then sees exactly one "sorry" and only when the system has genuinely given up.

### 5.3 Nothing consumes the DLQ

`google_pubsub_subscription.jobs_dlq` (`pubsub.tf:14-18`) has 14-day retention and **no subscriber and no alert**. Poisoned jobs vanish silently. One Cloud Monitoring alerting policy on `subscription/num_undelivered_messages{subscription_id="ai-family-media-bot-jobs-dlq"} > 0` fixes this with zero code. See §7.

Also note `message_retention_duration = "86400s"` on the main subscription (`pubsub.tf:24`): a worker down for more than 24 h loses jobs outright. That is a deliberate and defensible choice for bedtime stories (a 30-hour-late fairytale is worse than none) — but it should be stated as a decision in the runbook, not left as a config value.

### 5.4 F-7 (S3) — the placeholder-image fallback is wrong

```python
# image_vertex.py:85-89
except Exception:
    logger.exception("Vertex image generation failed — using placeholder image")
    return ImageResult(png_bytes=_placeholder_png(), model_id="placeholder-fallback", cost_usd=0.0)
```

This swallows **every** image failure — quota exhaustion, safety block, model not enabled in Model Garden, IAM revocation — returns a grey placeholder, and the pipeline **acks the job as a success** (`pipeline.py:99`) and increments `JOBS_PROCESSED{status="success"}`.

This is the same defect as the truncated story, and PRODUCT-DIRECTION §4 already argues the case against it: *"A truncated story is worse than no story."* A grey rectangle is the visual equivalent of half a sentence. The infrastructure to do the right thing already exists — ack-only-on-success, 5 retries, a DLQ.

**Recommendation: make it raise**, matching FR-5's treatment of `MAX_TOKENS`. The retry is clean: at the point the image call runs, nothing has been saved to GCS and nothing has been sent to Telegram (`pipeline.py:64` precedes `:70-76`), and storage keys are job-id-based so any partial state overwrites idempotently. The cost of a retry is one extra story call (~$0.0005). Keep `_placeholder_png` for `FakeImageProvider` only.

If the fallback is kept for demo-robustness reasons, it must at minimum be **counted** — `fmb_image_fallback_total` — and treated as a failure in the SLO, because right now the only trace is a log line and `model: placeholder-fallback` in the `"image done"` record (`pipeline.py:67`), which nothing aggregates.

---

## 6. RR-5 / RR-6 — probes and deploy safety

### 6.1 `/healthz` is correct; `/readyz` is not fit for the worker

`/healthz` (`app.py:89-92`) returns a static object and touches nothing. **RR-5's first half and contract #3 are satisfied.** Its weakness is the flip side of F-1: because it is *only* a process-alive check, it cannot restart a pod whose subscriber has died. That is what the proposed `/livez` addresses.

`/readyz` (`app.py:100-119`) checks all four ports. Examining what each actually does changes the picture the requirement assumes:

| Port | `check_ready()` | Real cloud call? |
|---|---|---|
| queue | `get_subscription` (`queue_pubsub.py:265-275`) | **Yes** — Pub/Sub admin RPC every 15 s |
| storage | `list_blobs(max_results=1)` (`storage_gcs.py:40-52`) | **Yes** — a GCS Class-A LIST every 15 s |
| story | `google.auth.default()` (`story_vertex.py:71-80`) | **No** — local ADC resolution, cached |
| image | `google.auth.default()` (`image_vertex.py:91-98`) | **No** — same |

So the scenario the requirement posits — *"Vertex AI has a transient outage and readiness flaps on the only worker"* — **cannot happen**, because readiness never touches Vertex. That is good for stability and bad for honesty: `docs/gcp-interview-runbook.md:69` presents `/readyz` reporting `story: true, image: true` as evidence of Vertex access, and it is not. It only proves that the metadata server handed out a token. The runbook wording needs correcting (§9).

What *can* flap the only worker is a **GCS or Pub/Sub blip**. With `periodSeconds: 15` and the default `failureThreshold: 3` (no explicit value in `deployment-worker.yaml:55-58`), ~45 s of GCS unavailability marks the worker NotReady.

**And the worker has no Service.** I verified: `deploy/charts/family-media-bot/templates/` contains no `Service`. So worker readiness gates *nothing at runtime* — it removes the pod from an endpoint list that does not exist. Its only real effects are: (a) blocking rollout progression, and (b) making `helm --wait` fail. Which means a transient GCS 503 during a deploy window causes `--atomic` to **roll back a perfectly healthy release**.

**Recommendation:**
- Worker readiness should be **local**: report ready iff the process is up and the streaming subscriber is attached (the same boolean F-1 introduces). No cloud call, no cost, no flap, and it means something — "this pod is actually consuming."
- Keep the cloud-touching `/readyz` on the **web** tier, where it gates the (future) webhook endpoint and where the runbook's demo command genuinely proves ADC.
- If a Vertex reachability check is wanted for the demo, it belongs in `helm test` (a one-shot hook), not in a probe that runs 5,760 times a day.
- Set `failureThreshold` and `timeoutSeconds` explicitly rather than inheriting defaults, so the numbers are visible in the manifest.

### 6.2 What `--atomic --wait` does and does not protect against

`--atomic` implies `--wait`, so `--atomic --wait` is redundant but harmless. Note also that `deploy/charts/family-media-bot/README.md:55,60` uses `--rollback-on-failure`, which is the Helm 4 spelling; Helm 3 is `--atomic`. CI must use whichever matches its pinned Helm version — pin it explicitly in the workflow.

**Protects against:** invalid manifests; image pull failures; CrashLoopBackOff at startup; unschedulable pods; a new pod that never becomes Ready inside `--timeout`. In all of those the release is rolled back to the previous revision automatically. That is real value and it is the right flag.

**Does not protect against — and these are the ones that bite here:**

1. **F-3 (S2) — ConfigMap changes that never reach the pods.** Neither deployment template carries a `checksum/config` annotation; the pod templates have only labels (`deployment-worker.yaml:17-23`, `deployment-web.yaml:17-23`), and I grepped the whole `deploy/` tree to confirm no `checksum/` annotation exists anywhere. Env comes from `envFrom: configMapRef` (`deployment-worker.yaml:38-40`), which is injected **once at container start and never updated**. So a values-only change renders a new ConfigMap, Helm applies it, `--wait` observes zero pod churn and reports success, and the running pods keep the old configuration indefinitely.

   This directly breaks the SPEC §10 acceptance criterion *"`/family list` reflects the registry with no image rebuild after an edit"* and IR-4's "changing a character must not require an image rebuild." It will be a green deploy with no behaviour change — the most confusing failure mode there is.

   Fix: `annotations: { checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }} }` on both pod templates. If the registry becomes a mounted volume rather than env (which I'd recommend for a YAML registry — volumes *do* update in place, though with kubelet sync lag), it still needs the checksum to force a restart at a predictable moment. **This is the single highest-value line in the chart** and it spans the infra stream — flag it there too.

2. **Anything that fails after the rollout window.** A pod that becomes Ready and *then* misbehaves is a successful deploy. Since readiness only resolves ADC (§6.1), a wrong `VERTEX_TEXT_MODEL_ID` or `VERTEX_IMAGE_MODEL_ID` in the ConfigMap deploys perfectly green and breaks 100 % of jobs. `--atomic` cannot see this. Only F-2's alerting can.

3. **Dependency blips during the deploy window** cause a *spurious* rollback, per §6.1.

4. **Mutable tags.** `pullPolicy: IfNotPresent` (`values.yaml:11`) plus a reused tag means a node with the image cached silently runs the old code. PRODUCT-DIRECTION §3.8 already prefers the digest — do that, and it also makes rollback exact.

5. **External state.** Rollback restores manifests only. Terraform, Pub/Sub configuration, and GCS objects are untouched.

6. **First install.** On a *first* install, `--atomic` performs `helm uninstall` on failure rather than rolling back — it deletes the release entirely, including the ServiceAccount. Worth knowing before the first CI run against a fresh namespace.

`--timeout 5m` is right: worst-case time to Ready is image pull + `initialDelaySeconds: 5` + `periodSeconds: 15` × `failureThreshold: 3` ≈ 50 s of probing, so 5 m leaves ample room without letting a genuinely broken rollout hang CI.

### 6.3 F-9 (S3) — strategy, maxUnavailable, and the PDB trap

**The worker deployment has no `strategy` block** (verified — only `deployment-web.yaml:11` has one). It therefore inherits `RollingUpdate` with `maxSurge: 25%` and `maxUnavailable: 25%`. At `replicas: 1` those round to **`maxSurge: 1`, `maxUnavailable: 0`**: Kubernetes creates a *second* worker pod and waits for it to be Ready before terminating the first.

That is actually the behaviour you want — zero-gap handover, and the brief overlap where two workers consume the same subscription is harmless under at-least-once. Two consequences to make explicit rather than leave to defaults:

- **Capacity.** IR-3 collapses to one non-Spot pool "sized for GKE system pods plus both application deployments." That is **wrong by one worker**: it must fit `2 × worker + 1 × web + system pods` during a rollout, or the surge pod goes `Pending`, `--wait` times out, and `--atomic` rolls back a healthy release. At `worker.resources.requests.cpu: 250m` / `memory: 256Mi` (`values.yaml:52-57`) the surge is cheap, but it must be counted. **Flag this to the infra stream** — it is the most likely way the new node-pool sizing gets it wrong.
- Make it explicit in the chart:
  ```yaml
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
  ```

**The PDB trap.** A `PodDisruptionBudget` with `minAvailable: 1` (or `maxUnavailable: 0`) on a **single-replica** Deployment makes the pod permanently **undrainable**: `kubectl drain` blocks forever, GKE node auto-upgrades on the `REGULAR` release channel (`gke.tf:28-30`) stall, and the cluster upgrade silently never completes. This is the classic single-replica PDB mistake and the runbook currently lists "PodDisruptionBudgets" as a future improvement (`docs/gcp-interview-runbook.md:122-125`), which would walk straight into it.

Correct options at `replicas: 1`:
- **`maxUnavailable: 1`** — documents intent, protects nothing, never blocks a drain. Honest.
- **No PDB at all**, relying on graceful shutdown (F-5's drain) plus at-least-once. Also honest.
- A meaningful PDB requires `replicas: 2` + `minAvailable: 1`. If you want the PDB as a portfolio artifact, that is the price.

My recommendation: **`maxUnavailable: 1`, with a comment in the chart explaining exactly why `minAvailable: 1` is wrong here.** That comment is worth more in an interview than the PDB itself.

---

## 7. Observability gaps

### 7.1 F-2 (S1) — nothing collects anything

Before discussing which metric would have caught which bug, the load-bearing fact: **no metric emitted by this application is collected anywhere.**

- There is **no `Service`** in the chart, so `/metrics` on port 8080 is not addressable from outside the pod.
- There is **no `PodMonitoring` or `ServiceMonitor`** anywhere in `deploy/` (grepped).
- `observability/` contains only `.gitkeep`.
- `gke.tf:42-44` sets `monitoring_config { enable_components = ["SYSTEM_COMPONENTS"] }` — no `managed_prometheus` block and no `WORKLOADS`. Whatever the provider's default happens to be, Terraform is not asserting workload metric collection, and there is no scrape config regardless.

Every histogram and counter in `metrics.py` is write-only. This is why the two bugs in question ran to production undetected: not because the signal was missing, but because there was no path from the signal to a human. **Fix this before adding a single new metric.**

Two paths, and I recommend doing both in that order:

**(a) Log-based metrics — zero code, works today.** `gke.tf:38-40` already ships `WORKLOADS` logs to Cloud Logging, and `logging_setup.py` emits structured JSON with every `extra={}` field promoted to a top-level key (`logging_setup.py:40-42`). So `jsonPayload.chars`, `jsonPayload.cost_usd`, `jsonPayload.duration_s`, `jsonPayload.job_id`, and `jsonPayload.model` are all already queryable. Define Cloud Logging log-based metrics over them and alert. This costs nothing and needs no application change.

**(b) Managed Prometheus — the proper path.** Add `managed_prometheus { enabled = true }` inside `monitoring_config` in `gke.tf`, add a `PodMonitoring` CR to the chart scraping `:8080/metrics` on both components, and the existing `metrics.py` series become real. This is also a good Helm-depth artifact for the target role.

### 7.2 What would have caught the 122-character truncation bug

The signal already existed and nobody could see it — `pipeline.py:56` logs `chars: len(story.text)` on every job. Concretely:

| Signal | Where it comes from | Alert |
|---|---|---|
| **`fmb_story_chars` histogram**, labelled `mode` (and `lang` after FR-4) | new; or log-based metric over the existing `jsonPayload.chars` at `pipeline.py:56` — **available today with zero code** | `histogram_quantile(0.5, …) < 400`. A 150–200-word Russian story is ~900–1200 chars; 122 is not marginal, it is 8× low. This is the single alert that would have caught it. |
| **`fmb_story_finish_reason_total{reason}`** counter | new, from `response.candidates[0].finish_reason` in `story_vertex.py:47-51` | `rate(…{reason="MAX_TOKENS"}) > 0`. The *causal* signal, and the one that generalises to future models and languages. Pairs directly with the FR-5 guard. |
| **`fmb_story_tokens_thought`** | new, from `usage_metadata.thoughts_token_count`; PRODUCT-DIRECTION §4 calls ~950 "the signature" | Alert on `thoughts / (thoughts + output) > 0.7`. Catches the same class of bug on any future model. |
| **Cost reconciliation** | `fmb_cost_usd_total` (`metrics.py:48`) vs the GCP billing export | Any sustained divergence flags the `candidates_token_count` undercount described in §4. |

Note the ordering: even a `chars` **log-based** metric, which needs no code change at all, would have fired on the very first Russian story. That is the cheapest possible fix and it should go in first.

### 7.3 What would have caught the cold-start latency

**`fmb_queue_wait_seconds` already exists and already measures exactly this.** `metrics.py:32-36`, observed at `queue_pubsub.py:155` from `Job.created_at` to receipt. Under KEDA it would have read p50 ≈ 283–342 s from the first request. It was emitted every single time and never collected.

Post-KEDA it becomes the RR-1 SLI. Alert: `histogram_quantile(0.95, rate(fmb_queue_wait_seconds_bucket[10m])) > 30`. If the warm worker ever regresses — dead stream, crashloop, saturated concurrency — this fires.

Second, complementary signal for the same thing, and it needs **no application code and no scraping at all**:

> **`pubsub.googleapis.com/subscription/oldest_unacked_message_age > 120 s` for 2 minutes.**

This is the highest-value single alert in the entire system. It is a Google-native metric that exists for free, and it catches, with one policy: a dead streaming subscriber (F-1), a crashlooping worker, a hung job (F-4), a saturated single replica, a Vertex outage, and an IAM revocation. It is also the *only* thing that would detect F-1 without the code changes in §2.2. If exactly one thing from this document gets implemented, make it this.

### 7.4 F-10 — `QUEUE_DEPTH` is a constant-zero lie

```python
# queue_pubsub.py:260-263
async def depth(self) -> int:
    # ... KEDA reads that authoritative metric for scaling.
    return 0
```

`worker.py:39` sets `metrics.QUEUE_DEPTH.set(await self._queue.depth())` on **every job**, so `fmb_queue_depth` is pinned to `0` forever. Consequences:

- A dashboard panel showing "queue depth: 0" during a total outage is *actively misleading* — worse than no panel, because it reads as reassurance.
- The comment's justification ("KEDA reads that authoritative metric") dies with D2/IR-2. Leaving it in place after removing KEDA leaves a dangling rationale that a reviewer will spot.

**Recommendation:** delete `depth()` from `QueuePort` (`ports/queue.py:46-48`), from all four queue adapters, from `worker.py:38-41`, and delete `QUEUE_DEPTH` from `metrics.py:54-57`. Backlog is a Pub/Sub-side fact and belongs in the Cloud Monitoring alert in §7.3, not in an application gauge that the application cannot honestly compute. This is squarely KISS/YAGNI and removes three lines from the worker's hot loop. Note it also simplifies `test_worker_delivery.py`, which currently has to stub `queue.depth`.

### 7.5 Other signals worth having

| Signal | Why |
|---|---|
| `fmb_image_fallback_total` | F-7 — today a placeholder image is indistinguishable from success |
| Alert: `"releasing Pub/Sub message outside local flow-control capacity"` (`queue_pubsub.py:145`) | Should be unreachable; if it fires, the flow-control invariant is broken |
| Alert: `"Pub/Sub streaming subscriber stopped unexpectedly"` (`queue_pubsub.py:255`) | The direct log signal for F-1. Trivial log-based alert, catches it in seconds |
| DLQ: `num_undelivered_messages{subscription_id=…-jobs-dlq} > 0` | §5.3 — nothing reads the DLQ today |
| `kube_pod_container_status_restarts_total` on the worker | With `replicas: 1`, a restart is 100 % capacity loss |
| `fmb_jobs_enqueued_total` − `fmb_jobs_processed_total` | Cross-tier leak detector, independent of Pub/Sub |

**Suggested SLO, so the alerts have a target:** 95 % of jobs replied within 60 s of enqueue, measured on `fmb_queue_wait_seconds + fmb_job_processing_seconds`. Measured baseline is ~14 s, so 60 s is 4× headroom and still catches every failure mode in §8.

---

## 8. Failure mode table

| # | Failure | Detection today | Detection proposed | Blast radius | Mitigation |
|---|---|---|---|---|---|
| 1 | **Streaming subscriber terminates** (`Unauthenticated`, `PermissionDenied`, or any non-API error — `streaming_pull_manager.py:1380-1396`) | **None.** One ERROR log, both probes green, zero restarts | `oldest_unacked_message_age > 120 s`; log-based alert on `queue_pubsub.py:255`; `/livez` 503 | **Total, indefinite.** Bot silently dead | Auto-reconnect with backoff in `_on_stream_done`; `/livez` reflecting stream state; backlog-age alert |
| 2 | Worker pod OOMKill / node preemption / auto-upgrade drain | Pod restart count (uncollected) | Restart-count alert; backlog age | Total until reschedule (~30–60 s); in-flight job redelivered after ≤ ack deadline | Lower `ack_deadline_seconds` to 60 s; `maxUnavailable: 1` PDB; drain fix (F-5) |
| 3 | **Hung Vertex call** (no client timeout — F-4) | None | Job-duration histogram p99; backlog age | Only worker blocked up to **1 h** (`max_lease_duration`); then duplicate work | `HttpOptions(timeout=…)`; `asyncio.wait_for(process, 90)`; `max_lease_duration = 120` |
| 4 | **Rolling update mid-job** (F-5) | None | Backlog age; `fmb_jobs_processed{status="error"}` | One child gets a duplicate or half reply | Drain window in `Worker.stop()`; `maxSurge: 1, maxUnavailable: 0` |
| 5 | SIGKILL at 120 s from orphaned executor thread (F-4) | Pod `Error` termination reason | Kubelet event alert on non-graceful termination | Message already nacked, so no loss; ungraceful exit, wasted spend | Bound the HTTP call; Python 3.12 or a dedicated executor |
| 6 | **ConfigMap edited, pods not restarted** (F-3) | **None — deploy reports success** | `helm diff` in CI; checksum forces restart | Config change silently ineffective; breaks IR-4 acceptance | `checksum/config` pod annotation |
| 7 | Vertex outage / quota exhaustion (story) | Log only | `fmb_jobs_processed{status="error"}` rate; DLQ depth | All jobs fail; **5 apology messages per job** | `retry_policy` backoff; apology only on final attempt |
| 8 | Vertex outage (image) — **F-7** | **None. Acked as success** | `fmb_image_fallback_total` | Every child gets a grey placeholder, silently | Raise instead of falling back; count it |
| 9 | Story truncated at `MAX_TOKENS` (FR-5) | **None** | `finish_reason` counter; `fmb_story_chars` p50 | Child gets a fragment mid-sentence | FR-5 guard + the two metrics in §7.2 |
| 10 | GCS transient 503 during a deploy | Rollout stalls | Readiness made local | **`--atomic` rolls back a healthy release** | Local worker readiness; keep cloud checks in `helm test` |
| 11 | Surge pod unschedulable on the collapsed node pool (F-9) | `--wait` timeout → rollback | Pre-flight capacity check in CI | Deploy fails, existing release survives | Size the pool for `2 × worker + web + system` |
| 12 | Poisoned job reaches DLQ | **None** | DLQ `num_undelivered_messages > 0` | One job lost silently after 5 apologies | DLQ alert; document the drain procedure |
| 13 | Ack lost after successful processing | None | Duplicate reports from users | Duplicate story, up to 5× without backoff | `retry_policy`; optional GCS `if_generation_match=0` sentinel |
| 14 | Web pod restart re-reads unconfirmed Telegram updates (F-11) | None | `fmb_jobs_enqueued_total` spike at restart | Duplicate jobs → duplicate stories | Accept and document; webhook removes it |
| 15 | Backlog > 24 h (`message_retention_duration`, `pubsub.tf:24`) | None | Backlog-age alert at 120 s catches it 700× earlier | Jobs discarded | Covered by #1's alert |

---

## 9. Runbook deltas — `docs/gcp-interview-runbook.md`

KEDA is load-bearing in this document; roughly a third of it changes. Line references are to the current file.

**Delete or rewrite:**

| Lines | Current | Change |
|---|---|---|
| 9-10 | "Helm installs KEDA and the application" | One Helm release, one namespace |
| 13-17 | "KEDA reads the subscription backlog from Google Managed Prometheus and scales worker pods from zero. GKE then scales a tainted Spot node pool from zero." | Delete both sentences. Replace with: web publishes; a warm worker holds an open streaming pull and starts within a second |
| 19-24 | ASCII diagram contains `KEDA/HPA` | `Telegram -> web pod -> Pub/Sub -> worker pod (warm)` |
| 31 | "Worker pool: Spot `e2-standard-2`, autoscaling `0..5`, tainted" | One non-Spot pool (per IR-3) |
| 32 | "KEDA: Helm chart `2.20.1`; application workers `0..10`" | Delete |
| 38 | "Helm namespaces: `keda` and `app`" | `app` only |
| 40-43 | The whole `e2-medium` sizing paragraph, justified by "three default KEDA controllers" | Rewrite: sizing must now fit system pods + web + **two** workers during a rollout surge (§6.3) |
| 51-57 | `kubectl -n app get deployments,pods,scaledobjects,hpa`; `helm list -n keda` | Drop `scaledobjects,hpa` and the `keda` release |
| 64-69 | Idle state: worker `0/0`; ScaledObject `READY=True, ACTIVE=False` | Worker `1/1`; delete the ScaledObject line |
| 69 | "`/readyz` reports queue, storage, story, and image as `true`" | **Factually misleading** — `story`/`image` only prove ADC resolves, not Vertex reachability (§6.1). Reword or move the Vertex check into `helm test` |
| 74-79 | `kubectl -n keda logs deployment/keda-operator -f`; `kubectl get nodes -L role -w` | Delete both; add `kubectl -n app logs deploy/…-worker -f` |
| 81-87 | "The expected sequence is Pub/Sub backlog `0 -> 1`, KEDA worker `0 -> 1`, GKE Spot worker node `0 -> 1` … scale-down after cooldown" | Replace with the measurement: **342 s → ~14 s**, delivery on an already-open stream. This is the strongest paragraph in the new runbook |
| 93-94 | "The same chart renders AWS or GCP" | GCP only (IR-1) |
| 99-101 | Ownership: KEDA release; app release owns TriggerAuthentication + ScaledObject | Delete the KEDA bullet and both CRD references |
| 106-108 | "Why Helm?" cites KEDA objects and `--rollback-on-failure --wait` | Drop KEDA; note `--atomic` (Helm 3) vs `--rollback-on-failure` (Helm 4) and which CI pins |
| 116-119 | "Why KEDA plus GKE autoscaling?" and "Why Managed Prometheus?" | Replace with **"Why no KEDA?"** (the measurement) and **"Why one warm replica, and what breaks?"** (SPOF, F-1, the mitigation) |
| 122-125 | "What would production change?" lists PodDisruptionBudgets | Reword to `replicas: 2` **plus** `minAvailable: 1` — a single-replica PDB is the trap in §6.3, and knowing that is the better talking point |

**Add — two new sections the runbook does not have at all:**

1. **"When the bot stops replying."** This is the biggest gap once KEDA goes. Previously an operator could look at the ScaledObject and the node count for a story about what was happening; now the pod is always green and there is nothing to look at. The procedure should be, in order:
   ```
   1. Pub/Sub console → subscription → oldest_unacked_message_age.
      Rising = the worker has stopped consuming.
   2. kubectl -n app logs deploy/…-worker | grep "streaming subscriber stopped"
      → confirms F-1. Fix: kubectl rollout restart deploy/…-worker
   3. Backlog flat at 0 but no reply → check the DLQ subscription depth.
   4. DLQ non-empty → pull one message, inspect job_id, correlate in Cloud Logging.
   5. Nothing anywhere → check the web pod: Telegram getUpdates 409
      means two pollers are running for one token.
   ```
2. **"Deploy and rollback."** `--atomic` semantics, the first-install-uninstalls caveat (§6.2 item 6), what a `--wait` timeout means (usually the surge pod is Pending — check node capacity, F-9), and `helm rollback <release> <rev>` for a manual revert.

**Also worth adding to the talking points**, because it is the honest version of the KEDA story and reads as senior:

> *"Removing KEDA fixed a 4m43s cold start, but it also removed an accidental watchdog — the cooldown-to-zero was recreating the pod every few minutes, which papered over a subscriber that can die permanently. I replaced the accident with an explicit liveness signal and a backlog-age alert."*

---

## 10. Prioritised action list

**Must, before this is trustworthy:**

1. **F-1** — reconnect logic in `_on_stream_done` + `/livez` reflecting stream state on the worker.
2. **F-2** — one Cloud Monitoring alert: `oldest_unacked_message_age > 120 s`. Zero code, catches most of §8.
3. **F-3** — `checksum/config` annotation on both pod templates. One line; without it IR-4 cannot pass.
4. **F-4** — `HttpOptions(timeout=…)` on both Vertex clients + `asyncio.wait_for(process, 90)` + `max_lease_duration = 120`.

**Should, same change set:**

5. **F-5** — drain window in `Worker.stop()` before cancelling.
6. **F-6** — `retry_policy` in `pubsub.tf`; apology only on `delivery_attempt >= 5`.
7. **F-8** — worker readiness becomes local; cloud checks move to the web tier and `helm test`.
8. **F-9** — explicit `strategy` on the worker; size the node pool for the surge; `PDB maxUnavailable: 1` with the explanatory comment.
9. `ack_deadline_seconds` 300 → 60 (`pubsub.tf:23`).

**Nice, and cheap:**

10. **F-7** — image failure raises instead of returning a placeholder.
11. **F-10** — delete `depth()` and `QUEUE_DEPTH` outright.
12. `fmb_story_chars` + `finish_reason` counter (pairs with FR-5), DLQ alert.
13. Comment in `close()` recording *why* the nack-before-cancel ordering is load-bearing (§3.1) — it is invisible and a refactor will break it.

---

## 11. Cross-stream flags

- **→ infra-architect:** IR-3's node-pool sizing must account for `2 × worker` during a rollout surge (§6.3), not one. This is the most likely way the collapsed pool gets undersized, and the symptom is a spurious `--atomic` rollback of a healthy release.
- **→ infra-architect:** F-3 (`checksum/config`) is a chart change and is a hard prerequisite for the IR-4 / SPEC §10 acceptance criterion. It belongs to the Helm stream but is a reliability defect.
- **→ infra-architect:** `gke.tf:42-44` does not assert workload metric collection, and there is no `Service` or `PodMonitoring`. F-2 needs a Terraform change *and* a chart change.
- **→ app-architect:** F-7 (placeholder image acked as success) is the same defect as FR-5's truncated story and should be resolved with the same reasoning. Also, the apology text at `pipeline.py:108-109` is hardcoded Russian, which conflicts with FR-4 once language selection lands.
- **→ QA expert:** there is currently **no test for shutdown with a job in flight**, which is where F-5 lives. `tests/test_worker_delivery.py:56-73` asserts the current cancel-immediately behaviour and will need updating when the drain lands. A fake `QueuePort` that records ack/nack ordering across a simulated `stop()` mid-`process()` would cover RR-3 and RR-7 directly.
- **Disagreement worth recording:** D2 is right and I am not reopening it, but the decision as written ("run the worker at a fixed `replicas: 1`") is incomplete. `replicas: 1` without F-1's liveness signal is *less* reliable than KEDA 0→N was, because the cooldown was an unintentional supervisor. The decision should be restated as **"one warm replica, with an explicit stream-liveness probe and a backlog-age alert"** — that is the version that is actually better than what it replaces, and it is a stronger thing to defend out loud.
