from __future__ import annotations

import importlib
import json
import struct
import tempfile
import unittest
import zlib
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from spikes.gate_f_runner.contracts import StageStatus
from spikes.gate_f_runner.raster import PNG_SIGNATURE, _verify_output_png, build_raster_registry
from spikes.gate_f_runner.runner import PipelineRunner

ROOT = Path(__file__).resolve().parents[1]


def png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def purpose_created_png(*, width: int = 2, height: int = 2, srgb: bool = True) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    colors = ((255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 255, 255), (255, 255, 0, 0))
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(colors[(y * width + x) % len(colors)])
        rows.append(bytes(row))
    chunks = [png_chunk(b"IHDR", ihdr)]
    if srgb:
        chunks.append(png_chunk(b"sRGB", b"\x00"))
    chunks.extend((png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9)), png_chunk(b"IEND", b"")))
    return PNG_SIGNATURE + b"".join(chunks)


def purpose_created_jpeg(*, width: int = 12, height: int = 8, orientation: int = 1) -> bytes:
    from io import BytesIO
    from PIL import Image

    image = Image.new("RGB", (width, height), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, width // 2, height))
    exif = Image.Exif()
    exif[0x0112] = orientation
    stream = BytesIO()
    image.save(stream, format="JPEG", quality=100, subsampling=0, optimize=False, progressive=False, exif=exif)
    image.close()
    return stream.getvalue()


def config_bytes() -> bytes:
    return (
        json.dumps(
            {
                "max_width": 8192,
                "max_height": 8192,
                "max_pixels": 40000000,
                "max_metadata_bytes": 1048576,
                "max_icc_profile_bytes": 1048576,
                "required_pillow_version": "12.1.0",
                "png_compress_level": 9,
                "rendering_intent": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def make_fixture(root: Path, source: bytes, *, media_type: str = "image/png", output_bytes: int = 1048576) -> tuple[Path, Path]:
    source_path = root / "source.bin"
    source_path.write_bytes(source)
    configs = root / "configs"
    configs.mkdir()
    config = config_bytes()
    (configs / "raster.json").write_bytes(config)
    spec = {
        "$schema": str(ROOT / "schemas" / "gate-f-run-spec" / "v0.1" / "run-spec.schema.json"),
        "format": "oneclick2d.gate-f-run-spec",
        "format_version": "0.1.0",
        "scope": "disposable-gate-f-spike",
        "execution_profile": "python-pillow-12.1.0-in-process-v1",
        "root_seed_u64": "00000000000000000042",
        "source": {"role": "source_raster", "sha256": sha256(source).hexdigest(), "media_type": media_type, "max_bytes": 26214400},
        "expected_result_role": "raster_normalization_report",
        "stages": [{
            "id": "stage.raster-normalize",
            "stage_type": "oc2d.spike.raster-normalize",
            "adapter_id": "raster.normalize.pillow.v1",
            "config_uri": "configs/raster.json",
            "config_sha256": sha256(config).hexdigest(),
            "limits": {
                "max_wall_time_ms": 30000,
                "max_cpu_time_ms": 30000,
                "max_peak_ram_bytes": 536870912,
                "max_scratch_bytes": 1048576,
                "max_output_bytes": output_bytes,
                "max_output_files": 2,
                "max_peak_vram_bytes": 0,
                "gpu_allowed": False,
            },
        }],
    }
    spec_path = root / "run-spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return spec_path, source_path


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFRasterAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import PIL

        if PIL.__version__ != "12.1.0":
            raise unittest.SkipTest("functional raster tests require locked Pillow 12.1.0")

    def _run(
        self,
        source: bytes,
        *,
        media_type: str = "image/png",
        output_bytes: int = 1048576,
    ) -> tuple[StageStatus, dict[str, object], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        spec_path, source_path = make_fixture(root, source, media_type=media_type, output_bytes=output_bytes)
        status, manifest_path = PipelineRunner(build_raster_registry(), root / "workspace").run(
            spec_path=spec_path,
            source_path=source_path,
            run_id="run.raster",
            source_revision="source.test",
            build_id="build.test",
        )
        return status, json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path

    def test_png_normalization_is_deterministic_and_metadata_free(self) -> None:
        first_status, first_manifest, first_path = self._run(purpose_created_png())
        second_status, second_manifest, second_path = self._run(purpose_created_png())
        self.assertEqual(StageStatus.SUCCEEDED, first_status)
        self.assertEqual(StageStatus.SUCCEEDED, second_status)
        first_png = next(item for item in first_manifest["stages"][0]["outputs"] if item["role"] == "normalized_raster")
        second_png = next(item for item in second_manifest["stages"][0]["outputs"] if item["role"] == "normalized_raster")
        self.assertEqual(first_png["sha256"], second_png["sha256"])
        self.assertEqual("numeric-tolerance", first_manifest["stages"][0]["determinism"])
        first_bytes = (first_path.parent / first_png["uri"]).read_bytes()
        _verify_output_png(first_bytes, (2, 2))
        report = json.loads((first_path.parent / first_manifest["result"]["uri"]).read_text(encoding="utf-8"))
        self.assertFalse(report["gate_f_feasibility_proven"])
        self.assertEqual("png-srgb-declared", report["color_policy"])
        self.assertEqual([], report["finding_codes"])

    def test_untagged_png_declares_assumption(self) -> None:
        status, manifest, manifest_path = self._run(purpose_created_png(srgb=False))
        self.assertEqual(StageStatus.SUCCEEDED, status)
        report = json.loads((manifest_path.parent / manifest["result"]["uri"]).read_text(encoding="utf-8"))
        self.assertEqual("untagged-assumed-srgb", report["color_policy"])
        self.assertEqual(["RASTER_UNTAGGED_ASSUMED_SRGB"], report["finding_codes"])

    def test_jpeg_normalization_is_deterministic_and_metadata_free(self) -> None:
        source = purpose_created_jpeg()
        first_status, first_manifest, first_path = self._run(source, media_type="image/jpeg")
        second_status, second_manifest, _ = self._run(source, media_type="image/jpeg")
        self.assertEqual(StageStatus.SUCCEEDED, first_status)
        self.assertEqual(StageStatus.SUCCEEDED, second_status)
        first_png = next(item for item in first_manifest["stages"][0]["outputs"] if item["role"] == "normalized_raster")
        second_png = next(item for item in second_manifest["stages"][0]["outputs"] if item["role"] == "normalized_raster")
        self.assertEqual(first_png["sha256"], second_png["sha256"])
        output = (first_path.parent / first_png["uri"]).read_bytes()
        _verify_output_png(output, (12, 8))
        self.assertNotIn(b"Exif", output)
        report = json.loads((first_path.parent / first_manifest["result"]["uri"]).read_text(encoding="utf-8"))
        self.assertEqual("JPEG", report["input"]["format"])
        self.assertEqual("image/jpeg", report["input"]["media_type"])
        self.assertEqual({"value": 1, "applied": False}, report["orientation"])
        self.assertEqual("untagged-assumed-srgb", report["color_policy"])
        self.assertEqual(["RASTER_UNTAGGED_ASSUMED_SRGB"], report["finding_codes"])

    def test_jpeg_exif_orientation_is_applied_before_metadata_removal(self) -> None:
        from io import BytesIO
        from PIL import Image

        status, manifest, manifest_path = self._run(purpose_created_jpeg(orientation=6), media_type="image/jpeg")
        self.assertEqual(StageStatus.SUCCEEDED, status)
        raster = next(item for item in manifest["stages"][0]["outputs"] if item["role"] == "normalized_raster")
        output = (manifest_path.parent / raster["uri"]).read_bytes()
        _verify_output_png(output, (8, 12))
        self.assertNotIn(b"Exif", output)
        with Image.open(BytesIO(output)) as normalized:
            top = normalized.getpixel((4, 2))
            bottom = normalized.getpixel((4, 9))
        self.assertGreater(top[0], top[2])
        self.assertGreater(bottom[2], bottom[0])
        report = json.loads((manifest_path.parent / manifest["result"]["uri"]).read_text(encoding="utf-8"))
        self.assertEqual({"value": 6, "applied": True}, report["orientation"])
        self.assertEqual({"width": 12, "height": 8}, {key: report["input"][key] for key in ("width", "height")})
        self.assertEqual({"width": 8, "height": 12}, {key: report["output"][key] for key in ("width", "height")})

    def test_media_type_mismatch_is_blocked(self) -> None:
        status, manifest, _ = self._run(purpose_created_png(), media_type="image/jpeg")
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_MEDIA_TYPE_MISMATCH", manifest["terminal_reason_code"])
        self.assertNotIn("result", manifest)

        status, manifest, _ = self._run(purpose_created_jpeg(), media_type="image/png")
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_MEDIA_TYPE_MISMATCH", manifest["terminal_reason_code"])
        self.assertNotIn("result", manifest)

    def test_invalid_jpeg_exif_orientation_is_blocked(self) -> None:
        status, manifest, _ = self._run(purpose_created_jpeg(orientation=9), media_type="image/jpeg")
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_EXIF_INVALID", manifest["terminal_reason_code"])
        self.assertNotIn("result", manifest)

    def test_jpeg_trailing_bytes_are_blocked(self) -> None:
        status, manifest, _ = self._run(purpose_created_jpeg() + b"trailing", media_type="image/jpeg")
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_CONTAINER_INVALID", manifest["terminal_reason_code"])

    def test_mpo_control_segment_is_blocked(self) -> None:
        source = purpose_created_jpeg()
        mpf_payload = b"MPF\x00"
        mpf_segment = b"\xff\xe2" + struct.pack(">H", len(mpf_payload) + 2) + mpf_payload
        status, manifest, _ = self._run(source[:2] + mpf_segment + source[2:], media_type="image/jpeg")
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_MULTIFRAME_UNSUPPORTED", manifest["terminal_reason_code"])

    def test_trailing_bytes_are_blocked(self) -> None:
        status, manifest, _ = self._run(purpose_created_png() + b"trailing")
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_CONTAINER_INVALID", manifest["terminal_reason_code"])

    def test_animation_control_chunk_is_blocked(self) -> None:
        source = purpose_created_png()
        source = source[:33] + png_chunk(b"acTL", struct.pack(">II", 1, 0)) + source[33:]
        status, manifest, _ = self._run(source)
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_MULTIFRAME_UNSUPPORTED", manifest["terminal_reason_code"])

    def test_aggregate_compressed_text_limit_is_blocked(self) -> None:
        source = purpose_created_png()
        payload = b"note\x00\x00" + zlib.compress(b"x" * 700000, 9)
        chunks = png_chunk(b"zTXt", payload) + png_chunk(b"zTXt", payload)
        source = source[:33] + chunks + source[33:]
        status, manifest, _ = self._run(source)
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_METADATA_LIMIT_EXCEEDED", manifest["terminal_reason_code"])

    def test_compressed_itxt_limit_is_blocked(self) -> None:
        source = purpose_created_png()
        payload = b"note\x00\x01\x00\x00\x00" + zlib.compress(b"x" * 1100000, 9)
        source = source[:33] + png_chunk(b"iTXt", payload) + source[33:]
        status, manifest, _ = self._run(source)
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_METADATA_LIMIT_EXCEEDED", manifest["terminal_reason_code"])

    def test_embedded_rgb_icc_is_converted_and_stripped(self) -> None:
        from io import BytesIO
        from PIL import Image, ImageCms

        image = Image.new("RGB", (2, 2), (64, 128, 192))
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        stream = BytesIO()
        image.save(stream, format="PNG", icc_profile=profile)
        image.close()
        status, manifest, manifest_path = self._run(stream.getvalue())
        self.assertEqual(StageStatus.SUCCEEDED, status)
        raster = next(item for item in manifest["stages"][0]["outputs"] if item["role"] == "normalized_raster")
        output = (manifest_path.parent / raster["uri"]).read_bytes()
        self.assertNotIn(b"iCCP", output)
        self.assertIn(b"sRGB", output)
        report = json.loads((manifest_path.parent / manifest["result"]["uri"]).read_text(encoding="utf-8"))
        self.assertEqual("embedded-icc-to-srgb", report["color_policy"])

    def test_pixel_bomb_is_typed_block(self) -> None:
        source = purpose_created_png(width=2, height=2)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        spec_path, source_path = make_fixture(root, source)
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        config_path = root / "configs" / "raster.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["max_pixels"] = 1
        config_data = (json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n").encode()
        config_path.write_bytes(config_data)
        spec["stages"][0]["config_sha256"] = sha256(config_data).hexdigest()
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        status, manifest_path = PipelineRunner(build_raster_registry(), root / "workspace").run(
            spec_path=spec_path, source_path=source_path, run_id="run.bomb", source_revision="source.test", build_id="build.test"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(StageStatus.BLOCKED, status)
        self.assertEqual("RASTER_DECOMPRESSION_LIMIT_EXCEEDED", manifest["terminal_reason_code"])

    def test_output_limit_leaves_no_committed_png(self) -> None:
        status, manifest, manifest_path = self._run(purpose_created_png(), output_bytes=32)
        self.assertEqual(StageStatus.FAILED, status)
        self.assertEqual("STAGE_RESOURCE_LIMIT_EXCEEDED", manifest["terminal_reason_code"])
        self.assertFalse(any((manifest_path.parent / "committed").rglob("normalized.png")))

    def test_locked_runtime_version_is_enforced(self) -> None:
        from spikes.gate_f_runner.raster import _load_pillow
        import PIL

        with patch.object(PIL, "__version__", "12.2.0"):
            with self.assertRaisesRegex(ValueError, "RASTER_RUNTIME_UNSUPPORTED"):
                _load_pillow()


class RasterOptionalDependencyTests(unittest.TestCase):
    def test_registry_build_does_not_import_pillow(self) -> None:
        original_import = __import__

        def guarded_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            registry = build_raster_registry()
        self.assertIsNotNone(registry.resolve("raster.normalize.pillow.v1"))


if __name__ == "__main__":
    unittest.main()
