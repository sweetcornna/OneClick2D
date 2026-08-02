from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from spikes.gate_f_runner.acceptance import MAX_BUNDLE_ARTIFACT_BYTES, verify_bundle
from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.local_preflight import run_local_preflight
from spikes.gate_f_runner.paired_experiment import PairOutcome, evaluate_experiment
from spikes.gate_f_runner.runtime import canonical_json_bytes, read_bounded_file as runtime_read_bounded_file


def rewrite_indexed_artifact(bundle: Path, name: str, data: bytes) -> None:
    artifact = bundle / name
    artifact.write_bytes(data)
    index = bundle / "bundle-index.json"
    index_value = json.loads(index.read_text(encoding="utf-8"))
    descriptor = next(item for item in index_value["entries"] if item["name"] == name)
    descriptor["sha256"] = sha256(data).hexdigest()
    descriptor["byte_length"] = len(data)
    index.write_bytes(canonical_json_bytes(index_value))


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFAcceptanceBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL

        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("acceptance preflight requires locked Pillow 12.1.0")

    def _run_verify_bundle_cli(self, bundle: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "spikes.gate_f_runner", "verify-bundle", "--bundle", str(bundle)],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )

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

    def test_reverification_binds_indexed_frame_bytes_to_report_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.frame-descriptor-binding")
            bundle = index.parent
            target = bundle / "candidate-frame-000.png"
            original_frame = target.read_bytes()
            original_index = index.read_bytes()
            replacements = {
                "invalid-png": b"not a PNG",
                "valid-wrong-png": (bundle / "candidate-frame-001.png").read_bytes(),
            }
            for label, replacement in replacements.items():
                with self.subTest(label=label):
                    target.write_bytes(original_frame)
                    index.write_bytes(original_index)
                    rewrite_indexed_artifact(bundle, "candidate-frame-000.png", replacement)
                    with self.assertRaisesRegex(StageContractError, "frame evidence"):
                        verify_bundle(bundle)

    def test_reverification_rejects_mismatched_frame_bytes_before_decode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.frame-byte-mismatch")
            bundle = index.parent
            frame_path = bundle / "candidate-frame-000.png"
            changed = bytearray(frame_path.read_bytes())
            changed[-1] ^= 1
            frame_path.write_bytes(changed)

            with (
                patch("spikes.gate_f_runner.acceptance._verify_frame_png") as verify_png,
                patch("spikes.gate_f_runner.acceptance.parse_layered_psd") as parse_psd,
            ):
                with self.assertRaisesRegex(StageContractError, "bundle artifact digest mismatch"):
                    verify_bundle(bundle)
            verify_png.assert_not_called()
            parse_psd.assert_not_called()

    def test_reverification_rejects_coherent_same_size_frame_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.coherent-frame-rewrite")
            bundle = index.parent
            replacement = (bundle / "candidate-frame-001.png").read_bytes()
            rewrite_indexed_artifact(bundle, "candidate-frame-000.png", replacement)
            report = json.loads((bundle / "candidate-report.json").read_text(encoding="utf-8"))
            report["frames"][0]["artifact"]["sha256"] = sha256(replacement).hexdigest()
            report["frames"][0]["artifact"]["byte_length"] = len(replacement)
            rewrite_indexed_artifact(bundle, "candidate-report.json", canonical_json_bytes(report))

            with self.assertRaisesRegex(StageContractError, "purpose-created fixture"):
                verify_bundle(bundle)

    def test_reverification_rejects_self_consistently_reindexed_frame_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.frame-parameters")
            bundle = index.parent
            report_path = bundle / "candidate-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["frames"][0]["parameters"]["head.yaw"] = 1
            rewrite_indexed_artifact(bundle, "candidate-report.json", canonical_json_bytes(report))
            with self.assertRaisesRegex(StageContractError, "frame evidence"):
                verify_bundle(bundle)

    def test_reverification_rejects_non_contract_paired_outcome_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.outcome-contract")
            bundle = index.parent
            outcomes_path = bundle / "paired-outcomes.json"
            statistics_path = bundle / "paired-statistics.json"
            original_outcomes = outcomes_path.read_bytes()
            original_statistics = statistics_path.read_bytes()
            original_index = index.read_bytes()

            string_booleans = json.loads(original_outcomes)
            for row in string_booleans:
                row["f_usable"] = "false"
            self_consistent_statistics = json.loads(original_statistics)
            self_consistent_statistics["f_usable_count"] = 20
            outcomes_path.write_bytes(original_outcomes)
            statistics_path.write_bytes(original_statistics)
            index.write_bytes(original_index)
            rewrite_indexed_artifact(bundle, "paired-outcomes.json", canonical_json_bytes(string_booleans))
            rewrite_indexed_artifact(bundle, "paired-statistics.json", canonical_json_bytes(self_consistent_statistics))
            with self.subTest(mutation="string-f-usable-with-reindexed-statistics"):
                with self.assertRaisesRegex(StageContractError, "paired outcome evidence"):
                    verify_bundle(bundle)

            def integer_f_usable(rows: list[dict[str, object]]) -> None:
                rows[0]["f_usable"] = 1

            def null_f_usable(rows: list[dict[str, object]]) -> None:
                rows[12]["f_usable"] = None

            def missing_f_usable(rows: list[dict[str, object]]) -> None:
                del rows[0]["f_usable"]

            def extra_field(rows: list[dict[str, object]]) -> None:
                rows[0]["extra"] = False

            def duplicate_asset_id(rows: list[dict[str, object]]) -> None:
                rows[1]["asset_id"] = rows[0]["asset_id"]

            def invalid_asset_id(rows: list[dict[str, object]]) -> None:
                rows[0]["asset_id"] = "x" * 129

            def invalid_reason(rows: list[dict[str, object]]) -> None:
                rows[0]["reason"] = "unknown"

            def invalid_outcome(rows: list[dict[str, object]]) -> None:
                rows[0]["outcome"] = "winner"

            mutations = {
                "integer-f-usable": integer_f_usable,
                "null-f-usable": null_f_usable,
                "missing-f-usable": missing_f_usable,
                "extra-field": extra_field,
                "duplicate-asset-id": duplicate_asset_id,
                "invalid-asset-id": invalid_asset_id,
                "invalid-reason": invalid_reason,
                "invalid-outcome": invalid_outcome,
            }
            for label, mutate in mutations.items():
                with self.subTest(mutation=label):
                    outcomes_path.write_bytes(original_outcomes)
                    statistics_path.write_bytes(original_statistics)
                    index.write_bytes(original_index)
                    changed = json.loads(original_outcomes)
                    mutate(changed)
                    rewrite_indexed_artifact(bundle, "paired-outcomes.json", canonical_json_bytes(changed))
                    with self.assertRaisesRegex(StageContractError, "paired outcome evidence"):
                        verify_bundle(bundle)

    def test_indexed_extra_regular_file_is_rejected_before_artifact_open_or_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index, _ = run_local_preflight(root, "run.extra-regular")
            bundle = index.parent
            extra = b"{}"
            (bundle / "extra.json").write_bytes(extra)
            value = json.loads(index.read_text(encoding="utf-8"))
            value["entries"].append({"name": "extra.json", "sha256": "0" * 64, "byte_length": len(extra)})
            index.write_bytes(canonical_json_bytes(value))
            with (
                patch("spikes.gate_f_runner.acceptance.read_bounded_file") as read_file,
                patch("spikes.gate_f_runner.acceptance.sha256") as digest,
            ):
                with self.assertRaisesRegex(StageContractError, "unindexed or missing files"):
                    verify_bundle(bundle)
            read_file.assert_not_called()
            digest.assert_not_called()

    def test_colon_ads_index_alias_is_rejected_before_artifact_open_or_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index, _ = run_local_preflight(root, "run.colon-alias")
            value = json.loads(index.read_text(encoding="utf-8"))
            value["entries"][0]["name"] = "candidate-report.json:alias"
            index.write_bytes(canonical_json_bytes(value))
            with (
                patch("spikes.gate_f_runner.acceptance.read_bounded_file", wraps=runtime_read_bounded_file) as read_file,
                patch("spikes.gate_f_runner.acceptance.sha256") as digest,
            ):
                with self.assertRaisesRegex(StageContractError, "entry name"):
                    verify_bundle(index.parent)
            self.assertEqual([index], [call.args[0] for call in read_file.call_args_list])
            digest.assert_not_called()

    def test_aggregate_byte_budget_is_rejected_before_artifact_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.aggregate-byte-budget")
            value = json.loads(index.read_text(encoding="utf-8"))
            value["entries"][0]["byte_length"] = MAX_BUNDLE_ARTIFACT_BYTES
            index.write_bytes(canonical_json_bytes(value))

            with patch(
                "spikes.gate_f_runner.acceptance.read_bounded_file",
                wraps=runtime_read_bounded_file,
            ) as read_file:
                with self.assertRaisesRegex(StageContractError, "bundle aggregate byte budget exceeded"):
                    verify_bundle(index.parent)
            self.assertEqual([index], [call.args[0] for call in read_file.call_args_list])

    def test_per_artifact_overdeclared_length_is_rejected_before_artifact_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.artifact-byte-budget")
            value = json.loads(index.read_text(encoding="utf-8"))
            value["entries"][0]["byte_length"] = MAX_BUNDLE_ARTIFACT_BYTES + 1
            index.write_bytes(canonical_json_bytes(value))

            with patch(
                "spikes.gate_f_runner.acceptance.read_bounded_file",
                wraps=runtime_read_bounded_file,
            ) as read_file:
                with self.assertRaisesRegex(StageContractError, "bundle entry name, digest or size is invalid"):
                    verify_bundle(index.parent)
            self.assertEqual([index], [call.args[0] for call in read_file.call_args_list])

    def test_direct_verifier_and_cli_reject_symlinked_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index, _ = run_local_preflight(root, "run.symlink-artifact")
            bundle = index.parent
            artifact = bundle / "candidate-report.json"
            outside = root / "outside-candidate-report.json"
            artifact.replace(outside)
            try:
                artifact.symlink_to(outside)
            except OSError:
                outside.replace(artifact)
                self.skipTest("file symlinks are unavailable")

            with self.assertRaisesRegex(StageContractError, "bundle artifact"):
                verify_bundle(bundle)
            completed = self._run_verify_bundle_cli(bundle)
            self.assertEqual(70, completed.returncode)
            self.assertIn("bundle verification failed", completed.stderr)
            self.assertNotIn(str(bundle), completed.stderr)

    @unittest.skipUnless(sys.platform == "win32", "Windows reparse points are unavailable")
    def test_direct_verifier_and_cli_reject_extra_reparse_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index, _ = run_local_preflight(root, "run.reparse-artifact")
            bundle = index.parent
            outside = root / "outside-artifact"
            outside.mkdir()
            artifact = bundle / "extra-artifact"
            completed = subprocess.run(
                f'mklink /J "{artifact}" "{outside}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(artifact, "is_junction", lambda: False)():
                self.skipTest("artifact reparse points are unavailable")
            try:
                with self.assertRaisesRegex(StageContractError, "bundle artifact"):
                    verify_bundle(bundle)
                completed = self._run_verify_bundle_cli(bundle)
                self.assertEqual(70, completed.returncode)
                self.assertIn("bundle verification failed", completed.stderr)
                self.assertNotIn(str(bundle), completed.stderr)
            finally:
                os.rmdir(artifact)

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_direct_verifier_and_cli_reject_junctioned_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index, _ = run_local_preflight(root, "run.junction-source")
            source_bundle = index.parent
            junction = root / "run.junction.bundle"
            completed = subprocess.run(
                f'mklink /J "{junction}" "{source_bundle}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(junction, "is_junction", lambda: False)():
                self.skipTest("directory junctions are unavailable")
            try:
                with self.assertRaisesRegex(StageContractError, "bundle directory"):
                    verify_bundle(junction)
                completed = self._run_verify_bundle_cli(junction)
                self.assertEqual(70, completed.returncode)
                self.assertIn("bundle verification failed", completed.stderr)
                self.assertNotIn(str(junction), completed.stderr)
            finally:
                os.rmdir(junction)

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

    def test_reverification_rejects_forged_outcomes_with_recomputed_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.forged-outcomes")
            bundle = index.parent
            outcomes = json.loads((bundle / "paired-outcomes.json").read_text(encoding="utf-8"))
            outcomes[13]["outcome"] = "candidate_win"
            recomputed = evaluate_experiment(
                PairOutcome(row["asset_id"], row["outcome"], row["f_usable"], row["reason"])
                for row in outcomes
            )
            rewrite_indexed_artifact(bundle, "paired-outcomes.json", canonical_json_bytes(outcomes))
            rewrite_indexed_artifact(bundle, "paired-statistics.json", canonical_json_bytes(recomputed))

            with self.assertRaisesRegex(StageContractError, "purpose-created fixture"):
                verify_bundle(bundle)

    def test_reverification_rejects_python_equal_statistics_type_substitutions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index, _ = run_local_preflight(Path(directory), "run.statistics-types")
            bundle = index.parent
            statistics_path = bundle / "paired-statistics.json"
            original_statistics = statistics_path.read_bytes()
            original_index = index.read_bytes()
            mutations = {"integer-as-float": ("asset_count", 20.0), "boolean-as-integer": ("primary_pair_rule_pass", 1)}
            for label, (field, replacement) in mutations.items():
                with self.subTest(label=label):
                    statistics_path.write_bytes(original_statistics)
                    index.write_bytes(original_index)
                    statistics = json.loads(original_statistics)
                    statistics[field] = replacement
                    changed = json.dumps(statistics, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    rewrite_indexed_artifact(bundle, "paired-statistics.json", changed)
                    with self.assertRaisesRegex(StageContractError, "statistics do not match"):
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
