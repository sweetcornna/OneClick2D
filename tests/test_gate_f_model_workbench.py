from __future__ import annotations

import base64
import json
import struct
import tempfile
import unittest
from functools import cache
from io import BytesIO
from pathlib import Path
from unittest import mock

from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.gui_server import GuiState
from spikes.gate_f_runner.model_workbench import (
    MODEL_CANVAS_SIZE,
    MODEL_PHASES,
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
from spikes.gate_f_runner.runtime import canonical_json_bytes, sha256_bytes, sha256_file
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


def write_model_fixture(run_dir: Path, source_sha256: str = "1" * 64, *, publish_result: bool = True) -> dict[str, object]:
    output = run_dir / "model-output"
    images = output / "input" / "input"
    images.mkdir(parents=True)
    rgba = _png("RGBA")
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


class GateFModelWorkbenchTests(unittest.TestCase):
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
            self.assertEqual(1.0, report["quality"]["neutral_fidelity"]["source_rgb_exact_ratio"])
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
            self.assertEqual(report["normalization"]["artifact"]["sha256"], report["model"]["source_sha256"])
            self.assertEqual([{"id": phase, "state": "completed"} for phase in MODEL_PHASES], report["phases"])
            self.assertEqual(report, json.loads(report_path.read_text(encoding="utf-8")))
            self.assertEqual(("MODEL_RESULT_PUBLISH", "completed"), phase_events[-1])
            self.assertTrue(report["model_used"])
            self.assertEqual(
                "raw_upload_and_model_derived_outputs_retained_until_manual_removal",
                report["source_retention"],
            )
            self.assertEqual(report, GuiState(root).workbench_status("run.model-upload"))

    def test_rejects_tampered_model_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-tamper"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            (run_dir / "model-output" / "input" / "input" / "face.png").write_bytes(b"tampered")
            with self.assertRaisesRegex(StageContractError, "does not match its inventory"):
                build_model_workbench_report(run_dir, run_dir.name, result)

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

    def test_model_report_cache_invalidates_when_artifact_changes_or_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-cache-tamper"
            run_dir.mkdir()
            write_model_fixture(run_dir)
            state = GuiState(root)
            self.assertTrue(state.workbench_status(run_dir.name)["model_used"])
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
