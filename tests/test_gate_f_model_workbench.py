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
import threading
import unittest
import zlib
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
    _indexed_files,
    _load_normalization_evidence,
    _neutral_fidelity,
    _png_facts,
    build_model_workbench_report,
    load_model_workbench_report,
    run_uploaded_model_workbench,
)
from spikes.gate_f_runner.raster import _temporary_max_image_pixels
from spikes.gate_f_runner.model_worker import (
    LEGACY_DEPENDENCIES_SHA256,
    LEGACY_PROFILE_ID,
    LEGACY_PROFILE_SHA256,
    LEGACY_SOURCE_PRESERVE_PROFILE_ID,
    LEGACY_SOURCE_PRESERVE_PROFILE_SHA256,
    LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID,
    LEGACY_SOURCE_PRESERVE_V4_PROFILE_SHA256,
    LEGACY_SOURCE_PRESERVE_V5_PROFILE_ID,
    LEGACY_SOURCE_PRESERVE_V5_PROFILE_SHA256,
    LEGACY_V4_NF4_MARIGOLD_DEVICE_POLICY_ID,
    LEGACY_V4_PSD_PIXEL_PROJECTION_ALGORITHM_ID,
    MAX_MODEL_ARTIFACT_MANIFEST_DEPTH,
    MAX_MODEL_ARTIFACT_MANIFEST_DIRECTORIES,
    NF4_MARIGOLD_DEVICE_POLICY_ID,
    PSD_PIXEL_PROJECTION_ALGORITHM_ID,
    PROFILE_ID,
    PROFILE_ROOT,
    _artifact_manifest,
    _artifact_manifest_digest,
    _load_profile,
)
from spikes.gate_f_runner.runtime import canonical_json_bytes, read_bounded_file, sha256_bytes, sha256_file
from tests.test_gate_f_model_worker import _minimal_psd, _valid_entrypoint_attestation_summary
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


# Static zlib-compressed canonical JSON bytes from released v0.3 historical reports.
# The compatibility fixtures are deliberately not derived from the current report builder.
LEGACY_V2_WORKBENCH_REPORT_V03_ZLIB_BASE64 = (
    "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"
)

LEGACY_V3_WORKBENCH_REPORT_V03_ZLIB_BASE64 = (
    "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"
    "SJFTm33Bevof14ObxQ=="
)


LEGACY_V4_WORKBENCH_REPORT_V04_ZLIB_BASE64 = (
    "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"
)


def _legacy_workbench_report_v03_bytes(encoded: str) -> bytes:
    return zlib.decompress(base64.b64decode(encoded))


_GRAYSCALE_PSD = base64.b64decode(
    "OEJQUwABAAAAAAAAAAEAAAACAAAAAgAIAAEAAAAAAAAAXjhCSU0EIQAAAAAAUQAAAAEBAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAAQAAAADwAAAA6AACAAAAAAAAAAAAAAABAAAAAQAC//8AAAAGAAAAAAAGOEJJTW5vcm3/AAgAAAAAOAAAAAAAAAAoAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wRmYWNlAAAAAAAAAQAAAAEAAAACAAAAAgAC//8AAAAGAAAAAAAGOEJJTW5vcm3/AAgAAAAAOAAAAAAAAAAoAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wVtb3V0aAAAAAEAAgD/AAEAAgBQAAEAAgD/AAEAAgCgAAAAAAAAAABQAACg"
)


# Frozen fixture PNGs. These are solid-colour 1280x1280 canvases that were once
# produced by ``PIL.Image.save(format="PNG")``, but PNG bytes depend on the zlib
# build linked into Pillow, so re-encoding them made the fixture digests differ
# between machines and broke the static persisted-report fixtures below. The bytes
# are frozen here instead so every environment hashes the same artifacts.
_FIXTURE_RGBA_PNG_ZLIB_BASE64 = (
    "eNrtyj8LAXEcx/Gv/Olyrm5RUkJ5AHSTRf6cuOWSPACDZ6DEJhuDR2AwKJvFZLtuVlYPwCoGucHA/Z6G3sO3d99en2XP7RjJbFJE"
    "DKdr90Xiok5LhMncvoFIaeTYjcF0c782DK8gnlcpaq2cb1pp3Rnvt4/16T0LxzLfBRNVeeajpqqfqqtolkTCFM4SU6syCIIgCIIg"
    "CIIgCIIgCIIgCIIgCIIgCIIgCIL/hR9ZXfSXd6zW1Ou0XfvQHC5+XZTbCA=="
)
_FIXTURE_GRAYSCALE_PNG_ZLIB_BASE64 = (
    "eNrrDPBz5+WS4mJgYOD19HAJYmBgZQBhDiDJYNu/QJuBQSDG08UxpGLO2yuGjAwKPAcMHL4+UU52F2bdW/WcwWTvW4YGXTaOFEmF"
    "CakNDtMYmSJHOaOcUc4oZ5QzyhnljHJGOaOcUc4oZ5QzyhnljHKQOV8Zk65wVWyY2pUKGm/wdPVzWeeU0AQAUzY+WA=="
)


@cache
def _png(mode: str) -> bytes:
    encoded = _FIXTURE_RGBA_PNG_ZLIB_BASE64 if mode == "RGBA" else _FIXTURE_GRAYSCALE_PNG_ZLIB_BASE64
    return zlib.decompress(base64.b64decode(encoded))


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
    artifact_manifest = _artifact_manifest(
        output / "input",
        output / "input" / ".entrypoint-attestation.json",
    )
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
        "entrypoint_attestation": _valid_entrypoint_attestation_summary(
            source_sha256,
            _artifact_manifest_digest(artifact_manifest),
        ),
        "files": files,
        "psd": main_psd,
    }
    if publish_result:
        (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))
    return result


def use_legacy_source_preserve_attestation(
    result: dict[str, object],
    *,
    retain_binding: bool = False,
) -> None:
    attestation = result["entrypoint_attestation"]
    if not isinstance(attestation, dict):
        raise AssertionError("fixture entrypoint attestation is invalid")
    if not retain_binding:
        attestation.pop("binding")
    attestation["policy_id"] = LEGACY_V4_NF4_MARIGOLD_DEVICE_POLICY_ID
    attestation["psd_pixel_projection_algorithm_id"] = LEGACY_V4_PSD_PIXEL_PROJECTION_ALGORITHM_ID
    components = attestation["components"]
    if not isinstance(components, dict) or not isinstance(components.get("vae"), dict):
        raise AssertionError("fixture component attestation is invalid")
    components["vae"]["execution_hook_devices"] = ["cuda:0"]


def refresh_model_inventory(
    run_dir: Path,
    result: dict[str, object],
    *,
    publish_result: bool = False,
    refresh_attestation: bool = True,
) -> None:
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
    attestation = result.get("entrypoint_attestation")
    if refresh_attestation and isinstance(attestation, dict) and isinstance(attestation.get("binding"), dict):
        artifact_manifest = _artifact_manifest(
            output / "input",
            output / "input" / ".entrypoint-attestation.json",
        )
        attestation["binding"]["artifact_manifest_digest"] = _artifact_manifest_digest(
            artifact_manifest
        )
    if publish_result:
        (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))


class GateFModelWorkbenchContractTests(unittest.TestCase):
    def test_max_image_pixels_context_serializes_process_global_updates(self) -> None:
        class FakeImage:
            MAX_IMAGE_PIXELS = 99

        class FakeBackend:
            Image = FakeImage

        backend = FakeBackend()
        first_entered = threading.Event()
        second_attempted = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        observed: list[tuple[str, int]] = []
        errors: list[BaseException] = []

        def first() -> None:
            try:
                with _temporary_max_image_pixels(backend, 11):
                    observed.append(("first", backend.Image.MAX_IMAGE_PIXELS))
                    first_entered.set()
                    release_first.wait(5)
            except BaseException as exc:  # pragma: no cover - surfaced in the main thread
                errors.append(exc)

        def second() -> None:
            try:
                first_entered.wait(5)
                second_attempted.set()
                with _temporary_max_image_pixels(backend, 22):
                    observed.append(("second", backend.Image.MAX_IMAGE_PIXELS))
                    second_entered.set()
            except BaseException as exc:  # pragma: no cover - surfaced in the main thread
                errors.append(exc)

        first_thread = threading.Thread(target=first)
        second_thread = threading.Thread(target=second)
        first_thread.start()
        self.assertTrue(first_entered.wait(1))
        second_thread.start()
        self.assertTrue(second_attempted.wait(1))
        self.assertFalse(second_entered.wait(0.05))
        release_first.set()
        first_thread.join(1)
        second_thread.join(1)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual([("first", 11), ("second", 22)], observed)
        self.assertEqual(99, backend.Image.MAX_IMAGE_PIXELS)

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
            self.assertEqual("0.6.0", report["format_version"])
            self.assertEqual("model", report["workflow"])
            self.assertTrue(report["model_used"])
            self.assertFalse(report["oc2d_produced"])
            self.assertEqual("GATE_F_NOT_EVALUATED", report["gate_f_status"])
            self.assertEqual(PROFILE_ID, report["model"]["identity"]["profile_id"])
            self.assertEqual(
                PSD_PIXEL_PROJECTION_ALGORITHM_ID,
                report["model"]["identity"]["postprocess_algorithm"],
            )
            self.assertEqual(
                NF4_MARIGOLD_DEVICE_POLICY_ID,
                report["model"]["identity"]["entrypoint_attestation"]["policy_id"],
            )
            self.assertEqual(
                "cuda:0",
                report["model"]["identity"]["entrypoint_attestation"]["execution_device"],
            )
            self.assertEqual(
                "resident-quantized",
                report["model"]["identity"]["entrypoint_attestation"]["components"]["unet"]["disposition"],
            )
            self.assertTrue(
                report["model"]["identity"]["entrypoint_attestation"]["components"]["unet"]
                ["upstream_cuda_move_suppressed"]
            )
            self.assertEqual(
                PSD_PIXEL_PROJECTION_ALGORITHM_ID,
                report["model"]["identity"]["entrypoint_attestation"]
                ["psd_pixel_projection_algorithm_id"],
            )
            self.assertEqual(
                report["model"]["source_sha256"],
                report["model"]["identity"]["entrypoint_attestation"]["binding"]["source_sha256"],
            )
            self.assertNotIn(
                "challenge",
                report["model"]["identity"]["entrypoint_attestation"]["binding"],
            )
            self.assertEqual(24, report["model"]["semantic_intermediate_count"])
            self.assertEqual(23, report["model"]["depth_intermediate_count"])
            self.assertEqual(2, report["psd"]["layer_count"])
            self.assertEqual(2, report["depth_psd"]["layer_count"])
            self.assertTrue(report["depth_psd"]["structural_readback_pass"])
            self.assertEqual("review_required", report["quality"]["status"])
            self.assertEqual("pass", report["quality"]["neutral_fidelity"]["status"])
            self.assertEqual("pass", report["quality"]["source_trust"]["status"])
            self.assertEqual([], report["quality"]["reason_codes"])
            self.assertIsNot(
                report["quality"]["reason_codes"],
                report["quality"]["neutral_fidelity"]["reason_codes"],
            )
            self.assertEqual(1.0, report["quality"]["neutral_fidelity"]["source_rgb_exact_ratio"])
            self.assertEqual(1.0, report["quality"]["neutral_fidelity"]["source_visible_coverage_ratio"])
            self.assertEqual(31, report["quality"]["neutral_fidelity"]["alpha_threshold"])
            self.assertEqual(
                "model-profile.postprocess.visible_alpha_threshold",
                report["quality"]["neutral_fidelity"]["alpha_threshold_source"],
            )
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

    def test_loads_static_v03_persisted_reports_for_historical_v2_and_v3(self) -> None:
        fixtures = (
            (
                "run.persisted-v03-v2",
                LEGACY_PROFILE_ID,
                LEGACY_PROFILE_SHA256,
                "not_applied",
                LEGACY_V2_WORKBENCH_REPORT_V03_ZLIB_BASE64,
            ),
            (
                "run.persisted-v03-v3",
                LEGACY_SOURCE_PRESERVE_PROFILE_ID,
                LEGACY_SOURCE_PRESERVE_PROFILE_SHA256,
                "source-visible-rgb-by-depth.v1",
                LEGACY_V3_WORKBENCH_REPORT_V03_ZLIB_BASE64,
            ),
        )
        for run_id, profile_id, profile_sha256, algorithm, encoded_report in fixtures:
            with self.subTest(profile_id=profile_id), tempfile.TemporaryDirectory() as directory:
                run_dir = Path(directory) / run_id
                run_dir.mkdir()
                result = write_model_fixture(run_dir, publish_result=False)
                result["profile_id"] = profile_id
                result["profile_sha256"] = profile_sha256
                result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
                result.pop("entrypoint_attestation")
                (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))

                persisted_bytes = _legacy_workbench_report_v03_bytes(encoded_report)
                persisted = json.loads(persisted_bytes)
                self.assertEqual("0.3.0", persisted["format_version"])
                self.assertEqual(profile_id, persisted["model"]["identity"]["profile_id"])
                self.assertNotIn("entrypoint_attestation", persisted["model"]["identity"])
                self.assertNotIn(
                    "alpha_threshold_source",
                    persisted["quality"]["neutral_fidelity"],
                )
                (run_dir / "workbench-report.json").write_bytes(persisted_bytes)

                report = load_model_workbench_report(run_dir)

                self.assertEqual("0.6.0", report["format_version"])
                self.assertEqual(profile_id, report["model"]["identity"]["profile_id"])
                self.assertEqual(algorithm, report["model"]["identity"]["postprocess_algorithm"])
                self.assertEqual(
                    "legacy-workbench-constant.v1",
                    report["quality"]["neutral_fidelity"]["alpha_threshold_source"],
                )
                self.assertTrue(report["model_used"])

                persisted["model_used"] = False
                (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(persisted))
                with self.assertRaisesRegex(
                    StageContractError,
                    "does not match validated evidence",
                ):
                    load_model_workbench_report(run_dir)

    def test_loads_static_v04_persisted_report_for_historical_v4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.persisted-v04-v4"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID
            result["profile_sha256"] = LEGACY_SOURCE_PRESERVE_V4_PROFILE_SHA256
            result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
            use_legacy_source_preserve_attestation(result)
            (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))

            persisted_bytes = _legacy_workbench_report_v03_bytes(
                LEGACY_V4_WORKBENCH_REPORT_V04_ZLIB_BASE64
            )
            persisted = json.loads(persisted_bytes)
            self.assertEqual("0.4.0", persisted["format_version"])
            self.assertEqual(
                LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID,
                persisted["model"]["identity"]["profile_id"],
            )
            self.assertIn("entrypoint_attestation", persisted["model"]["identity"])
            self.assertNotIn(
                "binding",
                persisted["model"]["identity"]["entrypoint_attestation"],
            )
            self.assertEqual(
                LEGACY_V4_PSD_PIXEL_PROJECTION_ALGORITHM_ID,
                persisted["model"]["identity"]["postprocess_algorithm"],
            )
            self.assertEqual(31, persisted["quality"]["neutral_fidelity"]["alpha_threshold"])
            (run_dir / "workbench-report.json").write_bytes(persisted_bytes)

            report = load_model_workbench_report(run_dir)
            self.assertEqual("0.6.0", report["format_version"])
            self.assertEqual(
                LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID,
                report["model"]["identity"]["profile_id"],
            )

            persisted["model_used"] = False
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(persisted))
            with self.assertRaisesRegex(
                StageContractError,
                "does not match validated evidence",
            ):
                load_model_workbench_report(run_dir)

    def test_rejects_v03_persisted_report_for_active_profile_as_unsupported_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.persisted-v03-active"
            run_dir.mkdir()
            result = write_model_fixture(run_dir)
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            report["format_version"] = "0.3.0"
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(report))

            with self.assertRaisesRegex(
                StageContractError,
                "format version 0.3.0 is unsupported for this profile",
            ):
                load_model_workbench_report(run_dir)

    def test_rejects_v04_persisted_report_for_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.persisted-v04-active"
            run_dir.mkdir()
            result = write_model_fixture(run_dir)
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            report["format_version"] = "0.4.0"
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(report))

            with self.assertRaisesRegex(
                StageContractError,
                "format version 0.4.0 is unsupported for this profile",
            ):
                load_model_workbench_report(run_dir)

    def test_rejects_v05_persisted_report_for_historical_v4_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.persisted-v05-v4"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID
            result["profile_sha256"] = LEGACY_SOURCE_PRESERVE_V4_PROFILE_SHA256
            result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
            use_legacy_source_preserve_attestation(result)
            (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            report["format_version"] = "0.5.0"
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(report))

            with self.assertRaisesRegex(
                StageContractError,
                "format version 0.5.0 is unsupported for this profile",
            ):
                load_model_workbench_report(run_dir)

    def test_loads_v05_persisted_report_for_historical_v5_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.persisted-v05-v5"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = LEGACY_SOURCE_PRESERVE_V5_PROFILE_ID
            result["profile_sha256"] = LEGACY_SOURCE_PRESERVE_V5_PROFILE_SHA256
            result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
            use_legacy_source_preserve_attestation(result, retain_binding=True)
            (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))

            current = build_model_workbench_report(run_dir, run_dir.name, result)
            self.assertEqual("0.6.0", current["format_version"])
            self.assertEqual(
                LEGACY_SOURCE_PRESERVE_V5_PROFILE_ID,
                current["model"]["identity"]["profile_id"],
            )
            self.assertEqual(
                LEGACY_V4_PSD_PIXEL_PROJECTION_ALGORITHM_ID,
                current["model"]["identity"]["postprocess_algorithm"],
            )
            persisted = {**current, "format_version": "0.5.0"}
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(persisted))

            self.assertEqual(current, load_model_workbench_report(run_dir))

    def test_rejects_unknown_persisted_report_version_with_version_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.persisted-unknown-version"
            run_dir.mkdir()
            result = write_model_fixture(run_dir)
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            report["format_version"] = "9.9.9"
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(report))

            with self.assertRaisesRegex(
                StageContractError,
                "format version is unsupported",
            ):
                load_model_workbench_report(run_dir)

    def test_active_profile_missing_attestation_fails_closed_with_bounded_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-missing-attestation"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result.pop("entrypoint_attestation")

            report = build_model_workbench_report(run_dir, run_dir.name, result)

            self.assertFalse(report["model_used"])
            self.assertIsNone(report["model"]["identity"]["entrypoint_attestation"])
            self.assertEqual(
                ["MODEL_ENTRYPOINT_ATTESTATION_MISSING"],
                report["quality"]["reason_codes"],
            )
            self.assertEqual("pass", report["quality"]["source_trust"]["status"])
            self.assertEqual("review_required", report["quality"]["neutral_fidelity"]["status"])
            self.assertIn("entrypoint_runtime_provenance", report["quality"]["review_items"])

    def test_active_profile_tampered_attestation_fails_closed_with_bounded_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-tampered-attestation"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["entrypoint_attestation"]["psd_pixel_projection_algorithm_id"] = "tampered"

            report = build_model_workbench_report(run_dir, run_dir.name, result)

            self.assertFalse(report["model_used"])
            self.assertIsNone(report["model"]["identity"]["entrypoint_attestation"])
            self.assertEqual(
                ["MODEL_ENTRYPOINT_ATTESTATION_MISMATCH"],
                report["quality"]["reason_codes"],
            )
            self.assertEqual("review_required", report["quality"]["neutral_fidelity"]["status"])

    def test_active_profile_tampered_attestation_source_binding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-tampered-attestation-source"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["entrypoint_attestation"]["binding"]["source_sha256"] = "0" * 64

            report = build_model_workbench_report(run_dir, run_dir.name, result)

            self.assertFalse(report["model_used"])
            self.assertIsNone(report["model"]["identity"]["entrypoint_attestation"])
            self.assertEqual(
                ["MODEL_ENTRYPOINT_ATTESTATION_MISMATCH"],
                report["quality"]["reason_codes"],
            )

    def test_active_profile_manifest_binding_for_another_set_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.model-tampered-attestation-manifest"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            other = root / "other-artifacts"
            other.mkdir()
            (other / "other.psd").write_bytes(b"different legal manifest")
            other_manifest = _artifact_manifest(
                other,
                other / ".entrypoint-attestation.json",
            )
            result["entrypoint_attestation"]["binding"]["artifact_manifest_digest"] = (
                _artifact_manifest_digest(other_manifest)
            )

            report = build_model_workbench_report(run_dir, run_dir.name, result)

            self.assertFalse(report["model_used"])
            self.assertIsNone(report["model"]["identity"]["entrypoint_attestation"])
            self.assertEqual(
                ["MODEL_ENTRYPOINT_ATTESTATION_MISMATCH"],
                report["quality"]["reason_codes"],
            )

    def test_active_profile_retained_artifact_change_breaks_manifest_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-post-attestation-change"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            stats_path = run_dir / "model-output" / "input" / "input" / "stats.json"
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            stats["total_time_s"] = 14.0
            stats_path.write_bytes(canonical_json_bytes(stats))
            refresh_model_inventory(run_dir, result, refresh_attestation=False)

            report = build_model_workbench_report(run_dir, run_dir.name, result)

            self.assertFalse(report["model_used"])
            self.assertEqual(
                ["MODEL_ENTRYPOINT_ATTESTATION_MISMATCH"],
                report["quality"]["reason_codes"],
            )

    def test_non_active_profile_attestation_error_does_not_call_unknown_profile_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-unknown-profile-attestation"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = "see-through.unknown"

            with self.assertRaisesRegex(
                StageContractError,
                "non-active model workbench result has unexpected attestation",
            ):
                build_model_workbench_report(run_dir, run_dir.name, result)

    def test_png_facts_rejects_oversized_canvas_before_pixel_decode(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.png"
            with Image.new("RGBA", (MODEL_CANVAS_SIZE + 1, MODEL_CANVAS_SIZE), (1, 2, 3, 4)) as image:
                image.save(path, format="PNG")

            with mock.patch(
                "PIL.PngImagePlugin.PngImageFile.load",
                side_effect=AssertionError("oversized PNG pixels were decoded"),
            ) as load:
                with self.assertRaisesRegex(StageContractError, "model workbench PNG"):
                    _png_facts(
                        path,
                        "RGBA",
                        (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE),
                    )
            load.assert_not_called()

    def test_active_profile_uses_profile_alpha_threshold_for_soft_source_edges(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-v5-alpha-threshold"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            image_root = run_dir / "model-output" / "input" / "input"
            with Image.new("RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), (0, 0, 0, 0)) as source:
                source.putpixel((0, 0), (11, 22, 33, 20))
                source.putpixel((1, 0), (44, 55, 66, 255))
                stream = BytesIO()
                source.save(stream, format="PNG")
                source_bytes = stream.getvalue()
            with Image.new("RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), (0, 0, 0, 0)) as reconstruction:
                reconstruction.putpixel((1, 0), (44, 55, 66, 255))
                reconstruction.save(image_root / "reconstruction.png", format="PNG")
            (run_dir / TRUSTED_MODEL_SOURCE_NAME).write_bytes(source_bytes)
            (image_root / "src_img.png").write_bytes(source_bytes)
            result["source_sha256"] = sha256_bytes(source_bytes)
            result["entrypoint_attestation"]["binding"]["source_sha256"] = result["source_sha256"]
            refresh_model_inventory(run_dir, result)

            report = build_model_workbench_report(run_dir, run_dir.name, result)
            fidelity = report["quality"]["neutral_fidelity"]

            self.assertEqual("pass", fidelity["status"])
            self.assertEqual(31, fidelity["alpha_threshold"])
            self.assertEqual(
                "model-profile.postprocess.visible_alpha_threshold",
                fidelity["alpha_threshold_source"],
            )
            self.assertEqual(1, fidelity["source_visible_pixel_count"])
            self.assertEqual(1, fidelity["reconstruction_visible_pixel_count"])
            self.assertEqual(0, fidelity["source_visible_omission_count"])

    def test_imports_legacy_profile_without_claiming_source_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-legacy"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = LEGACY_PROFILE_ID
            result["profile_sha256"] = LEGACY_PROFILE_SHA256
            result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
            result.pop("entrypoint_attestation")
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            self.assertEqual(LEGACY_PROFILE_ID, report["model"]["identity"]["profile_id"])
            self.assertEqual("not_applied", report["model"]["identity"]["postprocess_algorithm"])
            self.assertEqual(15, report["quality"]["neutral_fidelity"]["alpha_threshold"])
            self.assertEqual(
                "legacy-workbench-constant.v1",
                report["quality"]["neutral_fidelity"]["alpha_threshold_source"],
            )

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
                "requires a matching active model profile identity and model_used evidence",
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
            result.pop("entrypoint_attestation")
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            self.assertEqual(LEGACY_SOURCE_PRESERVE_PROFILE_ID, report["model"]["identity"]["profile_id"])
            self.assertEqual("source-visible-rgb-by-depth.v1", report["model"]["identity"]["postprocess_algorithm"])
            self.assertEqual(15, report["quality"]["neutral_fidelity"]["alpha_threshold"])
            self.assertEqual(
                "legacy-workbench-constant.v1",
                report["quality"]["neutral_fidelity"]["alpha_threshold_source"],
            )

    def test_imports_historical_v4_with_original_attestation_and_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.model-historical-v4"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID
            result["profile_sha256"] = LEGACY_SOURCE_PRESERVE_V4_PROFILE_SHA256
            result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
            use_legacy_source_preserve_attestation(result)

            report = build_model_workbench_report(run_dir, run_dir.name, result)
            identity = report["model"]["identity"]
            archived_v4 = json.loads(
                (PROFILE_ROOT / "see-through-v3-nf4.source-preserve-v4.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual("0.6.0", report["format_version"])
            self.assertEqual(LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID, identity["profile_id"])
            self.assertEqual(
                archived_v4["postprocess"]["algorithm_id"],
                identity["postprocess_algorithm"],
            )
            self.assertNotIn("binding", identity["entrypoint_attestation"])
            self.assertEqual(31, report["quality"]["neutral_fidelity"]["alpha_threshold"])
            self.assertEqual(
                "model-profile.postprocess.visible_alpha_threshold",
                report["quality"]["neutral_fidelity"]["alpha_threshold_source"],
            )

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
                    fidelity = _neutral_fidelity(
                        source_path,
                        reconstruction_path,
                        alpha_threshold=15,
                        alpha_threshold_source="legacy-workbench-constant.v1",
                    )
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


class GateFModelWorkbenchInventoryTraversalBoundTests(unittest.TestCase):
    """The workbench inventory walk must honour the same bounds as the manifest walk.

    ``model-output`` is re-walked whenever a persisted report is reloaded, so the
    worker's in-process ``_inventory`` bounds are not re-applied there. Without these
    the reload path would traverse an arbitrarily wide or deep tree before the
    described/discovered set comparison could reject it.
    """

    def _run_dir(self, root: Path) -> tuple[Path, dict[str, object]]:
        run_dir = root / "run.inventory-bounds"
        output_root = run_dir / "model-output"
        output_root.mkdir(parents=True)
        anchor = output_root / "anchor.bin"
        anchor.write_bytes(b"anchor")
        result: dict[str, object] = {
            "files": [
                {
                    "uri": "anchor.bin",
                    "byte_length": 6,
                    "sha256": sha256_file(anchor),
                }
            ]
        }
        return run_dir, result

    def _assert_rejects_before_hashing(
        self,
        run_dir: Path,
        result: dict[str, object],
        pattern: str,
    ) -> None:
        with mock.patch(
            "spikes.gate_f_runner.model_workbench.sha256_file",
            side_effect=lambda *args, **kwargs: self.fail(
                "inventory bounds must reject before any artifact is hashed"
            ),
        ):
            with self.assertRaisesRegex(StageContractError, pattern):
                _indexed_files(run_dir, result)

    def test_accepts_the_fixed_profile_output_shape(self) -> None:
        """The bounds must not be tighter than a legitimate run's output tree."""
        with tempfile.TemporaryDirectory() as directory:
            run_dir, result = self._run_dir(Path(directory))
            indexed = _indexed_files(run_dir, result)
        self.assertEqual({"anchor.bin"}, set(indexed))

    def test_rejects_depth_over_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir, result = self._run_dir(Path(directory))
            nested = run_dir / "model-output"
            for level in range(MAX_MODEL_ARTIFACT_MANIFEST_DEPTH + 1):
                nested = nested / f"level-{level}"
            nested.mkdir(parents=True)
            self._assert_rejects_before_hashing(run_dir, result, "depth exceeded")

    def test_rejects_directory_count_over_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir, result = self._run_dir(Path(directory))
            output_root = run_dir / "model-output"
            for index in range(MAX_MODEL_ARTIFACT_MANIFEST_DIRECTORIES + 1):
                (output_root / f"group-{index:03d}").mkdir()
            self._assert_rejects_before_hashing(run_dir, result, "directory count exceeded")

    def test_rejects_node_count_over_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir, result = self._run_dir(Path(directory))
            output_root = run_dir / "model-output"
            (output_root / "extra-a.bin").write_bytes(b"")
            (output_root / "extra-b.bin").write_bytes(b"")
            with mock.patch(
                "spikes.gate_f_runner.model_workbench.MAX_MODEL_ARTIFACT_MANIFEST_NODES", 2
            ):
                self._assert_rejects_before_hashing(run_dir, result, "node count exceeded")

    def test_rejects_entry_count_over_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir, result = self._run_dir(Path(directory))
            output_root = run_dir / "model-output"
            (output_root / "extra-a.bin").write_bytes(b"")
            (output_root / "extra-b.bin").write_bytes(b"")
            with mock.patch(
                "spikes.gate_f_runner.model_workbench.MAX_MODEL_ARTIFACT_MANIFEST_ENTRIES", 2
            ):
                self._assert_rejects_before_hashing(run_dir, result, "entry count exceeded")

    def test_rejects_relative_path_over_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir, result = self._run_dir(Path(directory))
            inner = run_dir / "model-output" / ("a" * 200) / ("b" * 200)
            inner.mkdir(parents=True)
            (inner / ("c" * 200)).write_bytes(b"")
            self._assert_rejects_before_hashing(run_dir, result, "path length exceeded")


if __name__ == "__main__":
    unittest.main()
