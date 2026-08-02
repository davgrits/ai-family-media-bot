# Infrastructure Design — IR-1 … IR-7

**Owner:** infra-architect
**Inputs:** `PRODUCT-DIRECTION.md`, `SPEC.md` §5 (IR-1…IR-7)
**Baseline read:** repo at `a7c2f29`, clean tree. All `path:line` citations below are against that commit.
**Status:** design only. No repo file was created, modified, or deleted producing this document.

Toolchain actually present on this machine (verified): `helm v4.2.2+gb05881c`, `terraform v1.15.7`, `docker`, **no `kind`**, **no `gh`**, **no `ruff`**.

---

## 0. Executive summary and the six things that need a decision before coding

| # | Finding | Impact |
|---|---|---|
| **A** | **`helm --atomic` does not exist in Helm 4.** IR-7 and PRODUCT-DIRECTION §3.7/§3.8 both specify `--atomic`. Helm 4.2.2 (the installed version, and the version `deploy/charts/family-media-bot/README.md:55` already targets via `--rollback-on-failure`) removed it. | Pipeline as literally specified fails. §6 resolves it. |
| **B** | **The lint claim in the direction doc is wrong in three ways.** There is no 105-char line at `pipeline.py:73` (that line is 62 chars). The 105-char line is `app/family_media_bot/pipeline.py:67`. There are **3** E501 violations, not 1. And with ruff 0.16.1's default rule set, `ruff check app/` reports **29 errors** and `ruff format --check` reports **10 files** needing reformatting. | First CI run is red on ~40 findings, not 1. §6.2 gives the containment strategy. |
| **C** | **The chart has no `Service`.** `helm test` against `/healthz` needs a stable in-cluster address. Nothing in `deploy/charts/family-media-bot/templates/` creates one. | IR-6 requires a new template, not just a test pod. |
| **D** | **Neither ConfigMap has a checksum annotation today.** Changing `LOG_LEVEL` in values and running `helm upgrade` currently does *not* restart the pods (`configmap.yaml` is consumed via `envFrom` at `deployment-web.yaml:35-37` / `deployment-worker.yaml:38-40`). This is a pre-existing defect that IR-4 forces us to fix anyway. | §4 fixes both ConfigMaps, not just the registry one. |
| **E** | **`pyyaml` is undeclared.** It is absent from both `app/requirements.txt` and `app/pyproject.toml`, and is present in `app/.venv` only *transitively* via `uvicorn[standard]`. `app/Dockerfile:16` installs `requirements.txt` and nothing else. The moment the IR-4 registry loader does `import yaml`, the image is one upstream dependency change away from `ModuleNotFoundError` at import → CrashLoopBackOff → an automatic rollback of a deploy whose own code is fine. | One line in two files (§1.2). Must land with or before the registry work. |
| **F** | **`metrics.py` is write-only.** `gke.tf:42-44` enables `monitoring_config.enable_components = ["SYSTEM_COMPONENTS"]` and nothing else — no managed Prometheus, no `PodMonitoring`, and the repo-root `observability/` directory holds nothing but a `.gitkeep`. The application exposes metrics that no collector scrapes, and there is no alerting of any kind. | §8 adds managed Prometheus, one `PodMonitoring`, and one alert policy. |

Security-sensitive items are concentrated in §7 (WIF attribute conditions; public GKE control-plane endpoint with no authorized networks). Destructive items are concentrated in §3.5. Findings **E** and **F** are the two defects this design fixes that no requirement explicitly asked for.

---

## 1. Deletion inventory (IR-1, IR-2)

### 1.1 Paths to delete outright

Tracked files (`git rm -r`):

```
infra/aws/                                            (17 tracked files, incl. bootstrap/README.md)
infra/.terraform.lock.hcl                             ← see note below
deploy/legacy/                                        (7 files under kustomize/aws/)
deploy/addons/                                        (all of it — see note below)
deploy/values/aws.yaml
deploy/charts/family-media-bot/templates/scaledobject.yaml
app/family_media_bot/adapters/queue_sqs.py
app/family_media_bot/adapters/storage_s3.py
app/family_media_bot/adapters/story_bedrock.py
app/family_media_bot/adapters/image_bedrock.py
.github/workflows/.gitkeep                            (replaced by real workflows in §6)
```

Two judgement calls in that list:

* **`infra/.terraform.lock.hcl` is a committed artifact of a mis-run `terraform init` at `infra/` root.** It locks `hashicorp/aws`, `cloudinit`, `null`, `time`, `tls` — providers no root under `infra/` declares once `infra/aws/` is gone. It is tracked (`git ls-files` lists it) and `infra/README.md:4` explicitly says "Never run Terraform from this directory". Delete it.
* **`deploy/addons/` in full, not just `deploy/addons/aws/`.** IR-1 names only `deploy/addons/aws/`, but IR-2 deletes KEDA, and `deploy/addons/gcp/keda-values.yaml` is *only* KEDA values (it is 38 lines of KEDA operator sizing and the `family-media-keda@…` GSA annotation at line 8). Once KEDA is gone the directory is empty. Delete the directory.

Untracked local strays to remove from the working copy (gitignored, so `git rm` is not involved — plain `rm`):

```
terraform.tfstate            repo root, 181 bytes, 0 resources, serial 1 — an empty local
                             state from a `terraform` invocation in the wrong directory
infra/.terraform/            provider cache from the same mis-run
infra/gcp/tfplan             41 KB saved plan on disk (gitignored, but it embeds
                             infrastructure metadata; do not leave it lying around)
infra/.DS_Store, infra/gcp/.DS_Store, .DS_Store
```

`infra/gcp/terraform.tfvars` and `infra/gcp/.terraform/` stay — they are correct working files.

### 1.2 Exact edits to surviving files

This is the exhaustive list. Anything missed here breaks `terraform validate`, `helm template`, or `python -c "import family_media_bot"`.

#### Terraform — `infra/gcp/`

**`infra/gcp/identity.tf`**

| Lines | Action |
|---|---|
| 1–2 | Rewrite the header comment. Currently `# Separate Google service accounts mirror AWS IRSA: … KEDA gets read-only subscription visibility.` Replace with a GCP-only statement. |
| 13–16 | **Delete** `resource "google_service_account" "keda"`. |
| 62–66 | **Delete** `resource "google_pubsub_subscription_iam_member" "keda_view"`. |
| 76–82 | **Delete** the KEDA comment block and `resource "google_project_iam_member" "keda_monitoring_viewer"`. |
| — | **Add** the CI identities from §5 here or in a new `infra/gcp/cicd.tf` (recommended: new file, so IR-5 is one reviewable unit). |

**`infra/gcp/workload_identity.tf`**

Line 4 references `google_service_account.keda.email`. Deleting the SA without deleting this line is a `terraform validate` failure. The whole `for_each` map collapses to one entry; keep the map (it still reads well and leaves room for a second identity) or flatten it. Recommended replacement for lines 1–19:

```hcl
locals {
  workload_identities = {
    app = {
      namespace       = "app"
      service_account = "family-media-bot"
      gsa             = google_service_account.app.email
    }
  }
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each = local.workload_identities

  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.value.gsa}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value.namespace}/${each.value.service_account}]"

  # The <project>.svc.id.goog pool is created with the GKE cluster. Without
  # this dependency, a first apply can race and IAM returns "Identity Pool does
  # not exist" even though the configuration is valid.
  depends_on = [google_container_cluster.main]
}
```

**`infra/gcp/outputs.tf`**

| Lines | Action |
|---|---|
| 32 | `description = "Pub/Sub subscription consumed by workers and observed by KEDA."` → drop `and observed by KEDA`. |
| 46–49 | **Delete** `output "keda_google_service_account_email"` — it dereferences the deleted SA. |
| — | **Add** the three CI outputs from §5.5. |

**`infra/gcp/variables.tf`**

| Lines | Action |
|---|---|
| 47–51 | `web_machine_type` → rename to `node_machine_type`, default `e2-standard-2`, new description (see §3). |
| 53–62 | **Delete** `web_max_nodes` (no autoscaling in the consolidated pool — §3.3). |
| 64–68 | **Delete** `worker_machine_type`. |
| 70–79 | **Delete** `worker_max_nodes`. |
| — | **Add** `node_disk_size_gb`, `node_disk_type`, and the four `github_*` variables from §5. |

**`infra/gcp/terraform.tfvars.example`**

Lines 6–9 mention KEDA, `web_machine_type`, `web_max_nodes`. Rewrite (full replacement in §3.2).

**`infra/gcp/gke.tf`** — full rewrite of lines 54–125, see §3.2.

**`infra/gcp/apis.tf`** — add `iam.googleapis.com`, `sts.googleapis.com`, `cloudresourcemanager.googleapis.com` to `local.required_apis` (needed by IR-5; `cloudresourcemanager` is already implicitly relied on by `data "google_project" "current"` at `pubsub.tf:35`).

**`infra/gcp/README.md`** — lines 7–8 ("a warm on-demand web node pool and a tainted Spot worker node pool that can scale to zero"), line 12 ("app and KEDA are distinct identities"), lines 15–16 (says Gemini **2.5** Flash for stories; `deploy/values/gcp.yaml:27` and `config.py:69` both say `gemini-3.5-flash` — stale, fix while here), line 76 ("shared AWS/GCP Helm chart"), line 83 ("install KEDA with its separate Workload Identity").

**`infra/README.md`** — line 8 (AWS row of the table), lines 11–14 (the "AWS root is retained" paragraph). The file reduces to a two-paragraph description of a single root; consider folding it into `infra/gcp/README.md` and deleting it.

#### Helm — `deploy/charts/family-media-bot/`

**`templates/configmap.yaml`** → rename to `templates/configmap-env.yaml`; delete lines 25–31 (the `{{- else if eq .Values.cloud.provider "aws" }}` branch) and line 15 / line 32 (the `{{- if eq … "gcp" }}` / `{{- end }}` wrapper — the GCP keys become unconditional).

**`templates/deployment-worker.yaml`** — line 10–12: the comment "KEDA takes ownership immediately after creation…" and `replicas: {{ .Values.worker.minReplicaCount }}` → `replicas: {{ .Values.worker.replicaCount }}`. Lines 26–29: delete `nodeSelector` and `tolerations` (IR-3).

**`templates/deployment-web.yaml`** — lines 26–27: delete `nodeSelector` (IR-3).

**`templates/NOTES.txt`** — line 1 (`{{ .Values.cloud.provider | upper }}`), lines 6–8 (the KEDA/ScaledObject block). Full rewrite in §5 of the chart section.

**`values.yaml`** — line 1–2 (comment naming `deploy/values/aws.yaml`), lines 3–4 (`cloud.provider`), lines 30–31 + 45–51 (`nodeSelector`, `tolerations`), lines 40–44 (KEDA replica envelope). Full replacement in §5.2.

**`values.schema.json`** — 19 AWS/KEDA hits. Full replacement in §5.3.

**`Chart.yaml`** — line 3 `description: Multi-cloud Kubernetes deployment…`; bump `version: 0.2.0` → `0.3.0` (breaking values change).

**`README.md` (chart)** — lines 1–6 (title + "canonical application chart for EKS and GKE"), lines 24–25, lines 29–45 (KEDA prerequisites), lines 49–61 (the two-provider deploy block), line 70 (`scaledobjects,hpa`), lines 74–75 ("the worker is `0/0`, and the ScaledObject is ready but inactive"). This is effectively a rewrite.

**`deploy/README.md`** — lines 6–12 (the tree), line 17 ("the KEDA scaler"), line 19 ("both deployment paths"). Rewrite.

**`deploy/values/gcp.yaml`** → `git mv` to `deploy/values/prod.yaml`; delete lines 1–2 (`cloud.provider`) and **line 6 (`tag: streaming-pubsub-20260719-111322`)** — see §5.4.

#### Application — `app/`

IR-1 assigns the adapters, config fields, and dependencies to this stream. The four adapter files are deleted (§1.1); these are the references that would otherwise break the import graph or leave dead branches:

**`app/family_media_bot/factory.py`** — delete lines 20–23 (`sqs` branch), 36–39 (`bedrock` story branch), 52–55 (`bedrock` image branch), 68–71 (`s3` branch). Each is a self-contained `if` block with a deferred import; removing them leaves `inmemory|pubsub`, `fake|vertex`, `fake|vertex`, `local|gcs`.

**`app/family_media_bot/config.py`** — delete lines 49–57 (`aws_region`, `s3_bucket`, `sqs_queue_url`, `bedrock_text_model_id`, `bedrock_image_model_id`, `bedrock_image_region` and their comment). Narrow the `Literal`s at lines 31–34:
```python
queue: Literal["inmemory", "pubsub"] = "inmemory"
story_provider: Literal["fake", "vertex"] = "fake"
image_provider: Literal["fake", "vertex"] = "fake"
storage: Literal["local", "gcs"] = "local"
```
Note the interaction with `model_config` `extra="ignore"` (line 17): a stale `SQS_QUEUE_URL` in someone's `.env` is silently ignored rather than raising. Good — no migration hazard.

**`app/requirements.txt`** — delete lines 10–13 (`boto3`, `botocore[crt]` and their comments). This removes ~60 MB from the image and is worth mentioning in the PR body.

**In the same edit, ADD `pyyaml>=6.0,<7.0`** — see finding **E** in §0. This is not an AWS-removal item; it is a latent CrashLoopBackOff that the registry work (IR-4) will trigger, and this is the file that fixes it.

**`app/pyproject.toml`** — line 8 (description says "AWS behind interfaces"), line 19 (`"boto3>=1.34,<2.0"`). Add `"pyyaml>=6.0,<7.0"` so the two dependency lists stay mirrored, as line 1 of `requirements.txt` claims they are.

**`app/tests/test_queue_adapters.py`** — lines 7, 31–59: the entire `SqsQueueTests` class and its import. The file's remaining `InMemoryQueue` tests stay.

**`.env.example`** — lines 4, 9–10, 22, 24–25, 27, 29 (comment text and the `SQS_QUEUE_URL=` key), and the whole AWS block at lines 50–58.

Docstring/comment-only references (no functional impact, but they defeat the §10 acceptance grep):

| File:line | Text |
|---|---|
| `app/family_media_bot/__init__.py:5-6` | "touches AWS … swaps to AWS later" |
| `app/family_media_bot/adapters/__init__.py:3-4` | "Dev impls … no AWS; the Bedrock/SQS/S3 impls…" |
| `app/family_media_bot/adapters/queue_inmemory.py:4` | "SqsQueue replaces it for the cross-process split." |
| `app/family_media_bot/adapters/queue_pubsub.py:262` | "KEDA reads that authoritative metric for scaling." |
| `app/family_media_bot/adapters/story_common.py:1` | "Helpers shared by the Bedrock and Vertex story adapters." |
| `app/family_media_bot/app.py:4, 91` | "MUST NOT touch Bedrock/SQS/S3" → "MUST NOT touch Pub/Sub, GCS, or Vertex AI" (keep the *rule*, change the nouns — it is contract #3) |
| `app/family_media_bot/metrics.py:41, 50` | "Per-job **Bedrock** cost" / "Cumulative **Bedrock** cost" — these are Prometheus metric *help* strings, safe to change |
| `app/family_media_bot/ports/__init__.py:1` | "the interfaces that keep AWS out of the app logic" |
| `app/family_media_bot/ports/image.py:3`, `ports/story.py:3` | "Prod impl: BedrockImageProvider / BedrockStoryProvider" |
| `app/family_media_bot/prompts.py:3, 14` | "(fake or Bedrock)", "Used as the system prompt by BedrockStoryProvider" |

`app/family_media_bot/adapters/story_common.py` keeps its code (it is shared helper logic Vertex still uses) — only the docstring changes.

#### Root docs

**`AGENTS.md`** — lines 7, 8, 19, 43. Line 19 in particular tells agents to "Run equivalent commands from `infra/aws/`". Also add the CI commands and the ruff pin from §6.

**`README.md`** — 58 hits. Structural, not line-by-line: the Mermaid diagram (lines ~38–92) loses the entire `subgraph AWS` block, the `AKEDA`/`GKEDA` nodes, and the dashed edges at 58–59 and 64–65 and 73–76 plus the `classDef available` styling at 87–88; the adapter table at 103–107 loses its `AWS` column; the design-decision table loses rows at 121, 124, 125; the repo layout at 154–159 loses three lines; the whole `## AWS demo target` section at 245–~290 goes; `## Configuration` 301–304; `## Infrastructure` 316, 323, 325–326. Add a `## CI/CD` section for §6.

**`ROADMAP.md`** — 18 hits: line 5, the `### AWS — defined, not currently deployed` section (38–46), and the KEDA lines at 35, 53, 54, 65, 67, 82, 90, 95, 101. Line 73 ("Push to Artifact Registry for GCP and ECR for AWS using OIDC") becomes a checked item once §6 lands.

**`Makefile`** — `helm-lint-aws` (30–35) and `helm-lint-addons` (44–51) targets and their `.PHONY` entry (line 1); line 12 and 14 of `help`. New targets in §6.4.

**`docs/`** — SPEC §10 explicitly permits historical references in `docs/`. `docs/api-contract.md` (10 hits), `docs/gcp-interview-runbook.md` (17), `docs/prompt-02-infra-terraform.md` (21) can stay as history. **Exception:** `docs/gcp-interview-runbook.md` is an *operational* runbook whose KEDA verification steps will be wrong after this change. Either update it or move it to `docs/history/`.

### 1.3 Verification that the deletion is complete

```bash
git grep -i -E 'aws|bedrock|sqs|boto|keda' -- . ':(exclude)docs'          # must be empty
terraform -chdir=infra/gcp fmt -check -recursive && terraform -chdir=infra/gcp validate
helm lint deploy/charts/family-media-bot -f deploy/values/prod.yaml --set image.tag=ci --strict
helm template x deploy/charts/family-media-bot -n app -f deploy/values/prod.yaml --set image.tag=ci
cd app && python -c "import family_media_bot.factory, family_media_bot.app"
```

Baseline for comparison, measured on `a7c2f29`: `terraform -chdir=infra/gcp fmt -check -recursive` exits 0; `helm lint … -f deploy/values/gcp.yaml` passes with one INFO (missing icon); `helm template` renders 6 objects (ServiceAccount, ConfigMap, 2 Deployments, ScaledObject, TriggerAuthentication).

---

## 2. Terraform state and safety (moved forward — it constrains everything else)

Covered here rather than at the end because §3's node-pool change is the only destructive item in the design and you need the state story first.

### 2.1 Backend

`infra/gcp/backend.tf:8-12` is a partial GCS backend: `prefix = "ai-family-media-bot/gcp"`, bucket supplied at `init`. The actual bucket, from the local backend cache at `infra/gcp/.terraform/terraform.tfstate`, is **`davidg-tfstate-bucket`**.

Assessment:

* **Correct pattern.** Partial config keeps the bucket name out of git while pinning the prefix so two stacks cannot collide. `infra/gcp/README.md:27-41` documents the bootstrap and includes `gcloud storage buckets update … --versioning`.
* **Unverified:** whether versioning is actually on for `davidg-tfstate-bucket`. This is the single cheapest insurance policy in the whole project. Verify with `gcloud storage buckets describe gs://davidg-tfstate-bucket --format='value(versioning.enabled)'` before the first apply of this redesign. If it returns anything but `True`, turn it on first.
* **Locking:** the GCS backend uses object generation preconditions. That is real locking, but it is per-operation. CI must not race a human. See the `concurrency:` block in §6.1.
* **Recommended addition to the bootstrap docs (not Terraform-managed, by design — the state bucket must not be in its own state):** a soft-delete / retention policy of 7 days and `--uniform-bucket-level-access` (already documented).

### 2.2 CI does not run `terraform apply`. Deliberately.

The pipeline in §6 runs `terraform plan` on PRs and **nothing** on main. Justification, in the order I would say it out loud:

1. The blast radius of a bad apply on a single-cluster personal project is the entire demo, mid-trial, with 75 days of credit on the clock.
2. It lets the CI Terraform identity be strictly read-only, which removes the most valuable target in the whole WIF design. A compromised workflow can read plan output; it cannot create a service account or a VM.
3. Infrastructure here changes a handful of times. The application changes constantly. Automating the thing that changes constantly and reviewing the thing that changes rarely is the correct split, and saying so is a stronger answer than "everything is GitOps".
4. The plan comment still gives the review artifact that matters.

If asked "why not Atlantis / TFC / an apply job gated on environment protection rules" — that is the right answer at team scale, and the honest reason it is not here is that a second human approver does not exist.

### 2.3 `deletion_protection`

`variables.tf:87-91` defaults `deletion_protection = true` and `gke.tf:46` wires it to the cluster. What it does and does not cover:

| Resource | Protected? |
|---|---|
| `google_container_cluster.main` | **Yes** — `terraform destroy` errors out until the flag is flipped and applied. |
| `google_container_node_pool.*` | **No.** Node pools have no deletion protection. §3's consolidation deletes two of them. |
| `google_storage_bucket.media` | Partially — `force_destroy = false` (`storage.tf:6`) blocks destroy of a *non-empty* bucket. An empty one is destroyed silently. |
| `google_pubsub_topic.jobs` / `.jobs_dlq`, subscriptions | **No.** Destroying `jobs_dlq` discards up to 14 days of dead letters. |
| `google_artifact_registry_repository.app` | **No.** Destroying it deletes every pushed image, including the one the live cluster is running. |
| `google_project_service.required` | `disable_on_destroy = false` (`apis.tf:20`) — good, destroy will not disable APIs out from under other things. |

**Recommendation:** add `lifecycle { prevent_destroy = true }` to `google_storage_bucket.media`, `google_pubsub_topic.jobs_dlq`, and `google_artifact_registry_repository.app`. Three lines each, and `prevent_destroy` is a plan-time error rather than an apply-time one, so it fails fast in the PR plan.

One caveat worth knowing: GCS bucket names are reserved for ~30 days after deletion, so `google_storage_bucket.media` (name is deterministic: `${project_id}-${project_name}-media`, `storage.tf:2`) cannot be recreated immediately if it is ever destroyed.

### 2.4 What a fresh `terraform apply` does to the live cluster

Three scenarios, in decreasing order of likelihood:

1. **Correct backend, correct state.** The only diffs are the ones this design introduces. Of those, exactly one class is destructive: the node pools (§3.5). Everything else is additive (WIF pool/provider/SAs) or removal of unused IAM (the KEDA SA and its two grants).
2. **Correct backend, but `terraform init` run without `-backend-config="bucket=…"`.** Terraform prompts for the bucket. If someone types the wrong one, Terraform sees an empty state and plans to **create everything**. It will not silently destroy the live environment — instead the apply fails partway with `409 ALREADY_EXISTS` on the VPC, the cluster, and the bucket, leaving a half-populated state file in the wrong bucket. Recoverable but ugly. Mitigation: the `make tf-init` target in §6.4 hardcodes the flag, and the CI job reads it from `vars.TF_STATE_BUCKET`.
3. **Someone runs `terraform` from `/Users/davgrits/repos/ai-family-media-bot` or from `infra/`.** This has already happened at least twice — the empty `terraform.tfstate` at the repo root and `infra/.terraform/` are the evidence. With no `.tf` files present it is a no-op that writes an empty state, which is what those artifacts are. Harmless, but delete them (§1.1) so the next person does not think they mean something.

---

## 3. Node pool consolidation (IR-3)

### 3.1 Sizing — the actual arithmetic

Application demand, read from `deploy/charts/family-media-bot/values.yaml`:

| Workload | CPU request | Memory request | Source |
|---|---|---|---|
| web × 1 | `50m` | `128Mi` | `values.yaml:33-35` |
| worker × 1 | `250m` | `256Mi` | `values.yaml:54-56` |
| **App subtotal** | **300m** | **384Mi** | |
| worker rolling-update surge (+1 pod, `maxSurge: 1`) | +250m | +256Mi | §5.2 |
| `helm test` pod | +10m | +32Mi | §5.5 |
| **App peak** | **~560m** | **~672Mi** | |

GKE overhead. Two distinct populations, and conflating them is where sizing goes wrong:

*Per-node DaemonSets* (paid once per node):

| Pod | CPU |
|---|---|
| `kube-proxy` | 100m |
| `fluentbit-gke` | 100m |
| `gke-metadata-server` (mandatory — `workload_metadata_config.mode = "GKE_METADATA"`, `gke.tf:70-72`) | 50m |
| `gke-metrics-agent` | ~6m |
| `pdcsi-node`, `ip-masq-agent`, `netd` | ~25m |
| `collector` (managed Prometheus, added in §8.1) | ~5m |
| **subtotal** | **~285m** |

*Cluster singletons* (there is only one node, so all of them land here):

| Pod | CPU |
|---|---|
| `kube-dns` × 2 | ~520m |
| `metrics-server` + nanny | ~48m |
| `kube-dns-autoscaler` | 20m |
| `konnectivity-agent` (+ autoscaler) | ~15m |
| `l7-default-backend`, `event-exporter` | ~15m |
| **subtotal** | **~620m** |

**System total ≈ 900m CPU, ~1 GiB memory requested** on a single-node zonal cluster. Verify on the live cluster with:

```bash
kubectl get pods -A -o json | jq -r '
  [.items[].spec.containers[].resources.requests.cpu // "0"] | @csv'   # then sum
kubectl describe node | sed -n '/Allocatable/,/Allocated resources/p'
```

Candidates against **~1460m CPU peak** (900m system + 560m app peak):

| Machine type | vCPU / RAM | GKE allocatable CPU | Fits? | Node $/mo (us-central1, on-demand) |
|---|---|---|---|---|
| `e2-medium` (current web pool, `variables.tf:50`) | 2 shared / 4 GB | **940m** (shared-core reservation) | **No.** System alone is ~96% of allocatable. | $24.46 |
| 2 × `e2-medium` | — | 1880m raw, minus a *second* copy of the 280m DaemonSet set → ~1320m usable | Marginal, and doubles disk + adds cross-node scheduling | $48.92 |
| **`e2-standard-2`** | **2 dedicated / 8 GB** | **1930m**, ~6.0 GiB | **Yes** — ~470m headroom at peak | **$48.91** |
| `e2-custom-2-4096` | 2 dedicated / 4 GB | 1930m, ~2.7 GiB | Yes | ~$42.5 |
| `e2-standard-4` | 4 / 16 GB | 3890m | Yes, 2.6× the need | $97.82 |

**Recommendation: one node pool, fixed at 1 × `e2-standard-2`, 50 GB `pd-balanced` boot disk.**

Why not `e2-medium`, which is what the current always-on pool uses: the existing setup only works because the worker lives on a *different* pool and KEDA was hand-tuned down to 20m requests (`deploy/addons/gcp/keda-values.yaml:17-24`). Collapsing to one pool moves 300m of app requests onto a node with 40m of spare allocatable CPU. It would not schedule.

Why not `e2-custom-2-4096`, which is $6/month cheaper: a custom machine type in a portfolio repo reads as premature optimisation, and 4 GB leaves 2.7 GiB allocatable — enough today, but the `helm test` pod, a `kubectl debug` ephemeral container, and any future sidecar all come out of that. $6/month is not worth explaining. Worth *mentioning* in interview as the cost-optimal option you costed and rejected.

Why 50 GB and not the 100 GB default: the boot disk is unset in `gke.tf` today, so GKE defaults to 100 GB `pd-balanced` at $0.10/GB-month. The COS node image plus the app image (python:3.11-slim + deps, ~1 GB, and ~60 MB smaller once boto3 goes) uses well under 20 GB. 50 GB halves the disk line for zero risk, and 50 GB `pd-balanced` still provisions 3,000 baseline IOPS.

### 3.2 The concrete `gke.tf` change

Replace `infra/gcp/gke.tf:54-125` (both pools) with a single pool:

```hcl
# One pool. Both deployments and every GKE system pod share it.
#
# Sizing (see docs): ~900m CPU of GKE system requests on a single-node zonal
# cluster (per-node DaemonSets plus every cluster singleton), 300m for the web
# and worker pods, 250m of headroom for the worker's rolling-update surge.
# e2-standard-2 allocates 1930m; e2-medium allocates 940m and does not fit.
#
# No autoscaling: SPEC §2 rules out cluster-autoscaler, and a fixed node count
# makes the monthly bill exactly predictable against a finite trial credit.
# upgrade_settings still gives GKE a temporary surge node during node upgrades.
resource "google_container_node_pool" "main" {
  name     = "main"
  location = var.cluster_location
  cluster  = google_container_cluster.main.name

  node_count = 1

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    strategy        = "SURGE"
    max_surge       = 1
    max_unavailable = 0
  }

  node_config {
    machine_type    = var.node_machine_type
    disk_size_gb    = var.node_disk_size_gb
    disk_type       = var.node_disk_type
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    # No taint, no role label: there is one pool and nothing selects on it.
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }

  depends_on = [
    google_project_iam_member.nodes_artifact_reader,
    google_project_iam_member.nodes_log_writer,
    google_project_iam_member.nodes_metric_writer,
  ]
}
```

`variables.tf` replacement for lines 47–79:

```hcl
variable "node_machine_type" {
  description = <<-EOT
    Machine type for the single node pool. Must allocate enough CPU for GKE
    system pods (~900m on a one-node zonal cluster), both application
    deployments (300m), and the worker's rolling-update surge (250m).
    e2-standard-2 allocates 1930m. e2-medium allocates only 940m and does not fit.
  EOT
  type        = string
  default     = "e2-standard-2"
}

variable "node_disk_size_gb" {
  description = "Boot disk per node. The COS image plus the application image use under 20 GB; 50 halves the default 100 GB disk cost."
  type        = number
  default     = 50

  validation {
    condition     = var.node_disk_size_gb >= 30
    error_message = "node_disk_size_gb must be at least 30 GB to leave room for the node image and pulled layers."
  }
}

variable "node_disk_type" {
  description = "Boot disk type. pd-balanced is the GKE default and gives 3,000 baseline IOPS at 50 GB."
  type        = string
  default     = "pd-balanced"
}
```

`terraform.tfvars.example` replacement for lines 6–9:

```hcl
# One e2-standard-2 holds GKE's system pods plus the web and worker
# deployments with headroom for a rolling update. e2-medium does not.
node_machine_type = "e2-standard-2"
node_disk_size_gb = 50
```

### 3.3 On dropping autoscaling entirely

The current pools both have `autoscaling` blocks (`gke.tf:75-78`, `115-118`). The replacement has none. SPEC §2 lists "cluster-autoscaler" as out of scope, and with `replicas: 1` on both deployments there is no workload that could ever demand a second node. A `min=1, max=1` autoscaler is a no-op with extra API surface; `min=1, max=2` is a silent doubling of the bill the first time something requests too much CPU.

`upgrade_settings { max_surge = 1 }` still lets GKE create a temporary extra node during an auto-upgrade, so node upgrades do not mean an outage. That is the one case autoscaling would otherwise have covered.

### 3.4 What this means for scheduling

Both deployments lose their `nodeSelector` (`values.yaml:30-31` for web, `45-46` for worker) and the worker loses its `tolerations` (`values.yaml:47-51`). The new pool has neither the `role` label nor the `workload=jobs` taint.

**Ordering hazard:** if the Helm change lands before the new pool exists, the pods still select `role: web` / `role: worker` and are fine. If the *Terraform* change lands first and the new pool has no `role` label, every pod goes `Pending` with `didn't match Pod's node affinity/selector`. Sequence in §3.5 avoids this.

### 3.5 Applying it — is this destructive?

**Yes, partly. Read the plan before applying.**

| Object | Plan verb | Live impact |
|---|---|---|
| `google_container_node_pool.workers` | **destroy** | None in practice. `node_count = 0` / `autoscaling.min_node_count = 0` (`gke.tf:92, 116`) means the pool is empty at idle. Confirm with `kubectl get nodes -l role=worker` first — if KEDA has scaled it up, wait or drain. |
| `google_container_node_pool.web` | **destroy** | **This is the disruptive one.** It hosts the web pod, the worker pod after the Helm change, KEDA, and every GKE singleton. Deleting it deletes the node. |
| `google_container_node_pool.main` | **create** | New node, ~3 min to `Ready`. |
| `google_container_cluster.main` | **no change** | `deletion_protection` is irrelevant here — it does not protect pools. |

Note that even *renaming* the resource address (`web` → `main`) with a `moved` block would not save you: `google_container_node_pool.name` is `ForceNew`, so any name change replaces the pool regardless of state surgery. Do not attempt it.

**Recommended zero-downtime sequence.** Five steps, and the KEDA ordering in steps 2–4 is the part people get wrong:

```bash
# 1. Terraform apply #1 — ADD the main pool, leave web/workers in place.
#    (Comment out nothing; just add the new resource block first.)
terraform -chdir=infra/gcp apply     # +1 node, ~$0.07/hour extra, ~15 min of overlap

# 2. Helm upgrade — remove nodeSelectors/tolerations, worker replicas 1,
#    ScaledObject template deleted. Pods become schedulable on either pool.
helm upgrade --install family-media-bot deploy/charts/family-media-bot \
  -n app -f deploy/values/prod.yaml --set image.tag=<sha> \
  --rollback-on-failure --wait --timeout 5m

# 3. Drain the old node so everything lands on main.
kubectl cordon <old-web-node>
kubectl drain <old-web-node> --ignore-daemonsets --delete-emptydir-data
kubectl get pods -A -o wide     # confirm nothing is left on the old node

# 4. NOW uninstall KEDA. Not before step 2.
helm uninstall keda -n keda && kubectl delete namespace keda

# 5. Terraform apply #2 — delete the web and workers pools, the KEDA GSA,
#    its Workload Identity binding, and its two IAM grants.
terraform -chdir=infra/gcp apply
```

**Why KEDA must be uninstalled after the Helm upgrade, not before.** `helm uninstall keda` removes the `scaledobjects.keda.sh` and `triggerauthentications.keda.sh` CRDs. If the CRDs are gone when you run the app-chart upgrade that *deletes* the ScaledObject and TriggerAuthentication (`templates/scaledobject.yaml`), Helm's diff cannot resolve the kind and the upgrade fails with `unable to build kubernetes objects … no matches for kind "ScaledObject"`. You then have to `helm upgrade --force` or hand-edit the release secret. Uninstall KEDA last and the problem never exists.

If you would rather take the ~4-minute outage: skip steps 1 and 3, do one apply. On a bedtime-story bot with one family as users, that is a defensible choice — but say out loud that you chose it, rather than discovering it.

### 3.6 Cost

us-central1, on-demand, 730 h/month, USD:

| Item | Rate | Monthly |
|---|---|---|
| GKE zonal cluster management | $0.10/h, fully offset by the one-free-zonal-cluster credit | **$0.00** |
| 1 × `e2-standard-2` | $0.067006/h | $48.91 |
| 50 GB `pd-balanced` boot disk | $0.10/GB-mo | $5.00 |
| Cloud NAT (1 VM + external IP + a few GB processed) | $0.0014/VM-h + IP + $0.045/GB | ~$5.00 |
| Artifact Registry (first 0.5 GB free, then $0.10/GB) | ~2 GB of image history | ~$0.20 |
| GCS media (30-day lifecycle, `storage.tf:8-15`) | | <$0.10 |
| Pub/Sub | first 10 GiB/month free | $0.00 |
| Cloud Logging | first 50 GiB/project/month free | $0.00 |
| Vertex AI per-request | usage, not infrastructure | excluded |
| **Total** | | **≈ $59 / month ≈ ₪215** |

Against the two-pool baseline in PRODUCT-DIRECTION §5 (~$65/mo, ₪220–280): consolidation is **roughly cost-neutral, slightly cheaper**, and it buys a warm worker. The saving comes from deleting the second boot disk and the second node's management overhead; the extra spend comes from `e2-standard-2` over `e2-medium`. They very nearly cancel.

Over the remaining 75 days: ≈ **₪540 of ₪881**, leaving ~₪340 for Vertex AI calls and overruns. Comfortable, but note that Vertex AI charges land on top of this: at $0.039/image (`PRODUCT-DIRECTION.md` §3.6) plus flash tokens, ~₪340 is thousands of stories. Not the binding constraint.

Two levers if the trial gets tight: drop the node to `e2-custom-2-4096` (−$6/mo), or delete the Cloud NAT and give nodes public IPs (−$5/mo, but that gives up private nodes, which is one of the better things in this repo — do not).

---

## 4. Registry ConfigMap (IR-4)

### 4.1 Where the file lives

**`deploy/charts/family-media-bot/files/characters.yaml`**, read with `.Files.Get`.

This is forced by a Helm constraint worth stating plainly: `.Files` is sandboxed to the chart directory. `.Files.Get "../../../config/characters.yaml"` returns an empty string, silently. So either the registry lives inside the chart, or it does not reach the pods via `.Files` at all.

Options considered:

| Option | Verdict |
|---|---|
| **`.Files.Get "files/characters.yaml"`** | **Chosen.** Chart is self-contained: `helm install ./chart` from a tarball produces a working release. `helm package` includes `files/` automatically. One key, one path, matches the app-architect's "read one file at startup". |
| `(.Files.Glob "files/registry/*.yaml").AsConfig` | Rejected. Produces a ConfigMap keyed by filename, which is elegant if the app scans a directory — but the app reads exactly one path. Glob would silently create keys nothing consumes, and a typo'd filename becomes an ignored file rather than an error. Revisit only if the registry is ever split per family. |
| `registry.characters` as a values blob | Rejected. Puts a multi-hundred-line YAML document inside `deploy/values/prod.yaml`, where it is nested-indented, unlintable by any YAML schema tool, and impossible to diff sanely in a PR. |
| `--set-file registry.contents=config/characters.yaml`, file outside the chart | Rejected as the primary path. It does let the file live at repo root, but it makes the chart non-self-contained and every `helm template`/`lint`/`install` invocation in the Makefile, the CI PR job, the CI main job, and the local kind loop must remember the flag or render an empty registry. Four places to forget. |
| Symlink `chart/files/characters.yaml → ../../../config/characters.yaml` | Rejected. Helm's chart loader's symlink behaviour is version-dependent and this cannot be verified here (no kind cluster available). Do not build a delivery mechanism on unverified loader behaviour. |

Consequence to accept and state: a data file lives in the chart. That is exactly what Helm's `files/` convention exists for, so it is idiomatic, not a smell — but it does mean the app's local-dev default path is awkward. Handle it in the Makefile, not by duplicating the file:

```make
REGISTRY := ../deploy/charts/family-media-bot/files/characters.yaml

run:
	cd $(APP_DIR) && CHARACTER_REGISTRY_PATH=$(REGISTRY) ./.venv/bin/python -m family_media_bot
```

### 4.2 Contract with the app-architect

| Item | Value |
|---|---|
| In-pod path | `/etc/family-media-bot/registry/characters.yaml` |
| Env var | `CHARACTER_REGISTRY_PATH` |
| Settings field | `character_registry_path: str = "./characters.yaml"` in `config.py` |
| Read timing | **At startup**, once, in `create_app()`'s lifespan (alongside the `factory.build_*` calls at `app.py:40-44`) |
| Failure mode | A missing or unparseable registry must fail startup loudly. The readiness probe then keeps the pod out of service and `--rollback-on-failure` reverts the release. Do **not** fall back to an empty registry. |
| Delivered to | **Both** deployments. Web needs it if `compose_prompt()` stays at enqueue time (`app.py:142`, `poller.py:79`); worker needs it if resolution moves. Mounting in both costs nothing and decouples this design from that open question in SPEC §8. |

### 4.3 Templates

**`templates/configmap-registry.yaml`** (new):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "family-media-bot.name" . }}-registry
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "family-media-bot.labels" . | nindent 4 }}
data:
  # The registry is a chart data file, not a value: it is reviewed as YAML in a
  # pull request, and .Files keeps it inside the chart so `helm install ./chart`
  # from a packaged tarball still works.
  characters.yaml: |
{{ .Files.Get "files/characters.yaml" | indent 4 }}
```

Note `{{ … }}` with a leading `indent 4`, not `{{- … | nindent 4 }}` — a block scalar needs every line indented, and `nindent` only prefixes the first.

**`files/characters.yaml`** (new, seeded from PRODUCT-DIRECTION §3.4):

```yaml
# Family character registry. English by design: image models are trained
# overwhelmingly on English captions, so an English `appearance` string yields a
# markedly more repeatable illustration than a translated one.
#
# Read-only. Edited by pull request; there is no write path from Telegram.
# Text descriptions only — never photographs of real children.
characters:
  - id: mila
    appearance: "5-year-old girl, curly red hair, freckles, green cloak"
    traits: "curious, brave, loves animals"
    names:
      en: Mila
      ru: Мила
      he: מילה
```

**Volume wiring**, identical in `deployment-web.yaml` and `deployment-worker.yaml`:

```yaml
    spec:
      volumes:
        - name: registry
          configMap:
            name: {{ include "family-media-bot.name" . }}-registry
      containers:
        - name: web            # or worker
          volumeMounts:
            - name: registry
              mountPath: {{ .Values.registry.mountPath }}   # /etc/family-media-bot/registry
              readOnly: true
          env:
            - name: CHARACTER_REGISTRY_PATH
              value: {{ printf "%s/characters.yaml" .Values.registry.mountPath | quote }}
```

**No `subPath`.** With `subPath` the kubelet never propagates ConfigMap updates to the file; without it, the kubelet refreshes the projected file within roughly a minute. The app reads at startup so the refresh alone would not take effect — but avoiding `subPath` keeps the door open for a future file-watch reload, and costs nothing here since the ConfigMap has exactly one key.

### 4.4 What triggers the restart

Pod-template annotations on **both** deployments:

```yaml
  template:
    metadata:
      annotations:
        checksum/registry: {{ include (print $.Template.BasePath "/configmap-registry.yaml") . | sha256sum }}
        checksum/config:   {{ include (print $.Template.BasePath "/configmap-env.yaml") . | sha256sum }}
```

Mechanics: the annotation is part of the pod template spec, so changing it changes the template hash, so the Deployment controller performs a rollout. Hashing the *rendered template* rather than `.Files.Get` directly also catches changes to the ConfigMap's name, labels, or namespace, not just its payload.

`checksum/config` is the fix for finding **D** in §0 — it does not exist today, so editing `LOG_LEVEL` in `deploy/values/gcp.yaml` and upgrading currently changes the ConfigMap and leaves the pods running the old value indefinitely.

Restart semantics per tier:

* **Web** uses `strategy: Recreate` (`deployment-web.yaml:11-12`). This is not cosmetic and must stay: the web pod runs Telegram long polling (`TELEGRAM_POLLING=true`, `deployment-web.yaml:41-42`) and Telegram permits exactly one `getUpdates` consumer per token (`config.py:42-43`). A rolling update would briefly run two pollers and one of them would get 409s. A registry edit therefore costs a few seconds of intake downtime. Acceptable — messages queue at Telegram and are delivered on the next poll.
* **Worker** uses RollingUpdate with `maxSurge: 1, maxUnavailable: 0` (§5.2). Two workers overlap briefly; Pub/Sub distributes messages between them safely. The old pod's in-flight job either finishes or is nacked on shutdown (RR-2/RR-3, reliability-engineer's stream). No job is lost to a registry edit.

**IR-4 as written is satisfied and slightly exceeded:** changing a character needs no image rebuild. It does need a `helm upgrade`, which on main is a `git push` — no manual step and no rebuild, since `docker/build-push-action` will hit its cache and the image digest will be unchanged.

**Size guard:** a ConfigMap caps at ~1 MiB (etcd value limit). A registry of a dozen characters is ~2 KB. If it ever approached the limit the design would have to move to GCS, which §2 of SPEC rules out — so add a comment in `files/characters.yaml` rather than a runtime check.

---

## 5. Helm chart shape (IR-6)

### 5.1 Structure

```
deploy/charts/family-media-bot/
├── Chart.yaml                       version 0.3.0, GCP-only description
├── values.yaml
├── values.schema.json
├── README.md
├── files/
│   └── characters.yaml              NEW — the registry (IR-4)
└── templates/
    ├── _helpers.tpl                 + "family-media-bot.image" helper
    ├── NOTES.txt                    rewritten
    ├── serviceaccount.yaml          unchanged
    ├── configmap-env.yaml           was configmap.yaml, AWS branch removed
    ├── configmap-registry.yaml      NEW (IR-4)
    ├── podmonitoring.yaml           NEW (§8.2) — gated on monitoring.podMonitoring
    ├── service-web.yaml             NEW — required by the test hook
    ├── deployment-web.yaml          - nodeSelector, + volume, + checksums
    ├── deployment-worker.yaml       - nodeSelector/tolerations, + volume, + checksums,
    │                                  + explicit RollingUpdate strategy
    └── tests/
        └── test-healthz.yaml        NEW (IR-6)

DELETED: templates/scaledobject.yaml
```

Two things deliberately **not** added:

* **No PodDisruptionBudget.** On a one-node cluster with `replicas: 1`, a PDB with `minAvailable: 1` makes `kubectl drain` — including the drain GKE performs during an auto-upgrade — hang forever. A PDB here would make the cluster less reliable, not more. Worth being able to say why.

  **Reconciling with the reliability stream,** which asked for a PDB with `maxUnavailable: 1` rather than `minAvailable: 1`. That correction is right and important: `maxUnavailable: 1` at `replicas: 1` permits `desired - maxUnavailable = 0` available pods, so the node stays drainable and the auto-upgrade hazard disappears. The disagreement is therefore not about the hazard — we agree on it — but about whether the object earns its place. At `replicas: 1`, `maxUnavailable: 1` permits every eviction the API server would have allowed anyway; it is a no-op that has to be read, linted, and explained. **Recommendation: no PDB now, and if one is added for completeness, it must be `maxUnavailable: 1` and never `minAvailable: 1`.** The moment `worker.replicaCount` goes above 1 the PDB becomes load-bearing and should be added with it. If the reliability stream would rather have the object present as documentation of the intent, that is a reasonable counter-position and the cost is one inert manifest — recording the disagreement rather than resolving it silently, per SPEC §9.
* **No HorizontalPodAutoscaler.** D2/D3. Note that `addons_config.horizontal_pod_autoscaling.disabled = false` (`gke.tf:33-35`) stays enabled — that addon is also what runs `metrics-server`, which `kubectl top` needs.

### 5.2 `values.yaml` (full replacement)

```yaml
# GCP-only workload defaults. Deployment inputs live in deploy/values/prod.yaml.
#
# image.tag is intentionally ABSENT: CI supplies it (or, preferably,
# image.digest) at upgrade time. See values.schema.json — the schema rejects a
# release that supplies neither.
image:
  # Full repository path, no tag. Artifact Registry outputs the whole path, so
  # templates must not append a name.
  repository: ""
  pullPolicy: IfNotPresent

app:
  name: family-media-bot
  telegramSecretName: telegram-bot-token
  workerConcurrency: 1
  logLevel: INFO
  logFormat: json
  adapters:
    queue: pubsub
    storage: gcs
    story: vertex
    image: vertex

serviceAccount:
  annotations: {}

registry:
  # The character registry ships as chart data (files/characters.yaml) and is
  # mounted read-only. Editing it needs a chart upgrade, not an image rebuild.
  mountPath: /etc/family-media-bot/registry

service:
  # ClusterIP only. Telegram intake is long polling (outbound), so nothing
  # external needs to reach the web pod; this exists for in-cluster probes and
  # the helm test hook.
  port: 8080

web:
  replicaCount: 1
  # Recreate, not RollingUpdate: Telegram allows exactly one getUpdates consumer
  # per bot token, so two web pods must never overlap.
  strategy: Recreate
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      memory: 512Mi

worker:
  # Fixed and warm. KEDA was measured at 4m43s from enqueue to activation on a
  # 13.7s job, because Pub/Sub backlog metrics sample on a ~60s interval; the
  # latency was the product. One warm replica is the fix and the simplification.
  replicaCount: 1
  # Surge, never gap: a replacement worker starts before the old one stops, so a
  # rollout cannot leave the queue unattended.
  maxSurge: 1
  maxUnavailable: 0
  terminationGracePeriodSeconds: 120
  resources:
    requests:
      cpu: 250m
      memory: 256Mi
    limits:
      memory: 1Gi

test:
  # The helm test hook reuses the application image: it is already in Artifact
  # Registry, already cached on the node, and adds no third-party supply chain.
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
```

### 5.3 `values.schema.json` — what it should still validate

The current schema is 300 lines, of which the `allOf` block (lines 201–299) is pure provider branching and the `aws` object (131–158) is dead. Both go. The result is ~110 lines and, importantly, has **no conditionals at all** — every constraint is unconditional, which is the whole point of D1.

What it must still earn its keep on:

1. **`image`: `repository` required, plus `anyOf` requiring `tag` or `digest`.** This is the mechanism that makes IR-6's "the image tag must not be committed" *safe* rather than merely tidy. With no `tag` key in `values.yaml` and no `tag` in `prod.yaml`, a `helm upgrade` that forgets `--set image.tag` fails schema validation client-side, before anything touches the cluster.
2. **`image.repository` pattern** `^[a-z0-9-]+-docker\.pkg\.dev/[^:]+$` — replaces the deleted SQS-URL pattern with a same-spirited guard: it catches a Docker Hub path, a trailing `:tag`, and a typo'd registry host.
3. **`serviceAccount.annotations` requires `iam.gke.io/gcp-service-account`** — unconditionally now, not inside an `if/then`. Without it Workload Identity silently falls back to the node SA, which has `artifactregistry.reader` but no `aiplatform.user`, and the failure appears as a 403 from Vertex at request time rather than at deploy time.
4. **`gcp` required, with all nine keys.** Same set as today's `then` branch (lines 237–247), lifted to the top level.
5. **`app.adapters` enums** reduced to the GCP and dev values.
6. **`worker.replicaCount` minimum 1, `web.replicaCount` minimum 1.** Encodes D2: no scale-to-zero.
7. **`additionalProperties: false` on `image`, `registry`, `service`, and `gcp`.** Today only `cloud` has it. Turning it on for the small leaf objects catches `image.tagg` and `gcp.projectID` typos, which are otherwise silently ignored and produce an empty `required` error three steps later.

```json
{
  "$schema": "https://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["image", "app", "serviceAccount", "gcp", "registry", "service", "web", "worker"],
  "additionalProperties": true,
  "properties": {
    "image": {
      "type": "object",
      "additionalProperties": false,
      "required": ["repository"],
      "anyOf": [{ "required": ["tag"] }, { "required": ["digest"] }],
      "properties": {
        "repository": {
          "type": "string",
          "pattern": "^[a-z0-9-]+-docker\\.pkg\\.dev/[^:]+$",
          "description": "Artifact Registry path, no tag."
        },
        "tag":    { "type": "string", "minLength": 1 },
        "digest": { "type": "string", "pattern": "^sha256:[a-f0-9]{64}$" },
        "pullPolicy": { "type": "string", "enum": ["Always", "IfNotPresent", "Never"] }
      }
    },
    "app": {
      "type": "object",
      "required": ["telegramSecretName", "workerConcurrency", "logLevel", "logFormat", "adapters"],
      "properties": {
        "telegramSecretName": { "type": "string", "minLength": 1 },
        "workerConcurrency":  { "type": "integer", "minimum": 1 },
        "logLevel":  { "type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR"] },
        "logFormat": { "type": "string", "enum": ["json", "console"] },
        "adapters": {
          "type": "object",
          "additionalProperties": false,
          "required": ["queue", "storage", "story", "image"],
          "properties": {
            "queue":   { "type": "string", "enum": ["pubsub", "inmemory"] },
            "storage": { "type": "string", "enum": ["gcs", "local"] },
            "story":   { "type": "string", "enum": ["vertex", "fake"] },
            "image":   { "type": "string", "enum": ["vertex", "fake"] }
          }
        }
      }
    },
    "serviceAccount": {
      "type": "object",
      "required": ["annotations"],
      "properties": {
        "annotations": {
          "type": "object",
          "required": ["iam.gke.io/gcp-service-account"],
          "additionalProperties": { "type": "string" }
        }
      }
    },
    "gcp": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "projectId", "region", "mediaBucket", "jobsTopic", "jobsSubscription",
        "vertexTextLocation", "vertexImageLocation", "vertexTextModel", "vertexImageModel"
      ],
      "properties": {
        "projectId":           { "type": "string", "minLength": 1 },
        "region":              { "type": "string", "minLength": 1 },
        "mediaBucket":         { "type": "string", "minLength": 1 },
        "jobsTopic":           { "type": "string", "minLength": 1 },
        "jobsSubscription":    { "type": "string", "minLength": 1 },
        "vertexTextLocation":  { "type": "string", "minLength": 1 },
        "vertexImageLocation": { "type": "string", "minLength": 1 },
        "vertexTextModel":     { "type": "string", "minLength": 1 },
        "vertexImageModel":    { "type": "string", "minLength": 1 }
      }
    },
    "registry": {
      "type": "object",
      "additionalProperties": false,
      "required": ["mountPath"],
      "properties": { "mountPath": { "type": "string", "pattern": "^/" } }
    },
    "service": {
      "type": "object",
      "additionalProperties": false,
      "required": ["port"],
      "properties": { "port": { "type": "integer", "minimum": 1, "maximum": 65535 } }
    },
    "web": {
      "type": "object",
      "required": ["replicaCount", "strategy", "resources"],
      "properties": {
        "replicaCount": { "type": "integer", "minimum": 1, "maximum": 1,
                          "description": "Telegram allows one getUpdates consumer per token." },
        "strategy":     { "type": "string", "enum": ["Recreate"] }
      }
    },
    "worker": {
      "type": "object",
      "required": ["replicaCount", "maxSurge", "maxUnavailable", "terminationGracePeriodSeconds", "resources"],
      "properties": {
        "replicaCount":   { "type": "integer", "minimum": 1 },
        "maxSurge":       { "type": "integer", "minimum": 1 },
        "maxUnavailable": { "type": "integer", "minimum": 0, "maximum": 0,
                            "description": "Never leave the queue unattended during a rollout." },
        "terminationGracePeriodSeconds": { "type": "integer", "minimum": 30 }
      }
    }
  }
}
```

The `web.replicaCount` `maximum: 1` and `worker.maxUnavailable` `maximum: 0` are the two most opinionated lines in the file. Both encode a correctness constraint (one Telegram poller; never zero consumers) that would otherwise live only in a comment. That is a good use of a schema and a good thing to be asked about.

One consequence to accept: `helm lint deploy/charts/family-media-bot` with no `-f` now **fails** ("image.repository … does not match pattern"), because `values.yaml` ships `repository: ""` and no tag. That is correct behaviour — the chart is not installable without deployment inputs — but every lint invocation in the Makefile and CI must pass `-f deploy/values/prod.yaml --set image.tag=…`. Documented in §6.4.

### 5.4 The image tag, and how `deploy/values/gcp.yaml:6` goes away

Today `deploy/values/gcp.yaml:6` reads `tag: streaming-pubsub-20260719-111322`. Three problems, exactly as PRODUCT-DIRECTION §3.8 says: the value drifts from what is committed, the deploy is not reproducible from git alone, and the edit happens on a laptop.

The replacement:

1. Delete the `tag` key from `deploy/values/prod.yaml` and do not add it to `values.yaml`.
2. `values.schema.json` requires `tag` **or** `digest` (§5.3), so its absence is an error, not a default.
3. A new helper prefers the digest:

```gotemplate
{{/* Immutable when a digest is supplied; tag is the fallback for local/kind. */}}
{{- define "family-media-bot.image" -}}
{{- $repo := required "image.repository is required" .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{ $repo }}@{{ .Values.image.digest }}
{{- else -}}
{{ $repo }}:{{ required "image.tag or image.digest is required" .Values.image.tag }}
{{- end -}}
{{- end }}
```

Both deployments and the test hook use `image: {{ include "family-media-bot.image" . | quote }}`, replacing the inline `required` expressions at `deployment-web.yaml:30` and `deployment-worker.yaml:33`.

4. The main workflow supplies **both**:
```
--set image.repository=$AR_REPO
--set-string image.tag=${{ github.sha }}
--set-string image.digest=${{ steps.build.outputs.digest }}
```
The digest is what actually runs (immutable, re-runnable, immune to a tag being overwritten). The tag is set anyway so `kubectl describe` and `helm get values` show a human-readable commit, and so `helm rollback` output is legible.

### 5.5 The `helm test` hook

**`templates/service-web.yaml`** (new — nothing in the chart creates a Service today, so the hook has nowhere to point):

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "family-media-bot.name" . }}-web
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "family-media-bot.labels" . | nindent 4 }}
    app.kubernetes.io/component: web
spec:
  type: ClusterIP
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
  selector:
    {{- include "family-media-bot.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: web
```

**`templates/tests/test-healthz.yaml`** (new):

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: {{ include "family-media-bot.name" . }}-test-healthz
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "family-media-bot.labels" . | nindent 4 }}
  annotations:
    helm.sh/hook: test
    helm.sh/hook-weight: "0"
    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
spec:
  restartPolicy: Never
  # The probe needs no cloud identity; do not hand it a token.
  automountServiceAccountToken: false
  containers:
    - name: healthz
      # Reuse the application image: already in Artifact Registry, already
      # cached on the node, no third-party image to vet or mirror. The
      # Dockerfile's own HEALTHCHECK uses the same urllib call.
      image: {{ include "family-media-bot.image" . | quote }}
      imagePullPolicy: {{ .Values.image.pullPolicy }}
      command: ["python", "-c"]
      args:
        - |
          import json, sys, time, urllib.error, urllib.request
          url = "http://{{ include "family-media-bot.name" . }}-web.{{ .Release.Namespace }}.svc:{{ .Values.service.port }}/healthz"
          last = None
          for _ in range(30):
              try:
                  with urllib.request.urlopen(url, timeout=5) as r:
                      if r.status != 200:
                          last = "status %s" % r.status
                      else:
                          body = json.load(r)
                          if body.get("status") == "ok":
                              print("healthz ok:", body)
                              sys.exit(0)
                          last = "body %r" % body
              except Exception as exc:            # noqa: BLE001 - probe, report and retry
                  last = "%s: %s" % (type(exc).__name__, exc)
              time.sleep(2)
          sys.exit("healthz never returned {\"status\":\"ok\"} — last: %s" % last)
      resources:
        {{- toYaml .Values.test.resources | nindent 8 }}
      securityContext:
        allowPrivilegeEscalation: false
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
        seccompProfile:
          type: RuntimeDefault
```

Design notes worth being able to defend:

* **Why `/healthz` and not `/readyz`.** Contract #3 (SPEC §8) says `/healthz` performs no cloud calls. A test hook that hit `/readyz` would fail in kind (no Pub/Sub, no GCS) and would turn a transient Vertex outage into a failed *deploy* rather than a failed request. The hook asserts "the release produced a serving process reachable through its Service", which is exactly the scope of a smoke test. `/readyz` is what the readinessProbe is for, and `--wait` already gates on it.
* **Why it asserts the body, not just the status.** A 200 from the wrong pod, or from an nginx that happens to be there, would pass a status-only check. `{"status":"ok"}` is the specific contract at `app.py:87-90`.
* **Why the retry loop when `--wait` already ran.** `--wait` gates on pod readiness; kube-proxy/endpoint programming lags that by a moment. 30 × 2s is cheap insurance against a flaky CI failure that costs more to debug than the loop costs to run.
* **`hook-delete-policy: before-hook-creation,hook-succeeded`** leaves the pod behind on failure so `kubectl logs` still works, and cleans up on success.
* **On a one-node cluster** the pod needs a scheduling slot: 10m/32Mi, accounted for in §3.1.

Run it as `helm test family-media-bot -n app --logs --timeout 2m`.

### 5.6 `templates/NOTES.txt` (full replacement)

```
Release {{ .Release.Name }} is installed in namespace {{ .Release.Namespace }}.

Image:
  {{ include "family-media-bot.image" . }}

Smoke-test the release:
  helm test {{ .Release.Name }} -n {{ .Release.Namespace }} --logs

Warm web tier (Telegram long polling — exactly one poller per bot token):
  kubectl -n {{ .Release.Namespace }} get pods -l app.kubernetes.io/component=web

Warm worker (fixed at {{ .Values.worker.replicaCount }} replica; no autoscaler by design):
  kubectl -n {{ .Release.Namespace }} get pods -l app.kubernetes.io/component=worker

Character registry currently mounted at {{ .Values.registry.mountPath }}/characters.yaml:
  kubectl -n {{ .Release.Namespace }} get configmap {{ include "family-media-bot.name" . }}-registry -o yaml

Follow application logs:
  kubectl -n {{ .Release.Namespace }} logs deploy/{{ include "family-media-bot.name" . }}-web -f
```

### 5.7 `deploy/values/prod.yaml` (was `deploy/values/gcp.yaml`)

```yaml
# Deployment inputs for the live GCP environment. Values come from
# `terraform -chdir=infra/gcp output`.
#
# image.tag / image.digest are deliberately NOT here — CI supplies them.
image:
  repository: us-central1-docker.pkg.dev/ai-family-media-bot/ai-family-media-bot/family-media-bot

app:
  adapters:
    queue: pubsub
    storage: gcs
    story: vertex
    image: vertex

serviceAccount:
  annotations:
    iam.gke.io/gcp-service-account: family-media-app@ai-family-media-bot.iam.gserviceaccount.com

gcp:
  projectId: ai-family-media-bot
  region: us-central1
  mediaBucket: ai-family-media-bot-ai-family-media-bot-media
  jobsTopic: ai-family-media-bot-jobs
  jobsSubscription: ai-family-media-bot-jobs
  vertexTextLocation: global
  vertexImageLocation: us-central1
  vertexTextModel: gemini-3.5-flash
  vertexImageModel: gemini-2.5-flash-image
```

### 5.8 `deploy/values/kind.yaml` (new — required by IR-7's smoke install)

IR-6 says "one values file". This is a second file, so justify it explicitly: `prod.yaml` is a *deployment input*; `kind.yaml` is a *test fixture*. They are not two environments in the multi-cloud sense that D1 deleted; nothing in `templates/` branches on which one is loaded.

```yaml
# kind smoke-test fixture. Fake adapters only: Workload Identity does not exist
# in kind, so this proves the chart, probes, ConfigMaps, Secret wiring, Service
# and test hook — not the cloud integrations.
image:
  repository: us-central1-docker.pkg.dev/ai-family-media-bot/ai-family-media-bot/family-media-bot

app:
  logFormat: console
  adapters:
    queue: inmemory
    storage: local
    story: fake
    image: fake

serviceAccount:
  annotations:
    # Inert in kind; present because the schema requires it, and requiring it
    # unconditionally is what stops a real deploy losing Workload Identity.
    iam.gke.io/gcp-service-account: kind-local@example.iam.gserviceaccount.com

gcp:
  projectId: kind-local
  region: us-central1
  mediaBucket: kind-local
  jobsTopic: kind-local
  jobsSubscription: kind-local
  vertexTextLocation: global
  vertexImageLocation: us-central1
  vertexTextModel: gemini-3.5-flash
  vertexImageModel: gemini-2.5-flash-image
```

Note the trade-off this creates: because `app.adapters` enums accept the dev values (`inmemory`/`local`/`fake`), the schema no longer prevents `queue: inmemory` reaching production. The alternative — an `if/then` on the adapter set — reintroduces exactly the conditional structure D1 deleted. The guard chosen instead is procedural: the main workflow hardcodes `-f deploy/values/prod.yaml` and nothing else. Say this out loud rather than pretending the schema covers it.

Also note: in `RUN_MODE=worker` with `queue: inmemory`, the worker consumes an in-process queue nothing publishes to. That is fine for a smoke test — the assertion is that both pods reach Ready and `/healthz` answers, not that a job flows.

---

## 6. Pipelines (IR-7)

### 6.0 Resolving finding A: `--atomic` does not exist in Helm 4

Verified on this machine:

```
$ helm version --short
v4.2.2+gb05881c
$ helm upgrade --help | grep -c -- --atomic
0
$ helm upgrade --help | grep -- --rollback-on-failure
  --rollback-on-failure   if set, Helm will rollback the upgrade to previous
                          success release upon failure. The --wait flag will be
                          defaulted to "watcher" if --rollback-on-failure is set
```

The repo is already on Helm 4 — `deploy/charts/family-media-bot/README.md:55` uses `--rollback-on-failure`.

**Recommendation: pin Helm `v4.2.2` in CI and use `--rollback-on-failure --wait --timeout 5m`.** Pinning to Helm 3 to keep the literal `--atomic` flag would be a downgrade to match a doc. Update SPEC IR-7 and PRODUCT-DIRECTION §3.7/§3.8 wording; the *semantics* IR-7 asks for (auto-rollback on a failed release, RR-6) are exactly what `--rollback-on-failure` provides.

Two Helm 4 behaviours to know because they change what the flags mean:
* `--wait` now takes a strategy. Bare `--wait` means `watcher`. Omitting it entirely means `hookOnly` — so Helm 4 waits for hooks by default but not for workloads.
* `--rollback-on-failure` implies `--wait=watcher`.

### 6.1 `.github/workflows/pr.yml`

```yaml
name: PR

on:
  pull_request:
    branches: [main]

# Deny by default; each job opts in to the minimum it needs.
permissions: {}

concurrency:
  group: pr-${{ github.event.pull_request.number }}
  cancel-in-progress: true

env:
  PYTHON_VERSION: "3.11"
  # Pin every tool. An unpinned linter turns an unrelated PR red on a Tuesday.
  RUFF_VERSION: "0.16.1"
  HELM_VERSION: "v4.2.2"
  TERRAFORM_VERSION: "1.15.7"

jobs:
  python:
    name: ruff + unittest
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
          cache-dependency-path: app/requirements.txt

      # ruff is documented in AGENTS.md but is not in app/.venv. CI installs it,
      # pinned, so lint results are reproducible across machines.
      - name: Install ruff
        run: pipx install "ruff==${RUFF_VERSION}"

      - name: ruff check
        working-directory: app
        run: ruff check --output-format=github .

      - name: ruff format --check
        working-directory: app
        run: ruff format --check .

      - name: Install runtime deps
        working-directory: app
        run: |
          python -m pip install -U pip
          python -m pip install -r requirements.txt

      - name: Unit tests
        working-directory: app
        run: python -m unittest discover -s tests -v

  helm:
    name: helm lint + template
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: azure/setup-helm@v4
        with:
          version: ${{ env.HELM_VERSION }}

      # The schema requires a tag or a digest, so lint MUST be given one.
      # That is the point: a chart with no image coordinates is not installable.
      - name: helm lint (prod values)
        run: |
          helm lint deploy/charts/family-media-bot \
            -f deploy/values/prod.yaml \
            --set-string image.tag=ci \
            --strict

      - name: helm lint (kind values)
        run: |
          helm lint deploy/charts/family-media-bot \
            -f deploy/values/kind.yaml \
            --set-string image.tag=ci \
            --strict

      - name: helm template
        run: |
          helm template family-media-bot deploy/charts/family-media-bot \
            --namespace app \
            -f deploy/values/prod.yaml \
            --set-string image.tag=ci \
            > /tmp/rendered.yaml
          cat /tmp/rendered.yaml

      # QR-3: assert against the rendered manifests, do not merely produce them.
      - name: Assert rendered manifests
        run: |
          set -euo pipefail
          fail() { echo "::error::$1"; exit 1; }

          grep -q 'kind: ScaledObject' /tmp/rendered.yaml && fail "KEDA ScaledObject is still rendered"
          grep -qi 'aws\|bedrock\|sqs' /tmp/rendered.yaml && fail "AWS reference in rendered output"
          grep -q 'nodeSelector' /tmp/rendered.yaml && fail "nodeSelector survived the single-pool change"
          grep -q 'workload: jobs' /tmp/rendered.yaml && fail "worker toleration survived"

          grep -q 'checksum/registry:' /tmp/rendered.yaml || fail "registry checksum annotation missing"
          grep -q 'checksum/config:'   /tmp/rendered.yaml || fail "env-configmap checksum annotation missing"
          grep -q 'kind: Service'      /tmp/rendered.yaml || fail "web Service missing"
          grep -q 'characters.yaml'    /tmp/rendered.yaml || fail "character registry not in the ConfigMap"
          grep -q 'iam.gke.io/gcp-service-account' /tmp/rendered.yaml || fail "Workload Identity annotation missing"
          echo "rendered manifests OK"

      # The test hook is a helm.sh/hook, so it is excluded from `helm template`
      # output unless asked for. Render it explicitly so a broken hook is caught
      # here rather than at `helm test` time in the kind job.
      - name: Render the test hook
        run: |
          helm template family-media-bot deploy/charts/family-media-bot \
            --namespace app -f deploy/values/prod.yaml \
            --set-string image.tag=ci \
            --show-only templates/tests/test-healthz.yaml

  terraform:
    name: terraform plan
    runs-on: ubuntu-latest
    # Fork PRs receive no OIDC token, so skip rather than fail.
    if: github.event.pull_request.head.repo.full_name == github.repository
    permissions:
      contents: read
      id-token: write          # mint the GitHub OIDC token for WIF
      pull-requests: write     # post the plan comment
    # Never let two plans race the GCS state lock, and never cancel one midway.
    concurrency:
      group: terraform-gcp
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@v4

      - id: auth
        uses: google-github-actions/auth@v2
        with:
          project_id: ${{ vars.GCP_PROJECT_ID }}
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          # READ-ONLY identity. CI never applies. See design §2.2.
          service_account: ${{ vars.GCP_PLANNER_SA }}

      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: ${{ env.TERRAFORM_VERSION }}
          terraform_wrapper: false

      - name: fmt
        run: terraform -chdir=infra/gcp fmt -check -recursive

      - name: init
        run: |
          terraform -chdir=infra/gcp init -input=false \
            -backend-config="bucket=${{ vars.TF_STATE_BUCKET }}"

      - name: validate
        run: terraform -chdir=infra/gcp validate

      - id: plan
        name: plan
        # -lock=false: the planner service account has objectViewer on the state
        # bucket only, and a read-only plan has nothing to protect.
        run: |
          set -o pipefail
          terraform -chdir=infra/gcp plan -input=false -no-color -lock=false \
            -var="project_id=${{ vars.GCP_PROJECT_ID }}" \
            -var="github_repository=${{ github.repository }}" \
            -var="github_repository_owner_id=${{ vars.GITHUB_OWNER_ID }}" \
            | tee /tmp/plan.txt
          {
            echo 'stdout<<PLAN_EOF'
            tail -c 60000 /tmp/plan.txt
            echo 'PLAN_EOF'
          } >> "$GITHUB_OUTPUT"
        continue-on-error: true

      - name: Comment the plan
        uses: actions/github-script@v7
        env:
          PLAN: ${{ steps.plan.outputs.stdout }}
          OUTCOME: ${{ steps.plan.outcome }}
        with:
          script: |
            const marker = '<!-- terraform-plan:infra/gcp -->';
            const body = [
              marker,
              `### Terraform plan \`infra/gcp\` — ${process.env.OUTCOME === 'success' ? 'ok' : 'FAILED'}`,
              '',
              '<details><summary>Show plan</summary>',
              '',
              '```terraform',
              process.env.PLAN,
              '```',
              '',
              '</details>',
              '',
              '_Plan only. `terraform apply` is run by a human — see design §2.2._',
            ].join('\n');

            const { data: comments } = await github.rest.issues.listComments({
              ...context.repo, issue_number: context.issue.number,
            });
            const existing = comments.find(c => c.body?.startsWith(marker));
            if (existing) {
              await github.rest.issues.updateComment({ ...context.repo, comment_id: existing.id, body });
            } else {
              await github.rest.issues.createComment({ ...context.repo, issue_number: context.issue.number, body });
            }

      - name: Fail if the plan failed
        if: steps.plan.outcome != 'success'
        run: exit 1

  kind:
    name: kind smoke install
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4

      - uses: azure/setup-helm@v4
        with:
          version: ${{ env.HELM_VERSION }}

      - uses: helm/kind-action@v1
        with:
          cluster_name: smoke
          node_image: kindest/node:v1.31.4

      - name: Build the image
        run: docker build -t family-media-bot:ci app

      - name: Load it into kind
        run: kind load docker-image family-media-bot:ci --name smoke

      - name: Namespace and Telegram secret
        run: |
          kubectl create namespace app
          kubectl -n app create secret generic telegram-bot-token \
            --from-literal=TELEGRAM_BOT_TOKEN=''
          # Empty token on purpose: the worker logs "would send" instead of
          # calling the Bot API, and no real bot is touched from CI.

      - name: helm install
        run: |
          helm upgrade --install family-media-bot deploy/charts/family-media-bot \
            --namespace app \
            -f deploy/values/kind.yaml \
            --set image.repository=family-media-bot \
            --set-string image.tag=ci \
            --set image.pullPolicy=Never \
            --rollback-on-failure --wait --timeout 3m

      - name: helm test
        run: helm test family-media-bot -n app --logs --timeout 2m

      - name: Diagnostics on failure
        if: failure()
        run: |
          kubectl -n app get all
          kubectl -n app describe pods
          kubectl -n app logs -l app.kubernetes.io/name=family-media-bot --all-containers --tail=200 || true
          kubectl -n app get events --sort-by=.lastTimestamp | tail -50
```

A note on `--set image.repository=family-media-bot` in the kind job: it violates the `^[a-z0-9-]+-docker\.pkg\.dev/` pattern from §5.3, so the schema rejects it. Two ways out — relax the pattern to `^([a-z0-9-]+-docker\.pkg\.dev/.+|[a-z0-9][a-z0-9._/-]*)$`, or keep the AR repository from `kind.yaml` and retag the local build as `us-central1-docker.pkg.dev/…:ci` before `kind load`. **Recommend the retag**: it keeps the pattern strict, and it makes the kind job exercise the same image reference shape production uses.

```yaml
      - name: Build and load
        run: |
          IMG=us-central1-docker.pkg.dev/ai-family-media-bot/ai-family-media-bot/family-media-bot:ci
          docker build -t "$IMG" app
          kind load docker-image "$IMG" --name smoke
```
and drop the `--set image.repository=` override, keeping `--set image.pullPolicy=Never`.

### 6.2 Containing finding B: the first lint run

Measured on `a7c2f29` with `ruff 0.16.1` (installed into a scratch venv outside the repo; the repo was not modified):

| Command | Result |
|---|---|
| `ruff check .` | **29 errors**, 8 auto-fixable. Rules hit: `BLE001` ×13, `UP041` ×4, `UP017` ×4, `S110` ×2, `FURB162` ×1, and others. |
| `ruff check --select E501 .` | **3 errors**: `adapters/image_fake.py:19` (101), **`pipeline.py:67` (105)**, `telemetry.py:49` (101). |
| `ruff format --check .` | **10 files** would be reformatted. |

So PRODUCT-DIRECTION §3.8's "one pre-existing 105-character line at `app/family_media_bot/pipeline.py:73`" is wrong three times over: wrong line number (73 is 62 chars; the 105-char line is 67), wrong count (3 E501s, not 1), and wrong scale (E501 is a small fraction of what CI will actually report).

There is a subtlety behind the 29: `app/pyproject.toml:31-33` sets only `line-length` and `target-version`, no `[tool.ruff.lint] select`. So the effective rule set is whatever ruff's default is *for the installed version*, and that default has widened over time. Pinning `RUFF_VERSION` is therefore not merely tidy — without it, a ruff release can turn an unrelated PR red.

**Recommended containment, in order:**

1. **Pin the version** in `pyproject.toml` and in CI (`RUFF_VERSION: "0.16.1"`).
2. **Make the rule set explicit** rather than inheriting a moving default:
```toml
[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]
ignore = [
  # The codebase deliberately catches broad exceptions at adapter boundaries and
  # in readiness checks, so a cloud outage degrades rather than crashes.
  "BLE001",
]
```
   This is a decision, not a workaround: `BLE001` fires 13 times because the port/adapter design intentionally converts every downstream failure into `False` from `check_ready()` (`storage_gcs.py:51`, `queue_pubsub.py:274`, `story_vertex.py:79`, `app.py:112`). Suppressing it project-wide with a stated reason is more honest than 13 `# noqa`s.
3. **Fix the 3 E501s and run `ruff format` once**, as a *separate, first* commit titled something like "Apply ruff formatting" — so the pipeline PR's diff is the pipeline, not 10 reformatted files. 4 of the 29 (`UP041`, `UP017`) are auto-fixable and worth taking in the same pass.
4. Note that deleting the four AWS adapters (§1.1) removes 4 of the 29 findings for free (`image_bedrock.py:76`, `queue_sqs.py:46,84`, `storage_s3.py:47`, `story_bedrock.py:74`) and 2 of the 10 unformatted files.

Do steps 1–4 before the pipeline PR, or the pipeline lands red and the interesting change is buried under formatting noise.

### 6.3 `.github/workflows/main.yml`

```yaml
name: Deploy

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions: {}

# Never let two deploys overlap, and never cancel one midway — a cancelled
# `helm upgrade` leaves the release pending-upgrade.
concurrency:
  group: deploy-prod
  cancel-in-progress: false

env:
  HELM_VERSION: "v4.2.2"
  GAR_HOST: us-central1-docker.pkg.dev
  IMAGE: us-central1-docker.pkg.dev/ai-family-media-bot/ai-family-media-bot/family-media-bot
  RELEASE: family-media-bot
  NAMESPACE: app

jobs:
  deploy:
    name: build, push, upgrade
    runs-on: ubuntu-latest
    environment: production        # gives a deployment record and an optional approval gate
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4

      - id: auth
        uses: google-github-actions/auth@v2
        with:
          project_id: ${{ vars.GCP_PROJECT_ID }}
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_DEPLOYER_SA }}

      - uses: google-github-actions/setup-gcloud@v2
        with:
          install_components: gke-gcloud-auth-plugin

      - name: Configure Docker for Artifact Registry
        run: gcloud auth configure-docker "${GAR_HOST}" --quiet

      - uses: docker/setup-buildx-action@v3

      - id: build
        uses: docker/build-push-action@v6
        with:
          context: ./app
          push: true
          # Tag with the commit for legibility. The DIGEST is what gets deployed.
          tags: |
            ${{ env.IMAGE }}:${{ github.sha }}
            ${{ env.IMAGE }}:latest
          provenance: false
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - uses: azure/setup-helm@v4
        with:
          version: ${{ env.HELM_VERSION }}

      - name: Cluster credentials
        run: |
          gcloud container clusters get-credentials ai-family-media-bot \
            --location us-central1-a \
            --project "${{ vars.GCP_PROJECT_ID }}"

      - name: helm upgrade
        run: |
          helm upgrade --install "${RELEASE}" deploy/charts/family-media-bot \
            --namespace "${NAMESPACE}" --create-namespace \
            -f deploy/values/prod.yaml \
            --set-string image.tag=${{ github.sha }} \
            --set-string image.digest=${{ steps.build.outputs.digest }} \
            --rollback-on-failure --wait --timeout 5m
          # --rollback-on-failure is Helm 4's replacement for Helm 3's --atomic:
          # a release that never becomes ready is rolled back automatically, so a
          # bad deploy cannot leave the bot down (RR-6).

      - name: helm test
        run: helm test "${RELEASE}" -n "${NAMESPACE}" --logs --timeout 2m

      # A failing test does not roll the release back on its own — helm test runs
      # after the upgrade has been recorded as successful. Do it explicitly.
      - name: Roll back on failed smoke test
        if: failure()
        run: |
          helm status "${RELEASE}" -n "${NAMESPACE}" || true
          helm rollback "${RELEASE}" -n "${NAMESPACE}" --wait --timeout 5m || true
          kubectl -n "${NAMESPACE}" get pods

      - name: Summary
        if: always()
        run: |
          {
            echo "### Deploy"
            echo "- commit: \`${{ github.sha }}\`"
            echo "- digest: \`${{ steps.build.outputs.digest }}\`"
            echo "- release revision: \`$(helm history "${RELEASE}" -n "${NAMESPACE}" --max 1 -o json | python -c 'import json,sys;print(json.load(sys.stdin)[0]["revision"])' 2>/dev/null || echo unknown)\`"
          } >> "$GITHUB_STEP_SUMMARY"
```

Why `helm test` has an explicit rollback step: `--rollback-on-failure` covers the upgrade, not the test — Helm records the upgrade as successful before `helm test` ever runs. Without the extra step, a release that comes up Ready but answers `/healthz` wrong would stay deployed with a green-then-red pipeline. This is the sort of gap that only shows up when you actually think about what `--atomic`/`--rollback-on-failure` does and does not cover, and it is worth being able to explain.

Also note: `latest` is pushed for human convenience only. Nothing deploys it. If you would rather not have a mutable tag in the registry at all, drop that line — the argument for keeping it is `docker pull …:latest` during a demo; the argument against is that a mutable tag in a registry is an invitation.

### 6.4 Least-privilege permissions summary

| Workflow / job | `permissions` | Why |
|---|---|---|
| both workflows, top level | `{}` | Deny-all default. Without this, the repo default (often `contents: write`) applies to every job. |
| `pr / python` | `contents: read` | Checkout only. |
| `pr / helm` | `contents: read` | Checkout only. |
| `pr / terraform` | `contents: read`, `id-token: write`, `pull-requests: write` | OIDC token for WIF; comment on the PR. Not `issues: write` — `pull-requests` is the narrower scope for PR comments. |
| `pr / kind` | `contents: read` | Everything is local to the runner. |
| `main / deploy` | `contents: read`, `id-token: write` | No `packages:`, no `contents: write` — this workflow does not tag, release, or commit. |

The one to defend: `pull-requests: write` on a job that also holds `id-token: write`. The mitigation is that the OIDC token in that job can only impersonate the *read-only planner* SA (§5), so the worst a compromised action in that job can do is post a comment and read infrastructure metadata.

### 6.5 Makefile changes

```make
.PHONY: help venv install lint fmt test helm-lint helm-template kind-smoke tf-init tf-plan run demo docker-build docker-run smoke clean

RUFF_VERSION := 0.16.1
STATE_BUCKET ?= davidg-tfstate-bucket
CHART        := deploy/charts/family-media-bot
REGISTRY     := ../deploy/charts/family-media-bot/files/characters.yaml

lint:
	cd $(APP_DIR) && ./.venv/bin/python -m ruff check . && ./.venv/bin/python -m ruff format --check .

fmt:
	cd $(APP_DIR) && ./.venv/bin/python -m ruff check --fix . && ./.venv/bin/python -m ruff format .

# The chart is not installable without image coordinates; the schema enforces it.
helm-lint:
	helm lint $(CHART) -f deploy/values/prod.yaml --set-string image.tag=dev --strict
	helm lint $(CHART) -f deploy/values/kind.yaml --set-string image.tag=dev --strict

helm-template:
	helm template family-media-bot $(CHART) -n app \
		-f deploy/values/prod.yaml --set-string image.tag=dev

kind-smoke:
	kind create cluster --name smoke || true
	IMG=us-central1-docker.pkg.dev/ai-family-media-bot/ai-family-media-bot/family-media-bot:dev; \
	docker build -t "$$IMG" $(APP_DIR) && kind load docker-image "$$IMG" --name smoke
	kubectl create namespace app --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n app create secret generic telegram-bot-token --from-literal=TELEGRAM_BOT_TOKEN='' \
		--dry-run=client -o yaml | kubectl apply -f -
	helm upgrade --install family-media-bot $(CHART) -n app \
		-f deploy/values/kind.yaml --set-string image.tag=dev --set image.pullPolicy=Never \
		--rollback-on-failure --wait --timeout 3m
	helm test family-media-bot -n app --logs --timeout 2m

# Always pass the bucket. A bare `terraform init` here prompts, and a wrong
# answer plans to create the entire live environment from scratch.
tf-init:
	terraform -chdir=infra/gcp init -backend-config="bucket=$(STATE_BUCKET)"

tf-plan: tf-init
	terraform -chdir=infra/gcp fmt -check -recursive
	terraform -chdir=infra/gcp validate
	terraform -chdir=infra/gcp plan -out=infra/gcp/tfplan

run:
	cd $(APP_DIR) && CHARACTER_REGISTRY_PATH=$(REGISTRY) ./.venv/bin/python -m family_media_bot
```

Add `ruff==0.16.1` to a new `app/requirements-dev.txt` and have `make install` install it, so `make lint` works locally and matches CI exactly. Remove the `helm-lint-aws` / `helm-lint-addons` targets and their `.PHONY` entries.

---

## 7. Workload Identity Federation (IR-5)

This is the security-sensitive part of the design. The threat it defends against is specific and non-obvious: **GitHub signs OIDC tokens for every repository on github.com with the same issuer and the same signing keys.** A token from a stranger's repo is cryptographically valid. The only thing standing between that token and your project is the attribute condition on the provider.

### 7.1 New file: `infra/gcp/cicd.tf`

```hcl
# ---------------------------------------------------------------------------
# GitHub Actions -> GCP, keyless.
#
# GitHub's OIDC issuer is global: every repository on github.com receives
# tokens signed by the same keys from the same issuer. A valid signature
# therefore proves nothing about WHO is calling. Two independent gates:
#
#   1. attribute_condition on the provider — rejects the token at the STS
#      exchange if it did not come from this repository, owned by this owner
#      account, on an allowed ref. Nothing downstream is consulted.
#   2. principalSet IAM bindings on each service account — decide WHICH ref
#      gets WHICH privileges. The main branch may deploy; a pull request may
#      only read.
#
# No service-account JSON key exists anywhere in this design.
# ---------------------------------------------------------------------------

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "Keyless OIDC federation for ${var.github_repository}"

  depends_on = [
    google_project_service.required["iam.googleapis.com"],
    google_project_service.required["sts.googleapis.com"],
  ]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
    "attribute.event_name" = "assertion.event_name"

    # Composite attribute. A principalSet can match exactly one attribute, so
    # binding "this repo AND this branch" requires them pre-joined here.
    "attribute.repo_ref" = "assertion.repository + \"@\" + assertion.ref"
  }

  # THE security control. Read it as: this token must come from this repository,
  # owned by this numeric account, and be either a push to the deploy branch or
  # a pull_request run.
  #
  # repository_owner_id, not repository_owner: GitHub account NAMES can be
  # released and re-registered by someone else. The numeric owner id cannot.
  # Pinning only the name would let a future squatter of "davgrits" mint tokens
  # that satisfy `assertion.repository == "davgrits/ai-family-media-bot"`.
  #
  # The parentheses are load-bearing: CEL binds && tighter than ||, so without
  # them the owner check would apply to only one arm of the disjunction.
  attribute_condition = <<-CEL
    assertion.repository == "${var.github_repository}" &&
    assertion.repository_owner_id == "${var.github_repository_owner_id}" &&
    (assertion.ref == "${var.github_deploy_ref}" || assertion.event_name == "pull_request")
  CEL

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"

    # allowed_audiences is deliberately omitted. Omitting it makes the only
    # accepted audience the provider's own full resource name, which is exactly
    # what google-github-actions/auth requests by default. Setting it explicitly
    # would require referencing this resource from inside itself.
  }

  depends_on = [google_iam_workload_identity_pool.github]
}

# --- Deployer: main branch only -------------------------------------------

resource "google_service_account" "github_deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions deployer (main branch)"
  description  = "Pushes images to Artifact Registry and upgrades the Helm release."
}

# Scoped to the one repository, not granted project-wide.
resource "google_artifact_registry_repository_iam_member" "deployer_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.app.location
  repository = google_artifact_registry_repository.app.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github_deployer.email}"
}

# GKE has no per-cluster IAM resource, so container.developer is necessarily
# project-scoped. It grants full control of Kubernetes objects but NOT of the
# cluster itself: it cannot resize, upgrade, or delete the cluster or its node
# pools (that is container.admin / container.clusterAdmin).
resource "google_project_iam_member" "deployer_container_developer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

# Only a push to the deploy ref may impersonate the deployer. A pull_request run
# passes the provider condition (it must, so it can plan) but its ref is
# refs/pull/<n>/merge, which never matches this principalSet.
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.github_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repo_ref/${var.github_repository}@${var.github_deploy_ref}"
}

# --- Planner: read-only, any allowed run ----------------------------------

resource "google_service_account" "github_planner" {
  account_id   = "github-planner"
  display_name = "GitHub Actions terraform plan (read-only)"
  description  = "Reads infrastructure state for pull-request plans. Never applies."
}

# roles/viewer is the coarsest grant in this design and the trade-off is
# deliberate: `terraform plan` must read every resource type this root manages,
# and enumerating a dozen *.viewer roles would drift the moment a resource is
# added. It is read-only and, critically, does NOT include
# secretmanager.versions.access, so no secret material is reachable.
# At team scale this becomes a custom role generated from the plan's API calls.
resource "google_project_iam_member" "planner_viewer" {
  project = var.project_id
  role    = "roles/viewer"
  member  = "serviceAccount:${google_service_account.github_planner.email}"
}

# Read the state object. Not objectAdmin: the PR plan runs with -lock=false, so
# it never needs to write the lock object.
resource "google_storage_bucket_iam_member" "planner_state_reader" {
  bucket = var.tf_state_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.github_planner.email}"
}

resource "google_service_account_iam_member" "planner_wif" {
  service_account_id = google_service_account.github_planner.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
```

### 7.2 New variables

```hcl
variable "github_repository" {
  description = "The only GitHub repository permitted to federate into this project, as \"owner/repo\"."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be exactly \"owner/repo\"."
  }
}

variable "github_repository_owner_id" {
  description = <<-EOT
    Immutable numeric GitHub account id of the repository owner. Account NAMES
    can be released and re-registered by a third party; the id cannot. Find it
    with:  gh api users/<owner> --jq .id
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_owner_id))
    error_message = "github_repository_owner_id must be the numeric account id, not the login name."
  }
}

variable "github_deploy_ref" {
  description = "The single fully-qualified git ref whose workflow runs may impersonate the deployer service account."
  type        = string
  default     = "refs/heads/main"

  validation {
    condition     = startswith(var.github_deploy_ref, "refs/heads/")
    error_message = "github_deploy_ref must be a branch ref (refs/heads/...). Tag-triggered deploys are not part of this design."
  }
}

variable "tf_state_bucket" {
  description = "GCS bucket holding this root's own Terraform state. Bootstrapped outside Terraform; referenced here only to grant the planner read access."
  type        = string
}
```

Also add to `infra/gcp/apis.tf`:
```hcl
"iam.googleapis.com",
"sts.googleapis.com",
"cloudresourcemanager.googleapis.com",
```

### 7.3 Why the attribute condition is shaped this way

Walk through what each clause stops:

| Attack | Stopped by |
|---|---|
| A stranger's repo mints a valid GitHub OIDC token and calls `sts.googleapis.com` | `assertion.repository == "owner/repo"` — the exchange is rejected before any IAM policy is read. |
| `davgrits` is renamed/deleted and a third party registers the name, then creates `ai-family-media-bot` | `assertion.repository_owner_id` — the numeric id does not transfer with a released name. |
| A collaborator (or a compromised token with repo write) pushes a workflow to a feature branch that runs `gcloud`/`helm` | The ref is `refs/heads/feature/x`, which matches neither the `github_deploy_ref` clause nor `event_name == "pull_request"`. STS refuses. |
| A tag push, a `workflow_dispatch` on a non-main branch, a `schedule` run | Same — no matching ref, and `event_name != "pull_request"`. |
| A pull request tries to push an image or upgrade the release | Passes the provider condition (it must, to plan) but its `attribute.repo_ref` is `owner/repo@refs/pull/N/merge`, which does not match the deployer's principalSet. It can only impersonate the read-only planner. |
| A fork PR | GitHub does not issue `id-token: write` to workflows triggered by `pull_request` from a fork, so no token is minted at all. The `if:` guard on the terraform job turns that into a skip instead of a failure. |
| Token replay against a *different* Google project's pool | `allowed_audiences` defaults to this provider's full resource name; a token minted for another provider has the wrong `aud`. |

Two hardenings considered and their status:

* **`assertion.repository_id` instead of / in addition to `assertion.repository`.** Strictly stronger: if this repo were deleted and someone squatted the name under the same owner, `repository` would match but `repository_id` would not. Not included in the block above because it adds a fourth opaque numeric variable for a scenario the owner-id check already mostly covers. Mention it as the next increment; add it with `gh api repos/<owner>/<repo> --jq .id` if you want the belt and the braces.
* **`assertion.environment == "production"`** (GitHub Environments). The main workflow already declares `environment: production`, so this clause would work and would let you attach a required-reviewer gate to the deploy. Worth adding once the environment exists — but note the ordering trap: add the `environment:` to the workflow *before* adding the clause, or the first deploy after the Terraform apply cannot authenticate.

One more thing to be aware of, because it is the most common WIF misconfiguration and this design avoids it: **never bind `roles/iam.workloadIdentityUser` to `principalSet://…/*` or to `attribute.repository_owner/<org>`.** The first grants every GitHub repository on the internet; the second grants every repository in the org, including ones a colleague creates tomorrow.

### 7.4 New outputs

```hcl
output "github_wif_provider" {
  description = "Set as the GitHub Actions variable GCP_WIF_PROVIDER."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_deployer_service_account_email" {
  description = "Set as the GitHub Actions variable GCP_DEPLOYER_SA."
  value       = google_service_account.github_deployer.email
}

output "github_planner_service_account_email" {
  description = "Set as the GitHub Actions variable GCP_PLANNER_SA."
  value       = google_service_account.github_planner.email
}
```

`google_iam_workload_identity_pool_provider.github.name` is already the full `projects/<number>/locations/global/workloadIdentityPools/github-actions/providers/github-oidc` form that `google-github-actions/auth` expects.

### 7.5 GitHub repository configuration

All **variables**, not secrets — none of these is sensitive, and using variables makes them visible in logs, which is what you want when debugging a federation failure:

| Name | Value |
|---|---|
| `GCP_PROJECT_ID` | `ai-family-media-bot` |
| `GCP_WIF_PROVIDER` | `terraform output -raw github_wif_provider` |
| `GCP_DEPLOYER_SA` | `terraform output -raw github_deployer_service_account_email` |
| `GCP_PLANNER_SA` | `terraform output -raw github_planner_service_account_email` |
| `TF_STATE_BUCKET` | `davidg-tfstate-bucket` |
| `GITHUB_OWNER_ID` | `gh api users/davgrits --jq .id` |

**Repository secrets required: none.** That is the headline, and it is worth saying exactly that way to a company that names GDPR and ISO.

### 7.6 The control-plane exposure this design does not fix

`gke.tf:19-22` sets `enable_private_nodes = true` and `enable_private_endpoint = false`, and there is **no `master_authorized_networks_config` block anywhere in `infra/gcp/`**. The GKE control plane is therefore reachable from any IP on the internet (authenticated — IAM only, no basic auth or client cert).

This is not new, but the CI design depends on it: GitHub-hosted runners have unpredictable egress IPs, so adding authorized networks would break `main.yml` immediately.

Options, honestly ranked:

1. **Keep it open, and say why.** Authentication is IAM-only, the deployer is a keyless federated identity limited to `container.developer` from one branch of one repo, and Google's control plane is a hardened managed endpoint. This is what the majority of GKE-plus-GitHub-Actions setups actually do. *Recommended for this project.* Add `master_auth { client_certificate_config { issue_client_certificate = false } }` explicitly — it is already the provider default in 6.x, but stating it makes the "IAM only, no static credential path" claim visible in the code.
2. **Connect Gateway** (`gcloud container fleet memberships get-credentials`). The correct answer: the control plane stays private, CI reaches it through a Google-side proxy authenticated by the same WIF identity. Costs a fleet membership and `roles/gkehub.gatewayEditor`. The right next increment if this were real.
3. Authorized networks + a self-hosted runner in the VPC. Correct and expensive; wrong for a free-trial portfolio project.

Flagging this explicitly is more valuable than silently doing (1), which is why it is here rather than buried.

---

## 8. Monitoring (Terraform)

Finding **F**: `metrics.py` currently emits into the void. Two changes fix that, and one alert makes it useful. The whole section is ~70 lines of Terraform and one 18-line template — deliberately small, because on a finite trial credit an observability stack costs more than the thing it observes.

### 8.1 The minimal `gke.tf` change

`gke.tf:42-44` today:

```hcl
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
  }
```

Replace with:

```hcl
  monitoring_config {
    # Deliberately still only SYSTEM_COMPONENTS. Adding POD, DEPLOYMENT,
    # STATEFULSET etc. bills per-sample kube-state metrics that nothing in this
    # project reads. The node count is 1 and the replica count is 1; there is
    # nothing to learn from kube-state that `kubectl get pods` does not tell you.
    enable_components = ["SYSTEM_COMPONENTS"]

    # Google Cloud Managed Service for Prometheus. THIS is the one line that
    # turns metrics.py from write-only into queryable: it runs a collector
    # DaemonSet that honours PodMonitoring custom resources. Without it, the
    # /metrics endpoint the application already serves is scraped by nobody.
    managed_prometheus {
      enabled = true
    }
  }
```

**This is an in-place cluster update.** `terraform plan` shows `~ update in-place` on `google_container_cluster.main`; no node, node pool, or pod is replaced. It is the cheapest change in the entire design and can land independently of everything else.

Cost note: managed Prometheus bills per sample ingested. Two pods × ~40 series × one 30s scrape ≈ 160 samples/min ≈ 7M samples/month, which sits inside the free tier's first 100M samples. Effectively $0 at this scale — but the scrape `interval` is the dial to turn if that ever changes, and it lives in the chart, not in Terraform.

### 8.2 Chart side — `templates/podmonitoring.yaml` (new)

The collector needs to be told what to scrape. `PodMonitoring` selects pods directly, so no Service is required for the worker (which is why §5.5 only creates a web Service).

```yaml
{{- if .Values.monitoring.podMonitoring }}
{{/*
Gated by a value because the monitoring.googleapis.com/v1 CRD exists only on a
GKE cluster with managed Prometheus enabled. kind does not have it, and an
unknown kind fails `helm install` outright — so deploy/values/kind.yaml sets
this false. This is the one place the chart is environment-aware, and it is a
capability check, not a cloud-provider conditional.
*/}}
{{- range $component := list "web" "worker" }}
---
apiVersion: monitoring.googleapis.com/v1
kind: PodMonitoring
metadata:
  name: {{ include "family-media-bot.name" $ }}-{{ $component }}
  namespace: {{ $.Release.Namespace }}
  labels:
    {{- include "family-media-bot.labels" $ | nindent 4 }}
    app.kubernetes.io/component: {{ $component }}
spec:
  selector:
    matchLabels:
      {{- include "family-media-bot.selectorLabels" $ | nindent 6 }}
      app.kubernetes.io/component: {{ $component }}
  endpoints:
    - port: http
      path: /metrics
      interval: 30s
{{- end }}
{{- end }}
```

Additions to `values.yaml`:

```yaml
monitoring:
  # PodMonitoring requires the managed-Prometheus CRD (GKE only).
  podMonitoring: true
```

to `deploy/values/kind.yaml`:

```yaml
monitoring:
  podMonitoring: false
```

and to `values.schema.json`, alongside `service` and `registry`:

```json
    "monitoring": {
      "type": "object",
      "additionalProperties": false,
      "required": ["podMonitoring"],
      "properties": { "podMonitoring": { "type": "boolean" } }
    }
```

with `"monitoring"` added to the top-level `required` array.

Add one assertion to the `pr / helm` job (§6.1), so the kind values file cannot silently drift into rendering a CRD kind that kind does not have:

```bash
helm template family-media-bot deploy/charts/family-media-bot -n app \
  -f deploy/values/kind.yaml --set-string image.tag=ci \
  | grep -q 'kind: PodMonitoring' && fail "PodMonitoring rendered for kind — the CRD does not exist there"
```

### 8.3 New file: `infra/gcp/monitoring.tf`

One alert. The choice of metric is the interesting part and is worth defending explicitly.

```hcl
# ---------------------------------------------------------------------------
# One alert policy, chosen on purpose.
#
# oldest_unacked_message_age is the highest-yield single signal in this
# architecture because it is an OUTCOME metric, not a cause metric. Every
# meaningful failure mode converges on it:
#
#   worker CrashLoopBackOff  ·  OOMKill  ·  a Vertex quota or 429
#   a lost Workload Identity binding (403 from Vertex or GCS)
#   a bad release that rolled back to a broken revision
#   a node drained during a GKE auto-upgrade with nothing to reschedule onto
#   the ack deadline being shorter than the pipeline
#
# All of them look like "messages are sitting in the subscription unacked".
# The alternative — a dashboard of CPU, memory, restart count, and error rate —
# is five alerts that each catch one cause and collectively still miss the sixth.
#
# The threshold is derived, not guessed: the measured pipeline is 13.7s
# (PRODUCT-DIRECTION.md §3.2), the Pub/Sub ack deadline is 300s
# (pubsub.tf:23), and the warm worker exists precisely so that enqueue-to-start
# is seconds. 120s is ~9x the happy path and well under the ack deadline, so it
# fires before the first redelivery rather than after it.
# ---------------------------------------------------------------------------

resource "google_monitoring_notification_channel" "email" {
  display_name = "${var.project_name} on-call"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.required["monitoring.googleapis.com"]]
}

resource "google_monitoring_alert_policy" "jobs_backlog_stalled" {
  display_name = "${var.project_name}: job backlog is not draining"
  combiner     = "OR"
  severity     = "WARNING"

  conditions {
    display_name = "oldest unacked message older than 120s"

    condition_threshold {
      filter = join(" AND ", [
        "resource.type = \"pubsub_subscription\"",
        "resource.label.subscription_id = \"${google_pubsub_subscription.jobs.name}\"",
        "metric.type = \"pubsub.googleapis.com/subscription/oldest_unacked_message_age\"",
      ])

      comparison      = "COMPARISON_GT"
      threshold_value = 120

      # Must be sustained. Pub/Sub samples this on roughly a 60s interval — the
      # same sampling lag that made KEDA useless (PRODUCT-DIRECTION §3.2) — and
      # a single elevated sample during a rolling update is expected, not a
      # fault. 300s of sustained backlog is not.
      duration = "300s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }

      trigger {
        count = 1
      }
    }
  }

  # Auto-close so a transient stall does not need manual acknowledgement on a
  # project with no on-call rotation.
  alert_strategy {
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    mime_type = "text/markdown"
    subject   = "Family media bot: jobs are queued but not being processed"
    content   = <<-EOT
      Messages on `${google_pubsub_subscription.jobs.name}` have gone unacked for
      more than two minutes. The measured pipeline is ~14s, so this means the
      worker is not consuming, not that it is slow.

      Triage, cheapest first:

      1. `kubectl -n app get pods -l app.kubernetes.io/component=worker`
         — Running? Restarting? Pending (the node pool is fixed at one node)?
      2. `kubectl -n app logs deploy/family-media-bot-worker --tail=100`
         — a 403 here means the Workload Identity binding or an IAM grant is gone.
      3. `helm history family-media-bot -n app`
         — did the last release auto-roll-back? See design §6.3.
      4. Dead letters: topic `${google_pubsub_topic.jobs_dlq.name}`, 5 delivery
         attempts, 14-day retention. A non-empty DLQ means the failure is in the
         pipeline, not in scheduling.

      This alert deliberately has no paging escalation. It is one family's
      bedtime story, not a revenue system.
    EOT
  }

  depends_on = [google_project_service.required["monitoring.googleapis.com"]]
}
```

New variable:

```hcl
variable "alert_email" {
  description = "Address that receives the Pub/Sub backlog alert. A personal address is correct here; there is no rotation."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alert_email))
    error_message = "alert_email must be a single email address."
  }
}
```

Add to `terraform.tfvars.example`:

```hcl
# Receives the "job backlog is not draining" alert. Confirm the Cloud
# Monitoring verification email after the first apply or the channel stays
# unverified and silently drops notifications.
alert_email = "you@example.com"
```

**A gotcha worth knowing:** an email notification channel is created in an *unverified* state and Google sends a confirmation link. Until it is clicked, the policy fires and the channel drops the notification silently. Verify it immediately after the first apply, then test the whole path end to end by scaling the worker to zero for six minutes:

```sh
kubectl -n app scale deploy/family-media-bot-worker --replicas=0
# send one /fairytale, wait ~6 minutes for the 300s duration plus sampling lag
kubectl -n app scale deploy/family-media-bot-worker --replicas=1
```

An alert that has never fired is not an alert. This is the one manual verification step worth adding to QR-5's checklist.

### 8.4 What is deliberately not built

* **No Grafana, no Prometheus Operator, no OTel Collector deployment.** `telemetry.py` already speaks OTLP and `otel_exporter_otlp_endpoint` defaults to `""`, so tracing is opt-in and off. Running a collector on a single `e2-standard-2` alongside the workload would consume a meaningful share of the node it is meant to observe. Cloud Monitoring is already there and already paid for.
* **No dashboard as Terraform.** A `google_monitoring_dashboard` is ~150 lines of JSON that mostly duplicates the GKE workload view Cloud Console gives for free. Worth adding only if there were something non-obvious to show.
* **No SLO / error-budget resources.** `google_monitoring_slo` needs a defined service and a request-based or windows-based SLI. Real, and the right thing at team scale, but it is ceremony on a one-family bot and would read as cargo cult rather than judgement.
* **No log-based metric on ERROR lines.** Tempting and cheap, but it double-counts: every error that matters already ends as an unacked message. A second alert that fires simultaneously with the first is noise.
* **No billing budget in this root.** A budget is a *billing account* resource (`google_billing_budget`), not a project resource, and a trial account frequently will not grant `roles/billing.admin` to the identity running this stack. Set it in the console at ₪600 — see R14. Refusing to put it in Terraform is the correct call, not a gap.

---

## 9. File-by-file change plan

Sequenced. Each numbered block is one PR.

### PR 1 — "Apply ruff formatting and pin the linter" (prerequisite, §6.2)

| Path | Change |
|---|---|
| `app/pyproject.toml` | add `[tool.ruff.lint]` `select` + `ignore = ["BLE001"]` |
| `app/requirements-dev.txt` | **new** — `ruff==0.16.1` |
| `app/family_media_bot/pipeline.py:67`, `telemetry.py:49`, `adapters/image_fake.py:19` | fix E501 |
| 10 files | `ruff format` |
| `Makefile` | `lint`, `fmt` targets; `install` picks up requirements-dev |
| `AGENTS.md` | document `make lint` and the pinned version |

### PR 2 — "Delete AWS and KEDA" (IR-1, IR-2)

Everything in §1.1 and §1.2 except the `gke.tf` rewrite and the new CI files. Largest diff, lowest risk — almost entirely deletion. Verify with §1.3.

### PR 3 — "One node pool" (IR-3)

| Path | Change |
|---|---|
| `infra/gcp/gke.tf:54-125` | replace both pools with `google_container_node_pool.main` (§3.2) |
| `infra/gcp/variables.tf:47-79` | `node_machine_type`, `node_disk_size_gb`, `node_disk_type` |
| `infra/gcp/terraform.tfvars.example` | rewrite |
| `infra/gcp/README.md:7-8` | pool description |
| chart `values.yaml`, `deployment-web.yaml:26-27`, `deployment-worker.yaml:26-29` | drop `nodeSelector` / `tolerations` |
| `README.md`, `ROADMAP.md` | cost table, Spot/scale-to-zero claims |

**Apply with the five-step sequence in §3.5. This is the only PR in the set that takes the bot offline if done wrong.**

### PR 4 — "Character registry ConfigMap" (IR-4)

| Path | Change |
|---|---|
| `deploy/charts/family-media-bot/files/characters.yaml` | **new** |
| `templates/configmap-registry.yaml` | **new** |
| `templates/deployment-web.yaml`, `deployment-worker.yaml` | volume, volumeMount, `CHARACTER_REGISTRY_PATH`, both checksum annotations |
| `values.yaml`, `values.schema.json` | `registry.mountPath` |
| `Makefile` | `run` exports `CHARACTER_REGISTRY_PATH` |

Coordinate merge order with the app-architect: the app must tolerate the env var being absent (fall back to the local default) *before* this lands, or vice versa — otherwise one of the two PRs breaks `make run`.

### PR 5 — "Reshape the Helm chart" (IR-6)

| Path | Change |
|---|---|
| `Chart.yaml` | `0.3.0`, description |
| `values.yaml` | full replacement (§5.2) |
| `values.schema.json` | full replacement (§5.3) |
| `templates/_helpers.tpl` | `family-media-bot.image` helper |
| `templates/service-web.yaml` | **new** |
| `templates/tests/test-healthz.yaml` | **new** |
| `templates/NOTES.txt` | full replacement |
| `templates/configmap.yaml` → `configmap-env.yaml` | rename |
| `deploy/values/gcp.yaml` → `deploy/values/prod.yaml` | rename, **drop `tag:` at line 6** |
| `deploy/values/kind.yaml` | **new** |
| `deploy/README.md`, chart `README.md` | rewrite |
| `Makefile` | `helm-lint`, `helm-template`, `kind-smoke` |

### PR 6 — "Workload Identity Federation for CI" (IR-5)

| Path | Change |
|---|---|
| `infra/gcp/cicd.tf` | **new** (§7.1) |
| `infra/gcp/variables.tf` | four `github_*` / `tf_state_bucket` variables |
| `infra/gcp/apis.tf` | `iam`, `sts`, `cloudresourcemanager` |
| `infra/gcp/outputs.tf` | three CI outputs |
| `infra/gcp/terraform.tfvars.example` | the new variables |
| `infra/gcp/README.md` | a "CI identity" section and how to read the outputs into GitHub variables |

Apply this **before** PR 7, and set the six GitHub variables from its outputs, or PR 7's own CI cannot authenticate.

### PR 7 — "GitHub Actions pipelines" (IR-7)

| Path | Change |
|---|---|
| `.github/workflows/.gitkeep` | delete |
| `.github/workflows/pr.yml` | **new** (§6.1) |
| `.github/workflows/main.yml` | **new** (§6.3) |
| `README.md` | new `## CI/CD` section |
| `ROADMAP.md` | tick the CI/CD items |
| `AGENTS.md` | CI expectations for contributors |

### PR 8 — "Scrape and alert on what we already emit" (§8)

| Path | Change |
|---|---|
| `infra/gcp/gke.tf:42-44` | `managed_prometheus { enabled = true }` — in-place cluster update |
| `infra/gcp/monitoring.tf` | **new** — notification channel + `oldest_unacked_message_age` alert policy |
| `infra/gcp/variables.tf` | `alert_email` |
| `infra/gcp/terraform.tfvars.example` | `alert_email` |
| chart `templates/podmonitoring.yaml` | **new** |
| chart `values.yaml`, `values.schema.json`, `deploy/values/kind.yaml` | `monitoring.podMonitoring` |
| `.github/workflows/pr.yml` | one more rendered-manifest assertion |
| `observability/.gitkeep` (repo root) | delete the empty directory — observability now lives in `infra/gcp/monitoring.tf` and the chart, and an empty placeholder implies a plan that no longer exists |

Independent of PRs 2–7 and non-destructive; can land at any point after PR 1. Verify the email channel and fire the alert deliberately once (§8.3).

### PR 9 — "Terraform safety" (§2.3)

`lifecycle { prevent_destroy = true }` on `google_storage_bucket.media`, `google_pubsub_topic.jobs_dlq`, `google_artifact_registry_repository.app`; explicit `master_auth { client_certificate_config { issue_client_certificate = false } }` on the cluster.

---

## 10. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Node-pool replacement takes the bot down** (§3.5). Deleting the `web` pool deletes the node hosting everything. | Certain if done as one apply | ~4 min outage | Five-step sequence: add pool → helm upgrade → drain → uninstall KEDA → delete pools. |
| R2 | **KEDA uninstalled before the app upgrade removes the ScaledObject.** CRDs vanish, `helm upgrade` fails with `no matches for kind "ScaledObject"`, and the release is stuck. | Moderate — it is the intuitive order | Manual `helm upgrade --force` or editing the release secret | Uninstall KEDA in step 4, never step 1. Documented in §3.5. |
| R3 | **`e2-standard-2` still does not fit** because the real system-pod requests exceed the ~900m estimate (e.g. Dataplane V2 gets enabled). The managed-Prometheus collector from §8.1 is already folded into the estimate at ~5m. | Low | Pods stuck `Pending` | Measure with `kubectl describe node` before PR 3. `node_machine_type` is a variable — bumping to `e2-standard-4` is a one-line change and a node replacement. Note the ~470m of headroom at peak absorbs a considerable estimate error. |
| R4 | **First CI run reports ~40 findings, not 1** (§6.2). | Certain if PR 1 is skipped | The pipeline PR is unreviewable | Land PR 1 first, separately. |
| R5 | **`--atomic` copy-pasted from the spec into the workflow.** Helm 4 exits non-zero on an unknown flag, so the deploy job fails after the image is already pushed. | High — it is written in two places in the source docs | Broken first deploy | §6.0. Fix the wording in SPEC and PRODUCT-DIRECTION too, or someone will reintroduce it. |
| R6 | **`terraform plan` in CI fails because the PR adds `github_*` variables the workflow does not pass.** Chicken-and-egg between PR 6 and PR 7. | High | Red PR | Give `github_repository` a default of the repo path and `github_deploy_ref` a default; only `github_repository_owner_id` and `tf_state_bucket` must come from CI variables, and both are set before PR 7 merges. |
| R7 | **WIF `attribute_condition` too permissive.** Binding `principalSet` on `attribute.repository_owner` or omitting the owner-id check lets other repositories in. | Low if §7 is followed verbatim; **catastrophic** if not | Third-party push access to Artifact Registry and the cluster | The exact CEL in §7.1. Verify after apply with a deliberate negative test: run the deploy workflow from a scratch branch and confirm STS returns `Unable to acquire impersonated credentials`. |
| R8 | **GKE control plane is open to the internet** (§7.6). | Existing condition | An IAM misconfiguration elsewhere becomes remotely exploitable | Accepted and documented. Connect Gateway is the upgrade path. Nothing here makes it worse. |
| R9 | **Terraform state bucket versioning unverified.** A corrupted state write on a bucket without versioning is unrecoverable. | Unknown | Total | One command, before anything else: `gcloud storage buckets describe gs://davidg-tfstate-bucket --format='value(versioning.enabled)'`. |
| R10 | **`helm test` passes but the release is broken**, because the hook only checks `/healthz` (which by contract touches nothing). | Moderate | A green deploy of a bot that cannot reach Vertex | Accepted deliberately — `/readyz` in the hook would make Vertex outages fail deploys. The readinessProbe plus `--wait` already gate on `/readyz`; the hook is the *additional* end-to-end reachability check. |
| R11 | **Registry edit restarts the web pod, dropping the Telegram poller for a few seconds.** | Certain, by design | Seconds of intake delay | Inherent to `strategy: Recreate`, which is itself required by the one-poller-per-token constraint. Telegram queues updates. |
| R12 | **The schema no longer prevents fake adapters reaching prod**, because the enums accept dev values for kind (§5.8). | Low | A deploy that silently generates fake stories | Procedural: `main.yml` hardcodes `-f deploy/values/prod.yaml`. Could be tightened with a rendered-manifest assertion in the deploy job (`grep -q 'STORY_PROVIDER: "vertex"'`). Recommended as a follow-up. |
| R13 | **`kind` is not installed on this machine.** `make kind-smoke` cannot be verified locally today. | Certain | The smoke test is written but unproven | `brew install kind` before PR 5, and run `make kind-smoke` once locally before relying on the CI job. |
| R14 | **Cost drift.** ~$59/mo is the steady state; a forgotten second node, a `latest` image accumulating in Artifact Registry, or a Vertex loop can move it. | Moderate | Trial credit exhausted before the interview | Fixed `node_count = 1` (no autoscaler), 30-day GCS lifecycle already in place (`storage.tf:8-15`), and a billing budget alert at ₪600 — worth adding, though budgets are a billing-account resource outside this Terraform root (§8.4). |
| R15 | **`pyyaml` CrashLoopBackOff** (finding **E**). The image installs `requirements.txt` only, and `pyyaml` is there transitively via `uvicorn[standard]` — a dependency contract nobody has promised. The failure is `ModuleNotFoundError` at import, so both tiers crash, `--rollback-on-failure` reverts, and the symptom points at the registry code rather than at the manifest. | Low today, certain the day `uvicorn[standard]` drops it | Total outage of a deploy that is otherwise correct | One line in `requirements.txt` and one in `pyproject.toml` (§1.2). Land it with or before PR 4. |
| R16 | **The alert never fires because the email channel was never verified** (§8.3). Cloud Monitoring creates email channels unverified and drops notifications silently until the confirmation link is clicked. | Moderate — it is an out-of-band manual step | Believing you have alerting when you do not | Verify immediately after the apply, then deliberately trigger the alert by scaling the worker to zero for six minutes. Add it to QR-5's checklist. |
| R17 | **`PodMonitoring` rendered into kind.** The `monitoring.googleapis.com/v1` CRD does not exist there, and an unknown kind fails `helm install` outright, reddening the smoke job for an unrelated reason. | Low | Broken PR pipeline | `monitoring.podMonitoring: false` in `deploy/values/kind.yaml`, plus the negative assertion in the `pr / helm` job (§8.2). |

---

## 11. Cross-stream notes

* **To the app-architect (IR-4 ↔ FR-1/FR-2).** The contract is in §4.2: `CHARACTER_REGISTRY_PATH=/etc/family-media-bot/registry/characters.yaml`, read once at startup in the lifespan handler, fail loudly on a missing or malformed file. The registry is mounted in **both** deployments so the SPEC §8 open question ("resolve at enqueue or in the worker?") can be answered on application-design grounds alone, with no infrastructure consequence either way. The seed file lives at `deploy/charts/family-media-bot/files/characters.yaml` because Helm's `.Files` cannot escape the chart directory — if you want it elsewhere, that constraint is the thing to argue with.
* **To the reliability-engineer (IR-3 ↔ RR-1/RR-3/RR-6/RR-7).** Three infra decisions land in your area: `worker.maxSurge: 1 / maxUnavailable: 0` (a rollout never leaves the subscription unattended), `terminationGracePeriodSeconds: 120` retained from `deployment-worker.yaml:30` against the 300s `ack_deadline_seconds` at `pubsub.tf:23`, and `--rollback-on-failure` as the RR-6 mechanism — with the caveat in §6.3 that it does **not** cover a `helm test` failure, which is why there is an explicit rollback step. Your `oldest_unacked_message_age > 120s` alert is designed in §8.3, with the threshold derived from the 13.7s measured pipeline and the 300s ack deadline, and a `duration = "300s"` so the ~60s Pub/Sub sampling lag cannot produce a false positive during a rollout — plus the manual verification step (R16) without which the alert is decorative. Two points where this design and yours need an explicit reconciliation: **the PDB** (§5.1 — your `maxUnavailable: 1` correction is accepted as the only acceptable form, but the recommendation is still to omit the object entirely at `replicas: 1`; the disagreement is recorded rather than papered over), and **node sizing** — your `2 × worker` requirement is what drove `e2-standard-2` over `e2-medium`, and the arithmetic is in §3.1 for you to check.
* **To the QA expert (IR-6/IR-7 ↔ QR-3/QR-4).** The rendered-manifest assertions live in the `pr / helm` job (§6.1) and are grep-based; if you want structural assertions, `kubeconform` plus a `yq` suite would slot into the same job. The kind smoke path (`make kind-smoke`, §6.5) is byte-identical to the CI job by construction, which is QR-4. Note R13: `kind` is not installed here yet.
* **Back to SPEC.** Two requirements need amending on the evidence in this document: **IR-7's `--atomic`** (§6.0) and **PRODUCT-DIRECTION §3.8's lint claim** (§6.2). Both are defects in the input documents, not in the design.
