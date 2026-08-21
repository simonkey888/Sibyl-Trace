from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CloudContractTests(unittest.TestCase):
    def test_builder_workflow_never_references_trading_secret_values(self):
        source = (ROOT / ".github/workflows/sibyl-v6-gcp-observe.yml").read_text(encoding="utf-8")
        for forbidden in (
            "PRIVATE_KEY",
            "LMTS_TOKEN_SECRET",
            "RELAYER_API_KEY",
            "POLY_SECRET",
            "LIVE_ARMED",
            "secrets.",
        ):
            self.assertNotIn(forbidden, source)

    def test_keyless_wif_permissions_only(self):
        source = (ROOT / ".github/workflows/sibyl-v6-gcp-observe.yml").read_text(encoding="utf-8")
        self.assertIn("id-token: write", source)
        self.assertIn("google-github-actions/auth@7c6bc770", source)
        self.assertNotIn("credentials_json", source)

    def test_bootstrap_refuses_unbudgeted_apply(self):
        source = (ROOT / "v6/cloud/bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("SIBYL_V6_APPLY", source)
        self.assertIn("SIBYL_V6_COST_AUTHORIZED_USD", source)
        self.assertIn("REFUSING_MUTATION", source)

    def test_builder_is_never_granted_secret_accessor(self):
        source = (ROOT / "v6/cloud/bootstrap.sh").read_text(encoding="utf-8")
        self.assertNotIn("--member \"serviceAccount:$BUILDER_SA\" \\\n  --role roles/secretmanager.secretAccessor", source)
        self.assertIn("BUILDER_SECRET_ACCESSOR_FORBIDDEN", source)

    def test_worker_pool_is_exactly_one_and_r1_has_no_live_armed(self):
        source = (ROOT / "v6/cloud/deploy-worker.sh").read_text(encoding="utf-8")
        self.assertIn("--instances 1", source)
        self.assertIn("DRY_RUN=true", source)
        self.assertIn("SIBYL_V6_LIVE_ALLOWED=false", source)
        self.assertNotIn("--set-secrets", source)
        self.assertNotIn("LIVE_ARMED=", source)

    def test_worker_deploy_requires_digest_not_mutable_tag(self):
        source = (ROOT / "v6/cloud/deploy-worker.sh").read_text(encoding="utf-8")
        self.assertIn("IMAGE_DIGEST", source)
        self.assertIn("@${IMAGE_DIGEST}", source)
        self.assertIn("^sha256:[0-9a-f]{64}$", source)

    def test_region_cannot_be_guessed(self):
        source = (ROOT / "v6/cloud/deploy-worker.sh").read_text(encoding="utf-8")
        self.assertIn("GCP_WORKER_REGION:?region must come from completed probe evidence", source)
        for region in ("us-east1", "us-central1", "southamerica-east1"):
            self.assertIn(region, source)

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
