from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.frame_sequence import FRAME_COUNT, PARAMETER_ORDER
from spikes.gate_f_runner.gui_server import GuiState
from spikes.gate_f_runner.model_motion_draft import (
    CANVAS_SIZE,
    DRAW_ORDER,
    NEUTRAL_PARAMETERS,
    PART_PADDING,
    SUBJECT_MATTE_EROSION_SIZE,
    MotionPart,
    _apply_subject_matte,
    _neutral_comparison,
    _render,
    _tighten_alpha,
    _source_feature_layer,
    _subject_matte,
    _underpaint_face,
    generate_model_motion_draft,
    load_model_motion_draft_report,
)
from spikes.gate_f_runner.model_workbench import (
    TRUSTED_MODEL_SOURCE_NAME,
    _canonical_source_png_bytes,
    load_model_workbench_report,
)
from spikes.gate_f_runner.model_worker import (
    LEGACY_DEPENDENCIES_SHA256,
    LEGACY_PROFILE_ID,
    LEGACY_PROFILE_SHA256,
)
from spikes.gate_f_runner.runtime import canonical_json_bytes, sha256_file
from spikes.gate_f_runner.raster import _load_pillow
from tests.test_gate_f_model_workbench import refresh_model_inventory, write_model_fixture


def _sparse_model_source(image_root: Path | None = None) -> Any:
    from PIL import Image, ImageDraw

    reconstruction = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    for index, semantic in enumerate(DRAW_ORDER):
        with Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0)) as layer:
            draw = ImageDraw.Draw(layer)
            color = (
                35 + (index * 37) % 190,
                30 + (index * 71) % 195,
                45 + (index * 53) % 180,
                255,
            )
            if semantic in {"eyewhite", "irides", "eyelash"}:
                y = 330 + index * 9
                draw.rectangle((330, y, 366, y + 22), fill=color)
                draw.rectangle((914, y, 950, y + 22), fill=color)
            else:
                column = index % 6
                row = index // 6
                x = 90 + column * 176
                y = 90 + row * 190
                draw.rectangle((x, y, x + 54, y + 42), fill=color)
            if image_root is not None:
                layer.save(image_root / f"{semantic}.png", format="PNG")
            reconstruction.alpha_composite(layer)
    return reconstruction


def persist_trusted_model_source(run_dir: Path, normalized_source: Any) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        normalized_path = Path(directory) / "normalized.png"
        normalized_source.save(normalized_path, format="PNG")
        trusted_data = _canonical_source_png_bytes(normalized_path)
    (run_dir / TRUSTED_MODEL_SOURCE_NAME).write_bytes(trusted_data)
    return trusted_data


def write_sparse_motion_fixture(run_dir: Path) -> None:
    with _sparse_model_source() as normalized_source:
        persist_trusted_model_source(run_dir, normalized_source)

    result = write_model_fixture(run_dir, publish_result=False)
    image_root = run_dir / "model-output" / "input" / "input"
    with _sparse_model_source(image_root) as reconstruction:
        reconstruction.save(image_root / "reconstruction.png", format="PNG")
        reconstruction.save(image_root / "src_img.png", format="PNG")
        reconstruction.save(image_root / "src_head.png", format="PNG")
    refresh_model_inventory(run_dir, result, publish_result=True)


def motion_loader_arguments(run_dir: Path) -> dict[str, str]:
    report = load_model_workbench_report(run_dir)
    reconstruction = report["model"]["reconstruction"]
    return {
        "expected_model_result_sha256": sha256_file(run_dir / "model-result.json"),
        "expected_reconstruction_sha256": str(reconstruction["sha256"]),
        "expected_reconstruction_uri": str(reconstruction["uri"]),
    }


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFModelMotionDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL

        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("model motion draft requires locked Pillow 12.1.0")

    def test_subject_matte_erodes_only_the_outer_alpha_boundary(self) -> None:
        from PIL import Image, ImageDraw

        with Image.new("RGBA", (15, 15), (20, 30, 40, 0)) as source:
            draw = ImageDraw.Draw(source)
            draw.rectangle((2, 2, 12, 12), fill=(20, 30, 40, 255))
            draw.rectangle((6, 6, 8, 8), fill=(20, 30, 40, 0))
            matte = _subject_matte(source)
            try:
                self.assertLessEqual(set(matte.get_flattened_data()), {0, 255})
                self.assertEqual(0, matte.getpixel((3, 3)))
                self.assertEqual(255, matte.getpixel((4, 4)))
                self.assertEqual(255, matte.getpixel((5, 7)))
                self.assertEqual(255, matte.getpixel((9, 7)))
                matted = _apply_subject_matte(source, matte)
            finally:
                matte.close()
        try:
            self.assertEqual(0, matted.getpixel((3, 3))[3])
            self.assertEqual(255, matted.getpixel((4, 4))[3])
            self.assertEqual(255, matted.getpixel((5, 7))[3])
            self.assertEqual(0, matted.getpixel((7, 7))[3])
            self.assertEqual(255, matted.getpixel((9, 7))[3])
        finally:
            matted.close()

    def test_neutral_comparison_exact_ratio_detects_each_rgb_channel_delta(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reconstruction_path = root / "reconstruction.png"
            neutral_path = root / "neutral.png"
            with Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (10, 20, 30, 255)) as reconstruction:
                reconstruction.save(reconstruction_path, format="PNG")
            for channel in range(3):
                color = [10, 20, 30, 255]
                color[channel] += 1
                with self.subTest(channel=channel):
                    with Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), tuple(color)) as neutral:
                        neutral.save(neutral_path, format="PNG")
                    comparison = _neutral_comparison(reconstruction_path, neutral_path)
                    self.assertEqual(0.0, comparison["neutral_reconstruction_rgb_exact_ratio"])

    def test_source_feature_layer_keeps_dark_source_detail_without_skin_fill(self) -> None:
        from PIL import Image

        with Image.new("RGBA", (3, 1)) as source, Image.new("RGBA", (3, 1), (255, 255, 255, 255)) as semantic:
            source.putdata([(240, 240, 240, 255), (60, 60, 60, 255), (180, 180, 180, 255)])
            feature = _source_feature_layer(source, semantic)
        try:
            alpha = feature.getchannel("A")
            try:
                self.assertEqual([0, 255, 118], list(alpha.get_flattened_data()))
            finally:
                alpha.close()
        finally:
            feature.close()

    def test_feature_alpha_tightening_removes_low_opacity_edge_noise(self) -> None:
        from PIL import Image

        with Image.new("RGBA", (3, 1)) as source:
            source.putdata([(20, 30, 40, 28), (20, 30, 40, 124), (20, 30, 40, 220)])
            tightened = _tighten_alpha(source, feature=True)
        try:
            alpha = tightened.getchannel("A")
            try:
                self.assertEqual([0, 128, 255], list(alpha.get_flattened_data()))
            finally:
                alpha.close()
        finally:
            tightened.close()

    def test_face_underpaint_fills_internal_feature_holes_only(self) -> None:
        from PIL import Image, ImageDraw

        with Image.new("RGBA", (48, 48), (0, 0, 0, 0)) as source:
            draw = ImageDraw.Draw(source)
            draw.rectangle((6, 6, 41, 41), fill=(240, 210, 200, 255))
            draw.rectangle((18, 18, 29, 29), fill=(0, 0, 0, 0))
            filled = _underpaint_face(source, _load_pillow())
        try:
            self.assertGreaterEqual(filled.getpixel((23, 23))[3], 250)
            self.assertEqual(0, filled.getpixel((0, 0))[3])
        finally:
            filled.close()

    def test_face_underpaint_targets_feature_holes_connected_to_the_outside(self) -> None:
        from PIL import Image, ImageDraw

        with Image.new("RGBA", (48, 48), (0, 0, 0, 0)) as source:
            draw = ImageDraw.Draw(source)
            draw.rectangle((6, 6, 41, 41), fill=(240, 210, 200, 255))
            draw.rectangle((18, 18, 29, 29), fill=(0, 0, 0, 0))
            draw.rectangle((22, 0, 25, 18), fill=(0, 0, 0, 0))
            filled = _underpaint_face(source, _load_pillow(), ((18, 18, 30, 30),))
        try:
            self.assertGreaterEqual(filled.getpixel((23, 23))[3], 250)
            self.assertEqual(0, filled.getpixel((23, 2))[3])
        finally:
            filled.close()

    def test_generates_validated_37_frame_quad_affine_draft_and_attaches_to_workbench(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run.motion-draft"
            run_dir.mkdir()
            write_sparse_motion_fixture(run_dir)

            report_path, report = generate_model_motion_draft(run_dir)

            self.assertEqual(run_dir / "motion-draft" / "motion-report.json", report_path)
            self.assertEqual(FRAME_COUNT, len(report["frames"]))
            self.assertEqual(list(PARAMETER_ORDER), [item["id"] for item in report["parameters"]])
            self.assertEqual(
                {
                    "binding.head-yaw",
                    "binding.head-pitch",
                    "binding.eye-left-open",
                    "binding.eye-right-open",
                    "binding.mouth-open",
                },
                {item["id"] for item in report["bindings"]},
            )
            self.assertTrue(all(item["triangle_indices"] == [0, 1, 2, 0, 2, 3] for item in report["geometry"]))
            self.assertTrue(all(item["winding"] == "positive-screen-y-down" for item in report["geometry"]))
            self.assertEqual(len(report["layers"]) * FRAME_COUNT, report["validation"]["sample_count"])
            self.assertLess(report["validation"]["neutral_reconstruction_rgb_exact_ratio"], 1.0)
            self.assertGreater(report["validation"]["neutral_reconstruction_rgb_mae"], 0.0)
            self.assertEqual(1, len({report["frames"][index]["artifact"]["sha256"] for index in (0, 12, 36)}))
            self.assertEqual("head", next(item for item in report["layers"] if item["semantic"] == "back hair")["motion_group"])
            mouth_index = DRAW_ORDER.index("mouth")
            mouth_x = 90 + (mouth_index % 6) * 176
            mouth_y = 90 + (mouth_index // 6) * 190
            subject_inset = SUBJECT_MATTE_EROSION_SIZE // 2
            self.assertEqual(
                [
                    mouth_x - PART_PADDING + subject_inset,
                    mouth_y - PART_PADDING + subject_inset,
                    mouth_x + 55 + PART_PADDING - subject_inset,
                    mouth_y + 43 + PART_PADDING - subject_inset,
                ],
                next(item for item in report["layers"] if item["semantic"] == "mouth")["box_ltrb"],
            )
            self.assertIn("premultiplied", report["profile"]["renderer_profile_id"])
            self.assertTrue(report["claims"]["dynamic_preview_research_draft"])
            self.assertFalse(report["claims"]["oc2d_produced"])
            self.assertFalse(report["claims"]["moc3_produced"])

            workbench = load_model_workbench_report(run_dir)
            self.assertEqual(report, workbench["motion_draft"])
            self.assertEqual("research_draft", workbench["capabilities"]["mesh_generation"])
            self.assertEqual("research_draft", workbench["capabilities"]["parameter_binding"])
            self.assertEqual("research_draft", workbench["capabilities"]["dynamic_preview"])

            frame, media_type, filename = GuiState(root).workbench_artifact(run_dir.name, "motion-frame-00")
            self.assertTrue(frame.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual("image/png", media_type)
            self.assertIsNone(filename)
            reconstruction, reconstruction_media_type, reconstruction_filename = GuiState(root).workbench_artifact(
                run_dir.name,
                "model-reconstruction",
            )
            self.assertEqual("image/png", reconstruction_media_type)
            self.assertIsNone(reconstruction_filename)
            self.assertNotEqual(reconstruction, frame)
            layer, layer_media_type, layer_filename = GuiState(root).workbench_artifact(
                run_dir.name, report["layers"][0]["artifact"]["id"]
            )
            self.assertTrue(layer.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual("image/png", layer_media_type)
            self.assertIsNone(layer_filename)

            from PIL import Image, ImageChops

            published_parts: list[MotionPart] = []
            try:
                for item in report["layers"]:
                    with Image.open(run_dir / item["artifact"]["uri"], formats=("PNG",)) as layer_image:
                        layer_image.load()
                        published_parts.append(
                            MotionPart(
                                id=item["id"],
                                semantic=item["semantic"],
                                side=item["side"],
                                source_artifact_id=item["source_artifact_id"],
                                box=tuple(item["box_ltrb"]),
                                draw_order=item["draw_order"],
                                motion_group=item["motion_group"],
                                image=layer_image.convert("RGBA"),
                            )
                        )
                expected_neutral = _render(published_parts, NEUTRAL_PARAMETERS, _load_pillow())
                try:
                    with Image.open(run_dir / report["frames"][0]["artifact"]["uri"], formats=("PNG",)) as stored:
                        stored.load()
                        with stored.convert("RGBA") as stored_rgba:
                            with ImageChops.difference(expected_neutral, stored_rgba) as difference:
                                self.assertIsNone(difference.getbbox())
                finally:
                    expected_neutral.close()
            finally:
                for part in published_parts:
                    part.image.close()

    def test_broken_layer_composition_lowers_neutral_fidelity(self) -> None:
        from PIL import Image

        backend = _load_pillow()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reconstruction_path = root / "reconstruction.png"
            intact_path = root / "intact.png"
            broken_path = root / "broken.png"
            with Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0)) as head_image, Image.new(
                "RGBA",
                (CANVAS_SIZE, CANVAS_SIZE),
                (0, 0, 0, 0),
            ) as static_image:
                head_image.paste((20, 40, 60, 255), (100, 100, 160, 160))
                static_image.paste((80, 100, 120, 255), (200, 200, 260, 260))
                with Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0)) as reconstruction:
                    reconstruction.alpha_composite(head_image)
                    reconstruction.alpha_composite(static_image)
                    reconstruction.save(reconstruction_path, format="PNG")
                parts = [
                    MotionPart("layer.head", "face", "not-applicable", "head", (100, 100, 160, 160), 0, "head", head_image.crop((100, 100, 160, 160))),
                    MotionPart("layer.static", "objects", "not-applicable", "static", (200, 200, 260, 260), 1, "static", static_image.crop((200, 200, 260, 260))),
                ]
            try:
                intact = _render(parts, NEUTRAL_PARAMETERS, backend)
                broken = _render(parts[:1], NEUTRAL_PARAMETERS, backend)
                try:
                    intact.save(intact_path, format="PNG")
                    broken.save(broken_path, format="PNG")
                finally:
                    intact.close()
                    broken.close()
                intact_fidelity = _neutral_comparison(reconstruction_path, intact_path)
                broken_fidelity = _neutral_comparison(reconstruction_path, broken_path)
                self.assertEqual(1.0, intact_fidelity["neutral_reconstruction_rgb_exact_ratio"])
                self.assertLess(
                    broken_fidelity["neutral_reconstruction_rgb_exact_ratio"],
                    intact_fidelity["neutral_reconstruction_rgb_exact_ratio"],
                )
            finally:
                for part in parts:
                    part.image.close()

    def test_motion_rejects_model_with_source_visible_omissions(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.motion-coverage"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            reconstruction_path = run_dir / "model-output" / "input" / "input" / "reconstruction.png"
            with Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0)) as reconstruction:
                reconstruction.putpixel((0, 0), (30, 90, 160, 220))
                reconstruction.save(reconstruction_path, format="PNG")
            refresh_model_inventory(run_dir, result, publish_result=True)

            fidelity = load_model_workbench_report(run_dir)["quality"]["neutral_fidelity"]
            self.assertEqual("review_required", fidelity["status"])
            self.assertGreater(fidelity["source_visible_omission_count"], 1_000_000)
            with self.assertRaisesRegex(StageContractError, "fidelity-passing active model profile"):
                generate_model_motion_draft(run_dir)
            self.assertFalse((run_dir / "motion-draft").exists())

    def test_loader_recomputes_evidence_and_rejects_frame_or_report_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.motion-tamper"
            run_dir.mkdir()
            write_sparse_motion_fixture(run_dir)
            _, report = generate_model_motion_draft(run_dir)
            loader_arguments = motion_loader_arguments(run_dir)

            frame_path = run_dir / report["frames"][0]["artifact"]["uri"]
            original_frame = frame_path.read_bytes()
            frame_path.write_bytes(original_frame[:-1] + bytes([original_frame[-1] ^ 1]))
            with self.assertRaisesRegex(StageContractError, "frame identity"):
                load_model_motion_draft_report(run_dir, **loader_arguments)

            frame_path.write_bytes(original_frame)
            report_path = run_dir / "motion-draft" / "motion-report.json"
            tampered = json.loads(report_path.read_text(encoding="utf-8"))
            tampered["validation"]["neutral_reconstruction_rgb_exact_ratio"] = 0.5
            report_path.write_bytes(canonical_json_bytes(tampered))
            with self.assertRaisesRegex(StageContractError, "validation or claims"):
                load_model_motion_draft_report(run_dir, **loader_arguments)

    def test_loader_rejects_extra_and_nonregular_output_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.motion-inventory"
            run_dir.mkdir()
            write_sparse_motion_fixture(run_dir)
            generate_model_motion_draft(run_dir)
            loader_arguments = motion_loader_arguments(run_dir)
            output = run_dir / "motion-draft"

            extra = output / "extra.bin"
            extra.write_bytes(b"extra")
            with self.assertRaisesRegex(StageContractError, "inventory"):
                load_model_motion_draft_report(run_dir, **loader_arguments)
            extra.unlink()

            nonregular = output / "extra-directory"
            nonregular.mkdir()
            with self.assertRaisesRegex(StageContractError, "inventory"):
                load_model_motion_draft_report(run_dir, **loader_arguments)

    def test_rejects_symlinked_run_directory_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / "actual-run"
            actual.mkdir()
            write_sparse_motion_fixture(actual)
            linked = root / "run.motion-link"
            try:
                linked.symlink_to(actual, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")

            with self.assertRaisesRegex(StageContractError, "run directory"):
                generate_model_motion_draft(linked)

    def test_rejects_legacy_model_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run.motion-legacy"
            run_dir.mkdir()
            result = write_model_fixture(run_dir, publish_result=False)
            result["profile_id"] = LEGACY_PROFILE_ID
            result["profile_sha256"] = LEGACY_PROFILE_SHA256
            result["dependencies_sha256"] = LEGACY_DEPENDENCIES_SHA256
            (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))

            with self.assertRaisesRegex(StageContractError, "active model profile"):
                generate_model_motion_draft(run_dir)
            self.assertFalse((run_dir / "motion-draft").exists())


if __name__ == "__main__":
    unittest.main()
