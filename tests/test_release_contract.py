from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "orchestrate-game-design"
MANIFEST_PATH = SKILL_ROOT / "bundle-manifest.json"
EXPECTED_VERSION = "0.1.2-beta"
EXPECTED_CONTRACT_ID = "human-led-deliverable-first-v1"


class ReleaseContractTests(unittest.TestCase):
    def test_release_identity_is_consistent_across_public_surfaces(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
        workspace = (SKILL_ROOT / "scripts" / "workspace.py").read_text(
            encoding="utf-8"
        )
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertEqual(EXPECTED_VERSION, manifest["version"])
        self.assertEqual(EXPECTED_CONTRACT_ID, manifest["contract_id"])
        self.assertRegex(
            workspace,
            rf'(?m)^VERSION = "{re.escape(EXPECTED_VERSION)}"$',
        )
        self.assertRegex(
            workspace,
            rf'(?m)^CONTRACT_ID = "{re.escape(EXPECTED_CONTRACT_ID)}"$',
        )
        self.assertIn(f"--expect-version {EXPECTED_VERSION}", skill)
        self.assertIn(f"Current development version: `{EXPECTED_VERSION}`", readme)
        self.assertIn(f"development bundle as {EXPECTED_VERSION}", changelog)

    def test_installed_bundle_contains_the_repository_license(self) -> None:
        root_license = (REPOSITORY_ROOT / "LICENSE").read_bytes()
        bundled_license = (SKILL_ROOT / "LICENSE").read_bytes()
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))

        self.assertEqual(root_license, bundled_license)
        self.assertIn("LICENSE", manifest["files"])


if __name__ == "__main__":
    unittest.main()
