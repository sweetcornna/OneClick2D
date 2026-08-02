"""Shared facts for the purpose-created Gate F fixture."""

# Common source of truth for deterministic byte-for-byte reproducibility verification.

from __future__ import annotations

from hashlib import sha256
import struct
import zlib
from pathlib import Path

from .contracts import StageContractError
from .runtime import canonical_json_bytes


MAX_ARM_BUNDLE_OUTPUT_BYTES = 33_554_432
_ROOT = Path(__file__).resolve().parents[2]


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def purpose_created_source(width: int = 101, height: int = 103) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256, 255))
        rows.append(bytes(row))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"sRGB", b"\x00")
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + _png_chunk(b"IEND", b"")
    )


def normalization_config() -> bytes:
    return canonical_json_bytes(
        {
            "max_width": 8192,
            "max_height": 8192,
            "max_pixels": 40000000,
            "max_metadata_bytes": 1048576,
            "max_icc_profile_bytes": 1048576,
            "required_pillow_version": "12.1.0",
            "png_compress_level": 9,
            "rendering_intent": 1,
        }
    )


def arm_run_spec(source: bytes, normalize: bytes, arm_config: bytes, arm: str) -> bytes:
    limits = {
        "max_wall_time_ms": 30000,
        "max_cpu_time_ms": 30000,
        "max_peak_ram_bytes": 536870912,
        "max_scratch_bytes": 1048576,
        "max_output_bytes": MAX_ARM_BUNDLE_OUTPUT_BYTES,
        "max_output_files": 2,
        "max_peak_vram_bytes": 0,
        "gpu_allowed": False,
    }
    if arm == "candidate":
        stage = {
            "id": "stage.arm-render",
            "stage_type": "oc2d.spike.candidate-baseline",
            "adapter_id": "candidate.baseline.pillow.v1",
            "config_uri": "configs/arm.json",
            "config_sha256": sha256(arm_config).hexdigest(),
            "limits": {**limits, "max_output_files": 43},
        }
        result_role = "candidate_baseline_report"
    elif arm == "comparator":
        stage = {
            "id": "stage.arm-render",
            "stage_type": "oc2d.spike.simple-cutout-comparator",
            "adapter_id": "simple-cutout.comparator.pillow.v1",
            "config_uri": "configs/arm.json",
            "config_sha256": sha256(arm_config).hexdigest(),
            "limits": {**limits, "max_output_files": 38},
        }
        result_role = "simple_cutout_comparator_report"
    else:
        raise StageContractError("unknown preflight arm")
    return canonical_json_bytes(
        {
            "$schema": str(_ROOT / "schemas" / "gate-f-run-spec" / "v0.1" / "run-spec.schema.json"),
            "format": "oneclick2d.gate-f-run-spec",
            "format_version": "0.1.0",
            "scope": "disposable-gate-f-spike",
            "execution_profile": "python-pillow-12.1.0-in-process-v1",
            "root_seed_u64": "00000000000000000042",
            "source": {
                "role": "source_raster",
                "sha256": sha256(source).hexdigest(),
                "media_type": "image/png",
                "max_bytes": 26214400,
            },
            "expected_result_role": result_role,
            "stages": [
                {
                    "id": "stage.raster-normalize",
                    "stage_type": "oc2d.spike.raster-normalize",
                    "adapter_id": "raster.normalize.pillow.v1",
                    "config_uri": "configs/normalize.json",
                    "config_sha256": sha256(normalize).hexdigest(),
                    "limits": limits,
                },
                stage,
            ],
        }
    )
