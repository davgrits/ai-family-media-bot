# Product Direction

**Status:** agreed, not yet implemented. Repo is at `a7c2f29`, clean.
**Date:** 2026-08-02
**Chosen direction:** Simplify to GCP-only (A), plus a real family-character registry (partial B).

---

## 1. Why this document exists

The project works end-to-end but reads as two products fused together: a
multi-cloud infrastructure demo and a bedtime-story bot. The infrastructure half
is over-built for what the bot does; the story half — the part that is actually
charming — is barely implemented.

This document records what we decided to change and, more importantly, *why*, so
each decision can be defended out loud rather than just pointed at.

Guiding principles: **KISS** and **YAGNI**. Every deletion below is a deliberate
trade-off, not an omission. "I built it, measured it, and removed it" is a
stronger position than either keeping unused machinery or never having tried it.

### Target context

The role is a DevOps Engineer position at Dropit Shopping (Tel Aviv). What it
weights, in the posting's own emphasis:

| They ask for | Repo today |
|---|---|
| Heavy Terraform automation on GCP | Strong — 534 lines, real IAM reasoning |
| Production GKE | Strong — private cluster, Workload Identity, node pools |
| **Helm — "highly important"** | Weak — chart exists, shallow usage |
| Security-minded (GDPR, ISO) | Strong — keyless identity, PII redaction, least privilege |
| Python for automation | Strong — 2,039 lines, ports/adapters, tests |
| Networking fundamentals (VPC, DNS, firewalls, routing) | Good — VPC-native, Cloud NAT, private nodes |
| LLM / agentic familiarity | Strong — this is an LLM product |
| CI/CD and developer experience | **Absent** — `.github/workflows/` holds only `.gitkeep` |

The two gaps to close are **Helm depth** and **CI/CD**. Everything else is
already defensible; it is mostly buried under AWS code that will never run.

---

## 2. Current state

### The product
Three Telegram commands (`/fairytale`, `/custom`, `/surprise`), plus plain text
via long polling. Output is a Russian bedtime story of ~150–200 words and one
illustration. Web tier enqueues; worker generates asynchronously.

**The family-characters idea is not actually implemented.** `Mode.FAIRYTALE`
falls back to a hardcoded English string in `prompts.py`:
`"mom = kind queen, dad = gentle king, me = brave little knight"`. The
illustration prompt is derived by taking the *first sentence of the story*, so
characters cannot look consistent from one picture to the next. This is the
largest gap between the intended demo and the running one.

### The infrastructure
GCP side is genuinely good: zonal GKE (free-tier management credit), private
nodes with Cloud NAT, VPC-native, Workload Identity, Pub/Sub with a DLQ after 5
attempts, GCS with 30-day lifecycle, per-resource least-privilege IAM.

### The mess
Almost entirely duplication for a cloud that is not deployed:

- `infra/aws/` — 8 Terraform files
- `deploy/legacy/kustomize/aws/` — 7 files superseded by Helm
- 4 AWS adapters (Bedrock story, Bedrock image, SQS, S3)
- `deploy/values/aws.yaml`, `deploy/addons/aws/`
- A `cloud.provider` conditional threaded through the Helm templates
- A 300-line `values.schema.json` validating both providers

A trial deletion pass removed **33 of 114 files** without touching anything that
runs.

---

## 3. Decisions

### 3.1 GCP only — delete AWS entirely

**Decision:** remove all AWS Terraform, adapters, Helm values, addons, legacy
Kustomize, config fields, and the provider conditional.

**Why:** the target company runs GCP. A second cloud that is never deployed
doubles the reading cost of the repo and halves its clarity. The port/adapter
boundary is what proves portability — a second live adapter is not required to
demonstrate it, and the interface itself is the evidence.

**Trade-off:** loses "multi-cloud" as a headline. That headline was costing more
than it earned.

### 3.2 Drop KEDA — one warm worker

**Decision:** remove KEDA, its ScaledObject, its Google service account, its
Workload Identity binding, and its `monitoring.viewer` grant. Run the worker at
a fixed `replicas: 1`.

**Why — measured, not assumed.** Observed cold-start behaviour:

| Stage | Measured |
|---|---|
| Enqueue → KEDA activation | 282.667 s (4 m 43 s) |
| Spot node startup | ~46 s |
| Actual pipeline processing | 13.7 s |
| Worker kept alive after reply (300 s cooldown + metric lag) | ~7 m 39 s |

The activation delay is **not** a streaming-pull problem. Google samples the
Pub/Sub backlog metric on roughly a 60-second interval, documents additional
delay before it becomes visible, and warns that backlog metrics can gap for
several minutes. Google's own autoscaling guidance recommends a minimum task
count above zero when low latency matters.

So: ~4m43s of activation to save a few shekels of idle compute, on a bot where a
child is waiting. The latency *is* the product. Raising the cooldown would not
help — it only increases idle cost without touching the first request after a
quiet period.

Removing KEDA and fixing one warm replica is therefore **the same decision** as
fixing the cold start. One change buys both the latency fix and the
simplification.

**Trade-off:** loses scale-to-zero and the autoscaling artifact. Mitigated by
being able to explain exactly what was measured and why it was removed.

### 3.3 One node pool, not Spot

**Decision:** collapse to a single non-Spot node pool. Delete the Spot worker
pool, the `workload=jobs` taint, the tolerations, and the `role` nodeSelectors.
Keep **two deployments** (web and worker) — the tier separation is real and
worth showing.

**Why:** "always warm" and "Spot" contradict each other. Spot nodes are
preempted; with a warm-worker design, preemption reintroduces exactly the gap we
just removed. Pick one. Given 3.2, pick warm.

**Trade-off:** taints, tolerations, and multi-pool management stop being
demonstrable artifacts. They remain discussable.

### 3.4 Character registry in code, in English

**Decision:** a YAML registry in git, mounted as a ConfigMap. English
descriptions. `/family list` only — **no `/family add`**.

```yaml
characters:
  - id: mila
    appearance: "5-year-old girl, curly red hair, freckles, green cloak"
    traits: "curious, brave, loves animals"
    names: { en: Mila, ru: Мила, he: מילה }
```

**Why English:** image models are trained overwhelmingly on English captions, so
an English appearance string yields a markedly better and more *repeatable*
illustration than a translated one. This is a technical reason, not a stylistic
one — worth saying out loud.

**Why read-only:** a bot cannot commit to git. `/family add` would require a
runtime overlay (GCS or Firestore), which reintroduces state, IAM, and a local
emulator for a registry that changes a few times a year. YAGNI. Characters are
edited by pull request.

**What this fixes:** `appearance` flows verbatim into *both* the story prompt and
the image prompt. That is what makes the same child look like the same child
across illustrations, and it replaces the first-sentence heuristic.

**Safety constraint (already in the code, keep it):** characters are text
descriptions only. Never photographs of real children.

### 3.5 Multi-language stories

**Decision:** English first, then Russian, then Hebrew. Registry stays English
regardless; only the *story output* language varies.

Language becomes one variable: a per-language system prompt, plus `names[lang]`
on output. Russian already works, and English is nearly free once the registry
is English. Hebrew is then a third prompt string plus an RTL rendering check.

**Keep the `ILLUSTRATION:` sentinel in English in every language** — it already
is, which is what keeps this cheap.

### 3.6 Model choices

| Concern | Model | Cost |
|---|---|---|
| Story | `gemini-3.5-flash` | $1.50 / $9.00 per M tokens (in/out) |
| Illustration | `gemini-2.5-flash-image` | $0.039 per image |

**Story — why Flash, not Pro:** a 200-word children's story is a low-difficulty
generation with a latency budget measured in seconds. Flash is that tier. Pro
costs several times more for no perceptible quality gain at this length.
*Switch trigger:* long-form stories or multi-turn continuity.

**Illustration — why Gemini Flash Image, not Imagen 4:** the differentiator is
**reference-image input**, which lets the same family character carry across
illustrations. Imagen 4 has higher raw fidelity but is text-to-image only — you
would re-describe the child in prose each time and get a different face each
time. For "my family in a fairytale," consistency beats fidelity. Cost is a wash
(Imagen 4 Fast $0.02, Standard $0.04, Ultra $0.06). Bonus: same `google-genai`
client and one auth path for both models.

> **Verify before claiming:** confirm the reference-image path for
> `gemini-2.5-flash-image` in this project's Model Garden.

**How to explain any model choice out loud:**
requirement → binding constraint → two candidates → the one differentiator that
decided it → what would make me switch.

### 3.7 Helm

**Decision:** one chart, one values file (`deploy/values/prod.yaml`), no
provider conditionals, a much smaller `values.schema.json`. Add a `helm test`
hook hitting `/healthz`.

**Why:** the posting calls Helm proficiency "highly important" and the current
chart is shallow. A `helm test` hook is ~15 lines and is the single thing that
makes Helm experience read as real rather than copy-pasted. `--rollback-on-failure` is the
most useful flag to be able to explain, because it auto-rolls-back a failed
release.

**Learning order:** values → templates → `_helpers.tpl` → schema → test hook →
`--rollback-on-failure` → (optional) publish the chart as an OCI artifact to
Artifact Registry.

### 3.8 CI/CD — GitHub Actions with Workload Identity Federation

**Decision:** replace manual deploys entirely.

Today: build by hand, hand-edit the image tag in the values file, run
`helm upgrade` from a laptop. Three problems — the tag drifts from what is
committed, deploys are not reproducible from git alone, and it requires personal
credentials.

**Target:**

- The image tag **leaves the values file**. CI passes `--set image.tag=$SHA`,
  better still the image *digest*, so the deploy is immutable and re-runnable.
- **PR workflow:** ruff → unittest → `helm lint` + `helm template` →
  `terraform plan` posted as a PR comment → `helm install` into kind as a smoke
  test.
- **Main workflow:** build → push to Artifact Registry →
  `helm upgrade --install --rollback-on-failure --wait --timeout 5m`.
  (Helm 4 renamed `--atomic`; the local toolchain is v4.2.2, where `--atomic`
  does not exist.)
- **Auth: Workload Identity Federation** — keyless OIDC, no service-account JSON
  in GitHub secrets. This is a security talking point that lands with a company
  naming GDPR and ISO.
- Requires a WIF pool and provider plus a deployer service account
  (`artifactregistry.writer` + `container.developer`) added to Terraform.

**Note:** `ruff` is referenced in `AGENTS.md` but is not installed in the venv.
CI must install it. Three pre-existing lines exceed the 100-character limit and
will fail the first lint run — `pipeline.py:67`, `telemetry.py:49`, and
`image_fake.py:19` — within roughly 29 ruff findings overall.

### 3.9 Local development and testing — kind

Three tiers, cheapest first:

1. **`make run`** — fake adapters, no Docker, no cluster. Fastest inner loop.
   Already works.
2. **Docker + the `gcloud` Pub/Sub emulator** — exercises the real queue adapter
   with no cloud account.
3. **kind + `helm install`** — proves the chart, probes, ConfigMap, and Secret
   wiring actually work.

**kind over Minikube:** one Docker container per node, boots in ~30 s, no VM or
driver layer, and **the identical command runs in GitHub Actions** as a chart
smoke test. Minikube's extras (addons, ingress, dashboard, LoadBalancer tunnel)
are things this project does not need.

**Honest caveat:** Workload Identity does not exist in kind, so local cluster
runs use the fake providers.

---

## 4. Known defect: stories truncated to ~122 characters

Diagnosed but **not yet fixed** — the fix was rolled back with the rest of the
implementation work. Recoverable via `git cherry-pick 08b61d4`.

**Symptom:** Telegram receives ~122 characters of Russian instead of a full
story.

**Not Telegram.** `telegram.py` only splits into a separate message above 1024
characters; 122 passes through as a photo caption.

**Root cause, in `adapters/story_vertex.py`:**

```python
max_output_tokens=max(1024, self._max_words * 4)   # → 1024 for 220 words
```

Three effects stack:

1. **Thinking tokens are drawn from the same `max_output_tokens` budget as the
   visible answer.** The model spends most of the 1024 reasoning, then emits a
   few dozen tokens of story before hitting the ceiling. 122 Russian characters
   ≈ 20 words ≈ ~55 tokens — implying roughly 950 tokens went to thinking. That
   is the signature.
2. **Cyrillic tokenizes 2–3× worse than English** (~1.3 tokens/word). 150–200
   Russian words is 400–600 tokens by itself, so the `* 4` heuristic — calibrated
   for English — was already short by about half.
3. **Nothing checks `finish_reason`.** `generate()` raises only on *empty* text.
   A `MAX_TOKENS` finish returns a partial string that the pipeline ships as if
   complete. **This is the real defect** — the token budget is only the trigger.

**Confirm in one run:**

```python
print(response.candidates[0].finish_reason, response.usage_metadata.thoughts_token_count)
```

`MAX_TOKENS` plus a number near 900 confirms it.

**The fix, four parts:**

1. Raise the budget: `self._max_words * 4 + 2048`. Tokens are billed as
   generated, not as reserved, so headroom is free on stories that end early.
2. `thinking_config=ThinkingConfig(thinking_level=ThinkingLevel.MINIMAL)`.
   (Gemini 3-family models take `thinking_level`; 2.5 takes `thinking_budget`.
   Both fields exist in `google-genai` 1.75.0 — confirm which the model accepts.)
3. Raise on `finish_reason == MAX_TOKENS` so the job fails and the queue retries,
   rather than delivering a fragment to a child mid-sentence.
4. Log `tokens_out` alongside `tokens_thought` so the split is visible.

**Related bug found in the same place:** the cost calculation uses only
`candidates_token_count`. Thinking tokens are billed at the output rate but are
not in that field, so **per-job cost has been understated by the entire
reasoning spend** — currently most of it. Add `thoughts_token_count`.

---

## 5. Cost model

Trial credit: ₪881.64 remaining of ₪898, 75 days left.

| Item | Approx. monthly |
|---|---|
| GKE cluster management (zonal → free-tier credit) | $0 |
| Web node `e2-medium` + 100 GB boot disk | ~$35 |
| Worker node `e2-standard-2` + disk | ~$25 |
| Cloud NAT | ~$5 |
| **Total** | **~₪220–280 / month** |

The credit comfortably covers the trial with headroom. Worth stating precisely:
always-warm is **affordable, not free** — the credit is finite and time-boxed.

**Interview line worth keeping:** *"For this workload Cloud Run would be cheaper
and simpler — scale-to-zero with no metric lag. I built it on GKE because that
is the environment I am targeting."* That is a judgment answer, and judgment is
what distinguishes a senior response from a tutorial one.

---

## 6. Scope

**In:**
- Delete all AWS artifacts
- Delete KEDA; one warm worker on one non-Spot node pool
- Character registry (English, git-backed, ConfigMap) wired into both prompts
- `/family list`
- Language selection: English → Russian → Hebrew
- Fix the truncation bug and the cost undercount
- Single Helm chart with a `helm test` hook
- GitHub Actions + Workload Identity Federation
- kind for local cluster testing

**Explicitly out:**
- `/family add` and any runtime character storage (Firestore, GCS overlay)
- KEDA, scale-to-zero, Spot capacity
- Multi-cloud anything
- Chart-as-OCI-artifact (nice-to-have, not required)

---

## 7. Work order

Sequenced so the highest-value, lowest-risk work lands first.

| # | Step | Effort | Notes |
|---|---|---|---|
| 1 | ~~Commit and push `main`~~ | — | **Done** (`a7c2f29`) — git now matches production |
| 2 | Fix truncation + cost undercount | Small | Highest visible impact; makes the demo good |
| 3 | Direction A deletions | Small | Mostly `git rm`; 33 of 114 files |
| 4 | Character registry + `/family list` + language | Medium | The actual product work |
| 5 | GitHub Actions + WIF | Medium | Closes the biggest gap against the posting |
| 6 | kind + `helm test` | Small | Also becomes the CI smoke test |

Steps 2 and 3 are the best value per hour. Step 3 is nearly all deletion, so it
is fast and low-risk. Step 5 is the one that most changes how the repo reads
against the job description.

---

## 8. Talking points

Each of these is a decision with a measurement or a reason behind it:

- **"I removed KEDA after measuring it."** 4m43s activation on a 13.7s job,
  because Pub/Sub backlog metrics sample on a ~60s interval and can gap for
  minutes. The latency was the product.
- **"The registry is English because image models are."** A technical reason for
  what looks like a stylistic choice.
- **"Cost was under-reported and I found it."** Thinking tokens are billed but
  excluded from `candidates_token_count`.
- **"A truncated story is worse than no story."** Failing loudly and retrying
  beats delivering half a sentence to a child.
- **"Cloud Run would be cheaper; I chose GKE deliberately."** Knowing when your
  own choice is not the cheapest one.
- **"No keys anywhere."** Workload Identity in the cluster, WIF in CI.
- **"chat_id never reaches the logs."** PII discipline, relevant to GDPR/ISO.

---

## 9. Open items

- Verify the reference-image path for `gemini-2.5-flash-image` in this project's
  Model Garden before relying on character consistency.
- Confirm whether `gemini-3.5-flash` accepts `thinking_level` or
  `thinking_budget`.
- `ruff` is not installed in `app/.venv` despite being the documented linter.
- A stale git worktree remains at `.claude/worktrees/new-session-8240fc` and can
  be removed with `git worktree remove`.
