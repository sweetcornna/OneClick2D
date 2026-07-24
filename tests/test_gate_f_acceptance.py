from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spikes.gate_f_runner.acceptance import verify_bundle
from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.local_preflight import run_local_preflight


class GateFAcceptanceBundleTests(unittest.TestCase):
    def test_local_preflight_builds_and_reverifies_bundle(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        index, report = run_local_preflight(root, "run.acceptance")
        self.assertTrue(index.is_file())
        self.assertEqual("LOCAL_TECHNICAL_PREFLIGHT_PASS", report["local_technical_preflight_status"])
        self.assertEqual("GATE_F_NOT_EVALUATED", report["gate_f_status"])
        self.assertFalse(report["ready_for_activated_scoring"])
        self.assertFalse(report["icc_profile_present"])
        self.assertEqual("pending", report["external_editor_status"])
        self.assertEqual(report, verify_bundle(index.parent))

    def test_bundle_tamper_is_rejected(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        index, _ = run_local_preflight(root, "run.tamper")
        candidate = index.parent / "candidate-report.json"
        candidate.write_bytes(candidate.read_bytes() + b" ")
        with self.assertRaises(StageContractError):
            verify_bundle(index.parent)

    def test_reverification_rejects_self_consistent_fake_statistics(self) -> None:
        import json
        from hashlib import sha256
        from spikes.gate_f_runner.runtime import canonical_json_bytes

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        index, _ = run_local_preflight(root, "run.fake-statistics")
        bundle = index.parent
        statistics_path = bundle / "paired-statistics.json"
        statistics = json.loads(statistics_path.read_text(encoding="utf-8"))
        statistics["wins"] = 20
        fake = canonical_json_bytes(statistics)
        statistics_path.write_bytes(fake)
        index_value = json.loads(index.read_text(encoding="utf-8"))
        entry = next(item for item in index_value["entries"] if item["name"] == "paired-statistics.json")
        entry["sha256"] = sha256(fake).hexdigest()
        entry["byte_length"] = len(fake)
        index.write_bytes(canonical_json_bytes(index_value))
        with self.assertRaises(StageContractError):
            verify_bundle(bundle)

    def test_reverification_rejects_reordered_frames_even_when_reindexed(self) -> None:
        import json
        from hashlib import sha256
        from spikes.gate_f_runner.runtime import canonical_json_bytes

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        index, _ = run_local_preflight(root, "run.reordered-frames")
        bundle = index.parent
        comparator_path = bundle / "comparator-report.json"
        report = json.loads(comparator_path.read_text(encoding="utf-8"))
        report["frames"][0], report["frames"][1] = report["frames"][1], report["frames"][0]
        for position, frame in enumerate(report["frames"]):
            frame["index"] = position
        changed = canonical_json_bytes(report)
        comparator_path.write_bytes(changed)
        index_value = json.loads(index.read_text(encoding="utf-8"))
        entry = next(item for item in index_value["entries"] if item["name"] == "comparator-report.json")
        entry["sha256"] = sha256(changed).hexdigest()
        entry["byte_length"] = len(changed)
        index.write_bytes(canonical_json_bytes(index_value))
        with self.assertRaises(StageContractError):
            verify_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
