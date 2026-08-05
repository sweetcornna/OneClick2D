from __future__ import annotations

import ast
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
    LEGACY_SOURCE_PRESERVE_V5_ENTRYPOINT_SHA256,
    LEGACY_SOURCE_PRESERVE_V5_NF4_MARIGOLD_DEVICE_POLICY_ID,
    LEGACY_SOURCE_PRESERVE_V5_PROFILE_ID,
    LEGACY_SOURCE_PRESERVE_V5_PROFILE_SHA256,
    LEGACY_SOURCE_PRESERVE_V4_ENTRYPOINT_SHA256,
    LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID,
    LEGACY_SOURCE_PRESERVE_V4_PROFILE_SHA256,
    LEGACY_V4_NF4_MARIGOLD_DEVICE_POLICY_ID,
    LEGACY_V4_PSD_PIXEL_PROJECTION_ALGORITHM_ID,
    MAX_MODEL_ARTIFACT_MANIFEST_DEPTH,
    MAX_MODEL_ARTIFACT_MANIFEST_DIRECTORIES,
    MAX_MODEL_ARTIFACT_MANIFEST_ENTRIES,
    MAX_MODEL_ARTIFACT_MANIFEST_NODES,
    MAX_MODEL_ARTIFACT_RELATIVE_PATH_BYTES,
    MAX_MODEL_RESULT_BYTES,
    MODEL_ARTIFACT_MANIFEST_HASH_CHUNK_BYTES,
    MODEL_PART_NAMES,
    MODEL_SEMANTIC_NAMES,
    NF4_MARIGOLD_DEVICE_POLICY_ID,
    PROFILE_ID,
    PSD_PIXEL_PROJECTION_ALGORITHM_ID,
    _artifact_manifest,
    _bounded_artifact_digest,
    _consume_entrypoint_attestation,
    _artifact_manifest_digest,
    _inventory,
    _invoke_model,
    _invoke_native,
    _invoke_wsl,
    _legacy_v4_entrypoint_attestation_dict,
    _load_profile,
    _native_path,
    _run_checked,
    _runtime,
    _validated_archived_entrypoint,
    _validated_entrypoint,
    _validated_postprocess,
    _verify_runtime,
    _verify_native_models,
    _verify_native_scheduler_cache,
    _verify_wsl_models,
    _wsl_path,
    run_model_worker,
)
from spikes.gate_f_runner.model_entrypoints.nf4_marigold_device_policy import Nf4MarigoldOffloadAdapter
from spikes.gate_f_runner.runtime import read_bounded_file


PINNED_MODEL_ROOT = Path.home() / "oneclick2d-model-spikes" / "see-through"
PINNED_MODEL_PYTHON = PINNED_MODEL_ROOT / ".venv" / "bin" / "python"
V4_ENTRYPOINT = ENTRYPOINT_ROOT / "see_through_v3_nf4_source_preserve_v4.py"
V5_ENTRYPOINT = ENTRYPOINT_ROOT / "see_through_v3_nf4_source_preserve_v5.py"
V6_ENTRYPOINT = ENTRYPOINT_ROOT / "see_through_v3_nf4_source_preserve_v6.py"
V4_PROFILE = (
    Path(__file__).parents[1]
    / "spikes"
    / "gate_f_runner"
    / "model_profiles"
    / "see-through-v3-nf4.source-preserve-v4.json"
)
V5_PROFILE = (
    Path(__file__).parents[1]
    / "spikes"
    / "gate_f_runner"
    / "model_profiles"
    / "see-through-v3-nf4.source-preserve-v5.json"
)
ATTESTATION_CHALLENGE = "ab" * 32
ATTESTATION_PSD_BYTES = b"attested-psd"


def _wsl_profile() -> dict[str, object]:
    profile, _ = _load_profile()
    runtime = dict(profile["runtime"])
    runtime.pop("isolation_notice")
    runtime.update(
        {
            "kind": "wsl2",
            "isolation": "wsl2-vm",
            "distribution": "Ubuntu",
        }
    )
    return {**profile, "runtime": runtime}


def _v5_entrypoint_control_namespace() -> dict[str, object]:
    tree = ast.parse(V5_ENTRYPOINT.read_text(encoding="utf-8"), filename=str(V5_ENTRYPOINT))
    selected: list[ast.stmt] = []
    function_names = {
        "_write_entrypoint_attestation",
        "_source_preserving_further_extr",
        "main",
    }
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if any(isinstance(target, ast.Name) and target.id == "_PSD_PROJECTION_EXECUTED" for target in targets):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in function_names:
            selected.append(node)
    namespace: dict[str, object] = {"Path": Path}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(V5_ENTRYPOINT), "exec"), namespace)
    return namespace


def _v5_entrypoint_manifest_namespace() -> dict[str, object]:
    tree = ast.parse(V5_ENTRYPOINT.read_text(encoding="utf-8"), filename=str(V5_ENTRYPOINT))
    selected: list[ast.stmt] = []
    constant_names = {
        "MAX_ARTIFACT_MANIFEST_BYTES",
        "MAX_ARTIFACT_MANIFEST_ENTRIES",
        "MAX_ARTIFACT_MANIFEST_DIRECTORIES",
        "MAX_ARTIFACT_MANIFEST_NODES",
        "MAX_ARTIFACT_MANIFEST_DEPTH",
        "MAX_ARTIFACT_RELATIVE_PATH_BYTES",
    }
    function_names = {"_sha256_file", "_bounded_artifact_files", "_artifact_manifest"}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if any(isinstance(target, ast.Name) and target.id in constant_names for target in targets):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in function_names:
            selected.append(node)
    namespace: dict[str, object] = {"Path": Path, "os": os, "hashlib": hashlib}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(V5_ENTRYPOINT), "exec"), namespace)
    return namespace


def _minimal_psd() -> bytes:
    return base64.b64decode(
        "OEJQUwABAAAAAAAAAAQAAAACAAAAAgAIAAMAAAAAAAAAXjhCSU0EIQAAAAAAUQAAAAEBAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAAQAAAAFgAAABWAACAAAAAAAAAAAAAAABAAAAAQAF//8AAAAGAAAAAAAGAAEAAAAGAAIAAAAG//4AAAAGOEJJTW5vcm3/AAgAAAAATAAAABQAAAAAAAAAAAAAAAEAAAABAAAAAAAAACgAAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//BGZhY2UAAAAAAAABAAAAAQAAAAIAAAACAAX//wAAAAYAAAAAAAYAAQAAAAYAAgAAAAb//gAAAAY4QklNbm9ybf8ACAAAAABMAAAAFAAAAAEAAAABAAAAAgAAAAIAAAAAAAAAKAAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8FbW91dGgAAAABAAIA/wABAAIA/wABAAIAAAABAAIAAAABAAIA/wABAAIA/wABAAIAAAABAAIAAAABAAIAAAABAAIA/wAAAAAAAAAA/wAAAAAAAAAAAAAA/////w=="
    )


def _u32(value: int) -> bytes:
    return struct.pack(">I", value)


def _git_blob_sha1(exact: bytes) -> str:
    header = f"blob {len(exact)}\0".encode("ascii")
    return hashlib.sha1(header + exact, usedforsecurity=False).hexdigest()


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


def _valid_entrypoint_attestation(
    *,
    challenge: str = ATTESTATION_CHALLENGE,
    source_sha256: str | None = None,
    artifact_manifest: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if source_sha256 is None:
        source_sha256 = hashlib.sha256(b"source").hexdigest()
    if artifact_manifest is None:
        artifact_manifest = [
            {
                "path": "result.psd",
                "sha256": hashlib.sha256(ATTESTATION_PSD_BYTES).hexdigest(),
                "byte_length": len(ATTESTATION_PSD_BYTES),
            }
        ]
    return {
        "format": "oneclick2d.model-entrypoint-attestation",
        "format_version": "0.1.0",
        "policy_id": NF4_MARIGOLD_DEVICE_POLICY_ID,
        "requested_cpu_offload": True,
        "execution_device": "cuda:0",
        "components": {
            "vae": {
                "storage_devices": ["meta"],
                "execution_hook_devices": [None, "cuda:0"],
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
        "binding": {
            "challenge": challenge,
            "source_sha256": source_sha256,
            "artifact_manifest_digest": _artifact_manifest_digest(artifact_manifest),
            "artifact_manifest": artifact_manifest,
        },
    }


def _valid_entrypoint_attestation_summary(
    source_sha256: str | None = None,
    artifact_manifest_digest: str | None = None,
) -> dict[str, object]:
    value = _valid_entrypoint_attestation(source_sha256=source_sha256)
    value.pop("format")
    value.pop("format_version")
    binding = value["binding"]
    if artifact_manifest_digest is not None:
        binding["artifact_manifest_digest"] = artifact_manifest_digest
    binding.pop("challenge")
    binding.pop("artifact_manifest")
    return value


def _published_entrypoint_attestation_summary(
    output: Path,
    source: Path,
) -> dict[str, object]:
    artifact_root = output / "input"
    manifest = _artifact_manifest(
        artifact_root,
        artifact_root / ".entrypoint-attestation.json",
    )
    return _valid_entrypoint_attestation_summary(
        hashlib.sha256(source.read_bytes()).hexdigest(),
        _artifact_manifest_digest(manifest),
    )


def _write_entrypoint_attestation_fixture(
    root: Path,
    *,
    challenge: str = ATTESTATION_CHALLENGE,
) -> tuple[Path, Path, dict[str, object]]:
    source = root / "source.png"
    source.write_bytes(b"source")
    artifact_root = root / "artifacts"
    artifact_root.mkdir()
    (artifact_root / "result.psd").write_bytes(ATTESTATION_PSD_BYTES)
    attestation = _valid_entrypoint_attestation(challenge=challenge)
    path = artifact_root / ".entrypoint-attestation.json"
    path.write_text(json.dumps(attestation), encoding="utf-8")
    return path, source, attestation


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
    def test_v5_entrypoint_attests_only_after_upstream_cleanup_returns(self) -> None:
        namespace = _v5_entrypoint_control_namespace()
        events: list[str] = []
        namespace["install_patches"] = lambda: events.append("install")
        namespace["UPSTREAM_SCRIPT"] = Path("upstream.py")
        fake_sys = mock.Mock(argv=["entrypoint.py", "--save_dir", "output"])
        namespace["sys"] = fake_sys
        namespace["os"] = mock.Mock(
            environ={"ONECLICK2D_ATTESTATION_SOURCE": "/mnt/d/private-source.png"}
        )
        runpy_module = mock.Mock()

        def run_path(*args, **kwargs):
            del args, kwargs
            events.append("upstream-returned")
            namespace["_PSD_PROJECTION_EXECUTED"] = True

        runpy_module.run_path.side_effect = run_path
        namespace["runpy"] = runpy_module
        namespace["_write_entrypoint_attestation"] = lambda: events.append("attested")

        namespace["main"]()

        self.assertEqual(["install", "upstream-returned", "attested"], events)
        self.assertEqual(
            [
                "upstream.py",
                "--srcp",
                "/mnt/d/private-source.png",
                "--save_dir",
                "output",
            ],
            fake_sys.argv,
        )

    def test_v5_entrypoint_attests_after_successful_system_exit(self) -> None:
        for exit_code in (0, None):
            with self.subTest(exit_code=exit_code):
                namespace = _v5_entrypoint_control_namespace()
                namespace["install_patches"] = mock.Mock()
                namespace["UPSTREAM_SCRIPT"] = Path("upstream.py")
                namespace["sys"] = mock.Mock(argv=["entrypoint.py"])
                namespace["os"] = mock.Mock(
                    environ={"ONECLICK2D_ATTESTATION_SOURCE": "/mnt/d/private-source.png"}
                )
                writer = mock.Mock()
                namespace["_write_entrypoint_attestation"] = writer
                runpy_module = mock.Mock()

                def successful_exit(*args, **kwargs):
                    del args, kwargs
                    namespace["_PSD_PROJECTION_EXECUTED"] = True
                    raise SystemExit(exit_code)

                runpy_module.run_path.side_effect = successful_exit
                namespace["runpy"] = runpy_module

                with self.assertRaises(SystemExit) as raised:
                    namespace["main"]()
                self.assertEqual(exit_code, raised.exception.code)
                writer.assert_called_once_with()

    def test_v5_entrypoint_does_not_attest_after_nonzero_system_exit(self) -> None:
        namespace = _v5_entrypoint_control_namespace()
        namespace["install_patches"] = mock.Mock()
        namespace["UPSTREAM_SCRIPT"] = Path("upstream.py")
        namespace["sys"] = mock.Mock(argv=["entrypoint.py"])
        namespace["os"] = mock.Mock(
            environ={"ONECLICK2D_ATTESTATION_SOURCE": "/mnt/d/private-source.png"}
        )
        writer = mock.Mock()
        namespace["_write_entrypoint_attestation"] = writer
        runpy_module = mock.Mock()

        def failed_exit(*args, **kwargs):
            del args, kwargs
            namespace["_PSD_PROJECTION_EXECUTED"] = True
            raise SystemExit(7)

        runpy_module.run_path.side_effect = failed_exit
        namespace["runpy"] = runpy_module

        with self.assertRaises(SystemExit) as raised:
            namespace["main"]()
        self.assertEqual(7, raised.exception.code)
        writer.assert_not_called()

    def test_v5_entrypoint_refuses_attestation_without_psd_projection(self) -> None:
        namespace = _v5_entrypoint_control_namespace()
        namespace["_PSD_PROJECTION_EXECUTED"] = False
        with self.assertRaisesRegex(RuntimeError, "projection did not complete"):
            namespace["_write_entrypoint_attestation"]()

    def test_v5_further_extraction_marks_projection_without_early_attestation(self) -> None:
        namespace = _v5_entrypoint_control_namespace()
        events: list[str] = []
        namespace["_source_preserve_visible_pixels"] = lambda path: events.append("source")
        namespace["_ORIGINAL_FURTHER_EXTR"] = lambda *args, **kwargs: events.append("upstream") or "ok"
        namespace["_project_postprocessed_pixels_into_psd"] = lambda path: events.append("projection")
        namespace["_MARIGOLD_ADAPTER"] = object()
        namespace["_write_entrypoint_attestation"] = mock.Mock()

        result = namespace["_source_preserving_further_extr"](
            "artifacts",
            rotate=False,
            save_to_psd=True,
            tblr_split=False,
        )

        self.assertEqual("ok", result)
        self.assertEqual(["source", "upstream", "projection"], events)
        self.assertTrue(namespace["_PSD_PROJECTION_EXECUTED"])
        namespace["_write_entrypoint_attestation"].assert_not_called()

    def test_profiles_have_pinned_safe_weight_artifacts(self) -> None:
        profile, exact = _load_profile()
        self.assertEqual(PROFILE_ID, profile["profile_id"])
        self.assertEqual("native-linux", profile["runtime"]["kind"])
        self.assertEqual("none-host-local", profile["runtime"]["isolation"])
        self.assertEqual("无隔离边界、仅限本机", profile["runtime"]["isolation_notice"])
        self.assertNotIn("distribution", profile["runtime"])
        self.assertEqual(["common"], profile["runtime"]["python_path_entries"])
        self.assertEqual("2.0.11", profile["runtime"]["versions"]["pycocotools"])
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
        self.assertIn(b"pycocotools==2.0.11", dependencies_path.read_bytes().splitlines())
        scheduler = next(model for model in profile["models"] if model["role"] == "scheduler_configuration")
        self.assertEqual(
            {
                "kind": "huggingface-hub-cache",
                "repo_id": "frankjoshua/juggernautXL_version6Rundiffusion",
                "revision": "main",
                "subfolder": "scheduler",
                "hf_home_relative_to_code_root": "models/hf-cache",
                "cache_repository": "models--frankjoshua--juggernautXL_version6Rundiffusion",
                "required_ref": "refs/main",
                "resolved_commit": "aadab4c7cb252b83a0e2d6f3386b8c837af23932",
            },
            scheduler["runtime_resolution"],
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

    def test_v4_archive_and_entrypoint_have_verifiable_preimages(self) -> None:
        archived = V4_PROFILE.read_bytes()
        profile = json.loads(archived)
        self.assertEqual(LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID, profile["profile_id"])
        self.assertEqual(
            LEGACY_SOURCE_PRESERVE_V4_PROFILE_SHA256,
            hashlib.sha256(archived).hexdigest(),
        )
        self.assertEqual(
            LEGACY_SOURCE_PRESERVE_V4_ENTRYPOINT_SHA256,
            hashlib.sha256(V4_ENTRYPOINT.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            LEGACY_SOURCE_PRESERVE_V4_ENTRYPOINT_SHA256,
            profile["entrypoint"]["sha256"],
        )

    def test_v5_archive_and_entrypoint_have_verifiable_preimages(self) -> None:
        archived = V5_PROFILE.read_bytes()
        profile = json.loads(archived)
        self.assertEqual(LEGACY_SOURCE_PRESERVE_V5_PROFILE_ID, profile["profile_id"])
        self.assertEqual(
            LEGACY_SOURCE_PRESERVE_V5_PROFILE_SHA256,
            hashlib.sha256(archived).hexdigest(),
        )
        self.assertEqual(
            LEGACY_SOURCE_PRESERVE_V5_ENTRYPOINT_SHA256,
            hashlib.sha256(V5_ENTRYPOINT.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            LEGACY_SOURCE_PRESERVE_V5_ENTRYPOINT_SHA256,
            profile["entrypoint"]["sha256"],
        )
        self.assertEqual(
            LEGACY_SOURCE_PRESERVE_V5_NF4_MARIGOLD_DEVICE_POLICY_ID,
            LEGACY_V4_NF4_MARIGOLD_DEVICE_POLICY_ID,
        )

    def test_profile_attests_device_policy_digest(self) -> None:
        profile, _ = _load_profile()
        device_policy = profile["entrypoint"]["device_policy"]
        self.assertEqual(DEVICE_POLICY_PATH.name, device_policy["path"])
        self.assertEqual(NF4_MARIGOLD_DEVICE_POLICY_ID, device_policy["policy_id"])
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

    def test_inventory_matches_hashlib_for_multi_chunk_files(self) -> None:
        payload = bytes(range(241)) * 20 * 1024  # spans multiple read chunks
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layer.png").write_bytes(payload)
            files = _inventory(root)
        self.assertEqual(
            [
                {
                    "uri": "layer.png",
                    "byte_length": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
            files,
        )

    def test_artifact_manifest_rejects_entry_count_over_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            for index in range(MAX_MODEL_ARTIFACT_MANIFEST_ENTRIES):
                (root / f"artifact-{index:03d}.bin").write_bytes(b"")
            with self.assertRaisesRegex(StageContractError, "entry count exceeded"):
                _artifact_manifest(root, root / ".entrypoint-attestation.json")

    def test_artifact_manifest_rejects_cumulative_bytes_before_reading_excess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"p")
            excess = root / "999-excess.bin"
            with excess.open("wb") as stream:
                stream.truncate(MAX_MODEL_RESULT_BYTES)
            real_digest = _bounded_artifact_digest

            def guarded_digest(path: Path, maximum: int) -> tuple[str, int]:
                if path == excess:
                    self.fail("cumulative bound must reject the excess file before reading it")
                return real_digest(path, maximum)

            with mock.patch(
                "spikes.gate_f_runner.model_worker._bounded_artifact_digest",
                side_effect=guarded_digest,
            ):
                with self.assertRaisesRegex(StageContractError, "byte count exceeded"):
                    _artifact_manifest(root, root / ".entrypoint-attestation.json")

    def _assert_manifest_traversal_rejects(self, root: Path, pattern: str) -> None:
        with mock.patch(
            "spikes.gate_f_runner.model_worker._bounded_artifact_digest",
            side_effect=lambda *args, **kwargs: self.fail(
                "traversal bounds must reject before any file is read"
            ),
        ):
            with self.assertRaisesRegex(StageContractError, pattern):
                _artifact_manifest(root, root / ".entrypoint-attestation.json")

    def test_artifact_manifest_rejects_depth_over_bound_during_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            nested = root
            for level in range(MAX_MODEL_ARTIFACT_MANIFEST_DEPTH + 1):
                nested = nested / f"level-{level}"
            nested.mkdir(parents=True)
            self._assert_manifest_traversal_rejects(root, "depth exceeded")

    def test_artifact_manifest_rejects_directory_count_over_bound_during_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            for index in range(MAX_MODEL_ARTIFACT_MANIFEST_DIRECTORIES + 1):
                (root / f"group-{index:03d}").mkdir()
            self._assert_manifest_traversal_rejects(root, "directory count exceeded")

    def test_artifact_manifest_rejects_relative_path_over_bound_during_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            inner = root / ("a" * 200) / ("b" * 200)
            inner.mkdir(parents=True)
            (inner / ("c" * 200)).write_bytes(b"")
            self._assert_manifest_traversal_rejects(root, "path length exceeded")

    def test_artifact_manifest_rejects_node_count_over_bound_during_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            (root / "001-extra.bin").write_bytes(b"")
            (root / "002-extra.bin").write_bytes(b"")
            with mock.patch("spikes.gate_f_runner.model_worker.MAX_MODEL_ARTIFACT_MANIFEST_NODES", 2):
                self._assert_manifest_traversal_rejects(root, "node count exceeded")

    def test_artifact_manifest_excludes_attestation_from_traversal_node_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            (root / "001-extra.bin").write_bytes(b"extra")
            attestation = root / ".entrypoint-attestation.json"
            attestation.write_bytes(b"{}")
            with mock.patch("spikes.gate_f_runner.model_worker.MAX_MODEL_ARTIFACT_MANIFEST_NODES", 2):
                manifest = _artifact_manifest(root, attestation)
        self.assertEqual(
            ["000-result.psd", "001-extra.bin"],
            [item["path"] for item in manifest],
        )

    def test_bounded_artifact_digest_reads_in_bounded_chunks(self) -> None:
        payload = bytes(range(256)) * 24 * 1024  # ~6 MiB, several chunks
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "artifact.bin"
            target.write_bytes(payload)
            requested: list[int] = []
            real_open = Path.open

            def recording_open(self_path: Path, *args: object, **kwargs: object) -> object:
                stream = real_open(self_path, *args, **kwargs)
                if self_path != target:
                    return stream
                real_read = stream.read

                def recording_read(size: int = -1) -> bytes:
                    requested.append(size)
                    return real_read(size)

                stream.read = recording_read  # type: ignore[method-assign]
                return stream

            with mock.patch.object(Path, "open", recording_open):
                digest, byte_length = _bounded_artifact_digest(target, MAX_MODEL_RESULT_BYTES)

        self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
        self.assertEqual(len(payload), byte_length)
        self.assertTrue(requested, "the digest must stream the artifact")
        self.assertLessEqual(
            max(requested),
            MODEL_ARTIFACT_MANIFEST_HASH_CHUNK_BYTES + 1,
            "peak buffered bytes must stay at the fixed chunk bound, not the artifact size",
        )

    def test_bounded_artifact_digest_rejects_growth_past_its_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "artifact.bin"
            target.write_bytes(b"a" * 4096)
            with self.assertRaisesRegex(StageContractError, "byte count exceeded"):
                _bounded_artifact_digest(target, 1024)

    def test_bounded_artifact_digest_rejects_symlinked_artifact_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact.bin").write_bytes(b"artifact")
            link = root / "linked.bin"
            try:
                link.symlink_to(root / "artifact.bin")
            except OSError:
                self.skipTest("symlinks are unavailable")
            with self.assertRaisesRegex(StageContractError, "non-regular node"):
                _bounded_artifact_digest(link, MAX_MODEL_RESULT_BYTES)

    def test_artifact_manifest_matches_hashlib_for_multi_chunk_artifacts(self) -> None:
        payload = bytes(range(251)) * 20 * 1024  # spans multiple read chunks
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(payload)
            manifest = _artifact_manifest(root, root / ".entrypoint-attestation.json")
        self.assertEqual(
            [
                {
                    "path": "000-result.psd",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byte_length": len(payload),
                }
            ],
            manifest,
        )

    def test_v5_entrypoint_manifest_bounds_match_worker_rules(self) -> None:
        namespace = _v5_entrypoint_manifest_namespace()
        self.assertEqual(MAX_MODEL_RESULT_BYTES, namespace["MAX_ARTIFACT_MANIFEST_BYTES"])
        self.assertEqual(MAX_MODEL_ARTIFACT_MANIFEST_ENTRIES, namespace["MAX_ARTIFACT_MANIFEST_ENTRIES"])
        self.assertEqual(
            MAX_MODEL_ARTIFACT_MANIFEST_DIRECTORIES,
            namespace["MAX_ARTIFACT_MANIFEST_DIRECTORIES"],
        )
        self.assertEqual(MAX_MODEL_ARTIFACT_MANIFEST_NODES, namespace["MAX_ARTIFACT_MANIFEST_NODES"])
        self.assertEqual(MAX_MODEL_ARTIFACT_MANIFEST_DEPTH, namespace["MAX_ARTIFACT_MANIFEST_DEPTH"])
        self.assertEqual(
            MAX_MODEL_ARTIFACT_RELATIVE_PATH_BYTES,
            namespace["MAX_ARTIFACT_RELATIVE_PATH_BYTES"],
        )

    def test_v5_entrypoint_manifest_rejects_traversal_bounds_like_worker(self) -> None:
        bounded = _v5_entrypoint_manifest_namespace()["_bounded_artifact_files"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root
            for level in range(MAX_MODEL_ARTIFACT_MANIFEST_DEPTH + 1):
                nested = nested / f"level-{level}"
            nested.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "depth exceeded"):
                bounded(root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(MAX_MODEL_ARTIFACT_MANIFEST_DIRECTORIES + 1):
                (root / f"group-{index:03d}").mkdir()
            with self.assertRaisesRegex(RuntimeError, "directory count exceeded"):
                bounded(root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inner = root / ("a" * 200) / ("b" * 200)
            inner.mkdir(parents=True)
            (inner / ("c" * 200)).write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "path length exceeded"):
                bounded(root)

    def test_v5_entrypoint_manifest_excludes_attestation_from_node_bound(self) -> None:
        namespace = _v5_entrypoint_manifest_namespace()
        namespace["MAX_ARTIFACT_MANIFEST_NODES"] = 2
        entrypoint_manifest = namespace["_artifact_manifest"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            (root / "001-extra.bin").write_bytes(b"extra")
            attestation = root / ".entrypoint-attestation.json"
            attestation.write_bytes(b"{}")
            manifest = entrypoint_manifest(root, attestation)
        self.assertEqual(
            ["000-result.psd", "001-extra.bin"],
            [item["path"] for item in manifest],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            (root / "001-extra.bin").write_bytes(b"")
            (root / "002-extra.bin").write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "node count exceeded"):
                entrypoint_manifest(root, root / ".entrypoint-attestation.json")

    def test_v5_entrypoint_manifest_enforces_entry_and_byte_bounds(self) -> None:
        namespace = _v5_entrypoint_manifest_namespace()
        entrypoint_manifest = namespace["_artifact_manifest"]
        namespace["MAX_ARTIFACT_MANIFEST_ENTRIES"] = 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            (root / "001-extra.bin").write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "entry count exceeded"):
                entrypoint_manifest(root, root / ".entrypoint-attestation.json")
        namespace["MAX_ARTIFACT_MANIFEST_ENTRIES"] = 256
        namespace["MAX_ARTIFACT_MANIFEST_BYTES"] = 4
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000-result.psd").write_bytes(b"psd")
            (root / "001-extra.bin").write_bytes(b"over")
            with self.assertRaisesRegex(RuntimeError, "byte count exceeded"):
                entrypoint_manifest(root, root / ".entrypoint-attestation.json")

    def test_legacy_v4_verification_is_independent_of_active_policy_identity(self) -> None:
        profile = json.loads(V4_PROFILE.read_bytes())
        summary = _valid_entrypoint_attestation_summary()
        summary.pop("binding")
        summary["policy_id"] = LEGACY_V4_NF4_MARIGOLD_DEVICE_POLICY_ID
        summary["components"]["vae"]["execution_hook_devices"] = ["cuda:0"]
        with mock.patch.multiple(
            "spikes.gate_f_runner.model_worker",
            DEVICE_POLICY_PATH=ENTRYPOINT_ROOT / "renamed-active-policy.py",
            NF4_MARIGOLD_DEVICE_POLICY_ID="see-through.v6.renamed-policy.v1",
            PSD_PIXEL_PROJECTION_ALGORITHM_ID="renamed-algorithm.v1",
        ):
            self.assertEqual(V4_ENTRYPOINT, _validated_archived_entrypoint(profile))
            attestation = _legacy_v4_entrypoint_attestation_dict(summary)
        self.assertEqual(LEGACY_V4_NF4_MARIGOLD_DEVICE_POLICY_ID, attestation["policy_id"])
        self.assertEqual(
            LEGACY_V4_PSD_PIXEL_PROJECTION_ALGORITHM_ID,
            attestation["psd_pixel_projection_algorithm_id"],
        )

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
            "python_path_entries_effective": True,
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
        with mock.patch.dict(os.environ, {"WSLENV": "SHOULD_NOT_PASS", "PYTHONPATH": "untrusted"}), mock.patch(
            "spikes.gate_f_runner.model_worker._run_checked",
            return_value=completed,
        ) as run:
            _verify_runtime(profile, runtime["dependencies_sha256"])
        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertNotIn("wsl.exe", command)
        self.assertEqual(PINNED_MODEL_ROOT, kwargs["cwd"])
        self.assertNotIn("WSLENV", kwargs["env"])
        self.assertNotIn("PYTHONPATH", kwargs["env"])
        self.assertEqual(
            json.dumps([str(PINNED_MODEL_ROOT / "common")], separators=(",", ":")),
            kwargs["env"]["ONECLICK2D_PYTHON_PATH_ENTRIES"],
        )
        actual["cuda_available"] = False
        completed.stdout = json.dumps(actual, separators=(",", ":")).encode("utf-8")
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", return_value=completed):
            with self.assertRaisesRegex(StageContractError, "CUDA runtime is unavailable"):
                _verify_runtime(profile, runtime["dependencies_sha256"])

    def test_runtime_attestation_binds_dependency_profile(self) -> None:
        profile, _ = _load_profile()
        with self.assertRaisesRegex(StageContractError, "dependency profile mismatch"):
            _verify_runtime(profile, "0" * 64)

    def test_runtime_profile_rejects_missing_and_extra_kind_specific_fields(self) -> None:
        profile, _ = _load_profile()
        for changed_runtime in (
            {key: value for key, value in profile["runtime"].items() if key != "isolation"},
            {**profile["runtime"], "distribution": "Ubuntu"},
            {**profile["runtime"], "isolation": "wsl2-vm"},
        ):
            with self.subTest(keys=set(changed_runtime), isolation=changed_runtime.get("isolation")):
                with self.assertRaisesRegex(StageContractError, "runtime identity"):
                    _runtime({**profile, "runtime": changed_runtime})

        wsl_profile = _wsl_profile()
        self.assertEqual("wsl2", _runtime(wsl_profile)["kind"])
        for key in ("kind", "isolation", "distribution"):
            changed_runtime = dict(wsl_profile["runtime"])
            changed_runtime.pop(key)
            with self.subTest(wsl_missing=key):
                with self.assertRaisesRegex(StageContractError, "runtime"):
                    _runtime({**wsl_profile, "runtime": changed_runtime})

    def test_entrypoint_attestation_records_effective_offload_devices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, source, _ = _write_entrypoint_attestation_fixture(Path(directory))
            summary = _consume_entrypoint_attestation(
                path,
                expected_challenge=ATTESTATION_CHALLENGE,
                source=source,
            )
            self.assertFalse(path.exists())
            self.assertEqual(NF4_MARIGOLD_DEVICE_POLICY_ID, summary["policy_id"])
            self.assertEqual("cuda:0", summary["execution_device"])
            self.assertEqual(
                "sequential-cpu-offload",
                summary["components"]["vae"]["disposition"],
            )
            self.assertTrue(summary["components"]["vae"]["upstream_cuda_move_suppressed"])
            self.assertEqual(
                PSD_PIXEL_PROJECTION_ALGORITHM_ID,
                summary["psd_pixel_projection_algorithm_id"],
            )
            self.assertEqual(hashlib.sha256(b"source").hexdigest(), summary["binding"]["source_sha256"])
            self.assertNotIn("challenge", summary["binding"])
            self.assertNotIn("artifact_manifest", summary["binding"])
            with self.assertRaises(TypeError):
                summary["policy_id"] = "changed"
            with self.assertRaises(TypeError):
                summary["components"]["vae"]["disposition"] = "changed"
            with self.assertRaises(TypeError):
                summary["binding"]["source_sha256"] = "changed"

    def test_entrypoint_attestation_rejects_missing_required_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, source, attestation = _write_entrypoint_attestation_fixture(Path(directory))
            del attestation["psd_pixel_projection_algorithm_id"]
            path.write_text(json.dumps(attestation), encoding="utf-8")
            with self.assertRaisesRegex(StageContractError, "entrypoint attestation is invalid"):
                _consume_entrypoint_attestation(
                    path,
                    expected_challenge=ATTESTATION_CHALLENGE,
                    source=source,
                )
            self.assertFalse(path.exists())

    def test_entrypoint_attestation_rejects_all_cuda_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, source, attestation = _write_entrypoint_attestation_fixture(Path(directory))
            attestation["components"]["vae"]["storage_devices"] = ["cuda:0"]
            attestation["components"]["vae"]["execution_hook_devices"] = []
            path.write_text(json.dumps(attestation), encoding="utf-8")
            with self.assertRaisesRegex(StageContractError, "device attestation"):
                _consume_entrypoint_attestation(
                    path,
                    expected_challenge=ATTESTATION_CHALLENGE,
                    source=source,
                )
            self.assertFalse(path.exists())

    def test_entrypoint_attestation_rejects_component_devices_not_equal_to_execution_device(self) -> None:
        cases = (
            ("vae", "execution_hook_devices", [None]),
            ("vae", "execution_hook_devices", [None, "cpu"]),
            ("vae", "execution_hook_devices", ["cuda"]),
            ("vae", "execution_hook_devices", ["cuda:1"]),
            ("vae", "execution_hook_devices", ["cuda:0", "cuda:1"]),
            ("unet", "storage_devices", ["cuda"]),
            ("unet", "storage_devices", ["cuda:1"]),
            ("unet", "storage_devices", ["cuda:0", "cuda:1"]),
        )
        for component, field, devices in cases:
            with self.subTest(component=component, field=field, devices=devices):
                with tempfile.TemporaryDirectory() as directory:
                    path, source, attestation = _write_entrypoint_attestation_fixture(Path(directory))
                    attestation["components"][component][field] = devices
                    path.write_text(json.dumps(attestation), encoding="utf-8")
                    with self.assertRaisesRegex(StageContractError, "device attestation"):
                        _consume_entrypoint_attestation(
                            path,
                            expected_challenge=ATTESTATION_CHALLENGE,
                            source=source,
                        )
                    self.assertFalse(path.exists())

    def test_entrypoint_attestation_rejects_challenge_mismatch_without_echoing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, source, _ = _write_entrypoint_attestation_fixture(Path(directory))
            with self.assertRaises(StageContractError) as raised:
                _consume_entrypoint_attestation(
                    path,
                    expected_challenge="cd" * 32,
                    source=source,
                )
            self.assertEqual("model entrypoint attestation challenge mismatch", str(raised.exception))
            self.assertNotIn(ATTESTATION_CHALLENGE, str(raised.exception))
            self.assertFalse(path.exists())

    def test_entrypoint_attestation_rejects_source_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, source, _ = _write_entrypoint_attestation_fixture(Path(directory))
            source.write_bytes(b"changed source")
            with self.assertRaisesRegex(StageContractError, "source digest mismatch"):
                _consume_entrypoint_attestation(
                    path,
                    expected_challenge=ATTESTATION_CHALLENGE,
                    source=source,
                )
            self.assertFalse(path.exists())

    def test_entrypoint_attestation_rejects_post_attestation_artifact_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, source, _ = _write_entrypoint_attestation_fixture(Path(directory))
            (path.parent / "late-write.bin").write_bytes(b"written after further_extr returned")
            with self.assertRaisesRegex(
                StageContractError,
                "artifact manifest changed after entrypoint attestation",
            ):
                _consume_entrypoint_attestation(
                    path,
                    expected_challenge=ATTESTATION_CHALLENGE,
                    source=source,
                )
            self.assertFalse(path.exists())

    def test_entrypoint_attestation_rejects_artifact_manifest_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, source, attestation = _write_entrypoint_attestation_fixture(Path(directory))
            attestation["binding"]["artifact_manifest_digest"] = "0" * 64
            path.write_text(json.dumps(attestation), encoding="utf-8")
            with self.assertRaisesRegex(StageContractError, "artifact manifest digest mismatch"):
                _consume_entrypoint_attestation(
                    path,
                    expected_challenge=ATTESTATION_CHALLENGE,
                    source=source,
                )
            self.assertFalse(path.exists())

    def test_entrypoint_attestation_rejects_open_binding_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, source, attestation = _write_entrypoint_attestation_fixture(Path(directory))
            attestation["binding"]["unexpected"] = True
            path.write_text(json.dumps(attestation), encoding="utf-8")
            with self.assertRaisesRegex(StageContractError, "attestation binding is invalid"):
                _consume_entrypoint_attestation(
                    path,
                    expected_challenge=ATTESTATION_CHALLENGE,
                    source=source,
                )
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
        profile = _wsl_profile()
        completed = [
            mock.Mock(returncode=0, stdout=(profile["code"]["commit"] + "\n").encode("ascii"), stderr=b""),
            mock.Mock(returncode=0, stdout=b" M inference/scripts/inference_psd_quantized.py\n", stderr=b""),
        ]
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", side_effect=completed):
            with self.assertRaisesRegex(StageContractError, "tracked changes"):
                _verify_wsl_models(profile)

    def test_wsl_model_verification_binds_executed_upstream_entrypoint_to_commit(self) -> None:
        profile = _wsl_profile()
        completed = [
            mock.Mock(returncode=0, stdout=(profile["code"]["commit"] + "\n").encode("ascii"), stderr=b""),
            mock.Mock(returncode=0, stdout=b"", stderr=b""),
            mock.Mock(returncode=0, stdout=("a" * 40 + "\n").encode("ascii"), stderr=b""),
            mock.Mock(returncode=0, stdout=("b" * 40 + "\n").encode("ascii"), stderr=b""),
        ]
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", side_effect=completed):
            with self.assertRaisesRegex(StageContractError, "does not match the pinned commit"):
                _verify_wsl_models(profile)

    def test_wsl_model_verification_preserves_commands_and_checks_scheduler_cache(self) -> None:
        profile = _wsl_profile()
        runtime = profile["runtime"]
        prefix = [
            "wsl.exe",
            "-d",
            runtime["distribution"],
            "--cd",
            f"~/{runtime['code_root_relative_to_home']}",
            "--",
        ]
        config_digests: dict[str, str] = {}
        weight_digests: dict[str, str] = {}
        weight_lengths: dict[str, int] = {}
        for model in profile["models"]:
            local_dir = model["local_dir_relative_to_code_root"]
            for descriptor in model["config_files"]:
                config_digests[f"{local_dir}/{descriptor['path']}"] = descriptor["git_blob_sha1"]
            for descriptor in model["weights"]:
                path = f"{local_dir}/{descriptor['path']}"
                weight_digests[path] = descriptor["sha256"]
                weight_lengths[path] = descriptor["byte_length"]
        calls: list[list[str]] = []

        def completed(command, **kwargs):
            del kwargs
            calls.append(command)
            self.assertEqual(prefix, command[: len(prefix)])
            tail = command[len(prefix) :]
            if tail == ["git", "rev-parse", "HEAD"]:
                stdout = (profile["code"]["commit"] + "\n").encode("ascii")
            elif tail == ["git", "status", "--porcelain=v1", "--untracked-files=no"]:
                stdout = b""
            elif tail[:2] == ["git", "rev-parse"]:
                stdout = ("a" * 40 + "\n").encode("ascii")
            elif tail[:2] == ["git", "hash-object"]:
                path = tail[-1]
                if path == profile["entrypoint"]["upstream_script"]:
                    digest = "a" * 40
                elif path in config_digests:
                    digest = config_digests[path]
                elif "/snapshots/" in path:
                    digest = profile["models"][0]["config_files"][0]["git_blob_sha1"]
                else:
                    self.fail(f"unexpected WSL hash path: {path!r}")
                stdout = (digest + "\n").encode("ascii")
            elif tail[0] == "sha256sum":
                stdout = (weight_digests[tail[1]] + "  " + tail[1] + "\n").encode("ascii")
            elif tail[:3] == ["stat", "-c", "%s"]:
                stdout = (str(weight_lengths[tail[3]]) + "\n").encode("ascii")
            elif tail[0] == "cat":
                stdout = (profile["models"][0]["revision"] + "\n").encode("ascii")
            else:
                self.fail(f"unexpected WSL verification command: {tail!r}")
            return mock.Mock(returncode=0, stdout=stdout, stderr=b"")

        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", side_effect=completed):
            _verify_wsl_models(profile)

        self.assertEqual(prefix + ["git", "rev-parse", "HEAD"], calls[0])
        self.assertEqual(
            prefix + ["git", "status", "--porcelain=v1", "--untracked-files=no"],
            calls[1],
        )
        self.assertTrue(any(command[len(prefix) :][0] == "cat" for command in calls))
        self.assertTrue(
            any("/snapshots/" in command[-1] and "/refs/main" not in command[-1] for command in calls)
        )

    def test_wsl_path_uses_forward_slashes_for_windows_argument(self) -> None:
        completed = mock.Mock(returncode=0, stdout=b"/mnt/d/project/live2d/source.png\n", stderr=b"")
        path = Path("D:/project/live2d/source.png")
        with mock.patch("spikes.gate_f_runner.model_worker._run_checked", return_value=completed) as run:
            self.assertEqual("/mnt/d/project/live2d/source.png", _wsl_path(path, "Ubuntu"))
        command = run.call_args.args[0]
        self.assertEqual(path.resolve().as_posix(), command[-1])
        self.assertNotIn("\\", command[-1])

    def test_native_path_is_identity_and_rejects_symlinks_or_root_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            regular = root / "regular.png"
            regular.write_bytes(b"source")
            self.assertEqual(regular.resolve().as_posix(), _native_path(regular, allowed_root=root))
            outside = root.parent / f"{root.name}-outside.png"
            outside.write_bytes(b"outside")
            try:
                with self.assertRaisesRegex(StageContractError, "outside the allowed root"):
                    _native_path(outside, allowed_root=root)
                symlink = root / "source-link.png"
                try:
                    symlink.symlink_to(regular)
                except (NotImplementedError, OSError):
                    return
                with self.assertRaisesRegex(StageContractError, "symlink"):
                    _native_path(symlink, allowed_root=root)
            finally:
                outside.unlink(missing_ok=True)

    def test_runtime_kind_dispatches_without_crossing_strategies(self) -> None:
        profile, _ = _load_profile()
        completed = (mock.Mock(returncode=1), None)
        with mock.patch(
            "spikes.gate_f_runner.model_worker._invoke_native",
            return_value=completed,
        ) as native, mock.patch("spikes.gate_f_runner.model_worker._invoke_wsl") as wsl:
            self.assertIs(completed, _invoke_model(Path("source"), Path("output"), profile, 30))
        native.assert_called_once()
        wsl.assert_not_called()

        wsl_profile = _wsl_profile()
        with mock.patch("spikes.gate_f_runner.model_worker._invoke_native") as native, mock.patch(
            "spikes.gate_f_runner.model_worker._invoke_wsl",
            return_value=completed,
        ) as wsl:
            self.assertIs(completed, _invoke_model(Path("source"), Path("output"), wsl_profile, 30))
        wsl.assert_called_once()
        native.assert_not_called()

    def test_native_invocation_matches_verified_ground_truth_shape(self) -> None:
        profile, _ = _load_profile()
        calls: list[tuple[list[str], dict[str, object]]] = []

        def completed(command, **kwargs):
            calls.append((command, kwargs))
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            code_root = home / "oneclick2d-model-spikes" / "see-through"
            (code_root / ".venv" / "bin").mkdir(parents=True)
            (code_root / ".venv" / "bin" / "python").write_bytes(b"python")
            (code_root / "common").mkdir()
            (code_root / "models" / "hf-cache").mkdir(parents=True)
            source = home / "source.png"
            source.write_bytes(b"source")
            output = home / "output"
            output.mkdir()
            with mock.patch.object(Path, "home", return_value=home), mock.patch(
                "spikes.gate_f_runner.model_worker._verify_native_models"
            ), mock.patch(
                "spikes.gate_f_runner.model_worker.secrets.token_hex",
                return_value="cd" * 32,
            ), mock.patch(
                "spikes.gate_f_runner.model_worker._consume_entrypoint_attestation"
            ) as consume, mock.patch(
                "spikes.gate_f_runner.model_worker._run_checked",
                side_effect=completed,
            ):
                with mock.patch.dict(os.environ, {"WSLENV": "DO_NOT_FORWARD", "PYTHONPATH": "untrusted"}):
                    _invoke_native(source, output, profile, 30)

            command, kwargs = calls[-1]
            process_env = kwargs["env"]
            self.assertEqual(code_root, kwargs["cwd"])
            self.assertEqual((code_root / ".venv" / "bin" / "python").as_posix(), command[0])
            self.assertEqual(V6_ENTRYPOINT.resolve().as_posix(), command[1])
            self.assertNotIn("wsl.exe", command)
            self.assertNotIn("--srcp", command)
            self.assertNotIn(source.resolve().as_posix(), command)
            self.assertEqual((output / "input").resolve().as_posix(), command[command.index("--save_dir") + 1])
            self.assertEqual("1", process_env["HF_HUB_OFFLINE"])
            self.assertEqual("1", process_env["TRANSFORMERS_OFFLINE"])
            self.assertEqual((code_root / "models" / "hf-cache").as_posix(), process_env["HF_HOME"])
            self.assertEqual("expandable_segments:True", process_env["PYTORCH_CUDA_ALLOC_CONF"])
            self.assertEqual(source.resolve().as_posix(), process_env["ONECLICK2D_ATTESTATION_SOURCE"])
            self.assertEqual("cd" * 32, process_env["ONECLICK2D_ATTESTATION_CHALLENGE"])
            self.assertNotIn("WSLENV", process_env)
            self.assertNotIn("PYTHONPATH", process_env)
            consume.assert_called_once_with(
                output / "input" / ".entrypoint-attestation.json",
                expected_challenge="cd" * 32,
                source=source,
            )

    def test_native_scheduler_cache_requires_main_ref_and_pinned_snapshot(self) -> None:
        profile, _ = _load_profile()
        commit = "a" * 40
        config_bytes = b'{"scheduler":"pinned"}\n'
        digest = _git_blob_sha1(config_bytes)
        models = []
        for model in profile["models"]:
            if model["role"] != "scheduler_configuration":
                models.append(model)
                continue
            resolution = {
                **model["runtime_resolution"],
                "resolved_commit": commit,
            }
            models.append(
                {
                    **model,
                    "revision": commit,
                    "config_files": [
                        {"path": "scheduler/scheduler_config.json", "git_blob_sha1": digest}
                    ],
                    "runtime_resolution": resolution,
                }
            )
        changed_profile = {**profile, "models": models}

        with tempfile.TemporaryDirectory() as directory:
            code_root = Path(directory).resolve()
            cache_root = (
                code_root
                / "models"
                / "hf-cache"
                / "hub"
                / "models--frankjoshua--juggernautXL_version6Rundiffusion"
            )
            (cache_root / "refs").mkdir(parents=True)
            (cache_root / "blobs").mkdir()
            snapshot_directory = cache_root / "snapshots" / commit / "scheduler"
            snapshot_directory.mkdir(parents=True)
            ref = cache_root / "refs" / "main"
            blob = cache_root / "blobs" / digest
            snapshot = snapshot_directory / "scheduler_config.json"
            ref.write_text(commit + "\n", encoding="ascii")
            blob.write_bytes(config_bytes)
            try:
                snapshot.symlink_to(blob)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks are unavailable")

            _verify_native_scheduler_cache(changed_profile, code_root)
            ref.write_text("b" * 40 + "\n", encoding="ascii")
            with self.assertRaisesRegex(StageContractError, "cache ref mismatch"):
                _verify_native_scheduler_cache(changed_profile, code_root)

            ref.write_text(commit + "\n", encoding="ascii")
            snapshot.unlink()
            outside = code_root / "outside.json"
            outside.write_bytes(config_bytes)
            snapshot.symlink_to(outside)
            with self.assertRaisesRegex(StageContractError, "cache snapshot is invalid"):
                _verify_native_scheduler_cache(changed_profile, code_root)

    def test_native_model_verification_rejects_symlinked_model_file(self) -> None:
        profile, _ = _load_profile()
        config_bytes = b"pinned config\n"
        digest = _git_blob_sha1(config_bytes)
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            code_root = home / "oneclick2d-model-spikes" / "see-through"
            upstream = code_root / "inference" / "scripts" / "inference_psd_quantized.py"
            upstream.parent.mkdir(parents=True)
            upstream.write_bytes(b"upstream")
            models = []
            for index, model in enumerate(profile["models"]):
                local_dir = f"models/model-{index}"
                model_root = code_root / local_dir
                model_root.mkdir(parents=True)
                config = model_root / "config.json"
                if index == 1:
                    target = code_root / "shared-config.json"
                    target.write_bytes(config_bytes)
                    try:
                        config.symlink_to(target)
                    except (NotImplementedError, OSError):
                        self.skipTest("symlinks are unavailable")
                else:
                    config.write_bytes(config_bytes)
                models.append(
                    {
                        **model,
                        "local_dir_relative_to_code_root": local_dir,
                        "config_files": [{"path": "config.json", "git_blob_sha1": digest}],
                        "weights": [],
                    }
                )
            changed_profile = {**profile, "models": models}
            completed = [
                mock.Mock(returncode=0, stdout=(profile["code"]["commit"] + "\n").encode("ascii")),
                mock.Mock(returncode=0, stdout=b""),
                mock.Mock(returncode=0, stdout=("a" * 40 + "\n").encode("ascii")),
                mock.Mock(returncode=0, stdout=("a" * 40 + "\n").encode("ascii")),
            ]
            with mock.patch.object(Path, "home", return_value=home), mock.patch(
                "spikes.gate_f_runner.model_worker._run_checked",
                side_effect=completed,
            ):
                with self.assertRaisesRegex(StageContractError, "native path contains a symlink"):
                    _verify_native_models(changed_profile)

    def test_wsl_invocation_forces_offline_pinned_model_paths(self) -> None:
        profile = _wsl_profile()
        calls: list[tuple[list[str], dict[str, str] | None]] = []

        def completed(command, **kwargs):
            calls.append((command, kwargs.get("env")))
            if "wslpath" in command:
                return mock.Mock(returncode=0, stdout=b"/mnt/d/model-spike\n", stderr=b"")
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "spikes.gate_f_runner.model_worker._verify_wsl_models"
        ), mock.patch(
            "spikes.gate_f_runner.model_worker.secrets.token_hex",
            return_value="cd" * 32,
        ) as token_hex, mock.patch(
            "spikes.gate_f_runner.model_worker._consume_entrypoint_attestation"
        ) as consume, mock.patch(
            "spikes.gate_f_runner.model_worker._run_checked", side_effect=completed
        ):
            root = Path(directory)
            (root / "source.png").write_bytes(b"source")
            (root / "output").mkdir()
            _invoke_wsl(root / "source.png", root / "output", profile, 30)
        command, process_env = calls[-1]
        self.assertIsNotNone(process_env)
        token_hex.assert_called_once_with(32)
        self.assertIn("HF_HUB_OFFLINE=1", command)
        self.assertIn("TRANSFORMERS_OFFLINE=1", command)
        self.assertFalse(any(item.startswith("ONECLICK2D_ENTRYPOINT_ATTESTATION=") for item in command))
        self.assertFalse(any(item.startswith("ONECLICK2D_ATTESTATION_CHALLENGE=") for item in command))
        self.assertFalse(any(item.startswith("ONECLICK2D_ATTESTATION_SOURCE=") for item in command))
        self.assertNotIn("--srcp", command)
        self.assertNotIn("cd" * 32, " ".join(command))
        self.assertEqual(
            "/mnt/d/model-spike/.entrypoint-attestation.json",
            process_env["ONECLICK2D_ENTRYPOINT_ATTESTATION"],
        )
        self.assertEqual("cd" * 32, process_env["ONECLICK2D_ATTESTATION_CHALLENGE"])
        self.assertEqual("/mnt/d/model-spike", process_env["ONECLICK2D_ATTESTATION_SOURCE"])
        self.assertTrue(
            {
                "ONECLICK2D_ENTRYPOINT_ATTESTATION",
                "ONECLICK2D_ATTESTATION_CHALLENGE",
                "ONECLICK2D_ATTESTATION_SOURCE",
            }.issubset(set(process_env["WSLENV"].split(":")))
        )
        self.assertIn("PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True", command)
        self.assertNotIn("PYTHONPATH=common", command)
        self.assertIn("--cpu_offload", command)
        self.assertIn("--no_group_offload", command)
        self.assertIn("models/seethroughv0.0.2_layerdiff3d_nf4", command)
        self.assertIn("models/seethroughv0.0.1_marigold_nf4", command)
        consume.assert_called_once_with(
            Path(directory) / "output" / "input" / ".entrypoint-attestation.json",
            expected_challenge="cd" * 32,
            source=Path(directory) / "source.png",
        )

    def test_wsl_invocations_never_reuse_attestation_challenges(self) -> None:
        profile = _wsl_profile()
        commands: list[list[str]] = []
        environments: list[dict[str, str]] = []

        def completed(command, **kwargs):
            if "wslpath" in command:
                return mock.Mock(returncode=0, stdout=b"/mnt/d/model-spike\n", stderr=b"")
            commands.append(command)
            environments.append(kwargs["env"])
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "spikes.gate_f_runner.model_worker._verify_wsl_models"
        ), mock.patch(
            "spikes.gate_f_runner.model_worker.secrets.token_hex",
            side_effect=("11" * 32, "22" * 32),
        ) as token_hex, mock.patch(
            "spikes.gate_f_runner.model_worker._consume_entrypoint_attestation"
        ), mock.patch(
            "spikes.gate_f_runner.model_worker._run_checked",
            side_effect=completed,
        ):
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            for name in ("first", "second"):
                output = root / name
                output.mkdir()
                _invoke_wsl(source, output, profile, 30)
        self.assertEqual(2, token_hex.call_count)
        self.assertNotIn("11" * 32, " ".join(commands[0]))
        self.assertNotIn("22" * 32, " ".join(commands[1]))
        self.assertEqual("11" * 32, environments[0]["ONECLICK2D_ATTESTATION_CHALLENGE"])
        self.assertEqual("22" * 32, environments[1]["ONECLICK2D_ATTESTATION_CHALLENGE"])

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
            self.assertEqual("model spike failed: local model worker error\n", stderr.getvalue())
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
                return (
                    mock.Mock(returncode=0, stdout=b"", stderr=b""),
                    _published_entrypoint_attestation_summary(output_path, source_path),
                )

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
            ):
                report = run_model_worker(source, output, timeout_seconds=30)
            self.assertTrue(report["model_used"])
            self.assertFalse(report["oc2d_produced"])
            self.assertEqual("GATE_F_NOT_EVALUATED", report["gate_f_status"])
            self.assertEqual("input/input.psd", report["psd"]["uri"])
            self.assertEqual(
                PSD_PIXEL_PROJECTION_ALGORITHM_ID,
                report["entrypoint_attestation"]["psd_pixel_projection_algorithm_id"],
            )
            self.assertEqual(
                hashlib.sha256(b"source").hexdigest(),
                report["entrypoint_attestation"]["binding"]["source_sha256"],
            )
            self.assertEqual(
                {"source_sha256", "artifact_manifest_digest"},
                set(report["entrypoint_attestation"]["binding"]),
            )
            published_manifest = _artifact_manifest(
                output / "input",
                output / "input" / ".entrypoint-attestation.json",
            )
            self.assertEqual(
                _artifact_manifest_digest(published_manifest),
                report["entrypoint_attestation"]["binding"]["artifact_manifest_digest"],
            )

    def test_worker_rejects_manifest_digest_for_another_legal_artifact_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"
            other = root / "other"
            other.mkdir()
            (other / "other.psd").write_bytes(b"other artifact set")
            other_digest = _artifact_manifest_digest(
                _artifact_manifest(other, other / ".entrypoint-attestation.json")
            )

            def invoke(source_path, output_path, profile, timeout_seconds):
                _write_complete_model_output(output_path)
                return (
                    mock.Mock(returncode=0, stdout=b"", stderr=b""),
                    _valid_entrypoint_attestation_summary(
                        hashlib.sha256(source_path.read_bytes()).hexdigest(),
                        other_digest,
                    ),
                )

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
            ):
                with self.assertRaisesRegex(
                    StageContractError,
                    "published artifact manifest digest mismatch",
                ):
                    run_model_worker(source, output, timeout_seconds=30)

    def test_worker_rejects_artifact_changed_after_attestation_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"

            def invoke(source_path, output_path, profile, timeout_seconds):
                _write_complete_model_output(output_path)
                return (
                    mock.Mock(returncode=0, stdout=b"", stderr=b""),
                    _published_entrypoint_attestation_summary(output_path, source_path),
                )

            def inventory_after_mutation(directory_path: Path):
                stats_path = directory_path / "input" / "input" / "stats.json"
                stats = json.loads(stats_path.read_text(encoding="utf-8"))
                stats["total_time_s"] = 14.0
                stats_path.write_text(json.dumps(stats, separators=(",", ":")), encoding="utf-8")
                return _inventory(directory_path)

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
            ), mock.patch(
                "spikes.gate_f_runner.model_worker._inventory",
                side_effect=inventory_after_mutation,
            ):
                with self.assertRaisesRegex(
                    StageContractError,
                    "published artifact manifest digest mismatch",
                ):
                    run_model_worker(source, output, timeout_seconds=30)

    def test_worker_rejects_entrypoint_source_binding_mismatch_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"source")
            output = root / "output"

            def invoke(source_path, output_path, profile, timeout_seconds):
                _write_complete_model_output(output_path)
                summary = _published_entrypoint_attestation_summary(output_path, source_path)
                summary["binding"]["source_sha256"] = "0" * 64
                return mock.Mock(returncode=0, stdout=b"", stderr=b""), summary

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
            ):
                with self.assertRaisesRegex(
                    StageContractError,
                    "source binding does not match",
                ):
                    run_model_worker(source, output, timeout_seconds=30)

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
                return (
                    mock.Mock(returncode=0, stdout=b"", stderr=b""),
                    _published_entrypoint_attestation_summary(output_path, source_path),
                )

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
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
                return (
                    mock.Mock(returncode=0, stdout=b"", stderr=b""),
                    _published_entrypoint_attestation_summary(output_path, source_path),
                )

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
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
                return (
                    mock.Mock(returncode=0, stdout=b"", stderr=b""),
                    _published_entrypoint_attestation_summary(output_path, source_path),
                )

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
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
                return (
                    mock.Mock(returncode=0, stdout=b"", stderr=b""),
                    _published_entrypoint_attestation_summary(output_path, source_path),
                )

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
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
                return (
                    mock.Mock(returncode=0, stdout=b"", stderr=b""),
                    _published_entrypoint_attestation_summary(output_path, source_path),
                )

            with mock.patch("spikes.gate_f_runner.model_worker._verify_runtime"), mock.patch(
                "spikes.gate_f_runner.model_worker._invoke_model", side_effect=invoke
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
                "spikes.gate_f_runner.model_worker._invoke_model",
                return_value=(mock.Mock(returncode=1, stdout=b"", stderr=b"private detail"), None),
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
