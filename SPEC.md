# Implementation Spec

**Status:** awaiting approval. No implementation beyond §5 has started.
**Baseline:** `main` at `829ccb0`, three commits ahead of `origin/main`, unpushed.
**Direction:** [PRODUCT-DIRECTION.md](PRODUCT-DIRECTION.md)

## How to read this

This document is the complete statement of *what gets built, decided, and
verified*. Implementation detail — real HCL, real YAML, named test cases,
composed prompt shapes — lives in four design annexes produced by parallel
workstreams:

| Annex | Lines | Covers |
|---|---|---|
| [docs/design/app-architecture.md](docs/design/app-architecture.md) | 752 | FR-1…FR-6 |
| [docs/design/infrastructure.md](docs/design/infrastructure.md) | 2,325 | IR-1…IR-8 |
| [docs/design/reliability.md](docs/design/reliability.md) | 583 | RR-1…RR-9 |
| [docs/design/test-plan.md](docs/design/test-plan.md) | 784 | QR-1…QR-5 |

**§3 is approved in full** (2026-08-03). All nine decisions are binding.

---

## 1. Goal

A GCP-only portfolio project that is:

1. **Readable end-to-end in ~20 minutes** — no dead code, no unused cloud.
2. **Convincing as a product** — a bedtime fairytale starring the family's own
   characters, illustrated so those characters stay recognisable.
3. **Deep where the target role is deep** — Terraform on GCP, GKE, Helm, CI/CD,
   keyless security.

## 2. Non-goals

Do not design for these, and do not leave hooks for them:

- Any AWS support, or any second cloud
- KEDA, scale-to-zero, Spot capacity, cluster-autoscaler
- `/family add` or any runtime-mutable character storage
- Per-chat persisted state of any kind (this rules out a saved language choice)
- Photo-derived characters (a `VisionProvider`)
- Chart published as an OCI artifact — acknowledged, deferred
- Grafana, SLOs, dashboards-as-code, billing budgets

---

## 3. Decisions — all approved 2026-08-03

Every recommended option below was approved. They are now binding; the
"consequence" column is retained as the rationale for each.

| # | Question | Approved | Consequence if the other way |
|---|---|---|---|
| **Q1** | Add `language` to the frozen `Job`? | **Yes, additive with a default.** Prior art `a1e8dd0` did exactly this. Without it, `pipeline.py` cannot know which language to apologise in. | Language must ride inside `prompt`, which makes it unreadable to the error path. |
| **Q2** | Default language for the `Job` field? | **`"ru"`** — every job queued before the field existed was Russian. | A different default silently re-languages in-flight jobs on deploy. |
| **Q3** | How is language chosen per request? | **Per-message `lang:xx` token, stateless.** A picker implies memory; §2 forbids memory. | Keeping the branch's inline-keyboard picker requires `ProfileStore`, which contradicts §2. |
| **Q4** | Test framework | **Convert harvested tests to stdlib `unittest`.** `AGENTS.md` mandates it and the existing 31 tests use it. | Switching the project to pytest invalidates `AGENTS.md` and the QA annex's naming plan. |
| **Q5** | Node-pool consolidation is destructive | **Accept, using the 5-step sequence** (§8.3). | Skipping it leaves two pools and the KEDA-shaped taint topology. |
| **Q6** | Worker replica count | **`replicas: 1`** with a `/livez` health signal (RR-3). | `replicas: 2` removes the SPOF but doubles worker cost and needs job idempotency. |
| **Q7** | `roles/viewer` for the CI planner SA | **Accept** — read-only, excludes `secretmanager.versions.access`. | Enumerating a dozen `*.viewer` roles drifts whenever a resource type is added. |
| **Q8** | GKE control plane open to all IPs | **Accept and document** — hosted runners have unpredictable egress. | Authorized networks or Connect Gateway; more work, breaks CI until configured. |
| **Q9** | Ruff formatting PR first | **Yes, separate prerequisite PR** (~29 findings). | Every later PR carries unrelated formatting noise. |

---

## 4. Fixed decisions

Settled. Report a genuine defect if found, but design to the decision.

| # | Decision |
|---|---|
| D1 | GCP only. All AWS artifacts deleted. |
| D2 | No KEDA. Worker at fixed `replicas: 1`. |
| D3 | One non-Spot node pool. Two deployments (web, worker) retained. |
| D4 | Character registry in git, English, delivered as a ConfigMap. |
| D5 | `/family list` only. No write path from Telegram. |
| D6 | Story languages: English, Russian, Hebrew. |
| D7 | `gemini-3.5-flash` (story), `gemini-2.5-flash-image` (illustration). |
| D8 | One Helm chart, one values file, plus a `helm test` hook. |
| D9 | GitHub Actions with Workload Identity Federation. No JSON keys. |
| D10 | kind for local cluster testing. |
| D11 | Adapters never substitute a fabricated success for a provider failure. |
| D12 | Harvest from `a1e8dd0`; do not rebuild what already exists. |

---

## 5. Already shipped

Three commits on `main`, unpushed. 31 tests pass. These implement D11.

| Commit | Change |
|---|---|
| `7a8402a` | Token budget covers thinking + non-Latin scripts; only `FinishReason.STOP` accepted; check runs before the empty-text check; thinking tokens counted in cost; `QueueDelivery.attempt` added so the chat is apologised to once, not five times. |
| `2c86fa9` | `VertexImageProvider` raises instead of returning a placeholder gradient at `$0`. |
| `829ccb0` | Both Vertex adapters refuse to construct on an unpriced model id rather than reporting `$0`. |

---

## 6. Harvest plan — branch `a1e8dd0`

`worktree-enrich-languages-and-modes` (17 Jul, pushed) predates the GCP
migration and is AWS-backed, so nothing merges directly. Harvest by porting.

### Take

| Source | Port notes |
|---|---|
| `i18n.py` (181 ln) | `Lang` enum, `DEFAULT_LANG`, `resolve()`, `_STRINGS` for en/ru/he. **Strip** `/family add`, photo affordances, and `/language` picker strings — D5 and Q3 remove those flows. |
| `models.py` | Additive `language: str = "ru"` on `Job` + `new_job(..., language=)`. Matches Q1/Q2 exactly. |
| `prompts.py` | Per-language bedtime system prompts, replacing the hardcoded Russian at `prompts.py:17`. |
| `router.py` (250 ln) | Shared conversation brain for webhook + poller, so the two entry points cannot drift. **Trim** `/family add` and callback-query flows. |
| `commands.py` | Parsing extensions. |
| `tests/` | `conftest.py`, `test_commands.py`, `test_i18n.py`, `test_prompts.py`, `test_router.py`. Convert pytest → `unittest` per Q4. The i18n-completeness test (every key in every language) is the highest-value single item. |

### Do not take

| Source | Reason |
|---|---|
| `adapters/profiles_localdir.py`, `profiles_s3.py`, `ports/profiles.py` | Per-chat persisted state — §2 forbids. |
| `adapters/vision_bedrock.py`, `vision_fake.py`, `ports/vision.py` | Photo-derived characters — replaced by D4. |
| `telegram.py` inline keyboards / `answerCallbackQuery` / `getFile` | Only needed by the picker Q3 removes. |
| `infra/s3.tf`, `story_bedrock.py` | AWS. |

### Conflicts

- Branch persists language **per chat**; Q3 makes it per-message. `i18n.py`'s
  `LANGUAGE_BUTTONS` and `CHOOSE_LANGUAGE` become dead — drop them.
- Branch's `/family` is read-write; D5 is read-only.
- Branch uses **pytest**; repo uses **unittest** (Q4).

---

## 7. Functional requirements

**FR-1 — Character registry.** Version-controlled registry at
`deploy/charts/family-media-bot/files/characters.yaml` (inside the chart —
`.Files.Get` cannot traverse outside it). Each entry: stable `id`, English
`appearance`, English `traits`, per-language `names`. English required; `ru`
and `he` optional. Loaded **once at startup**. Malformed → fatal. Absent →
governed by an explicit `CHARACTERS_REQUIRED` flag, never inferred.

**FR-2 — Prompt composition.** `appearance` reaches *both* the story prompt and
the image prompt **verbatim**. Replaces the hardcoded roles at `prompts.py:48`.
`derive_illustration_prompt` (`prompts.py:62`) is **deleted**, not repaired —
with FR-4 it would feed Russian or Hebrew prose to an English-trained image
model. Registry wins any conflict, and the conflict is prevented at source: the
system prompt forbids the model from describing appearance in its
`ILLUSTRATION:` line ("refer to characters by name only"). Hint = scene,
registry = identity, disjoint.

**FR-3 — `/family list`.** Lists characters in the active language using
`names[lang]`. Read-only. `Mode` must not grow a member for it — it is a query,
not a generation mode. `commands.py` stays pure; the answer is produced in a
dispatch layer.

**FR-4 — Language selection.** Per-message token (Q3). Registry stays English.
The `ILLUSTRATION:` sentinel stays English in every language. Hebrew is RTL —
verify rendering.

**FR-5 — Story integrity.** ✅ Shipped in `7a8402a`.

**FR-6 — Cost accounting.** ✅ Shipped in `7a8402a` and `829ccb0`.

**FR-7 — Retry-aware notification.** ✅ Shipped in `7a8402a`.

**FR-8 — `api-contract.md` states what is frozen and what is not.** The document
currently mixes the contract with its AWS binding, and asserts two things that
are no longer true: a `0→N` worker tier (removed by D2) and `POST /webhook` as
the only entry point (long polling is live).

Rewrite so the **contract is provider-neutral** — job shape, endpoints, port
interfaces, the `generated/<job_id>.png` key layout, and "authenticated by
workload identity, no API keys in secrets" as a property rather than a vendor.
Name GCP in exactly one place: the ports table, whose "Prod" column becomes
**"Prod (GCP)"** listing `PubSubQueue`, `VertexStoryProvider`,
`VertexImageProvider`, `GcsStorage`.

Substituting "Vertex AI" for "Bedrock" throughout would repeat the original
mistake in the other direction and discard the port/adapter argument the
document exists to make.

State the distinction explicitly: **the job shape and endpoints are frozen**
(with `language` the one sanctioned additive change, Q1); **the provider binding
is not**. This converts an apparent contradiction — frozen, yet edited — into a
stated rule.

---

## 8. Infrastructure requirements

**IR-1 — AWS removal.** Delete `infra/aws/`, `deploy/legacy/`,
`deploy/addons/aws/`, `deploy/values/aws.yaml`, the four AWS adapters, AWS
config fields, AWS dependencies, and the `cloud.provider` conditional. A trial
pass removed 33 of 114 files. Verify with the §14 grep.

**IR-2 — KEDA removal.** ScaledObject, KEDA values, `family-media-keda` GSA,
its Workload Identity binding, its `monitoring.viewer` grant.

**IR-3 — Node pool.** One non-Spot pool, `e2-standard-2`. Arithmetic: ~900m CPU
of GKE system requests on a single-node zonal cluster, 300m for web+worker,
250m headroom for the worker's rolling-update surge. `e2-standard-2` allocates
1930m; `e2-medium` allocates 940m and **does not fit**. No cluster autoscaling —
a fixed node count makes the bill predictable against a finite trial credit.

**Destructive.** `machine_type`, `disk_size_gb`, and `name` are ForceNew, so the
`web` pool — which hosts everything — is destroyed and recreated. A `moved`
block does not help. Required sequence:

1. `terraform apply` #1 — **add** the new pool, leave the old ones.
2. `helm upgrade` — drop nodeSelectors/tolerations, worker `replicas: 1`,
   ScaledObject template deleted.
3. Drain the old node.
4. **Now** uninstall KEDA — never before step 2, or the CRDs vanish and the
   upgrade fails with `no matches for kind "ScaledObject"` on a stuck release.
5. `terraform apply` #2 — delete the old pools, the KEDA GSA, its binding, and
   its grants.

**IR-4 — Registry ConfigMap.** Rendered from the chart's `files/` directory. A
`checksum/characters` annotation on **both** pod templates forces the roll.
Without it — the current state — a ConfigMap-only change deploys green and never
reaches the pods, breaking §14's acceptance criterion. *Found independently by
three workstreams.*

**IR-5 — Workload Identity Federation.** New `infra/gcp/cicd.tf`: pool,
provider, deployer SA, planner SA, IAM bindings. GitHub signs OIDC tokens for
every repo on github.com with the same issuer and keys, so a valid signature
proves nothing about *who* is calling. Two independent gates:

1. **`attribute_condition` on the provider** — rejects at the STS exchange if
   the token is not from this repository, owned by this owner account, on an
   allowed ref. Pin `repository`, `repository_owner_id` (numeric — account
   *names* can be released and re-registered), and the ref. Parentheses are
   load-bearing in CEL.
2. **`principalSet` IAM bindings** — which ref gets which privileges.

Deployer (main only) and planner (read-only) are split, so a PR can never reach
a write credential. No service-account JSON exists anywhere.

**IR-6 — Helm chart.** One chart, one values file (`deploy/values/prod.yaml`),
no provider conditionals. **A `Service` template must be added — none exists
today**, so the `helm test` hook cannot otherwise be built. Plus
`deploy/values/kind.yaml` for the smoke install. The image tag leaves the values
file entirely; the schema rejects a release supplying neither tag nor digest.

**IR-7 — Pipelines.** `pr.yml`: ruff, unit tests, `helm lint`, `helm template`,
`terraform plan` as a PR comment, kind smoke install. `main.yml`: build, push to
Artifact Registry, `helm upgrade --install --rollback-on-failure --wait`.
Deny-by-default `permissions:`; each job opts in. Concurrency group prevents
overlapping deploys — a cancelled `helm upgrade` leaves the release
`pending-upgrade`. **CI never runs `terraform apply`.**

> `--atomic` does not exist in Helm 4. Local toolchain is v4.2.2; the flag is
> `--rollback-on-failure`.

**IR-8 — Monitoring.** One alert policy on `oldest_unacked_message_age > 120s`
with `duration = "300s"` so the ~60s Pub/Sub sampling lag cannot false-positive
during a rollout. Threshold derived: 13.7s measured pipeline, 300s ack deadline,
so 120s is ~9× the happy path and fires before the first redelivery. Plus
`managed_prometheus { enabled = true }` in `gke.tf` (an **in-place** update, no
node replacement) and a `PodMonitoring` template gated on a value, because the
CRD does not exist in kind.

---

## 9. Reliability requirements

**RR-1 — Warm worker.** Verified: the arithmetic is clean, seconds not minutes.

**RR-2 — At-least-once delivery.** Verified to hold. The callback→asyncio
handoff is race-free. **Load-bearing subtlety:** the nack-before-`cancel()`
ordering in `close()` works because `Dispatcher.stop()` joins its worker thread
and drains ahead of the poison pill. A refactor that reorders it silently breaks
delivery — add a comment saying so.

**RR-3 — Stream death is invisible. `[S1 — highest severity]`** The Pub/Sub
subscriber can terminate permanently: `google-cloud-pubsub` treats
`Unauthenticated`, `PermissionDenied`, `NotFound`, `Cancelled` **and any
non-`GoogleAPICallError` exception** as terminating
(`streaming_pull_manager.py:1396`, verified). `_on_stream_done`
(`queue_pubsub.py:246-258`) logs one line and returns — no reconnect. The pod
stays `Running`/`Ready`/`Live` with zero restarts, consuming nothing, forever.

Under KEDA this self-healed by accident: cooldown scaled to zero and the next
spike built a fresh pod. **Fixed `replicas: 1` converts a self-healing transient
into an indefinite silent outage.** D2 stands; the fix is a health signal:
reconnect-with-backoff, plus a worker `/livez` reading a local boolean (no cloud
call, so contract #3 holds).

**RR-4 — No upper time bound.** No timeout on either Vertex call, and
`FlowControl` sets only `max_messages`, so `max_lease_duration` defaults to
**3600s** (verified) against a 300s ack deadline. A hung call blocks the only
worker, then duplicates. It also orphans a non-cancellable `to_thread`, and
Python 3.11's `shutdown_default_executor()` has no timeout → SIGKILL at 120s.

**RR-5 — Zero drain window.** `Worker.stop()` (`worker.py:65-74`) sets `_stop`
then immediately cancels. Every rolling update discards an in-flight job, and
`telegram.py:79-83` has a cancellation window that delivers a partial reply then
nacks → duplicate story.

**RR-6 — Probes.** `/healthz` is correct. `/readyz` is **not fit for the
worker**: it cannot flap on a Vertex outage (both `check_ready()` calls only do
`google.auth.default()`), but it does make a live GCS LIST every 15s, gates
nothing, and can let a transient GCS 503 roll back a healthy release. The
runbook presents it as proof of Vertex access — it is not.

**RR-7 — Deploy safety.** `strategy` with `maxUnavailable: 1`. A PDB with
`minAvailable: 1` on a single replica makes the node **undrainable** and stalls
GKE auto-upgrades.

**RR-8 — DLQ is unconsumed.** Nothing reads or alerts on it.

**RR-9 — `QUEUE_DEPTH` is a constant-zero lie.** `PubSubQueue.depth()` returns
a hardcoded `0` (`queue_pubsub.py:260-263`), and the worker publishes it as a
gauge every iteration.

---

## 10. Quality requirements

**QR-1 — Unit tests.** Six new files per the QA annex §2, following
`test_<concern>.py` / `test_<behavior>`: registry loader, prompt composition,
`/family list`, language selection, cost, plus shared fixtures built first.

**QR-2 — The truncation regression.** ✅ Partly shipped. The remaining item is
`test_absent_finish_reason_is_treated_as_a_failure`, which makes the *class*
unreinventable.

**Why the suite missed it, in one line:** `test_gcp_adapters.py:238` asserted
`result.text == "Жила-была звезда."` — 17 characters. **The suite's own model of
a successful story was a fragment shorter than the one that reached
production.** Structurally: the mock was a `SimpleNamespace` containing exactly
the fields the code reads, so a mock built from the implementation can never
reveal a field the implementation fails to read.

Three enforceable rules follow: adapters may not invent values; assert
provenance, not shape; build mocks from the provider's response type via a
shared fully-populated factory. Plus an AST meta-test that fails CI on any new
silent `except`.

**QR-3 — Helm verification.** `helm template` piped through
`yaml.safe_load_all` into a unittest harness, ~20 assertions (worker has exactly
1 replica; no KEDA objects; ConfigMap contains the registry; no AWS env vars),
hard-failing under `REQUIRE_HELM=1`.

**QR-4 — kind smoke test.** One shared script for laptop and CI. **Split fake
strategy:** fake story/image/storage, but a *real* `PubSubQueue` against the
emulator via `PUBSUB_EMULATOR_HOST` and a *real* `TelegramClient` against an
in-cluster stub — faking those two erases the tier boundary and the delivery leg
that are the only reasons to use a cluster at all.

**QR-5 — Manual demo checklist.** 22 steps with a 5-attribute
character-consistency rubric.

**CI gates.** Block on hermetic, report on cloud- or model-dependent.

---

## 11. Defect register

| # | Defect | Severity | Status |
|---|---|---|---|
| 1 | Story truncated at token ceiling | High | ✅ `7a8402a` |
| 2 | Non-STOP finish reasons ship partials | High | ✅ `7a8402a` |
| 3 | Cost excludes thinking tokens | Med | ✅ `7a8402a` |
| 4 | Five apologies per failed request | High | ✅ `7a8402a` |
| 5 | Image failure → fabricated placeholder | High | ✅ `2c86fa9` |
| 6 | Unpriced model → silent `$0` | Med | ✅ `829ccb0` |
| 7 | **Stream death invisible; probes stay green** | **S1** | Open — RR-3 |
| 8 | **Nothing collects any metric** | **S1** | Open — IR-8 |
| 9 | Missing `checksum/config`; ConfigMap edits never land | S2 | Open — IR-4 |
| 10 | No timeout; 3600s lease vs 300s ack deadline | S2 | Open — RR-4 |
| 11 | `Worker.stop()` has no drain window | S2 | Open — RR-5 |
| 12 | No `Service` template exists | S2 | Open — IR-6 |
| 13 | `pyyaml` undeclared → CrashLoopBackOff once FR-1 lands | S2 | Open |
| 14 | `pipeline.py:60` silent fallback to the first-sentence heuristic | S2 | Open — FR-2 |
| 15 | `/readyz` unfit for worker; GCS 503 can roll back a good release | S3 | Open — RR-6 |
| 16 | DLQ unconsumed, unalerted | S3 | Open — RR-8 |
| 17 | `QUEUE_DEPTH` constant-zero | S3 | Open — RR-9 |
| 18 | `_MAX_PROMPT_CHARS = 2000` silently slices | S3 | Open — raise to 4000 |
| 19 | `FakeStoryProvider` returns no `illustration_hint`, so FR-2's primary path is unexercised locally | S3 | Open |
| 20 | Character names are children's names — PII beyond contract #5's list | S3 | Open — log `id` and counts only |
| 21 | ~29 ruff findings; 3 E501 (`pipeline.py:67`, `telemetry.py:49`, `image_fake.py:19`) | Low | Open — Q9 |
| 22 | Version skew: venv 3.14.5 vs image 3.11-slim | Low | Open — pin 3.11 in CI |

---

## 12. Contracts

Violating any of these is a defect regardless of what else improves:

1. **`Job` is frozen.** Q1's `language` field is the one sanctioned additive
   change — defaulted, so messages queued by an older web tier still parse.
   Frozen covers the job shape and the endpoints; it does **not** cover the
   provider binding, which changes freely (FR-8).
2. `ILLUSTRATION:` stays **English** in all story languages.
3. `/healthz` performs **no** cloud calls.
4. A message is acked **only** after the pipeline reports success.
5. `chat_id`, request text, raw payloads, **and character names** never reach
   log records.
6. Characters are **text descriptions only** — never photographs of real
   children.
7. Business logic imports no cloud SDK.
8. **No adapter substitutes a fabricated value for a provider failure.**

---

## 13. Sequencing

| PR | Title | Requirements |
|---|---|---|
| 0 | Apply ruff formatting, pin the linter | Q9 |
| 1 | Delete AWS and KEDA | IR-1, IR-2 |
| 2 | Rewrite `api-contract.md` | FR-8 |
| 3 | One node pool | IR-3 |
| 4 | Declare pyyaml; character registry + loader | FR-1, #13 |
| 5 | Registry ConfigMap + checksum annotation | IR-4 |
| 6 | Harvest i18n, prompts, router; language on Job | FR-3, FR-4, §6 |
| 7 | Prompt composition; delete `derive_illustration_prompt` | FR-2, #14 |
| 8 | Reshape the chart; add Service + test hook | IR-6, #12 |
| 9 | Stream reconnect + `/livez`; drain window; timeouts | RR-3, RR-4, RR-5 |
| 10 | Workload Identity Federation | IR-5 |
| 11 | GitHub Actions pipelines | IR-7 |
| 12 | Scrape and alert on what we already emit | IR-8, #8 |
| 13 | Test suite per the QA annex | QR-1…QR-5 |

PRs 0–3 are near-pure deletion and low risk. PR 9 carries the S1 defect and
should not slip behind the cosmetic work.

---

## 14. Acceptance

- `git grep -i -E 'aws|bedrock|sqs|boto|keda'` returns only historical
  references in `docs/` and commit messages
- A `/fairytale` returns a complete, untruncated story in the requested
  language, illustrated with recognisable family characters
- `/family list` reflects the registry with **no image rebuild** after an edit
- A push to `main` deploys with no manual step and no static credential
- `make test`, `make helm-lint`, and the kind smoke test pass locally and in CI
- Killing the Pub/Sub stream causes the worker to go unhealthy and restart
- A ConfigMap-only change restarts the pods

## 15. Risks

- **Character consistency is subjective** and only observable against a live
  model. Whether `gemini-2.5-flash-image` honours the reference-image path is
  the single largest unresolved product risk — verify before relying on it.
- The node-pool migration touches the only node running everything.
- The WIF attribute condition is the sole barrier against any GitHub repo; a
  malformed CEL expression fails open in the sense that it may admit more than
  intended.
- `roles/viewer` on the planner SA is coarse (Q7).
- GKE control plane is reachable from any IP (Q8).
