from __future__ import annotations

import base64
import contextlib
import importlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from functools import cache
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from spikes.gate_f_runner.__main__ import main
from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.gui_server import GuiState
from spikes.gate_f_runner.model_candidate import generate_model_candidate_preflight
from spikes.gate_f_runner.model_motion_draft import generate_model_motion_draft
from spikes.gate_f_runner.model_workbench import (
    MODEL_CANVAS_SIZE,
    MODEL_PHASES,
    TRUSTED_MODEL_SOURCE_NAME,
    _load_normalization_evidence,
    _neutral_fidelity,
    build_model_workbench_report,
    load_model_workbench_report,
    run_uploaded_model_workbench,
)
from spikes.gate_f_runner.model_worker import (
    LEGACY_DEPENDENCIES_SHA256,
    LEGACY_PROFILE_ID,
    LEGACY_PROFILE_SHA256,
    LEGACY_SOURCE_PRESERVE_PROFILE_ID,
    LEGACY_SOURCE_PRESERVE_PROFILE_SHA256,
    PROFILE_ID,
    _load_profile,
)
from spikes.gate_f_runner.runtime import canonical_json_bytes, read_bounded_file, sha256_bytes, sha256_file
from tests.test_gate_f_model_worker import _minimal_psd
from tests.test_gate_f_simple_cutout import purpose_created_asymmetric_png

PART_NAMES = (
    "front hair",
    "back hair",
    "headwear",
    "face",
    "eyebrow",
    "eyelash",
    "irides",
    "eyewhite",
    "eyewear",
    "ears",
    "earwear",
    "nose",
    "mouth",
    "neck",
    "neckwear",
    "topwear",
    "handwear",
    "bottomwear",
    "legwear",
    "footwear",
    "tail",
    "wings",
    "objects",
)


_GRAYSCALE_PSD = base64.b64decode(
    "OEJQUwABAAAAAAAAAAEAAAACAAAAAgAIAAEAAAAAAAAAXjhCSU0EIQAAAAAAUQAAAAEBAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAAQAAAADwAAAA6AACAAAAAAAAAAAAAAABAAAAAQAC//8AAAAGAAAAAAAGOEJJTW5vcm3/AAgAAAAAOAAAAAAAAAAoAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wRmYWNlAAAAAAAAAQAAAAEAAAACAAAAAgAC//8AAAAGAAAAAAAGOEJJTW5vcm3/AAgAAAAAOAAAAAAAAAAoAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wVtb3V0aAAAAAEAAgD/AAEAAgBQAAEAAgD/AAEAAgCgAAAAAAAAAABQAACg"
)


@cache
def _png(mode: str) -> bytes:
    from PIL import Image

    color = (30, 90, 160, 220) if mode == "RGBA" else 120
    with Image.new(mode, (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), color) as image:
        stream = BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()


@cache
def _profile_psd(data: bytes, channels: int) -> bytes:
    value = bytearray(data)
    value[14:18] = struct.pack(">I", MODEL_CANVAS_SIZE)
    value[18:22] = struct.pack(">I", MODEL_CANVAS_SIZE)
    return bytes(value[: -(2 * 2 * channels)]) + bytes(MODEL_CANVAS_SIZE * MODEL_CANVAS_SIZE * channels)


def write_model_fixture(run_dir: Path, source_sha256: str | None = None, *, publish_result: bool = True) -> dict[str, object]:
    output = run_dir / "model-output"
    images = output / "input" / "input"
    images.mkdir(parents=True)
    trusted_source = run_dir / TRUSTED_MODEL_SOURCE_NAME
    if trusted_source.exists():
        rgba = trusted_source.read_bytes()
    else:
        rgba = _png("RGBA")
        trusted_source.write_bytes(rgba)
    if source_sha256 is None:
        source_sha256 = sha256_bytes(rgba)
    depth = _png("L")
    (images / "reconstruction.png").write_bytes(rgba)
    (images / "src_img.png").write_bytes(rgba)
    (images / "src_head.png").write_bytes(rgba)
    for name in (*PART_NAMES[:2], "head", *PART_NAMES[2:]):
        (images / f"{name}.png").write_bytes(rgba)
    for name in PART_NAMES:
        (images / f"{name}_depth.png").write_bytes(depth)
    (images / "info.json").write_bytes(canonical_json_bytes({"parts": {name: {} for name in PART_NAMES}}))
    (images / "stats.json").write_bytes(
        canonical_json_bytes(
            {
                "quant_mode": "nf4",
                "peak_vram_gb": 6.25,
                "layerdiff_time_s": 10.0,
                "marigold_time_s": 2.0,
                "psd_time_s": 1.0,
                "total_time_s": 13.0,
            }
        )
    )
    psd = _profile_psd(_minimal_psd(), 4)
    depth_psd = _profile_psd(_GRAYSCALE_PSD, 1)
    (output / "input" / "input.psd").write_bytes(psd)
    (output / "input" / "input_depth.psd").write_bytes(depth_psd)
    (output / "input" / "input.psd.json").write_bytes(
        canonical_json_bytes(
            {
                "frame_size": [MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE],
                "parts": {
                    "face": {"xyxy": [0, 0, 1, 1], "tag": "face", "part_id": 0, "depth_median": 0.5},
                    "mouth": {"xyxy": [1, 1, 2, 2], "tag": "mouth", "part_id": 9, "depth_median": 0.75},
                },
            }
        )
    )
    files = [
        {
            "uri": path.relative_to(output).as_posix(),
            "byte_length": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(output.rglob("*"))
        if path.is_file()
    ]
    profile, profile_bytes = _load_profile()
    main_psd = next(item for item in files if item["uri"] == "input/input.psd")
    result = {
        "format": "oneclick2d.model-worker-result",
        "format_version": "0.1.0",
        "scope": "disposable-local-model-spike",
        "state": "completed",
        "profile_id": PROFILE_ID,
        "profile_sha256": sha256_bytes(profile_bytes),
        "dependencies_sha256": profile["runtime"]["dependencies_sha256"],
        "source_sha256": source_sha256,
        "model_used": True,
        "oc2d_produced": False,
        "gate_f_status": "GATE_F_NOT_EVALUATED",
        "files": files,
        "psd": main_psd,
    }
    if publish_result:
        (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))
    return result


def refresh_model_inventory(run_dir: Path, result: dict[str, object], *, publish_result: bool = False) -> None:
    output = run_dir / "model-output"
    files = [
        {
            "uri": path.relative_to(output).as_posix(),
            "byte_length": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(output.rglob("*"))
        if path.is_file()
    ]
    result["files"] = files
    result["psd"] = next(item for item in files if item["uri"] == "input/input.psd")
    if publish_result:
        (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))


class GateFModelWorkbenchContractTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows drive-relative paths are platform-specific")
    def test_uploaded_model_rejects_drive_relative_workspace_before_worker_or_writes(self) -> None:
        workspace = Path("C:relative\\uploaded-model-workspace")
        with mock.patch("spikes.gate_f_runner.model_workbench.run_model_worker") as worker:
            with mock.patch("spikes.gate_f_runner.runtime.Path.mkdir", wraps=Path.mkdir) as mkdir:
                with self.assertRaises(ValueError):
                    run_uploaded_model_workbench(
                        workspace,
                        "run.model-workbench-drive-relative",
                        purpose_created_asymmetric_png(),
                        "image/png",
                    )
        worker.assert_not_called()
        mkdir.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_uploaded_model_rejects_nested_workspace_ancestor_junction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            junction = root / "workspace-parent-junction"
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
                with mock.patch("spikes.gate_f_runner.model_workbench.run_model_worker") as worker:
                    with self.assertRaises(ValueError):
                        run_uploaded_model_workbench(
                            junction / "nested" / "workspace",
                            "run.model-workbench-junction",
                            purpose_created_asymmetric_png(),
                            "image/png",
                        )
                worker.assert_not_called()
                self.assertFalse((outside / "nested").exists())
            finally:
                os.rmdir(junction)


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFModelWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL

        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("model workbench requires locked Pillow 12.1.0")

    def test_imports_fixed_model_identity_layers_and_allowlisted_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-import"
            run_dir.mkdir()
            write_model_fixture(run_dir)
            report = load_model_workbench_report(run_dir)
            self.assertEqual("0.3.0", report["format_version"])
            self.assertEqual("model", report["workflow"])
            self.assertTrue(report["model_used"])
            self.assertFalse(report["oc2d_produced"])
            self.assertEqual("GATE_F_NOT_EVALUATED", report["gate_f_status"])
            self.assertEqual(PROFILE_ID, report["model"]["identity"]["profile_id"])
            self.assertEqual(24, report["model"]["semantic_intermediate_count"])
            self.assertEqual(23, report["model"]["depth_intermediate_count"])
            self.assertEqual(2, report["psd"]["layer_count"])
            self.assertEqual(2, report["depth_psd"]["layer_count"])
            self.assertTrue(report["depth_psd"]["structural_readback_pass"])
            self.assertEqual("review_required", report["quality"]["status"])
            self.assertEqual("pass", report["quality"]["neutral_fidelity"]["status"])
            self.assertEqual("pass", report["quality"]["source_trust"]["status"])
            self.assertEqual([], report["quality"]["reason_codes"])
            self.assertEqual(1.0, report["quality"]["neutral_fidelity"]["source_rgb_exact_ratio"])
            self.assertEqual(1.0, report["quality"]["neutral_fidelity"]["source_visible_coverage_ratio"])
            self.assertEqual(TRUSTED_MODEL_SOURCE_NAME, report["model"]["trusted_source"]["uri"])
            self.assertEqual(report["model"]["source_sha256"], report["model"]["trusted_source"]["sha256"])
            self.assertEqual("available", report["capabilities"]["source_comparison"])
            self.assertEqual("not_generated", report["capabilities"]["dynamic_preview"])

            state = GuiState(root)
            self.assertEqual("run.model-import", state.list_workbenches()[0]["run_id"])
            image, media_type, filename = state.workbench_artifact("run.model-import", "model-source")
            self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual("image/png", media_type)
            self.assertIsNone(filename)
            image, media_type, filename = state.workbench_artifact("run.model-import", "model-layer-00")
            self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual("image/png", media_type)
            self.assertIsNone(filename)
            _, media_type, filename = state.workbench_artifact("run.model-import", "output-psd")
            self.assertEqual("image/vnd.adobe.photoshop", media_type)
            self.assertEqual("local-see-through-layers.psd", filename)
            with self.assertRaisesRegex(StageContractError, "unknown workbench artifact"):
                state.workbench_artifact("run.model-import", "src_img.png")

    def test_imports_legacy_profile_without_claiming_source_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-legacy"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = LEGACY_PROFILE_ID
            result["profile_sha256"] = LEGACY_PROFILE_SHA256
            result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            self.assertEqual(LEGACY_PROFILE_ID, report["model"]["identity"]["profile_id"])
            self.assertEqual("not_applied", report["model"]["identity"]["postprocess_algorithm"])

    def test_import_without_retained_trusted_source_cannot_activate_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-missing-trusted-source"
            run_dir.mkdir()
            write_model_fixture(run_dir)
            (run_dir / TRUSTED_MODEL_SOURCE_NAME).unlink()

            report = load_model_workbench_report(run_dir)

            self.assertFalse(report["model_used"])
            self.assertIsNone(report["model"]["trusted_source"])
            self.assertEqual("review_required", report["quality"]["source_trust"]["status"])
            self.assertEqual(
                ["MODEL_TRUSTED_SOURCE_EVIDENCE_MISSING"],
                report["quality"]["reason_codes"],
            )
            self.assertEqual("review_required", report["quality"]["neutral_fidelity"]["status"])
            with self.assertRaisesRegex(
                StageContractError,
                "requires a fidelity-passing active model profile",
            ):
                generate_model_motion_draft(run_dir)
            self.assertFalse((run_dir / "motion-draft").exists())

    def test_imports_legacy_source_preserve_profile_with_original_algorithm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-legacy-source-preserve"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = LEGACY_SOURCE_PRESERVE_PROFILE_ID
            result["profile_sha256"] = LEGACY_SOURCE_PRESERVE_PROFILE_SHA256
            result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            self.assertEqual(LEGACY_SOURCE_PRESERVE_PROFILE_ID, report["model"]["identity"]["profile_id"])
            self.assertEqual("source-visible-rgb-by-depth.v1", report["model"]["identity"]["postprocess_algorithm"])

    def test_neutral_fidelity_flags_changed_visible_pixels(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-fidelity"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            stream = BytesIO()
            with Image.new("RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), (220, 20, 30, 220)) as image:
                image.save(stream, format="PNG")
            (run_dir / "model-output" / "input" / "input" / "reconstruction.png").write_bytes(stream.getvalue())
            refresh_model_inventory(run_dir, result)
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            fidelity = report["quality"]["neutral_fidelity"]
            self.assertEqual("review_required", fidelity["status"])
            self.assertEqual(0.0, fidelity["source_rgb_exact_ratio"])
            self.assertGreater(fidelity["source_rgb_mae"], 100)
            self.assertIn("neutral_visible_pixel_fidelity", report["quality"]["review_items"])

    def test_neutral_fidelity_rejects_one_pixel_reconstruction_of_opaque_source(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-coverage"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            reconstruction_path = run_dir / "model-output" / "input" / "input" / "reconstruction.png"
            with Image.new("RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), (0, 0, 0, 0)) as reconstruction:
                reconstruction.putpixel((0, 0), (30, 90, 160, 220))
                reconstruction.save(reconstruction_path, format="PNG")
            refresh_model_inventory(run_dir, result)

            fidelity = build_model_workbench_report(run_dir, run_dir.name, result)["quality"]["neutral_fidelity"]

            self.assertEqual("review_required", fidelity["status"])
            self.assertEqual(MODEL_CANVAS_SIZE * MODEL_CANVAS_SIZE, fidelity["source_visible_pixel_count"])
            self.assertEqual(1, fidelity["reconstruction_visible_pixel_count"])
            self.assertEqual(1, fidelity["source_visible_covered_pixel_count"])
            self.assertEqual(MODEL_CANVAS_SIZE * MODEL_CANVAS_SIZE - 1, fidelity["source_visible_omission_count"])
            self.assertLess(fidelity["source_visible_coverage_ratio"], 0.00001)

    def test_neutral_fidelity_exact_ratio_detects_each_rgb_channel_delta(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.png"
            reconstruction_path = root / "reconstruction.png"
            with Image.new("RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), (10, 20, 30, 255)) as source:
                source.save(source_path, format="PNG")
            for channel in range(3):
                color = [10, 20, 30, 255]
                color[channel] += 1
                with self.subTest(channel=channel):
                    with Image.new("RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), tuple(color)) as reconstruction:
                        reconstruction.save(reconstruction_path, format="PNG")
                    fidelity = _neutral_fidelity(source_path, reconstruction_path)
                    self.assertEqual(0.0, fidelity["source_rgb_exact_ratio"])
                    self.assertEqual([1.0 if index == channel else 0.0 for index in range(3)], fidelity["source_rgb_channel_mae"])

    def test_uploaded_model_uses_normalized_png_and_publishes_only_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: dict[str, object] = {}
            phase_events: list[tuple[str, str]] = []

            def worker(source: Path, output: Path, *, timeout_seconds: int) -> dict[str, object]:
                observed["source"] = source
                observed["timeout"] = timeout_seconds
                observed["signature"] = source.read_bytes()[:8]
                result = write_model_fixture(output.parent, sha256_file(source), publish_result=False)
                return result

            with mock.patch("spikes.gate_f_runner.model_workbench.run_model_worker", side_effect=worker):
                report_path, report = run_uploaded_model_workbench(
                    root,
                    "run.model-upload",
                    purpose_created_asymmetric_png(),
                    "image/png",
                    lambda phase, state: phase_events.append((phase, state)),
                )
            self.assertEqual(b"\x89PNG\r\n\x1a\n", observed["signature"])
            self.assertEqual(3600, observed["timeout"])
            self.assertEqual(report["model"]["trusted_source"]["sha256"], report["model"]["source_sha256"])
            self.assertEqual("pass", report["quality"]["source_trust"]["status"])
            self.assertEqual(
                report["normalization"]["artifact"]["sha256"],
                report["quality"]["source_trust"]["normalized_source_sha256"],
            )
            self.assertEqual([{"id": phase, "state": "completed"} for phase in MODEL_PHASES], report["phases"])
            self.assertEqual(report, json.loads(report_path.read_text(encoding="utf-8")))
            self.assertEqual(("MODEL_RESULT_PUBLISH", "completed"), phase_events[-1])
            self.assertTrue(report["model_used"])
            self.assertEqual(
                "raw_upload_and_model_derived_outputs_retained_until_manual_removal",
                report["source_retention"],
            )
            self.assertEqual(report, GuiState(root).workbench_status("run.model-upload"))

    def test_model_cli_publishes_trusted_source_and_reaches_motion_and_candidate(self) -> None:
        from tests.test_gate_f_model_motion_draft import _sparse_model_source

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            run_id = "run.model-cli-workflow"
            run_dir = workspace / run_id
            source_path = root / "source.png"
            with _sparse_model_source() as source:
                source.save(source_path, format="PNG")

            def worker(source: Path, output: Path, *, timeout_seconds: int) -> dict[str, object]:
                self.assertEqual(run_dir / TRUSTED_MODEL_SOURCE_NAME, source)
                self.assertTrue(source.is_file())
                self.assertEqual(17, timeout_seconds)
                result = write_model_fixture(output.parent, sha256_file(source), publish_result=False)
                image_root = output / "input" / "input"
                with _sparse_model_source(image_root) as reconstruction:
                    reconstruction.save(image_root / "reconstruction.png", format="PNG")
                    reconstruction.save(image_root / "src_img.png", format="PNG")
                    reconstruction.save(image_root / "src_head.png", format="PNG")
                refresh_model_inventory(output.parent, result)
                return result

            argv = [
                "gate-f-runner",
                "model",
                "--source",
                str(source_path),
                "--run-id",
                run_id,
                "--workspace-root",
                str(workspace),
                "--timeout-seconds",
                "17",
            ]
            with mock.patch("sys.argv", argv), mock.patch(
                "spikes.gate_f_runner.model_worker.run_model_worker",
                side_effect=worker,
            ), contextlib.redirect_stdout(StringIO()):
                self.assertEqual(0, main())

            report = load_model_workbench_report(run_dir)
            self.assertTrue(report["model_used"])
            self.assertEqual("pass", report["quality"]["source_trust"]["status"])
            self.assertEqual([], report["quality"]["reason_codes"])
            self.assertEqual(
                report["model"]["source_sha256"],
                report["model"]["trusted_source"]["sha256"],
            )
            self.assertIn("normalization", report)

            _, motion = generate_model_motion_draft(run_dir)
            self.assertEqual(37, len(motion["frames"]))
            _, candidate = generate_model_candidate_preflight(run_dir)
            self.assertEqual("LOCAL_MODEL_CANDIDATE_PREFLIGHT_COMPLETED", candidate["local_status"])
            self.assertEqual("GATE_F_NOT_EVALUATED", candidate["gate_f_status"])

    def test_uploaded_model_rejects_worker_rewritten_source_reference_and_reconstruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def worker(source: Path, output: Path, *, timeout_seconds: int) -> dict[str, object]:
                result = write_model_fixture(output.parent, sha256_file(source), publish_result=False)
                images = output / "input" / "input"
                rewritten = _png("RGBA")
                (images / "src_img.png").write_bytes(rewritten)
                (images / "reconstruction.png").write_bytes(rewritten)
                refresh_model_inventory(output.parent, result)
                return result

            with mock.patch("spikes.gate_f_runner.model_workbench.run_model_worker", side_effect=worker):
                report_path, report = run_uploaded_model_workbench(
                    root,
                    "run.model-source-rewrite",
                    purpose_created_asymmetric_png(),
                    "image/png",
                )

            self.assertFalse(report["model_used"])
            self.assertEqual("review_required", report["quality"]["status"])
            self.assertEqual("review_required", report["quality"]["source_trust"]["status"])
            self.assertEqual("review_required", report["quality"]["neutral_fidelity"]["status"])
            self.assertEqual(
                ["MODEL_SOURCE_REFERENCE_RGBA_MISMATCH"],
                report["quality"]["reason_codes"],
            )
            self.assertIn("trusted_source_reference", report["quality"]["review_items"])
            self.assertEqual(report, json.loads(report_path.read_text(encoding="utf-8")))
            self.assertEqual(report, load_model_workbench_report(root / "run.model-source-rewrite"))

    def test_normalization_manifest_uri_escape_rejects_before_descriptor_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id = "run.model-normalization-escape"

            def worker(source: Path, output: Path, *, timeout_seconds: int) -> dict[str, object]:
                return write_model_fixture(output.parent, sha256_file(source), publish_result=False)

            with mock.patch("spikes.gate_f_runner.model_workbench.run_model_worker", side_effect=worker):
                run_uploaded_model_workbench(root, run_id, purpose_created_asymmetric_png(), "image/png")
            run_dir = root / run_id
            manifest_path = run_dir / "run-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["stages"][0]["outputs"][0]["uri"] = "../outside.png"
            manifest_bytes = canonical_json_bytes(manifest)
            manifest_path.write_bytes(manifest_bytes)
            (run_dir / "run-manifest.sha256").write_bytes((sha256_bytes(manifest_bytes) + "\n").encode("ascii"))

            with mock.patch("spikes.gate_f_runner.model_workbench.sha256_file") as digest, mock.patch(
                "spikes.gate_f_runner.model_workbench.read_bounded_file", wraps=read_bounded_file
            ) as bounded:
                with self.assertRaisesRegex(StageContractError, "descriptor is invalid"):
                    _load_normalization_evidence(run_dir)
            digest.assert_not_called()
            self.assertEqual(["run-manifest.json", "run-manifest.sha256"], [call.args[0].name for call in bounded.call_args_list])

    def test_normalization_manifest_rejects_extra_inventory_before_artifact_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id = "run.model-normalization-extra"

            def worker(source: Path, output: Path, *, timeout_seconds: int) -> dict[str, object]:
                return write_model_fixture(output.parent, sha256_file(source), publish_result=False)

            with mock.patch("spikes.gate_f_runner.model_workbench.run_model_worker", side_effect=worker):
                run_uploaded_model_workbench(root, run_id, purpose_created_asymmetric_png(), "image/png")
            run_dir = root / run_id
            (run_dir / "committed" / "unexpected.bin").write_bytes(b"must not be read")

            with mock.patch("spikes.gate_f_runner.model_workbench.sha256_file") as digest, mock.patch(
                "spikes.gate_f_runner.model_workbench.read_bounded_file", wraps=read_bounded_file
            ) as bounded:
                with self.assertRaisesRegex(StageContractError, "inventory is not exact"):
                    _load_normalization_evidence(run_dir)
            digest.assert_not_called()
            self.assertEqual(["run-manifest.json", "run-manifest.sha256"], [call.args[0].name for call in bounded.call_args_list])

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_normalization_manifest_internal_junction_rejects_before_descriptor_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id = "run.model-normalization-junction"

            def worker(source: Path, output: Path, *, timeout_seconds: int) -> dict[str, object]:
                return write_model_fixture(output.parent, sha256_file(source), publish_result=False)

            with mock.patch("spikes.gate_f_runner.model_workbench.run_model_worker", side_effect=worker):
                run_uploaded_model_workbench(root, run_id, purpose_created_asymmetric_png(), "image/png")
            run_dir = root / run_id
            committed = run_dir / "committed"
            outside = root / "outside-committed"
            committed.rename(outside)
            completed = subprocess.run(
                f'mklink /J "{committed}" "{outside}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(committed, "is_junction", lambda: False)():
                outside.rename(committed)
                self.skipTest("directory junctions are unavailable")
            try:
                with mock.patch("spikes.gate_f_runner.model_workbench.sha256_file") as digest, mock.patch(
                    "spikes.gate_f_runner.model_workbench.read_bounded_file", wraps=read_bounded_file
                ) as bounded:
                    with self.assertRaisesRegex(StageContractError, "manifest URI is invalid"):
                        _load_normalization_evidence(run_dir)
                digest.assert_not_called()
                self.assertEqual(["run-manifest.json", "run-manifest.sha256"], [call.args[0].name for call in bounded.call_args_list])
            finally:
                os.rmdir(committed)

    def test_rejects_tampered_model_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-tamper"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            (run_dir / "model-output" / "input" / "input" / "face.png").write_bytes(b"tampered")
            with self.assertRaisesRegex(StageContractError, "does not match its inventory"):
                build_model_workbench_report(run_dir, run_dir.name, result)

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_rejects_junctioned_model_output_before_evidence_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-output-junction"
            run_dir.mkdir()
            outside_run = root / "outside-run"
            outside_run.mkdir()
            result = write_model_fixture(outside_run, publish_result=False)
            junction = run_dir / "model-output"
            outside_output = outside_run / "model-output"
            completed = subprocess.run(
                f'mklink /J "{junction}" "{outside_output}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(junction, "is_junction", lambda: False)():
                self.skipTest("directory junctions are unavailable")
            try:
                with mock.patch("spikes.gate_f_runner.model_workbench.sha256_file") as digest:
                    with self.assertRaisesRegex(StageContractError, "file inventory"):
                        build_model_workbench_report(run_dir, run_dir.name, result)
                digest.assert_not_called()
            finally:
                os.rmdir(junction)

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_rejects_junctioned_model_output_intermediate_before_evidence_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-intermediate-junction"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            output = run_dir / "model-output"
            original_input = output / "input"
            outside_input = root / "outside-input"
            original_input.rename(outside_input)
            completed = subprocess.run(
                f'mklink /J "{original_input}" "{outside_input}"',
                shell=True,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0 or not getattr(original_input, "is_junction", lambda: False)():
                outside_input.rename(original_input)
                self.skipTest("directory junctions are unavailable")
            try:
                with mock.patch("spikes.gate_f_runner.model_workbench.sha256_file") as digest:
                    with self.assertRaisesRegex(StageContractError, "does not match its inventory"):
                        build_model_workbench_report(run_dir, run_dir.name, result)
                digest.assert_not_called()
            finally:
                os.rmdir(original_input)

    @unittest.skipUnless(sys.platform == "win32", "Windows junctions are unavailable")
    def test_rejects_unindexed_nested_junction_before_artifact_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-unindexed-junction"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            outside = root / "outside-unindexed"
            outside.mkdir()
            (outside / "ignored.bin").write_bytes(b"must not be read")
            junction = run_dir / "model-output" / "input" / "input" / "unindexed"
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
                with mock.patch("spikes.gate_f_runner.model_workbench.sha256_file") as digest:
                    with self.assertRaisesRegex(StageContractError, "unsafe entry"):
                        build_model_workbench_report(run_dir, run_dir.name, result)
                digest.assert_not_called()
            finally:
                os.rmdir(junction)

    def test_rejects_structurally_invalid_depth_psd_even_when_inventory_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-depth-profile"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            output = run_dir / "model-output" / "input"
            (output / "input_depth.psd").write_bytes((output / "input.psd").read_bytes())
            refresh_model_inventory(run_dir, result)
            with self.assertRaises(StageContractError):
                build_model_workbench_report(run_dir, run_dir.name, result)

    def test_rejects_semantic_canvas_mismatch_even_when_inventory_matches(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-canvas"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            stream = BytesIO()
            with Image.new("RGBA", (2, 2), (30, 90, 160, 220)) as image:
                image.save(stream, format="PNG")
            (run_dir / "model-output" / "input" / "input" / "face.png").write_bytes(stream.getvalue())
            refresh_model_inventory(run_dir, result)
            with self.assertRaisesRegex(StageContractError, "semantic canvas"):
                build_model_workbench_report(run_dir, run_dir.name, result)

    def test_persisted_model_report_cannot_bypass_result_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-double-file"
            run_dir.mkdir()
            write_model_fixture(run_dir)
            report = load_model_workbench_report(run_dir)
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(report))
            state = GuiState(root)
            self.assertTrue(state.workbench_status(run_dir.name)["model_used"])

            report["model_used"] = False
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(report))
            with self.assertRaisesRegex(StageContractError, "does not match validated evidence"):
                state.workbench_status(run_dir.name)
            self.assertEqual([], state.list_workbenches())

    def test_gui_model_load_rejects_unindexed_file_without_hashing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-unindexed-large"
            run_dir.mkdir()
            write_model_fixture(run_dir)
            unindexed = run_dir / "model-output" / "unindexed-large.bin"
            with unindexed.open("wb") as stream:
                stream.truncate(600 * 1024 * 1024)
            real_digest = sha256_file

            def guarded_digest(path: Path) -> str:
                if path == unindexed:
                    self.fail("unindexed file must not be hashed")
                return real_digest(path)

            with mock.patch("spikes.gate_f_runner.model_workbench.sha256_file", side_effect=guarded_digest), mock.patch(
                "spikes.gate_f_runner.gui_server.sha256_file", side_effect=guarded_digest
            ):
                with self.assertRaisesRegex(StageContractError, "inventory is incomplete"):
                    GuiState(root).workbench_status(run_dir.name)

    def test_model_report_cache_invalidates_when_artifact_changes_or_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-cache-tamper"
            run_dir.mkdir()
            write_model_fixture(run_dir)
            state = GuiState(root)
            with mock.patch("spikes.gate_f_runner.gui_server.load_model_workbench_report", wraps=load_model_workbench_report) as loader:
                self.assertTrue(state.workbench_status(run_dir.name)["model_used"])
                self.assertTrue(state.workbench_status(run_dir.name)["model_used"])
            self.assertEqual(2, loader.call_count)
            face = run_dir / "model-output" / "input" / "input" / "face.png"
            changed = bytearray(face.read_bytes())
            changed[-1] ^= 1
            face.write_bytes(changed)
            with self.assertRaisesRegex(StageContractError, "does not match its inventory"):
                state.workbench_status(run_dir.name)

            run_dir = root / "run.model-cache-delete"
            run_dir.mkdir()
            write_model_fixture(run_dir)
            state = GuiState(root)
            self.assertTrue(state.workbench_status(run_dir.name)["model_used"])
            (run_dir / "model-output" / "input" / "input_depth.psd").unlink()
            with self.assertRaises(StageContractError):
                state.workbench_status(run_dir.name)

    def test_failed_model_validation_never_publishes_result_or_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def worker(source: Path, output: Path, *, timeout_seconds: int) -> dict[str, object]:
                result = write_model_fixture(output.parent, sha256_file(source), publish_result=False)
                (output / "input" / "input_depth.psd").unlink()
                return result

            with mock.patch("spikes.gate_f_runner.model_workbench.run_model_worker", side_effect=worker):
                with self.assertRaises(StageContractError):
                    run_uploaded_model_workbench(
                        root,
                        "run.model-validation-fail",
                        purpose_created_asymmetric_png(),
                        "image/png",
                    )
            run_dir = root / "run.model-validation-fail"
            self.assertFalse((run_dir / "model-result.json").exists())
            self.assertFalse((run_dir / "workbench-report.json").exists())
            self.assertFalse((run_dir / "model-output").exists())


if __name__ == "__main__":
    unittest.main()
