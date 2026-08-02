from __future__ import annotations

import base64
import functools
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import struct
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from spikes.gate_f_runner.__main__ import main
from spikes.gate_f_runner.contracts import StageContractError
from spikes.gate_f_runner.model_worker import (
    DEVICE_POLICY_PATH,
    ENTRYPOINT_ROOT,
    MODEL_PART_NAMES,
    MODEL_SEMANTIC_NAMES,
    NF4_MARIGOLD_DEVICE_POLICY_ID,
    PROFILE_ID,
    PSD_PIXEL_PROJECTION_ALGORITHM_ID,
    _consume_entrypoint_attestation,
    _inventory,
    _invoke_wsl,
    _load_profile,
    _run_checked,
    _validated_entrypoint,
    _validated_postprocess,
    _verify_runtime,
    _verify_wsl_models,
    _wsl_path,
    run_model_worker,
)
from spikes.gate_f_runner.model_entrypoints.nf4_marigold_device_policy import Nf4MarigoldOffloadAdapter
from spikes.gate_f_runner.runtime import read_bounded_file


PINNED_MODEL_ROOT = Path.home() / "oneclick2d-model-spikes" / "see-through"
PINNED_MODEL_PYTHON = PINNED_MODEL_ROOT / ".venv" / "bin" / "python"
V4_ENTRYPOINT = ENTRYPOINT_ROOT / "see_through_v3_nf4_source_preserve_v4.py"


def _minimal_psd() -> bytes:
    return base64.b64decode(
        "OEJQUwABAAAAAAAAAAQAAAACAAAAAgAIAAMAAAAAAAAAXjhCSU0EIQAAAAAAUQAAAAEBAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAAQAAAAFgAAABWAACAAAAAAAAAAAAAAABAAAAAQAF//8AAAAGAAAAAAAGAAEAAAAGAAIAAAAG//4AAAAGOEJJTW5vcm3/AAgAAAAATAAAABQAAAAAAAAAAAAAAAEAAAABAAAAAAAAACgAAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//BGZhY2UAAAAAAAABAAAAAQAAAAIAAAACAAX//wAAAAYAAAAAAAYAAQAAAAYAAgAAAAb//gAAAAY4QklNbm9ybf8ACAAAAABMAAAAFAAAAAEAAAABAAAAAgAAAAIAAAAAAAAAKAAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8FbW91dGgAAAABAAIA/wABAAIA/wABAAIAAAABAAIAAAABAAIA/wABAAIA/wABAAIAAAABAAIAAAABAAIAAAABAAIA/wAAAAAAAAAA/wAAAAAAAAAAAAAA/////w=="
    )


def _u32(value: int) -> bytes:
    return struct.pack(">I", value)


def _packbits(data: bytes) -> bytes:
    encoded = bytearray()
    index = 0
    while index < len(data):
        run = 1
        while index + run < len(data) and run < 128 and data[index + run] == data[index]:
            run += 1
        if run >= 2:
            encoded.extend((257 - run, data[index]))
            index += run
            continue
        start = index
        index += 1
        while index < len(data) and index - start < 128:
            run = 1
            while index + run < len(data) and run < 128 and data[index + run] == data[index]:
                run += 1
            if run >= 2:
                break
            index += 1
        encoded.append(index - start - 1)
        encoded.extend(data[start:index])
    return bytes(encoded)


def _writer_resource() -> bytes:
    writer = "psd-tools 1.14.2"
    unicode_writer = _u32(len(writer)) + writer.encode("utf-16-be")
    payload = _u32(1) + b"\1" + unicode_writer + unicode_writer + _u32(1)
    return b"8BIM" + struct.pack(">H", 1057) + b"\0\0" + _u32(len(payload)) + payload + b"\0"


@functools.lru_cache(maxsize=2)
def _layered_psd(*, grayscale: bool) -> bytes:
    width = height = 1280
    channel_ids = (-1, 0) if grayscale else (-1, 0, 1, 2)
    records = bytearray()
    channel_data = bytearray()
    for layer_index, name in enumerate(("face", "mouth")):
        channels: list[tuple[int, bytes]] = []
        for channel_index, channel_id in enumerate(channel_ids):
            value = 255 if channel_id == -1 else 64 + layer_index * 64 + channel_index
            encoded_row = _packbits(bytes([value]) * width)
            data = struct.pack(">H", 1) + struct.pack(">H", len(encoded_row)) * height + encoded_row * height
            channels.append((channel_id, data))
        records.extend(struct.pack(">iiiiH", 0, 0, height, width, len(channels)))
        for channel_id, data in channels:
            records.extend(struct.pack(">hI", channel_id, len(data)))
        records.extend(b"8BIMnorm" + bytes((255, 0, 8, 0)))
        pascal_name = bytes((len(name),)) + name.encode("ascii")
        pascal_name += b"\0" * (-len(pascal_name) % 4)
        extra = _u32(0) + _u32(40) + b"\0\0\xff\xff" * 10 + pascal_name
        records.extend(_u32(len(extra)) + extra)
        for _, data in channels:
            channel_data.extend(data)
    layer_info = struct.pack(">h", len(("face", "mouth"))) + records + channel_data
    layer_info += b"\0" * (-len(layer_info) % 4)
    layer_and_mask = _u32(len(layer_info)) + layer_info + _u32(0)
    document_channels = 1 if grayscale else 4
    color_mode = 1 if grayscale else 3
    header = b"8BPS" + struct.pack(">H", 1) + b"\0" * 6
    header += struct.pack(">HIIHH", document_channels, height, width, 8, color_mode)
    resource = _writer_resource()
    merged_values = (64,) if grayscale else (255, 64, 96, 128)
    merged = b"".join(bytes((value,)) * (width * height) for value in merged_values)
    return header + _u32(0) + _u32(len(resource)) + resource + _u32(len(layer_and_mask)) + layer_and_mask + struct.pack(">H", 0) + merged


@functools.lru_cache(maxsize=2)
def _model_png(mode: str) -> bytes:
    from PIL import Image

    color = (30, 90, 160, 255) if mode == "RGBA" else 120
    with Image.new(mode, (1280, 1280), color) as image:
        stream = BytesIO()
        image.save(stream, format="PNG")
    return stream.getvalue()


def _write_complete_model_output(output: Path) -> None:
    result_root = output / "input"
    semantic_root = result_root / "input"
    semantic_root.mkdir(parents=True)
    rgba = _model_png("RGBA")
    depth = _model_png("L")
    for name in MODEL_SEMANTIC_NAMES:
        (semantic_root / f"{name}.png").write_bytes(rgba)
    for name in MODEL_PART_NAMES:
        (semantic_root / f"{name}_depth.png").write_bytes(depth)
    for name in ("reconstruction.png", "src_head.png", "src_img.png"):
        (semantic_root / name).write_bytes(rgba)
    (semantic_root / "info.json").write_text(
        json.dumps({"parts": {name: {} for name in MODEL_PART_NAMES}}, separators=(",", ":")),
        encoding="utf-8",
    )
    (semantic_root / "stats.json").write_text(
        json.dumps(
            {
                "quant_mode": "nf4",
                "peak_vram_gb": 6.25,
                "layerdiff_time_s": 10.0,
                "marigold_time_s": 2.0,
                "psd_time_s": 1.0,
                "total_time_s": 13.0,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (result_root / "input.psd").write_bytes(_layered_psd(grayscale=False))
    (result_root / "input_depth.psd").write_bytes(_layered_psd(grayscale=True))
    (result_root / "input.psd.json").write_text(
        json.dumps(
            {
                "parts": {
                    name: {"xyxy": [0, 0, 1280, 1280], "tag": name, "depth_median": 0.5}
                    for name in ("face", "mouth")
                },
                "frame_size": [1280, 1280],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _valid_entrypoint_attestation() -> dict[str, object]:
    return {
        "format": "oneclick2d.model-entrypoint-attestation",
        "format_version": "0.1.0",
        "policy_id": NF4_MARIGOLD_DEVICE_POLICY_ID,
        "requested_cpu_offload": True,
        "execution_device": "cuda:0",
        "components": {
            "vae": {
                "storage_devices": ["meta"],
                "execution_hook_devices": ["cuda:0"],
                "upstream_cuda_move_suppressed": True,
                "disposition": "sequential-cpu-offload",
            },
            "unet": {
                "storage_devices": ["cuda:0"],
                "execution_hook_devices": [],
                "upstream_cuda_move_suppressed": True,
                "disposition": "resident-quantized",
            },
            "text_encoder": {
                "storage_devices": [],
                "execution_hook_devices": [],
                "upstream_cuda_move_suppressed": True,
                "disposition": "cached-and-released",
            },
        },
        "psd_pixel_projection_algorithm_id": PSD_PIXEL_PROJECTION_ALGORITHM_ID,
        "psd_projection_verified": True,
    }


def _unpack_packbits(data: bytes, expected_length: int) -> bytes:
    decoded = bytearray()
    offset = 0
    while offset < len(data) and len(decoded) < expected_length:
        control = data[offset]
        offset += 1
        if control <= 127:
            count = control + 1
            decoded.extend(data[offset : offset + count])
            offset += count
        elif control >= 129:
            count = 257 - control
            decoded.extend(data[offset : offset + 1] * count)
            offset += 1
    if len(decoded) != expected_length:
        raise AssertionError("PSD PackBits row length mismatch")
    return bytes(decoded)


def _decode_psd_layers(path: Path) -> dict[str, tuple[tuple[int, int, int, int], bytes]]:
    exact = path.read_bytes()
    if exact[:6] != b"8BPS\0\1":
        raise AssertionError("not a version-1 PSD")
    offset = 26
    for _ in range(2):
        length = struct.unpack_from(">I", exact, offset)[0]
        offset += 4 + length
    layer_and_mask_length = struct.unpack_from(">I", exact, offset)[0]
    offset += 4
    section_end = offset + layer_and_mask_length
    layer_info_length = struct.unpack_from(">I", exact, offset)[0]
    offset += 4
    layer_info_end = offset + layer_info_length
    layer_count = abs(struct.unpack_from(">h", exact, offset)[0])
    offset += 2
    records: list[dict[str, object]] = []
    for _ in range(layer_count):
        top, left, bottom, right, channel_count = struct.unpack_from(">iiiiH", exact, offset)
        offset += 18
        channels = []
        for _ in range(channel_count):
            channel_id, length = struct.unpack_from(">hI", exact, offset)
            offset += 6
            channels.append((channel_id, length))
        if exact[offset : offset + 4] != b"8BIM":
            raise AssertionError("PSD layer blend signature mismatch")
        offset += 12
        extra_length = struct.unpack_from(">I", exact, offset)[0]
        offset += 4
        extra_end = offset + extra_length
        mask_length = struct.unpack_from(">I", exact, offset)[0]
        offset += 4 + mask_length
        ranges_length = struct.unpack_from(">I", exact, offset)[0]
        offset += 4 + ranges_length
        name_length = exact[offset]
        offset += 1
        name = exact[offset : offset + name_length].decode("ascii")
        offset += name_length
        offset += -(name_length + 1) % 4
        offset = extra_end
        records.append(
            {
                "name": name,
                "bbox": (left, top, right, bottom),
                "channels": channels,
            }
        )

    decoded_layers: dict[str, tuple[tuple[int, int, int, int], bytes]] = {}
    for record in records:
        left, top, right, bottom = record["bbox"]
        width = right - left
        height = bottom - top
        decoded_channels: dict[int, bytes] = {}
        for channel_id, length in record["channels"]:
            channel_end = offset + length
            compression = struct.unpack_from(">H", exact, offset)[0]
            offset += 2
            if compression == 0:
                channel = exact[offset:channel_end]
            elif compression == 1:
                row_lengths = struct.unpack_from(f">{height}H", exact, offset)
                offset += height * 2
                rows = []
                for row_length in row_lengths:
                    rows.append(_unpack_packbits(exact[offset : offset + row_length], width))
                    offset += row_length
                channel = b"".join(rows)
            else:
                raise AssertionError("unsupported PSD compression")
            offset = channel_end
            decoded_channels[channel_id] = channel
        rgba = bytearray(width * height * 4)
        for index in range(width * height):
            rgba[index * 4 : index * 4 + 4] = bytes(
                (
                    decoded_channels[0][index],
                    decoded_channels[1][index],
                    decoded_channels[2][index],
                    decoded_channels[-1][index],
                )
            )
        decoded_layers[record["name"]] = (record["bbox"], bytes(rgba))
    if offset > layer_info_end or layer_info_end > section_end:
        raise AssertionError("PSD layer section bounds mismatch")
    return decoded_layers


def _decoded_psd_pixel(
    layer: tuple[tuple[int, int, int, int], bytes],
    x: int,
    y: int,
) -> tuple[int, int, int, int]:
    (left, top, right, bottom), pixels = layer
    if not left <= x < right or not top <= y < bottom:
        raise AssertionError("pixel is outside PSD layer")
    offset = ((y - top) * (right - left) + (x - left)) * 4
    return tuple(pixels[offset : offset + 4])  # type: ignore[return-value]


def _write_source_projection_fixture(directory: Path) -> None:
    from PIL import Image

    size = (24, 24)
    source = Image.new("RGBA", size, (10, 20, 30, 255))
    for x in range(12, 16):
        for y in range(size[1]):
            source.putpixel((x, y), (10, 20, 30, 0))
    for x in range(16, 20):
        for y in range(size[1]):
            source.putpixel((x, y), (10, 20, 30, 31))
    for x in range(20, 24):
        for y in range(size[1]):
            source.putpixel((x, y), (10, 20, 30, 32))
    source.save(directory / "src_img.png", format="PNG")

    colors = {"face": (100, 1, 1), "nose": (1, 100, 1), "mouth": (1, 1, 100)}
    starts = {"face": 0, "nose": 4, "mouth": 8}
    depths = {"face": 120, "nose": 80, "mouth": 40}
    for name in MODEL_PART_NAMES:
        rgba = Image.new("RGBA", size, (*colors.get(name, (0, 0, 0)), 0))
        depth = Image.new("L", size, depths.get(name, 255))
        if name in starts:
            for x in range(starts[name], size[0]):
                for y in range(size[1]):
                    rgba.putpixel((x, y), (*colors[name], 255))
        rgba.save(directory / f"{name}.png", format="PNG")
        depth.save(directory / f"{name}_depth.png", format="PNG")
    (directory / "info.json").write_text(
        json.dumps({"parts": {name: {} for name in MODEL_PART_NAMES}}, separators=(",", ":")),
        encoding="utf-8",
    )


class GateFModelWorkerTests(unittest.TestCase):
    def test_profiles_have_pinned_safe_weight_artifacts(self) -> None:
        profile, exact = _load_profile()
        self.assertEqual(PROFILE_ID, profile["profile_id"])
        self.assertEqual(profile, json.loads(exact))
        self.assertRegex(profile["code"]["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(3, len(profile["models"]))
        weights = [weight for model in profile["models"] for weight in model["weights"]]
        self.assertEqual(8, len(weights))
        for model in profile["models"]:
            self.assertRegex(model["revision"], r"^[0-9a-f]{40}$")
            self.assertNotIn("..", Path(model["local_dir_relative_to_code_root"]).parts)
            self.assertGreater(len(model["config_files"]), 0)
            for config in model["config_files"]:
                self.assertNotIn("..", Path(config["path"]).parts)
                self.assertRegex(config["git_blob_sha1"], r"^[0-9a-f]{40}$")
        for weight in weights:
            self.assertTrue(weight["path"].endswith(".safetensors"))
            self.assertGreater(weight["byte_length"], 0)
            self.assertRegex(weight["sha256"], r"^[0-9a-f]{64}$")

        dependencies_path = (
            Path(__file__).parents[1]
            / "spikes"
            / "gate_f_runner"
            / "model_profiles"
            / profile["runtime"]["dependencies_profile"]
        )
        self.assertEqual(
            profile["runtime"]["dependencies_sha256"],
            hashlib.sha256(dependencies_path.read_bytes()).hexdigest(),
        )
        entrypoint_path = ENTRYPOINT_ROOT / profile["entrypoint"]["path"]
        self.assertEqual(entrypoint_path, _validated_entrypoint(profile))
        self.assertEqual("source-visible-rgb-by-depth-mask-clean.v2", _validated_postprocess(profile)["algorithm_id"])
        invalid_profile = {**profile, "postprocess": {**profile["postprocess"], "visible_alpha_threshold": 0}}
        with self.assertRaisesRegex(StageContractError, "postprocess profile"):
            _validated_postprocess(invalid_profile)

        landmark_path = Path(__file__).parents[1] / "spikes" / "gate_f_runner" / "model_profiles" / "hysts-anime-face-v0.1.0.json"
        landmark = json.loads(landmark_path.read_text(encoding="utf-8"))
        self.assertEqual("MIT", landmark["code"]["license"])
        self.assertEqual(2, len(landmark["models"]))
        self.assertTrue(all(model["path"] == "model.safetensors" for model in landmark["models"]))

    def test_profile_entrypoint_digest_matches_executed_file(self) -> None:
        profile, _ = _load_profile()
        entrypoint = profile["entrypoint"]
        entrypoint_path = ENTRYPOINT_ROOT / entrypoint["path"]
        self.assertEqual(entrypoint["sha256"], hashlib.sha256(entrypoint_path.read_bytes()).hexdigest())

    def test_profile_attests_device_policy_digest(self) -> None:
        profile, _ = _load_profile()
        device_policy = profile["entrypoint"]["device_policy"]
        self.assertEqual(DEVICE_POLICY_PATH.name, device_policy["path"])
        self.assertEqual(device_policy["sha256"], hashlib.sha256(DEVICE_POLICY_PATH.read_bytes()).hexdigest())
        self.assertEqual(ENTRYPOINT_ROOT / profile["entrypoint"]["path"], _validated_entrypoint(profile))

    def test_entrypoint_validation_rejects_mismatched_executed_file_digest(self) -> None:
        profile, _ = _load_profile()
        entrypoint_path = ENTRYPOINT_ROOT / profile["entrypoint"]["path"]
        real_read = read_bounded_file

        def changed_entrypoint(path: Path, limit: int = 64 * 1024 * 1024) -> bytes:
            if path == entrypoint_path:
                return real_read(path, limit) + b"\n# changed after profile publication\n"
            return real_read(path, limit)

        with mock.patch("spikes.gate_f_runner.model_worker.read_bounded_file", side_effect=changed_entrypoint):
            with self.assertRaisesRegex(StageContractError, "entrypoint digest mismatch"):
                _validated_entrypoint(profile)

    def test_inventory_describes_bounded_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layer.png").write_bytes(b"layer")
            inventory = _inventory(root)
            self.assertEqual("layer.png", inventory[0]["uri"])
            self.assertEqual(5, inventory[0]["byte_length"])

    def test_inventory_rejects_symlinks_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layer.png").write_bytes(b"layer")
            link = root / "linked.png"
            try:
                link.symlink_to(root / "layer.png")
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaises(StageContractError):
                _inventory(root)

    def test_checked_command_sanitizes_timeouts(self) -> None:
        process = mock.Mock()
        process.stdout = mock.Mock()
        process.stdout.read.return_value = b""
        process.stderr = mock.Mock()
        process.stderr.read.return_value = b""
        process.wait.side_effect = [subprocess.TimeoutExpired(["private", "path"], 1), 1]
        with mock.patch("spikes.gate_f_runner.model_worker.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(StageContractError, "isolated model command timed out") as raised:
                _run_checked(["private", "path"], timeout=1)
        self.assertNotIn("private", str(raised.exception))

    def test_checked_command_stops_after_bounded_output(self) -> None:
        process = mock.Mock()
        process.stdout = mock.Mock()
        process.stdout.read.side_effect = [b"12345", b""]
        process.stderr = mock.Mock()
        process.stderr.read.return_value = b""
        process.wait.return_value = 1
        with mock.patch("spikes.gate_f_runner.model_worker.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(StageContractError, "output exceeded its bound"):
                _run_checked(["command"], timeout=1, output_limit=4)
        process.kill.assert_called()

    def test_runtime_attestation_requires_exact_versions_and_cuda(self) -> None:
        profile, _ = _load_profile()
        runtime = profile["runtime"]
        actual = {
            "python": runtime["python_version"],
            "torch": runtime["torch_version"],
            "cuda": runtime["cuda_version"],
            "cuda_available": True,
            "packages": runtime["versions"],
            "timm_direct_url": {
                "url": "https://github.com/huggingface/pytorch-image-models",
                "vcs_info": {
                    "vcs": "git",
                    "commit_id": runtime["timm_commit"],
                },
            },
        }
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(actual, separators=(",", ":")).encode("utf-8"),
            stderr=b"",
        )
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", return_value=completed):
            _verify_runtime(profile, runtime["dependencies_sha256"])
        actual["cuda_available"] = False
        completed.stdout = json.dumps(actual, separators=(",", ":")).encode("utf-8")
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", return_value=completed):
            with self.assertRaisesRegex(StageContractError, "CUDA runtime is unavailable"):
                _verify_runtime(profile, runtime["dependencies_sha256"])

    def test_runtime_attestation_binds_dependency_profile(self) -> None:
        profile, _ = _load_profile()
        with self.assertRaisesRegex(StageContractError, "dependency profile mismatch"):
            _verify_runtime(profile, "0" * 64)

    def test_entrypoint_attestation_records_effective_offload_devices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attestation.json"
            path.write_text(json.dumps(_valid_entrypoint_attestation()), encoding="utf-8")
            _consume_entrypoint_attestation(path)
            self.assertFalse(path.exists())

    def test_entrypoint_attestation_rejects_all_cuda_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attestation.json"
            attestation = _valid_entrypoint_attestation()
            attestation["components"]["vae"]["storage_devices"] = ["cuda:0"]
            attestation["components"]["vae"]["execution_hook_devices"] = []
            path.write_text(json.dumps(attestation), encoding="utf-8")
            with self.assertRaisesRegex(StageContractError, "device attestation"):
                _consume_entrypoint_attestation(path)
            self.assertFalse(path.exists())

    def test_nf4_marigold_policy_never_places_all_components_on_cuda(self) -> None:
        timeline: list[tuple[str, str, str]] = []

        class Tensor:
            def __init__(self, device: str) -> None:
                self.device = device

        class Component:
            def __init__(self, name: str) -> None:
                self.name = name
                self.tensor = Tensor("cpu")

            @property
            def device(self) -> str:
                return self.tensor.device

            def parameters(self, recurse: bool = True):
                del recurse
                return iter((self.tensor,))

            def buffers(self, recurse: bool = True):
                del recurse
                return iter(())

            def modules(self):
                return iter((self,))

            def to(self, *args, **kwargs):
                device = kwargs.get("device", args[0] if args else None)
                if device is not None:
                    self.tensor.device = str(device)
                    timeline.append(pipeline.snapshot())
                return self

        class ReleasedTextEncoder:
            def parameters(self, recurse: bool = True):
                del recurse
                return iter(())

            def buffers(self, recurse: bool = True):
                del recurse
                return iter(())

            def modules(self):
                return iter((self,))

        class MockMarigoldPipeline:
            def __init__(self) -> None:
                self.vae = Component("vae")
                self.unet = Component("unet")
                self.text_encoder = Component("text_encoder")

            def live_components(self):
                return (self.vae, self.unet, self.text_encoder)

            def snapshot(self):
                return tuple(
                    item.tensor.device if hasattr(item, "tensor") else "released"
                    for item in self.live_components()
                )

            def cache_tag_embeds(self):
                self.text_encoder.to(device="cpu")
                self.text_encoder = ReleasedTextEncoder()

        pipeline = MockMarigoldPipeline()

        def mocked_cpu_offload(component, *, execution_device, offload_buffers):
            del offload_buffers
            component.tensor.device = "meta"
            component._hf_hook = mock.Mock(execution_device=execution_device)
            timeline.append((pipeline.vae.tensor.device, pipeline.unet.tensor.device, "released"))

        adapter = Nf4MarigoldOffloadAdapter(
            pipeline,
            cpu_offload=mocked_cpu_offload,
            execution_device="cuda:0",
        )
        adapter.vae.to(device="cuda")
        adapter.unet.to(device="cuda")
        adapter.text_encoder.to(device="cuda")
        adapter.cache_tag_embeds()

        self.assertTrue(timeline)
        self.assertTrue(all(sum(device.startswith("cuda") for device in state) < 3 for state in timeline))
        attestation = adapter.attestation(psd_projection_verified=True)
        self.assertEqual(["meta"], attestation["components"]["vae"]["storage_devices"])
        self.assertEqual(["cuda:0"], attestation["components"]["vae"]["execution_hook_devices"])
        self.assertEqual(["cuda:0"], attestation["components"]["unet"]["storage_devices"])
        self.assertEqual([], attestation["components"]["text_encoder"]["storage_devices"])

    def test_wsl_model_verification_rejects_tracked_checkout_changes(self) -> None:
        profile, _ = _load_profile()
        completed = [
            mock.Mock(returncode=0, stdout=(profile["code"]["commit"] + "\n").encode("ascii"), stderr=b""),
            mock.Mock(returncode=0, stdout=b" M inference/scripts/inference_psd_quantized.py\n", stderr=b""),
        ]
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", side_effect=completed):
            with self.assertRaisesRegex(StageContractError, "tracked changes"):
                _verify_wsl_models(profile)

    def test_wsl_model_verification_binds_executed_upstream_entrypoint_to_commit(self) -> None:
        profile, _ = _load_profile()
        completed = [
            mock.Mock(returncode=0, stdout=(profile["code"]["commit"] + "\n").encode("ascii"), stderr=b""),
            mock.Mock(returncode=0, stdout=b"", stderr=b""),
            mock.Mock(returncode=0, stdout=("a" * 40 + "\n").encode("ascii"), stderr=b""),
            mock.Mock(returncode=0, stdout=("b" * 40 + "\n").encode("ascii"), stderr=b""),
        ]
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", side_effect=completed):
            with self.assertRaisesRegex(StageContractError, "does not match the pinned commit"):
                _verify_wsl_models(profile)

    def test_wsl_path_uses_forward_slashes_for_windows_argument(self) -> None:
        completed = mock.Mock(returncode=0, stdout=b"/mnt/d/project/live2d/source.png\n", stderr=b"")
        path = Path("D:/project/live2d/source.png")
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", return_value=completed) as run:
            self.assertEqual("/mnt/d/project/live2d/source.png", _wsl_path(path, "Ubuntu"))
        command = run.call_args.args[0]
        self.assertEqual(path.resolve().as_posix(), command[-1])
        self.assertNotIn("\\", command[-1])

    def test_wsl_invocation_forces_offline_pinned_model_paths(self) -> None:
        profile, _ = _load_profile()
        calls = []

        def completed(command, **kwargs):
            calls.append(command)
            if "wslpath" in command:
                return mock.Mock(returncode=0, stdout=b"/mnt/d/model-spike\n", stderr=b"")
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "spikes.gate_f_runner.model_worker._verify_wsl_models"
        ), mock.patch("spikes.gate_f_runner.model_worker._consume_entrypoint_attestation"), mock.patch(
            "spikes.gate_f_runner.model_worker._run_checked", side_effect=completed
        ):
            root = Path(directory)
            (root / "output").mkdir()
            _invoke_wsl(root / "source.png", root / "output", profile, 30)
        command = calls[-1]
        self.assertIn("HF_HUB_OFFLINE=1", command)
        self.assertIn("TRANSFORMERS_OFFLINE=1", command)
        self.assertIn("ONECLICK2D_ENTRYPOINT_ATTESTATION=/mnt/d/model-spike/.entrypoint-attestation.json", command)
        self.assertIn("PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True", command)
        self.assertIn("--cpu_offload", command)
        self.assertIn("--no_group_offload", command)
        self.assertIn("models/seethroughv0.0.2_layerdiff3d_nf4", command)
        self.assertIn("models/seethroughv0.0.1_marigold_nf4", command)

    def test_model_cli_sanitizes_timeout_and_removes_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            workspace = root / "workspace"
            stderr = StringIO()
            argv = [
                "gate-f-runner",
                "model",
                "--source",
                str(source),
                "--run-id",
                "run.timeout",
                "--workspace-root",
                str(workspace),
            ]
            with mock.patch("sys.argv", argv), mock.patch(
                "spikes.gate_f_runner.model_worker._verify_runtime",
                side_effect=StageContractError("isolated model command timed out"),
            ), redirect_stderr(stderr):
                self.assertEqual(70, main())
            self.assertEqual("model spike failed: isolated model worker error\n", stderr.getvalue())
            self.assertNotIn(str(source), stderr.getvalue())
            self.assertFalse((workspace / "run.timeout").exists())

@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFModelWorkerPillowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if importlib.metadata.version("Pillow") != "12.1.0":
            raise unittest.SkipTest("complete model artifact fixtures require locked Pillow 12.1.0")

    def test_worker_reports_model_only_after_successful_pinned_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"

            def invoke(source_path, output_path, profile, timeout_seconds):
                _write_complete_model_output(output_path)
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_wsl", side_effect=invoke
            ):
                report = run_model_worker(source, output, timeout_seconds=30)
            self.assertTrue(report["model_used"])
            self.assertFalse(report["oc2d_produced"])
            self.assertEqual("GATE_F_NOT_EVALUATED", report["gate_f_status"])
            self.assertEqual("input/input.psd", report["psd"]["uri"])

    def test_worker_rejects_successful_process_with_partial_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"

            def invoke(source_path, output_path, profile, timeout_seconds):
                result_root = output_path / "input"
                (result_root / source_path.stem).mkdir(parents=True)
                (result_root / "input.psd").write_bytes(_minimal_psd())
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_wsl", side_effect=invoke
            ):
                with self.assertRaisesRegex(StageContractError, "semantic metadata is invalid"):
                    run_model_worker(source, output, timeout_seconds=30)

    def test_worker_rejects_nonfixed_semantic_ontology(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"

            def invoke(source_path, output_path, profile, timeout_seconds):
                _write_complete_model_output(output_path)
                info = output_path / "input" / source_path.stem / "info.json"
                changed = {"parts": {**{name: {} for name in MODEL_PART_NAMES[:-1]}, "other": {}}}
                info.write_text(json.dumps(changed, separators=(",", ":")), encoding="utf-8")
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_wsl", side_effect=invoke
            ):
                with self.assertRaisesRegex(StageContractError, "semantic ontology is invalid"):
                    run_model_worker(source, output, timeout_seconds=30)

    def test_worker_rejects_unparseable_psd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"

            def invoke(source_path, output_path, profile, timeout_seconds):
                _write_complete_model_output(output_path)
                (output_path / "input" / "input.psd").write_bytes(b"8BPSmodel")
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_wsl", side_effect=invoke
            ):
                with self.assertRaisesRegex(StageContractError, "model worker PSD is invalid"):
                    run_model_worker(source, output, timeout_seconds=30)

    def test_worker_rejects_unparseable_depth_psd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"

            def invoke(source_path, output_path, profile, timeout_seconds):
                _write_complete_model_output(output_path)
                (output_path / "input" / "input_depth.psd").write_bytes(b"8BPSdepth")
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_wsl", side_effect=invoke
            ):
                with self.assertRaisesRegex(StageContractError, "model worker depth PSD is invalid"):
                    run_model_worker(source, output, timeout_seconds=30)

    def test_worker_rejects_psd_metadata_that_does_not_match_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"

            def invoke(source_path, output_path, profile, timeout_seconds):
                _write_complete_model_output(output_path)
                metadata_path = output_path / "input" / "input.psd.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["parts"]["face"]["xyxy"] = [1, 0, 1280, 1280]
                metadata_path.write_text(json.dumps(metadata, separators=(",", ":")), encoding="utf-8")
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_wsl", side_effect=invoke
            ):
                with self.assertRaisesRegex(StageContractError, "PSD metadata is invalid"):
                    run_model_worker(source, output, timeout_seconds=30)

    def test_worker_failure_never_returns_model_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"
            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_wsl",
                return_value=mock.Mock(returncode=1, stdout=b"", stderr=b"private detail"),
            ):
                with self.assertRaisesRegex(StageContractError, "model worker process failed"):
                    run_model_worker(source, output, timeout_seconds=30)


@unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow is not installed")
class GateFV4EntrypointPillowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if importlib.metadata.version("Pillow") != "12.1.0":
            raise unittest.SkipTest("v4 pixel fixtures require locked Pillow 12.1.0")
        if not PINNED_MODEL_PYTHON.is_file():
            raise unittest.SkipTest("pinned See-through worker environment is unavailable")
        cls._temporary = tempfile.TemporaryDirectory()
        cls.semantic_root = Path(cls._temporary.name) / "input"
        cls.semantic_root.mkdir()
        _write_source_projection_fixture(cls.semantic_root)
        environment = os.environ.copy()
        environment["MPLCONFIGDIR"] = str(Path(cls._temporary.name) / "matplotlib")
        completed = subprocess.run(
            [
                str(PINNED_MODEL_PYTHON),
                str(V4_ENTRYPOINT),
                "--source-preserve-and-assemble-only",
                str(cls.semantic_root),
            ],
            cwd=PINNED_MODEL_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr.decode("utf-8", errors="replace"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()
        super().tearDownClass()

    def test_source_refill_requires_source_alpha_above_noise_floor(self) -> None:
        from PIL import Image

        with Image.open(self.semantic_root / "mouth.png", formats=("PNG",)) as image:
            mouth = image.convert("RGBA")
            self.assertEqual((10, 20, 30, 255), mouth.getpixel((8, 4)))
            self.assertEqual((1, 1, 100, 255), mouth.getpixel((12, 4)))
            self.assertEqual((1, 1, 100, 255), mouth.getpixel((16, 4)))
            self.assertEqual((10, 20, 30, 255), mouth.getpixel((20, 4)))
        with Image.open(self.semantic_root / "nose.png", formats=("PNG",)) as image:
            nose = image.convert("RGBA")
            self.assertEqual((10, 20, 30, 255), nose.getpixel((4, 4)))
            self.assertEqual((1, 100, 1, 255), nose.getpixel((8, 4)))

    def test_psd_readback_preserves_only_depth_selected_source_visible_rgb(self) -> None:
        layers = _decode_psd_layers(self.semantic_root.parent / "input.psd")
        self.assertTrue({"face", "nose", "mouth"}.issubset(layers))
        self.assertEqual((10, 20, 30, 255), _decoded_psd_pixel(layers["face"], 0, 4))
        self.assertEqual((100, 1, 1, 255), _decoded_psd_pixel(layers["face"], 4, 4))
        self.assertEqual((10, 20, 30, 255), _decoded_psd_pixel(layers["nose"], 4, 4))
        self.assertEqual((1, 100, 1, 255), _decoded_psd_pixel(layers["nose"], 8, 4))
        self.assertEqual((10, 20, 30, 255), _decoded_psd_pixel(layers["mouth"], 8, 4))
        self.assertEqual((1, 1, 100, 255), _decoded_psd_pixel(layers["mouth"], 12, 4))
        self.assertEqual((1, 1, 100, 255), _decoded_psd_pixel(layers["mouth"], 16, 4))
        self.assertEqual((10, 20, 30, 255), _decoded_psd_pixel(layers["mouth"], 20, 4))


if __name__ == "__main__":
    unittest.main()
