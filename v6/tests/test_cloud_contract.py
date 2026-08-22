from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CloudContractTests(unittest.TestCase):
    def workflow(self) -> str:
        return (ROOT / ".github/workflows/sibyl-v6-r1.yml").read_text(encoding="utf-8")

    def test_builder_jobs_never_reference_trading_secret_values(self):
        source = self.workflow()
        gcp = source[source.index("  gcp-inventory:") :]
        for forbidden in (
            "PRIVATE_KEY",
            "LMTS_TOKEN_SECRET",
            "RELAYER_API_KEY",
            "POLY_SECRET",
            "LIVE_ARMED",
            "secrets.",
            "credentials_json",
        ):
            self.assertNotIn(forbidden, gcp)

    def test_keyless_wif_permissions_are_scoped_to_gcp_jobs(self):
        source = self.workflow()
        before_gcp = source[: source.index("  gcp-inventory:")]
        gcp = source[source.index("  gcp-inventory:") :]
        self.assertNotIn("id-token: write", before_gcp)
        self.assertIn("id-token: write", gcp)
        self.assertIn("google-github-actions/auth@7c6bc770", gcp)
        self.assertNotIn("credentials_json", gcp)

    def test_bootstrap_refuses_unbudgeted_apply(self):
        source = (ROOT / "v6/cloud/bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("SIBYL_V6_APPLY", source)
        self.assertIn("SIBYL_V6_COST_AUTHORIZED_USD", source)
        self.assertIn("REFUSING_MUTATION", source)

    def test_builder_is_never_granted_secret_accessor(self):
        source = (ROOT / "v6/cloud/bootstrap.sh").read_text(encoding="utf-8")
        forbidden_binding = 'serviceAccount:$BUILDER_SA" \\\n  --role roles/secretmanager.secretAccessor'
        self.assertNotIn(forbidden_binding, source)
        self.assertIn("BUILDER_SECRET_ACCESSOR_FORBIDDEN", source)

    def test_live_armed_secret_container_has_no_r1_value_or_accessor(self):
        source = (ROOT / "v6/cloud/bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("GCP_LIVE_ARMED_SECRET", source)
        self.assertIn("LIVE_ARMED_ENABLED_VERSIONS=0", source)
        self.assertNotIn("secrets versions add", source)
        self.assertNotIn("versions access", source)

    def test_worker_pool_is_exactly_one_and_r1_has_no_live_armed(self):
        source = (ROOT / "v6/cloud/deploy-worker.sh").read_text(encoding="utf-8")
        self.assertIn("--instances 1", source)
        self.assertIn("DRY_RUN=true", source)
        self.assertIn("SIBYL_V6_LIVE_ALLOWED=false", source)
        self.assertIn("SIBYL_V6_RUN_UPSTREAM=0", source)
        self.assertNotIn("--set-secrets", source)
        self.assertNotIn("LIVE_ARMED=", source)
        self.assertIn("manualInstanceCount", source)

    def test_worker_deploy_requires_digest_not_mutable_tag(self):
        source = (ROOT / "v6/cloud/deploy-worker.sh").read_text(encoding="utf-8")
        self.assertIn("IMAGE_DIGEST", source)
        self.assertIn("@${IMAGE_DIGEST}", source)
        self.assertIn("^sha256:[0-9a-f]{64}$", source)

    def test_region_cannot_be_guessed(self):
        deploy = (ROOT / "v6/cloud/deploy-worker.sh").read_text(encoding="utf-8")
        probes = (ROOT / "v6/cloud/probe-regions.sh").read_text(encoding="utf-8")
        self.assertIn("GCP_WORKER_REGION:?region must come from completed probe evidence", deploy)
        for region in ("us-east1", "us-central1", "southamerica-east1"):
            self.assertIn(region, deploy)
            self.assertIn(region, probes)
        self.assertIn("sibyl_v6.select_region", probes)

    def test_probe_jobs_use_exact_digest_and_are_disposable(self):
        source = (ROOT / "v6/cloud/probe-regions.sh").read_text(encoding="utf-8")
        self.assertIn('IMAGE_REF="${IMAGE_URI}@${IMAGE_DIGEST}"', source)
        self.assertIn("gcloud run jobs delete", source)
        self.assertIn("--max-retries 0", source)
        self.assertIn("--repetitions,5", source)

    def test_deploy_lane_requires_explicit_dispatch_and_positive_cost_gate(self):
        source = self.workflow()
        deploy = source[source.index("  gcp-deploy:") :]
        self.assertIn("github.event_name == 'workflow_dispatch'", deploy)
        self.assertIn("inputs.apply == true", deploy)
        self.assertIn("COST_AUTHORIZED_USD", deploy)
        self.assertIn("value <= 0", deploy)
        self.assertIn("refs/heads/feat/sibyl-v6-cross-market-mm-r1", deploy)

    def test_container_default_is_continuous_exact_pair_paper_observer(self):
        dockerfile = (ROOT / "v6/Dockerfile").read_text(encoding="utf-8")
        observer = (ROOT / "v6/sibyl_v6/paper_cloud_loop.py").read_text(encoding="utf-8")
        selector = (ROOT / "v6/sibyl_v6/live_pair_selector.py").read_text(encoding="utf-8")
        self.assertIn(
            'ENTRYPOINT ["python3", "-m", "sibyl_v6.paper_cloud_loop"]', dockerfile
        )
        self.assertIn("DRY_RUN=true", dockerfile)
        self.assertIn("SIBYL_V6_LIVE_ALLOWED=false", dockerfile)
        self.assertIn("SIBYL_V6_RUN_UPSTREAM=0", dockerfile)
        self.assertIn("audit_current_pairs()", observer)
        self.assertIn("select_current_exact_pair(audit, preferred)", observer)
        self.assertIn("time.sleep(max(args.interval, 1.0))", observer)
        self.assertNotIn("CLOUD_PAPER_EXPECTED_AUDITED_PAIR_NOT_SELECTED", observer)
        self.assertIn("rule_audit.audit_live_pairs()", selector)

    def test_no_gcs_fuse_or_bucket_mount(self):
        cloud = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "v6/cloud").glob("*")
            if path.is_file()
        ).casefold()
        self.assertNotIn("gcsfuse", cloud)
        self.assertNotIn("--add-volume-mount", cloud)


if __name__ == "__main__":
    unittest.main()
