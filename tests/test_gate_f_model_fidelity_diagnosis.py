from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from spikes.gate_f_runner.model_fidelity_diagnosis import (
    MODEL_OUTPUT_DIRECTORY,
    FidelityDiagnosisError,
    _part_names,
    diagnose_model_fidelity,
)
from spikes.gate_f_runner.model_workbench import MODEL_CANVAS_SIZE
from spikes.gate_f_runner.__main__ import _diagnose_fidelity, build_parser


def _cleaned_alpha(value: int, threshold: int) -> int:
    if value <= threshold:
        return 0
    return round((value - threshold) * 255 / (255 - threshold))


def _write_fixture(run_dir: Path, threshold: int) -> dict[str, int]:
    from PIL import Image

    output = run_dir / MODEL_OUTPUT_DIRECTORY
    output.mkdir(parents=True)
    regions = {
        "H1": (slice(20, 23), slice(20, 24)),
        "H2": (slice(50, 52), slice(50, 55)),
        "covered": (slice(80, 82), slice(80, 83)),
    }
    expected = {name: (rows.stop - rows.start) * (columns.stop - columns.start) for name, (rows, columns) in regions.items()}

    source = Image.new("RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), (0, 0, 0, 0))
    layer = Image.new("RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), (0, 0, 0, 0))
    try:
        source_pixels = source.load()
        layer_pixels = layer.load()
        raw_alphas = {"H1": threshold + 9, "H2": threshold - 11, "covered": 200}
        for name, (rows, columns) in regions.items():
            for y in range(rows.start, rows.stop):
                for x in range(columns.start, columns.stop):
                    source_pixels[x, y] = (31, 79, 127, 255)
                    layer_pixels[x, y] = (
                        31,
                        79,
                        127,
                        _cleaned_alpha(raw_alphas[name], threshold),
                    )
        source.save(run_dir / "trusted-model-source.png", format="PNG")
        for index, part_name in enumerate(_part_names()):
            image = layer if index == 1 else Image.new(
                "RGBA", (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), (0, 0, 0, 0)
            )
            try:
                image.save(output / f"{part_name}.png", format="PNG")
            finally:
                if image is not layer:
                    image.close()
        layer.save(output / "reconstruction.png", format="PNG")
    finally:
        source.close()
        layer.close()

    source_visible = sum(expected.values())
    covered = expected["covered"]
    omitted = expected["H1"] + expected["H2"]
    exact_ratio = (covered + expected["H1"]) / source_visible
    report = {
        "quality": {
            "neutral_fidelity": {
                "alpha_threshold": threshold,
                "source_visible_pixel_count": source_visible,
                "reconstruction_visible_pixel_count": covered,
                "source_visible_covered_pixel_count": covered,
                "source_visible_omission_count": omitted,
                "source_rgb_exact_ratio": round(exact_ratio, 6),
                "source_rgb_mae": 4.25,
                "pass_thresholds": {
                    "source_visible_coverage_ratio_minimum": 1.0,
                    "source_rgb_exact_ratio_minimum": 0.995,
                    "source_rgb_mae_maximum": 0.5,
                },
            }
        }
    }
    (run_dir / "workbench-report.json").write_text(
        json.dumps(report, sort_keys=True), encoding="utf-8"
    )
    return expected


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFModelFidelityDiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL

        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("fidelity diagnosis requires locked Pillow 12.1.0")
        cls.fixture_directory = tempfile.TemporaryDirectory()
        cls.fixture_run = Path(cls.fixture_directory.name) / "run.fixture"
        cls.expected = _write_fixture(cls.fixture_run, 31)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture_directory.cleanup()

    def _fixture_copy(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        directory = tempfile.TemporaryDirectory()
        run_dir = Path(directory.name) / "run.fixture"
        shutil.copytree(self.fixture_run, run_dir)
        return directory, run_dir

    def test_known_h1_h2_split_and_projection(self) -> None:
        result = diagnose_model_fidelity(self.fixture_run)
        self.assertEqual(self.expected["H1"], result["groups"]["H1"]["pixel_count"])
        self.assertEqual(self.expected["H2"], result["groups"]["H2"]["pixel_count"])
        self.assertEqual(self.expected["covered"], result["counts"]["covered"])
        self.assertEqual(0, result["union_reconstruction_alpha"]["difference_pixel_count"])
        self.assertTrue(result["union_reconstruction_alpha"]["equal"])
        self.assertEqual(self.expected["H1"], result["groups"]["H1"]["argmax_layer_distribution"]["back hair"])
        self.assertFalse(result["h1_covered_projection"]["all_thresholds_pass"])
        self.assertEqual("LOCAL_FIDELITY_DIAGNOSIS_COMPLETED", result["local_status"])
        self.assertEqual("GATE_F_NOT_EVALUATED", result["gate_f_status"])

    def test_diagnosis_is_strictly_read_only(self) -> None:
        before = _fingerprint(self.fixture_run)
        diagnose_model_fidelity(self.fixture_run)
        after = _fingerprint(self.fixture_run)
        self.assertEqual(before, after)

    def test_threshold_is_parameterized(self) -> None:
        for threshold in (31, 47):
            with self.subTest(threshold=threshold), tempfile.TemporaryDirectory() as directory:
                run_dir = Path(directory) / f"run.threshold-{threshold}"
                expected = _write_fixture(run_dir, threshold)
                result = diagnose_model_fidelity(run_dir)
                self.assertEqual(threshold, result["alpha_threshold"])
                self.assertEqual(expected["H1"], result["groups"]["H1"]["pixel_count"])
                self.assertEqual(expected["H2"], result["groups"]["H2"]["pixel_count"])

    def test_missing_file_fails_closed(self) -> None:
        directory, run_dir = self._fixture_copy()
        try:
            (run_dir / MODEL_OUTPUT_DIRECTORY / "back hair.png").unlink()
            with self.assertRaisesRegex(FidelityDiagnosisError, "semantic layer is missing or invalid"):
                diagnose_model_fidelity(run_dir)
        finally:
            directory.cleanup()

    def test_symlinked_artifact_parent_fails_closed(self) -> None:
        directory = tempfile.TemporaryDirectory()
        try:
            root = Path(directory.name)
            run_dir = root / "run.fixture"
            run_dir.mkdir()
            linked_output = run_dir / "model-output"
            try:
                linked_output.symlink_to(
                    self.fixture_run / "model-output", target_is_directory=True
                )
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            shutil.copy2(
                self.fixture_run / "trusted-model-source.png",
                run_dir / "trusted-model-source.png",
            )
            shutil.copy2(
                self.fixture_run / "workbench-report.json",
                run_dir / "workbench-report.json",
            )
            with self.assertRaisesRegex(
                FidelityDiagnosisError, "reconstruction is missing or invalid"
            ):
                diagnose_model_fidelity(run_dir)
        finally:
            directory.cleanup()

    def test_wrong_canvas_fails_closed(self) -> None:
        from PIL import Image

        directory, run_dir = self._fixture_copy()
        try:
            with Image.new("RGBA", (MODEL_CANVAS_SIZE - 1, MODEL_CANVAS_SIZE), (0, 0, 0, 0)) as image:
                image.save(run_dir / "trusted-model-source.png", format="PNG")
            with self.assertRaisesRegex(FidelityDiagnosisError, "source canvas is outside its profile"):
                diagnose_model_fidelity(run_dir)
        finally:
            directory.cleanup()

    def test_union_reconstruction_mismatch_fails_closed(self) -> None:
        from PIL import Image

        directory, run_dir = self._fixture_copy()
        try:
            reconstruction = run_dir / MODEL_OUTPUT_DIRECTORY / "reconstruction.png"
            with Image.open(reconstruction, formats=("PNG",)) as image:
                changed = image.copy()
            try:
                changed.putpixel((0, 0), (0, 0, 0, 1))
                changed.save(reconstruction, format="PNG")
            finally:
                changed.close()
            with self.assertRaisesRegex(FidelityDiagnosisError, "union alpha does not match"):
                diagnose_model_fidelity(run_dir)
        finally:
            directory.cleanup()

    def test_missing_report_threshold_fails_closed(self) -> None:
        directory, run_dir = self._fixture_copy()
        try:
            report_path = run_dir / "workbench-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            del report["quality"]["neutral_fidelity"]["alpha_threshold"]
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(FidelityDiagnosisError, "report is invalid"):
                diagnose_model_fidelity(run_dir)
        finally:
            directory.cleanup()

    def test_cli_emits_only_deterministic_json_on_success(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = _diagnose_fidelity(
                Namespace(
                    run_id=self.fixture_run.name,
                    workspace_root=self.fixture_run.parent,
                )
            )
        self.assertEqual(0, code)
        result = json.loads(stdout.getvalue())
        self.assertEqual("LOCAL_FIDELITY_DIAGNOSIS_COMPLETED", result["local_status"])
        self.assertEqual("GATE_F_NOT_EVALUATED", result["gate_f_status"])

    def test_cli_exit_codes_and_parser_surface(self) -> None:
        parsed = build_parser().parse_args(
            ["diagnose-fidelity", "--run-id", self.fixture_run.name]
        )
        self.assertIs(parsed.func, _diagnose_fidelity)
        stderr = StringIO()
        with redirect_stderr(stderr):
            invalid_code = _diagnose_fidelity(
                Namespace(run_id="x", workspace_root=self.fixture_run.parent)
            )
            failed_code = _diagnose_fidelity(
                Namespace(run_id="run.missing", workspace_root=self.fixture_run.parent)
            )
        self.assertEqual(64, invalid_code)
        self.assertEqual(70, failed_code)
        self.assertNotIn(str(self.fixture_run.parent), stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
