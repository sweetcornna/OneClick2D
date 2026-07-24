from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spikes.gate_f_runner.local_workbench import run_uploaded_workbench
from spikes.gate_f_runner.psd_reader import parse_layered_psd
from tests.test_gate_f_raster_adapter import purpose_created_jpeg
from tests.test_gate_f_simple_cutout import purpose_created_asymmetric_png


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFLocalWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL
        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("local workbench requires locked Pillow 12.1.0")

    def test_png_generates_37_frames_and_readback_psd_without_model_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path, report = run_uploaded_workbench(root, "run.workbench-png", purpose_created_asymmetric_png(), "image/png")
            self.assertEqual("LOCAL_WORKBENCH_COMPLETED", report["local_status"])
            self.assertEqual("GATE_F_NOT_EVALUATED", report["gate_f_status"])
            self.assertFalse(report["model_used"])
            self.assertFalse(report["oc2d_produced"])
            self.assertEqual(37, len(report["candidate"]["frames"]))
            self.assertEqual(5, len(report["candidate"]["layers"]))
            self.assertTrue(report["psd"]["structural_readback_pass"])
            self.assertEqual("not_evaluated", report["psd"]["external_editor_status"])
            self.assertEqual(report, json.loads(report_path.read_text(encoding="utf-8")))
            parsed = parse_layered_psd((report_path.parent / report["psd"]["uri"]).read_bytes())
            self.assertEqual(8, len(parsed.layers))
            self.assertEqual("Read Me — deterministic fixed-region baseline; no model", parsed.layers[0].name)
            self.assertEqual("Source Reference", parsed.layers[-1].name)
            self.assertFalse(parsed.layers[-1].visible)
            self.assertTrue(parsed.layers[-1].locked)

    def test_jpeg_is_normalized_and_processed(self) -> None:
        from io import BytesIO
        from PIL import Image

        image = Image.new("RGB", (101, 103), (50, 90, 130))
        image.paste((190, 130, 100), (20, 5, 81, 62))
        stream = BytesIO()
        image.save(stream, format="JPEG", quality=100, subsampling=0)
        image.close()
        with tempfile.TemporaryDirectory() as directory:
            _, report = run_uploaded_workbench(Path(directory), "run.workbench-jpeg", stream.getvalue(), "image/jpeg")
            self.assertEqual("JPEG", report["normalization"]["input"]["format"])
            self.assertEqual(37, len(report["candidate"]["frames"]))

    def test_transparent_head_is_typed_block_without_psd(self) -> None:
        from io import BytesIO
        from PIL import Image

        with Image.open(BytesIO(purpose_created_asymmetric_png())) as image:
            rgba = image.convert("RGBA")
            rgba.putpixel((50, 30), (10, 20, 30, 128))
            stream = BytesIO()
            rgba.save(stream, format="PNG")
            rgba.close()
        with tempfile.TemporaryDirectory() as directory:
            report_path, report = run_uploaded_workbench(Path(directory), "run.workbench-block", stream.getvalue(), "image/png")
            self.assertEqual("blocked", report["state"])
            self.assertEqual("CANDIDATE_SUITABILITY_UNSUPPORTED", report["reason_code"])
            self.assertEqual(
                [
                    {"id": "UPLOAD_RECEIVED", "state": "completed"},
                    {"id": "RASTER_NORMALIZE", "state": "completed"},
                    {"id": "DETERMINISTIC_BASELINE_37_FRAMES", "state": "blocked"},
                    {"id": "PSD_WRITE", "state": "unavailable"},
                    {"id": "PSD_READBACK", "state": "unavailable"},
                ],
                report["phases"],
            )
            self.assertFalse((report_path.parent / "output.psd").exists())
            self.assertFalse(report["model_used"])

    def test_invalid_raster_marks_normalization_blocked_and_later_phases_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path, report = run_uploaded_workbench(
                Path(directory),
                "run.workbench-invalid-raster",
                b"not a PNG",
                "image/png",
            )
            self.assertEqual("blocked", report["state"])
            self.assertEqual("RASTER_CONTAINER_INVALID", report["reason_code"])
            self.assertEqual(
                [
                    {"id": "UPLOAD_RECEIVED", "state": "completed"},
                    {"id": "RASTER_NORMALIZE", "state": "blocked"},
                    {"id": "DETERMINISTIC_BASELINE_37_FRAMES", "state": "unavailable"},
                    {"id": "PSD_WRITE", "state": "unavailable"},
                    {"id": "PSD_READBACK", "state": "unavailable"},
                ],
                report["phases"],
            )
            self.assertEqual(report, json.loads(report_path.read_text(encoding="utf-8")))

    def test_candidate_failure_preserves_completed_and_unavailable_phase_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "spikes.gate_f_runner.local_workbench.MAX_PUBLISHED_BYTES",
            1,
        ):
            _, report = run_uploaded_workbench(
                Path(directory),
                "run.workbench-resource-failure",
                purpose_created_asymmetric_png(),
                "image/png",
            )
            self.assertEqual("failed", report["state"])
            self.assertEqual("STAGE_RESOURCE_LIMIT_EXCEEDED", report["reason_code"])
            self.assertEqual(
                [
                    {"id": "UPLOAD_RECEIVED", "state": "completed"},
                    {"id": "RASTER_NORMALIZE", "state": "completed"},
                    {"id": "DETERMINISTIC_BASELINE_37_FRAMES", "state": "failed"},
                    {"id": "PSD_WRITE", "state": "unavailable"},
                    {"id": "PSD_READBACK", "state": "unavailable"},
                ],
                report["phases"],
            )

    def test_same_input_produces_same_frames_and_psd(self) -> None:
        source = purpose_created_asymmetric_png()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            _, first_report = run_uploaded_workbench(Path(first), "run.workbench-first", source, "image/png")
            _, second_report = run_uploaded_workbench(Path(second), "run.workbench-second", source, "image/png")
            self.assertEqual(
                [frame["artifact"]["sha256"] for frame in first_report["candidate"]["frames"]],
                [frame["artifact"]["sha256"] for frame in second_report["candidate"]["frames"]],
            )
            self.assertEqual(first_report["psd"]["sha256"], second_report["psd"]["sha256"])


if __name__ == "__main__":
    unittest.main()
