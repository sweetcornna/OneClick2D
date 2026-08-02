from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from spikes.gate_f_runner.candidate_baseline import _parse_config, build_gate_f_registry
from spikes.gate_f_runner.contracts import StageContractError, StageStatus
from spikes.gate_f_runner.runtime import canonical_json_bytes
from tests.test_gate_f_simple_cutout import normalization_config_bytes, purpose_created_asymmetric_png

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_CONFIG = ROOT / "examples" / "gate-f-candidate-baseline" / "config.json"


def make_fixture(root: Path, *, output_files: int = 43) -> tuple[Path, Path]:
    source = purpose_created_asymmetric_png()
    source_path = root / "source.png"
    source_path.write_bytes(source)
    configs = root / "configs"
    configs.mkdir()
    normalize = normalization_config_bytes()
    candidate = CANDIDATE_CONFIG.read_bytes()
    (configs / "normalize.json").write_bytes(normalize)
    (configs / "candidate.json").write_bytes(candidate)
    limits = {"max_wall_time_ms": 30000, "max_cpu_time_ms": 30000, "max_peak_ram_bytes": 536870912, "max_scratch_bytes": 1048576, "max_output_bytes": 16777216, "max_output_files": 2, "max_peak_vram_bytes": 0, "gpu_allowed": False}
    candidate_limits = dict(limits)
    candidate_limits["max_output_files"] = output_files
    spec = {
        "$schema": str(ROOT / "schemas" / "gate-f-run-spec" / "v0.1" / "run-spec.schema.json"),
        "format": "oneclick2d.gate-f-run-spec",
        "format_version": "0.1.0",
        "scope": "disposable-gate-f-spike",
        "execution_profile": "python-pillow-12.1.0-in-process-v1",
        "root_seed_u64": "00000000000000000042",
        "source": {"role": "source_raster", "sha256": sha256(source).hexdigest(), "media_type": "image/png", "max_bytes": 26214400},
        "expected_result_role": "candidate_baseline_report",
        "stages": [
            {"id": "stage.raster-normalize", "stage_type": "oc2d.spike.raster-normalize", "adapter_id": "raster.normalize.pillow.v1", "config_uri": "configs/normalize.json", "config_sha256": sha256(normalize).hexdigest(), "limits": limits},
            {"id": "stage.candidate-baseline", "stage_type": "oc2d.spike.candidate-baseline", "adapter_id": "candidate.baseline.pillow.v1", "config_uri": "configs/candidate.json", "config_sha256": sha256(candidate).hexdigest(), "limits": candidate_limits},
        ],
    }
    spec_path = root / "run-spec.json"
    spec_path.write_bytes(canonical_json_bytes(spec))
    return spec_path, source_path


class CandidateBaselineConfigTests(unittest.TestCase):
    def test_historical_v0_1_and_unknown_versions_are_explicitly_unsupported(self) -> None:
        current = json.loads(CANDIDATE_CONFIG.read_bytes())
        historical = json.loads(CANDIDATE_CONFIG.read_bytes())
        historical["format_version"] = "0.1.0"
        historical.pop("required_renderer_profile_id")
        unknown = dict(current)
        unknown["format_version"] = "9.9.9"
        for label, value in (("historical-v0.1", historical), ("unknown", unknown)):
            with self.subTest(label=label):
                with self.assertRaisesRegex(StageContractError, "^unsupported candidate config version$"):
                    _parse_config(canonical_json_bytes(value))

    def test_modified_v0_2_config_is_a_frozen_profile_mismatch(self) -> None:
        changed = json.loads(CANDIDATE_CONFIG.read_bytes())
        changed["frame_sequence"]["seed_u64"] = "00000000000000000043"
        with self.assertRaisesRegex(StageContractError, "^candidate config does not match frozen baseline profile$"):
            _parse_config(canonical_json_bytes(changed))


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class CandidateBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL
        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("candidate baseline requires locked Pillow 12.1.0")

    def _run(self, run_id: str, output_files: int = 43) -> tuple[StageStatus, dict[str, object], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        spec, source = make_fixture(root, output_files=output_files)
        from spikes.gate_f_runner.runner import PipelineRunner
        status, path = PipelineRunner(build_gate_f_registry(), root / "workspace").run(spec_path=spec, source_path=source, run_id=run_id, source_revision="source.test", build_id="build.test")
        return status, json.loads(path.read_text(encoding="utf-8")), path

    def test_candidate_emits_layers_geometry_and_shared_sequence(self) -> None:
        first_status, first, first_path = self._run("run.candidate-first")
        second_status, second, _ = self._run("run.candidate-second")
        self.assertEqual(StageStatus.SUCCEEDED, first_status)
        self.assertEqual(StageStatus.SUCCEEDED, second_status)
        first_outputs = first["stages"][1]["outputs"]
        second_outputs = second["stages"][1]["outputs"]
        self.assertEqual(43, len(first_outputs))
        self.assertEqual([item["sha256"] for item in first_outputs], [item["sha256"] for item in second_outputs])
        report = json.loads((first_path.parent / first["result"]["uri"]).read_text(encoding="utf-8"))
        self.assertEqual("6/6", report["validation"]["required_slot_presence"])
        self.assertTrue(report["validation"]["positive_area_all_frames"])
        self.assertEqual(148, report["validation"]["sample_count"])
        self.assertEqual(37, report["sequence"]["frame_count"])
        self.assertEqual("2b9c10df115be77ff3eb17807329a016d1350a3d387ea47bdaab2dd409b0ea8c", report["sequence"]["sha256"])
        self.assertEqual("oc2d.spike.pillow-rgba-renderer.v1", report["rendering"]["contract_id"])
        self.assertFalse(any(report["claims"].values()))
        self.assertEqual(["oc2d.character", "oc2d.face.base", "oc2d.eye.left", "oc2d.eye.right", "oc2d.mouth", "oc2d.torso"], [item["slot_id"] for item in report["ontology"]])

    def test_transparent_fixed_head_is_typed_suitability_block(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source_data = bytearray(purpose_created_asymmetric_png())
        from PIL import Image
        from io import BytesIO
        stream = BytesIO()
        with Image.open(BytesIO(bytes(source_data))) as image:
            rgba_image = image.convert("RGBA")
            rgba_image.putpixel((50, 30), (10, 20, 30, 128))
            rgba_image.save(stream, format="PNG", pnginfo=None)
            rgba_image.close()
        # Reuse the raster adapter's untagged-sRGB acceptance for this block classification test.
        source = stream.getvalue()
        spec, source_path = make_fixture(root)
        source_path.write_bytes(source)
        spec_value = json.loads(spec.read_text(encoding="utf-8"))
        spec_value["source"]["sha256"] = sha256(source).hexdigest()
        spec.write_bytes(canonical_json_bytes(spec_value))
        from spikes.gate_f_runner.runner import PipelineRunner
        status, manifest_path = PipelineRunner(build_gate_f_registry(), root / "workspace").run(spec_path=spec, source_path=source_path, run_id="run.candidate-transparent", source_revision="source.test", build_id="build.test")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("CANDIDATE_SUITABILITY_UNSUPPORTED", manifest["terminal_reason_code"])

    def test_output_limit_leaves_no_candidate_commit(self) -> None:
        status, manifest, path = self._run("run.candidate-limit", output_files=42)
        self.assertEqual(StageStatus.FAILED, status)
        self.assertEqual("STAGE_RESOURCE_LIMIT_EXCEEDED", manifest["terminal_reason_code"])
        self.assertFalse((path.parent / "committed" / "stage.candidate-baseline").exists())

    def test_registry_is_lazy_about_pillow(self) -> None:
        original_import = __import__
        def guarded(name: str, *args: object, **kwargs: object) -> object:
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded):
            registry = build_gate_f_registry()
        self.assertIsNotNone(registry.resolve("candidate.baseline.pillow.v1"))


if __name__ == "__main__":
    unittest.main()
