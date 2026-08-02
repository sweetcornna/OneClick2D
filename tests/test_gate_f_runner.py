from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from spikes.gate_f_runner.contracts import SpecValidationError, StageStatus
from spikes.gate_f_runner.runner import PipelineRunner, load_run_spec
from spikes.gate_f_runner.runtime import (
    RunWorkspace,
    contained_run_path,
    derive_stage_seed,
    sha256_file,
    strict_load_json_bytes,
)
from spikes.gate_f_runner.synthetic import build_synthetic_registry

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "gate-f-spike-smoke"
SPEC = FIXTURE / "run-spec.json"
SOURCE = FIXTURE / "source.synthetic.json"


class GateFRunnerTests(unittest.TestCase):
    def test_stage_observer_is_ordered_and_non_evidentiary(self) -> None:
        events: list[tuple[str, str, StageStatus | None]] = []
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_status, first_manifest_path = PipelineRunner(build_synthetic_registry(), Path(first)).run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.observed",
                source_revision="source.test",
                build_id="build.test",
                stage_observer=lambda stage, event, status: events.append((stage, event, status)),
            )
            second_status, second_manifest_path = PipelineRunner(build_synthetic_registry(), Path(second)).run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.unobserved",
                source_revision="source.test",
                build_id="build.test",
            )
            self.assertEqual(StageStatus.SUCCEEDED, first_status)
            self.assertEqual(StageStatus.SUCCEEDED, second_status)
            self.assertEqual(
                [
                    ("stage.synthetic-normalize", "started", None),
                    ("stage.synthetic-normalize", "completed", StageStatus.SUCCEEDED),
                    ("stage.synthetic-proposal", "started", None),
                    ("stage.synthetic-proposal", "completed", StageStatus.SUCCEEDED),
                    ("stage.synthetic-verify", "started", None),
                    ("stage.synthetic-verify", "completed", StageStatus.SUCCEEDED),
                ],
                events,
            )
            first_manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
            second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(first_manifest["result"]["sha256"], second_manifest["result"]["sha256"])
            self.assertNotIn("observer", first_manifest)

    def test_stage_observer_failure_does_not_fail_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def fail_observer(stage: str, event: str, status: StageStatus | None) -> None:
                raise RuntimeError("observer failure")

            status, manifest_path = PipelineRunner(build_synthetic_registry(), Path(directory)).run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.observer-failure",
                source_revision="source.test",
                build_id="build.test",
                stage_observer=fail_observer,
            )
            self.assertEqual(StageStatus.SUCCEEDED, status)
            self.assertEqual("succeeded", json.loads(manifest_path.read_text(encoding="utf-8"))["terminal_status"])

    def test_smoke_run_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_status, first_manifest_path = PipelineRunner(
                build_synthetic_registry(), Path(first)
            ).run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.first",
                source_revision="source.test",
                build_id="build.test",
            )
            second_status, second_manifest_path = PipelineRunner(
                build_synthetic_registry(), Path(second)
            ).run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.second",
                source_revision="source.test",
                build_id="build.test",
            )
            self.assertEqual(StageStatus.SUCCEEDED, first_status)
            self.assertEqual(StageStatus.SUCCEEDED, second_status)
            first_manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
            second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(first_manifest["result"]["sha256"], second_manifest["result"]["sha256"])
            self.assertEqual(
                [stage["seed_u64"] for stage in first_manifest["stages"]],
                [stage["seed_u64"] for stage in second_manifest["stages"]],
            )
            self.assertEqual(
                [stage["resolved_stage_spec_sha256"] for stage in first_manifest["stages"]],
                [stage["resolved_stage_spec_sha256"] for stage in second_manifest["stages"]],
            )
            for manifest_path, manifest in ((first_manifest_path, first_manifest), (second_manifest_path, second_manifest)):
                run_dir = manifest_path.parent
                self.assertEqual(3, len(manifest["stages"]))
                self.assertEqual("disposable-gate-f-spike", manifest["scope"])
                for stage in manifest["stages"]:
                    self.assertTrue(stage["scratch_cleaned"])
                    for artifact in stage["outputs"]:
                        self.assertEqual(artifact["sha256"], sha256_file(run_dir / artifact["uri"]))
                forbidden = {".oc2d", ".psd", ".moc3", ".onnx", ".pt", ".pth"}
                self.assertFalse(any(path.suffix.lower() in forbidden for path in run_dir.rglob("*")))

    def test_contained_run_path_rejects_lexical_traversal_and_nonregular_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.contained"
            run_dir.mkdir()
            (run_dir / "artifact.bin").write_bytes(b"artifact")

            self.assertEqual(
                run_dir / "artifact.bin",
                contained_run_path(root, run_dir.name, "artifact.bin", kind="file"),
            )
            for relative in ("../outside.bin", "nested/../../outside.bin", "/outside.bin", "C:/outside.bin", "nested\\outside.bin", "artifact.bin:stream"):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    contained_run_path(root, run_dir.name, relative, kind="file")

            nonregular = run_dir / "nonregular"
            nonregular.mkdir()
            with self.assertRaises(ValueError):
                contained_run_path(root, run_dir.name, nonregular.name, kind="file")

    def test_contained_run_path_rejects_symlinked_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (outside / "artifact.bin").write_bytes(b"artifact")
            linked_run = root / "run.linked"
            try:
                linked_run.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            with self.assertRaises(ValueError):
                contained_run_path(root, linked_run.name, "artifact.bin", kind="file")

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_contained_run_path_rejects_junctioned_run_directory(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (outside / "artifact.bin").write_bytes(b"artifact")
            junction = root / "run.junction"
            completed = subprocess.run(
                f'mklink /J "{junction}" "{outside}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(junction, "is_junction", lambda: False)():
                self.skipTest("directory junctions are unavailable")
            try:
                with self.assertRaises(ValueError):
                    contained_run_path(root, junction.name, "artifact.bin", kind="file")
            finally:
                os.rmdir(junction)

    @unittest.skipUnless(sys.platform == "win32", "Windows drive-relative paths are platform-specific")
    def test_drive_relative_workspace_is_rejected_before_run_or_model_creation(self) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from spikes.gate_f_runner.__main__ import _model

        workspace = Path("C:relative\\workspace")
        with patch("spikes.gate_f_runner.runtime.Path.mkdir", wraps=Path.mkdir) as mkdir:
            with self.assertRaisesRegex(SpecValidationError, "workspace root"):
                RunWorkspace(workspace, "run.drive-relative").create()
        mkdir.assert_not_called()

        with patch("spikes.gate_f_runner.model_worker.run_model_worker") as worker:
            with patch("spikes.gate_f_runner.runtime.Path.mkdir", wraps=Path.mkdir) as mkdir:
                code = _model(
                    Namespace(
                        run_id="run.model-drive-relative",
                        timeout_seconds=1,
                        workspace_root=workspace,
                        source=Path("source.png"),
                    )
                )
        self.assertEqual(70, code)
        worker.assert_not_called()
        mkdir.assert_not_called()

    def test_run_workspace_rejects_symlinked_workspace_root_before_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            outside = parent / "outside"
            outside.mkdir()
            linked_root = parent / "workspace-link"
            try:
                linked_root.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            with self.assertRaisesRegex(SpecValidationError, "workspace root"):
                RunWorkspace(linked_root, "run.workspace-link").create()
            self.assertFalse((outside / "run.workspace-link").exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_run_workspace_and_model_command_reject_junctioned_workspace_root(self) -> None:
        import os
        from argparse import Namespace
        from unittest.mock import patch
        from spikes.gate_f_runner.__main__ import _model

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            outside = parent / "outside"
            outside.mkdir()
            junction = parent / "workspace-junction"
            completed = subprocess.run(
                f'mklink /J "{junction}" "{outside}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(junction, "is_junction", lambda: False)():
                self.skipTest("directory junctions are unavailable")
            try:
                with self.assertRaisesRegex(SpecValidationError, "workspace root"):
                    RunWorkspace(junction, "run.workspace-junction").create()
                with patch("spikes.gate_f_runner.model_worker.run_model_worker") as worker:
                    code = _model(
                        Namespace(
                            run_id="run.model-junction",
                            timeout_seconds=1,
                            workspace_root=junction,
                            source=parent / "source.png",
                        )
                    )
                self.assertEqual(70, code)
                worker.assert_not_called()
                self.assertFalse((outside / "run.workspace-junction").exists())
                self.assertFalse((outside / "run.model-junction").exists())
            finally:
                os.rmdir(junction)

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_run_workspace_and_model_cli_reject_nested_ancestor_junction(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            outside = parent / "outside"
            outside.mkdir()
            junction = parent / "workspace-parent-junction"
            completed = subprocess.run(
                f'mklink /J "{junction}" "{outside}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(junction, "is_junction", lambda: False)():
                self.skipTest("directory junctions are unavailable")
            workspace_root = junction / "nested" / "workspace"
            try:
                with self.assertRaisesRegex(SpecValidationError, "workspace root"):
                    RunWorkspace(workspace_root, "run.workspace-nested-junction").create()
                self.assertFalse((outside / "nested").exists())

                model = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "spikes.gate_f_runner",
                        "model",
                        "--source",
                        str(parent / "source.png"),
                        "--run-id",
                        "run.model-nested-junction",
                        "--workspace-root",
                        str(workspace_root),
                        "--timeout-seconds",
                        "1",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(70, model.returncode)
                self.assertIn("model spike failed", model.stderr)
                self.assertNotIn("Traceback", model.stderr)
                self.assertFalse((outside / "nested").exists())
            finally:
                os.rmdir(junction)

    def test_duplicate_json_key_is_rejected(self) -> None:
        with self.assertRaises(SpecValidationError):
            strict_load_json_bytes(b'{"a": 1, "a": 2}')

    def test_nonfinite_json_is_rejected(self) -> None:
        with self.assertRaises(SpecValidationError):
            strict_load_json_bytes(b'{"value": NaN}')

    def test_unicode_surrogate_is_rejected(self) -> None:
        with self.assertRaises(SpecValidationError):
            strict_load_json_bytes(b'{"value": "\\ud800"}')

    def test_stage_seed_has_stable_golden(self) -> None:
        self.assertEqual(
            "12576661891383108838",
            derive_stage_seed("00000000000000000042", "stage.synthetic-normalize"),
        )

    def test_config_digest_mismatch_is_rejected(self) -> None:
        spec_value = json.loads(SPEC.read_text(encoding="utf-8"))
        spec_value["stages"][0]["config_sha256"] = "f" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "configs").mkdir()
            for config in (FIXTURE / "configs").iterdir():
                (root / "configs" / config.name).write_bytes(config.read_bytes())
            spec_path = root / "run-spec.json"
            spec_path.write_text(json.dumps(spec_value), encoding="utf-8")
            with self.assertRaises(SpecValidationError):
                load_run_spec(spec_path, build_synthetic_registry())

    def test_candidate_mutation_is_rejected_before_commit(self) -> None:
        from spikes.gate_f_runner.contracts import Determinism, ProducerKind, StageOutcome
        from spikes.gate_f_runner.runner import AdapterRegistry

        class MutatingAdapter:
            adapter_id = "synthetic.normalize.v1"
            contract_id = "oc2d.spike.synthetic-normalize.v1"
            stage_type = "oc2d.spike.synthetic-normalize"
            implementation_version = "0.1.0"
            producer_kind = ProducerKind.DETERMINISTIC
            determinism = Determinism.BYTE_EXACT
            execution_profile = "python-stdlib-in-process-v1"
            execution_provider = "python-stdlib"

            def execute(self, context: object) -> StageOutcome:
                artifact = context.sink.write_bytes(
                    "normalized.json", b"{}\n", role="normalized_synthetic", media_type="application/json"
                )
                artifact.path.write_bytes(b"tampered\n")
                return StageOutcome(StageStatus.SUCCEEDED, outputs=(artifact,))

        from spikes.gate_f_runner.synthetic import SyntheticProposalAdapter, SyntheticVerifyAdapter

        registry = AdapterRegistry()
        registry.register(MutatingAdapter())
        registry.register(SyntheticProposalAdapter())
        registry.register(SyntheticVerifyAdapter())
        with tempfile.TemporaryDirectory() as directory:
            status, manifest_path = PipelineRunner(registry, Path(directory)).run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.mutated",
                source_revision="source.test",
                build_id="build.test",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(StageStatus.FAILED, status)
            self.assertEqual("STAGE_RESOURCE_LIMIT_EXCEEDED", manifest["terminal_reason_code"])
            self.assertFalse(any((Path(directory) / "run.mutated" / "committed").rglob("normalized.json")))

    def test_input_mutation_is_rejected_before_commit(self) -> None:
        from spikes.gate_f_runner.contracts import Determinism, ProducerKind, StageOutcome
        from spikes.gate_f_runner.runner import AdapterRegistry

        class MutatingInputAdapter:
            adapter_id = "synthetic.normalize.v1"
            contract_id = "oc2d.spike.synthetic-normalize.v1"
            stage_type = "oc2d.spike.synthetic-normalize"
            implementation_version = "0.1.0"
            producer_kind = ProducerKind.DETERMINISTIC
            determinism = Determinism.BYTE_EXACT
            execution_profile = "python-stdlib-in-process-v1"
            execution_provider = "python-stdlib"

            def execute(self, context: object) -> StageOutcome:
                context.spec.input_artifacts[0].path.write_bytes(b"tampered\n")
                artifact = context.sink.write_bytes(
                    "normalized.json", b"{}\n", role="normalized_synthetic", media_type="application/json"
                )
                return StageOutcome(StageStatus.SUCCEEDED, outputs=(artifact,))

        from spikes.gate_f_runner.synthetic import SyntheticProposalAdapter, SyntheticVerifyAdapter

        registry = AdapterRegistry()
        registry.register(MutatingInputAdapter())
        registry.register(SyntheticProposalAdapter())
        registry.register(SyntheticVerifyAdapter())
        with tempfile.TemporaryDirectory() as directory:
            status, manifest_path = PipelineRunner(registry, Path(directory)).run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.input-mutated",
                source_revision="source.test",
                build_id="build.test",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(StageStatus.FAILED, status)
            self.assertEqual("STAGE_CONTRACT_VIOLATION", manifest["terminal_reason_code"])
            self.assertFalse(any((Path(directory) / "run.input-mutated" / "committed").rglob("normalized.json")))
            source_copy = Path(directory) / "run.input-mutated" / manifest["source"]["uri"]
            self.assertEqual(manifest["source"]["sha256"], sha256_file(source_copy))

    def test_input_mutation_followed_by_exception_is_contract_failure(self) -> None:
        from spikes.gate_f_runner.contracts import Determinism, ProducerKind
        from spikes.gate_f_runner.runner import AdapterRegistry

        class MutateThenFailAdapter:
            adapter_id = "synthetic.normalize.v1"
            contract_id = "oc2d.spike.synthetic-normalize.v1"
            stage_type = "oc2d.spike.synthetic-normalize"
            implementation_version = "0.1.0"
            producer_kind = ProducerKind.DETERMINISTIC
            determinism = Determinism.BYTE_EXACT
            execution_profile = "python-stdlib-in-process-v1"
            execution_provider = "python-stdlib"

            def execute(self, context: object) -> object:
                context.spec.input_artifacts[0].path.unlink()
                raise RuntimeError("failure after deleting input")

        from spikes.gate_f_runner.synthetic import SyntheticProposalAdapter, SyntheticVerifyAdapter

        registry = AdapterRegistry()
        registry.register(MutateThenFailAdapter())
        registry.register(SyntheticProposalAdapter())
        registry.register(SyntheticVerifyAdapter())
        with tempfile.TemporaryDirectory() as directory:
            status, manifest_path = PipelineRunner(registry, Path(directory)).run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.input-deleted",
                source_revision="source.test",
                build_id="build.test",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(StageStatus.FAILED, status)
            self.assertEqual("STAGE_CONTRACT_VIOLATION", manifest["terminal_reason_code"])
            source_copy = Path(directory) / "run.input-deleted" / manifest["source"]["uri"]
            self.assertEqual(manifest["source"]["sha256"], sha256_file(source_copy))

    def test_invalid_stage_type_is_rejected(self) -> None:
        spec_value = json.loads(SPEC.read_text(encoding="utf-8"))
        spec_value["stages"][0]["stage_type"] = "oc2d.spike.BAD"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "configs").mkdir()
            for config in (FIXTURE / "configs").iterdir():
                (root / "configs" / config.name).write_bytes(config.read_bytes())
            spec_path = root / "run-spec.json"
            spec_path.write_text(json.dumps(spec_value), encoding="utf-8")
            with self.assertRaises(SpecValidationError):
                load_run_spec(spec_path, build_synthetic_registry())

    def test_valid_stage_type_for_wrong_adapter_is_rejected(self) -> None:
        spec_value = json.loads(SPEC.read_text(encoding="utf-8"))
        spec_value["stages"][0]["stage_type"] = "oc2d.spike.synthetic-proposal"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "configs").mkdir()
            for config in (FIXTURE / "configs").iterdir():
                (root / "configs" / config.name).write_bytes(config.read_bytes())
            spec_path = root / "run-spec.json"
            spec_path.write_text(json.dumps(spec_value), encoding="utf-8")
            with self.assertRaises(SpecValidationError):
                load_run_spec(spec_path, build_synthetic_registry())

    def test_config_name_cannot_overwrite_materialized_spec(self) -> None:
        spec_value = json.loads(SPEC.read_text(encoding="utf-8"))
        original_config = (FIXTURE / "configs" / "normalize.json").read_bytes()
        spec_value["stages"][0]["config_uri"] = "run-spec.json"
        spec_value["stages"][0]["config_sha256"] = __import__("hashlib").sha256(original_config).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run-spec.json").write_bytes(original_config)
            for config in (FIXTURE / "configs").iterdir():
                if config.name != "normalize.json":
                    (root / config.name).write_bytes(config.read_bytes())
            spec_path = root / "spec.json"
            spec_value["stages"][1]["config_uri"] = "proposal.json"
            spec_value["stages"][2]["config_uri"] = "verify.json"
            spec_path.write_text(json.dumps(spec_value), encoding="utf-8")
            status, manifest_path = PipelineRunner(build_synthetic_registry(), root / "workspace").run(
                spec_path=spec_path,
                source_path=SOURCE,
                run_id="run.config-collision",
                source_revision="source.test",
                build_id="build.test",
            )
            self.assertEqual(StageStatus.SUCCEEDED, status)
            self.assertNotEqual(
                (manifest_path.parent / "spec" / "run-spec.json").read_bytes(),
                original_config,
            )

    def test_existing_run_id_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = PipelineRunner(build_synthetic_registry(), Path(directory))
            runner.run(
                spec_path=SPEC,
                source_path=SOURCE,
                run_id="run.same",
                source_revision="source.test",
                build_id="build.test",
            )
            manifest = Path(directory) / "run.same" / "run-manifest.json"
            original = manifest.read_bytes()
            with self.assertRaises(SpecValidationError):
                runner.run(
                    spec_path=SPEC,
                    source_path=SOURCE,
                    run_id="run.same",
                    source_revision="source.test",
                    build_id="build.test",
                )
            self.assertEqual(original, manifest.read_bytes())

    def test_preexisting_cancel_request_stops_before_first_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = PipelineRunner(build_synthetic_registry(), root)
            original_create = __import__("spikes.gate_f_runner.runtime", fromlist=["RunWorkspace"]).RunWorkspace.create

            def create_with_cancel(workspace: object) -> None:
                original_create(workspace)
                workspace.cancel_sentinel.touch()

            from unittest.mock import patch

            with patch("spikes.gate_f_runner.runtime.RunWorkspace.create", create_with_cancel):
                status, manifest_path = runner.run(
                    spec_path=SPEC,
                    source_path=SOURCE,
                    run_id="run.cancelled",
                    source_revision="source.test",
                    build_id="build.test",
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(StageStatus.CANCELLED, status)
            self.assertEqual([], manifest["stages"])
            self.assertNotIn("result", manifest)
            self.assertEqual("USER_CANCELLED", manifest["terminal_reason_code"])

    def test_cli_missing_spec_returns_bounded_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "spikes.gate_f_runner",
                    "run",
                    "--spec",
                    str(Path(directory) / "missing.json"),
                    "--source",
                    str(SOURCE),
                    "--run-id",
                    "run.missing",
                    "--source-revision",
                    "source.test",
                    "--build-id",
                    "build.test",
                    "--workspace-root",
                    directory,
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(64, completed.returncode)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertNotIn(directory, completed.stderr)

    @unittest.skipUnless(os.name == "posix", "POSIX host-shell policy is platform-specific")
    def test_model_cli_rejects_windows_drive_absolute_source_from_posix_host_shell(self) -> None:
        reason_code = "WINDOWS_SOURCE_PATH_REQUIRES_WINDOWS_HOST_SHELL"
        for index, windows_source in enumerate(("C:/Users/artist/source.png", "C:\\Users\\artist\\source.png")):
            with self.subTest(windows_source=windows_source), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory) / "workspace"
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "spikes.gate_f_runner",
                        "model",
                        "--source",
                        windows_source,
                        "--run-id",
                        f"run.posix-windows-source-{index}",
                        "--workspace-root",
                        str(workspace),
                        "--timeout-seconds",
                        "1",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(64, completed.returncode)
                self.assertEqual("", completed.stdout)
                self.assertEqual(f"{reason_code}\n", completed.stderr)
                self.assertNotIn(windows_source, completed.stderr)
                self.assertFalse(workspace.exists())

    @unittest.skipUnless(os.name == "nt", "Windows host-shell policy is platform-specific")
    def test_model_cli_accepts_forward_slash_drive_absolute_source_for_normalization_on_windows_host(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch

        from spikes.gate_f_runner.__main__ import main

        windows_source = Path("C:/Users/artist/source.png")
        self.assertTrue(windows_source.is_absolute())
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            argv = [
                "gate-f-runner",
                "model",
                "--source",
                str(windows_source),
                "--run-id",
                "run.windows-source",
                "--workspace-root",
                str(workspace),
                "--timeout-seconds",
                "1",
            ]
            result = {
                "profile_id": "model.profile.test",
                "gate_f_status": "GATE_F_NOT_EVALUATED",
            }
            report_path = workspace / "run.windows-source" / "workbench-report.json"

            def normalized_workbench(*args: object, **kwargs: object) -> tuple[Path, dict[str, object], dict[str, object]]:
                report_path.parent.mkdir()
                (report_path.parent / "model-result.json").write_text("{}\n", encoding="utf-8")
                return report_path, {}, result

            source_bytes = b"\x89PNG\r\n\x1a\nsource"
            with patch("sys.argv", argv), patch(
                "spikes.gate_f_runner.__main__.read_bounded_file",
                return_value=source_bytes,
            ) as reader, patch(
                "spikes.gate_f_runner.model_workbench.run_normalized_model_workbench",
                side_effect=normalized_workbench,
            ) as workbench, patch(
                "spikes.gate_f_runner.model_worker.run_model_worker",
            ) as worker, redirect_stdout(StringIO()):
                self.assertEqual(0, main())
            reader.assert_called_once()
            self.assertEqual(windows_source, reader.call_args.args[0])
            self.assertEqual(source_bytes, workbench.call_args.args[2])
            self.assertEqual("image/png", workbench.call_args.args[3])
            self.assertIs(worker, workbench.call_args.args[4])
            self.assertTrue((workspace / "run.windows-source" / "model-result.json").is_file())

    def test_cancel_is_idempotent_only_for_regular_nonreparse_sentinel(self) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from spikes.gate_f_runner.__main__ import _cancel

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.cancel-regular"
            run_dir.mkdir()
            args = Namespace(run_id=run_dir.name, workspace_root=root)
            self.assertEqual(0, _cancel(args))
            sentinel = run_dir / "cancel.request"
            self.assertTrue(stat.S_ISREG(sentinel.lstat().st_mode))
            with patch("spikes.gate_f_runner.runtime.os.open") as open_file:
                self.assertEqual(0, _cancel(args))
            open_file.assert_not_called()

            sentinel.unlink()
            sentinel.mkdir()
            self.assertEqual(64, _cancel(args))

    def test_cancel_rejects_dangling_symlink_and_symlinked_ancestor_without_writes(self) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from spikes.gate_f_runner.__main__ import _cancel

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.cancel-dangling"
            run_dir.mkdir()
            sentinel = run_dir / "cancel.request"
            try:
                sentinel.symlink_to(root / "missing")
            except OSError:
                self.skipTest("file symlinks are unavailable")
            with patch("spikes.gate_f_runner.runtime.os.open") as open_file:
                self.assertEqual(64, _cancel(Namespace(run_id=run_dir.name, workspace_root=root)))
            open_file.assert_not_called()

            outside = root / "outside"
            outside.mkdir()
            linked_run = root / "run.cancel-linked"
            try:
                linked_run.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            with patch("spikes.gate_f_runner.runtime.os.open") as open_file:
                self.assertEqual(64, _cancel(Namespace(run_id=linked_run.name, workspace_root=root)))
            open_file.assert_not_called()
            self.assertFalse((outside / "cancel.request").exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_cancel_rejects_nested_workspace_ancestor_junction_without_writes(self) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from spikes.gate_f_runner.__main__ import _cancel

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            outside = parent / "outside"
            run_dir = outside / "nested" / "workspace" / "run.cancel-junction"
            run_dir.mkdir(parents=True)
            junction = parent / "workspace-parent-junction"
            completed = subprocess.run(
                f'mklink /J "{junction}" "{outside}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(junction, "is_junction", lambda: False)():
                self.skipTest("directory junctions are unavailable")
            try:
                workspace = junction / "nested" / "workspace"
                with patch("spikes.gate_f_runner.runtime.os.open") as open_file:
                    self.assertEqual(64, _cancel(Namespace(run_id=run_dir.name, workspace_root=workspace)))
                open_file.assert_not_called()
                self.assertFalse((run_dir / "cancel.request").exists())
            finally:
                os.rmdir(junction)

    def test_cancel_filesystem_error_is_path_free(self) -> None:
        from argparse import Namespace
        from io import StringIO
        from unittest.mock import patch

        from spikes.gate_f_runner.__main__ import _cancel

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.cancel-error"
            run_dir.mkdir()
            stderr = StringIO()
            with patch("spikes.gate_f_runner.runtime.os.open", side_effect=PermissionError), patch("sys.stderr", stderr):
                code = _cancel(Namespace(run_id="run.cancel-error", workspace_root=root))
            self.assertEqual(70, code)
            self.assertNotIn(directory, stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "spikes.gate_f_runner",
                    "smoke",
                    "--run-id",
                    "run.cli",
                    "--workspace-root",
                    directory,
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIn("status=succeeded", completed.stdout)
            self.assertTrue((Path(directory) / "run.cli" / "run-manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
