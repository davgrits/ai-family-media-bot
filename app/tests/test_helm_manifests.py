"""Assertions on the *rendered* Helm manifests.

`helm template` exiting 0 only proves the templates are syntactically valid. It
says nothing about whether the worker has one replica, whether the registry is
mounted, or whether a ConfigMap edit will actually reach the pods — all of which
have been wrong in this chart at some point.

Skipped when helm is unavailable so `make test` still works on a bare machine.
Set REQUIRE_HELM=1 in CI to turn a missing helm into a failure rather than a
silent pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).parents[2]
CHART = REPO / "deploy" / "charts" / "family-media-bot"
VALUES = REPO / "deploy" / "values" / "prod.yaml"

_HELM = shutil.which("helm")
_REQUIRED = os.environ.get("REQUIRE_HELM") == "1"


def render(*extra: str) -> list[dict]:
    result = subprocess.run(
        [
            _HELM,
            "template",
            "release",
            str(CHART),
            "--namespace",
            "app",
            "-f",
            str(VALUES),
            "--set",
            "image.tag=ci",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


@unittest.skipUnless(_HELM or _REQUIRED, "helm not installed")
class RenderedManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _HELM:
            raise AssertionError("REQUIRE_HELM=1 but helm is not installed")
        cls.docs = render()
        cls.by_kind: dict[str, list[dict]] = {}
        for doc in cls.docs:
            cls.by_kind.setdefault(doc["kind"], []).append(doc)

    def _deployment(self, component: str) -> dict:
        for doc in self.by_kind["Deployment"]:
            if (
                doc["spec"]["template"]["metadata"]["labels"].get("app.kubernetes.io/component")
                == component
            ):
                return doc
        raise AssertionError(f"no {component} deployment rendered")

    def _pod_specs(self) -> list[dict]:
        return [d["spec"]["template"]["spec"] for d in self.by_kind["Deployment"]]

    # --- shape ---------------------------------------------------------------

    def test_expected_objects_render(self) -> None:
        self.assertEqual(len(self.by_kind["Deployment"]), 2)
        self.assertEqual(len(self.by_kind["ConfigMap"]), 2)
        self.assertEqual(len(self.by_kind["Service"]), 1)
        self.assertEqual(len(self.by_kind["ServiceAccount"]), 1)

    def test_no_autoscaling_objects_render(self) -> None:
        # KEDA was removed deliberately; a ScaledObject reappearing would mean
        # the cold start came back with it.
        self.assertNotIn("ScaledObject", self.by_kind)
        self.assertNotIn("HorizontalPodAutoscaler", self.by_kind)

    def test_worker_has_exactly_one_replica(self) -> None:
        self.assertEqual(self._deployment("worker")["spec"]["replicas"], 1)

    def test_worker_surges_before_terminating(self) -> None:
        # A registry edit must not leave the queue unattended.
        strategy = self._deployment("worker")["spec"]["strategy"]
        self.assertEqual(strategy["type"], "RollingUpdate")
        self.assertEqual(strategy["rollingUpdate"]["maxSurge"], 1)
        self.assertEqual(strategy["rollingUpdate"]["maxUnavailable"], 0)

    def test_web_recreates_rather_than_rolls(self) -> None:
        # Telegram allows one getUpdates consumer per token, so two overlapping
        # web pods would give one of them 409s.
        self.assertEqual(self._deployment("web")["spec"]["strategy"]["type"], "Recreate")

    # --- the registry --------------------------------------------------------

    def test_registry_configmap_contains_the_cast(self) -> None:
        names = [c["metadata"]["name"] for c in self.by_kind["ConfigMap"]]
        self.assertIn("family-media-bot-registry", names)

        registry = next(
            c for c in self.by_kind["ConfigMap"] if c["metadata"]["name"].endswith("-registry")
        )
        parsed = yaml.safe_load(registry["data"]["characters.yaml"])
        self.assertGreater(len(parsed["characters"]), 0)

    def test_both_tiers_mount_the_registry_read_only(self) -> None:
        for spec in self._pod_specs():
            mount = spec["containers"][0]["volumeMounts"][0]
            self.assertTrue(mount["readOnly"])
            self.assertEqual(mount["name"], "registry")

    def test_registry_is_not_mounted_with_subpath(self) -> None:
        # subPath stops the kubelet propagating ConfigMap updates to the file.
        for spec in self._pod_specs():
            self.assertNotIn("subPath", spec["containers"][0]["volumeMounts"][0])

    def test_both_tiers_are_told_where_the_registry_is(self) -> None:
        for spec in self._pod_specs():
            env = {e["name"]: e.get("value") for e in spec["containers"][0]["env"]}
            self.assertTrue(env["CHARACTERS_FILE"].endswith("/characters.yaml"))
            self.assertEqual(env["CHARACTERS_REQUIRED"], "true")

    def test_a_registry_edit_changes_both_pod_templates(self) -> None:
        """The acceptance criterion. Without the checksum annotation, editing the
        registry updates the ConfigMap and leaves the pods serving the old cast —
        a green deploy that changed nothing."""
        before = [
            d["spec"]["template"]["metadata"]["annotations"]["checksum/registry"]
            for d in self.by_kind["Deployment"]
        ]
        self.assertEqual(len(set(before)), 1, "both tiers should agree on the checksum")

        path = CHART / "files" / "characters.yaml"
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "\n# edit\n", encoding="utf-8")
            after = [
                d["spec"]["template"]["metadata"]["annotations"]["checksum/registry"]
                for d in render()
                if d["kind"] == "Deployment"
            ]
        finally:
            path.write_text(original, encoding="utf-8")

        self.assertNotEqual(set(before), set(after))

    # --- leftovers and secrets ----------------------------------------------

    def test_no_aws_configuration_survives(self) -> None:
        blob = json.dumps(self.docs).lower()
        for term in ("sqs", "bedrock", "s3_bucket", "aws_region", "keda"):
            with self.subTest(term=term):
                self.assertNotIn(term, blob)

    def test_the_telegram_token_is_referenced_never_inlined(self) -> None:
        for spec in self._pod_specs():
            token = next(
                e for e in spec["containers"][0]["env"] if e["name"] == "TELEGRAM_BOT_TOKEN"
            )
            self.assertIn("secretKeyRef", token["valueFrom"])
            self.assertNotIn("value", token)

    def test_containers_run_unprivileged_as_a_non_root_user(self) -> None:
        for spec in self._pod_specs():
            ctx = spec["containers"][0]["securityContext"]
            self.assertTrue(ctx["runAsNonRoot"])
            self.assertFalse(ctx["allowPrivilegeEscalation"])
            self.assertEqual(ctx["capabilities"]["drop"], ["ALL"])

    def test_the_helm_test_hook_targets_healthz_not_readyz(self) -> None:
        # A release test should answer "did this come up", not "is Google up".
        pod = self.by_kind["Pod"][0]
        self.assertEqual(pod["metadata"]["annotations"]["helm.sh/hook"], "test")
        self.assertIn("/healthz", " ".join(pod["spec"]["containers"][0]["args"]))


@unittest.skipUnless(_HELM, "helm not installed")
class SchemaEnforcementTests(unittest.TestCase):
    def _rejects(self, *overrides: str) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            render(*overrides)

    def test_a_release_without_an_image_tag_is_rejected(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(
                [_HELM, "template", "r", str(CHART), "-n", "app", "-f", str(VALUES)],
                capture_output=True,
                text=True,
                check=True,
            )

    def test_a_worker_replica_count_of_zero_is_rejected(self) -> None:
        # Scale-to-zero was removed on purpose; the schema stops it returning by
        # values file rather than by code change.
        self._rejects("--set", "worker.replicaCount=0")

    def test_an_adapter_the_app_would_refuse_is_rejected(self) -> None:
        self._rejects("--set", "app.adapters.queue=sqs")


if __name__ == "__main__":
    unittest.main()
