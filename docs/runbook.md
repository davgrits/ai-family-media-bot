# Operating runbook

The short operating and explanation guide for the live GCP deployment.

## Thirty-second architecture

Terraform creates a zonal private-node GKE cluster, Artifact Registry, Pub/Sub,
GCS, Vertex AI permissions, and two Google service accounts. Helm installs the
application. Workload Identity maps the Kubernetes service account to a Google
service account, so no JSON key is ever downloaded.

The web pod long-polls Telegram and publishes a frozen `Job` JSON contract to
Pub/Sub. A warm worker holds one long-lived streaming pull on the subscription,
generates a story and image with Vertex AI, stores the image in GCS, replies
through Telegram, and acknowledges the delivery. Failures are nacked and after
five attempts reach the dead-letter topic.

```text
Telegram -> web pod -> Pub/Sub -> worker pod
               |                    -> Vertex AI
               |                    -> GCS
               `---- Workload Identity ----^
```

## Why the worker is warm and not scaled to zero

The most load-bearing operational decision here, and it was measured rather than
assumed.

| Stage | Measured |
|---|---|
| Enqueue → autoscaler activation | 282.7 s (4 m 43 s) |
| Spot node startup | ~46 s |
| Actual pipeline work | 13.7 s |
| Worker kept alive after the reply (300 s cooldown + metric lag) | ~7 m 39 s |

The activation delay is not a streaming-pull problem. Google samples the Pub/Sub
backlog metric on roughly a 60-second interval, documents additional delay before
it becomes visible, and warns that the metric can gap for several minutes.
Google's own autoscaling guidance recommends a minimum task count above zero when
low latency matters.

So: ~4m43s of activation to save a few shekels of idle compute, on a bot where a
child is waiting. Raising the cooldown would not help — it increases idle cost
without touching the first request after a quiet period.

Two consequences worth stating out loud:

1. **A fixed replica count needs its own health signal.** The autoscaler's
   cooldown was an accidental supervisor: if the Pub/Sub stream died, scaling to
   zero and back rebuilt the pod. A warm single replica does not get that for
   free, so stream liveness has to become an explicit probe.
2. **Warm and Spot contradict each other.** Spot nodes are preempted, and a warm
   worker on preemptible capacity reintroduces the gap warmth was meant to remove.

For this workload Cloud Run would be cheaper and simpler — scale-to-zero with no
metric lag. GKE was chosen deliberately, because the target environment is GKE.

## Verify a healthy deployment

```bash
kubectl get nodes -L role
kubectl -n app get deployments,pods
helm list -n app
```

Expect both tiers `1/1` and no autoscaling objects.

Readiness detail:

```bash
kubectl -n app port-forward deploy/family-media-bot-web 8080:8080 &
curl -s localhost:8080/readyz | python3 -m json.tool
```

`/readyz` checks that Application Default Credentials resolve and that the queue
and bucket are reachable. It does **not** prove Vertex AI works: the story and
image checks only resolve credentials, because a readiness probe must not bill a
model call every 15 seconds.

`/healthz` deliberately calls nothing. A cloud outage must not become a restart
loop.

## Watch a job end to end

```bash
kubectl -n app logs deploy/family-media-bot-web -f
kubectl -n app logs deploy/family-media-bot-worker -f
```

Send `/fairytale` and expect:
`message received → job enqueued → Pub/Sub message received → job started →
story done → image done → saved → replied → job completed`.

`story done` carries `tokens_out` and `tokens_thought`. The split matters:
reasoning tokens are billed at the output rate but excluded from the visible
output count, so a cost figure that ignores them understates every job.

## Failure modes to know

| Symptom | Likely cause | Check |
|---|---|---|
| Bot silent, worker pod `Running` and `Ready`, zero restarts | The Pub/Sub streaming subscriber terminated permanently. Both probes stay green — this is the one that hides. | worker logs for `streaming subscriber stopped`, and the subscription's `oldest_unacked_message_age` |
| Story arrives truncated mid-sentence | Should now be impossible: only `FinishReason.STOP` is accepted | worker logs for `stopped early` |
| Illustration is a blank gradient | Should now be impossible: the image adapter raises rather than substituting a placeholder | worker logs for `job failed` |
| Per-job cost reported as `$0` | Should now be impossible: an unpriced model id refuses to start | worker pod events |
| Five apologies for one request | Regression — the chat should be notified only on the final delivery attempt | worker logs for `job failed` and its `final` field |
| Telegram 409 | Two `getUpdates` consumers on one token, usually a local `make demo` alongside the cluster | stop the local process |

## Deploy

See the [chart guide](../deploy/charts/family-media-bot/README.md). In short:

```bash
helm upgrade --install family-media-bot deploy/charts/family-media-bot \
  --namespace app -f deploy/values/prod.yaml \
  --set-string image.tag='<git-sha>' --rollback-on-failure --wait --timeout 5m
```

`--rollback-on-failure` is Helm 4's replacement for `--atomic`.

## Ownership boundaries

- **Terraform** owns cloud resources: network, cluster, node pool, Pub/Sub, GCS,
  Artifact Registry, service accounts, IAM bindings.
- **Helm** owns Kubernetes objects: Deployments, ConfigMap, service account.
- **Neither owns the Telegram token.** It is created imperatively as a Secret, so
  its value never enters a values file or the Helm release.

## Questions worth having answers to

- **Why two tiers if both are warm?** Their latency and failure profiles differ.
  Intake must answer Telegram in milliseconds; generation takes ~14 s. Separate
  deployments mean a generation backlog cannot stop the bot from answering, and
  the two can be scaled independently later without restructuring.
- **Why Pub/Sub at all, if the worker is always warm?** Durability and retry. A
  worker restart mid-generation returns the job to the queue instead of losing a
  child's story, and the DLQ captures what repeatedly fails.
- **Why `gemini-2.5-flash-image` over Imagen 4?** Imagen has higher raw fidelity
  but is text-to-image only. Gemini Flash Image accepts reference images, which is
  what keeps the same family character recognisable across illustrations. For this
  product, consistency beats fidelity.
- **Why is the image tag not in the values file?** So a release always traces to a
  commit, and a committed tag cannot drift from what is actually running.
