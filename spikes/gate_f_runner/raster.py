"""Pillow-backed raster intake for the disposable Gate F spike."""

from __future__ import annotations

import io
import struct
import warnings
import zlib
from dataclasses import dataclass
from typing import Any

from .contracts import (
    Determinism,
    ProducerKind,
    ResourceLimitExceeded,
    StageContext,
    StageOutcome,
    StageStatus,
)
from .runner import AdapterRegistry
from .runtime import canonical_json_bytes, strict_load_json_bytes

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_ALLOWED_OUTPUT_CHUNKS = {b"IHDR", b"sRGB", b"IDAT", b"IEND"}
PNG_ANIMATION_CHUNKS = {b"acTL", b"fcTL", b"fdAT"}
JPEG_STANDALONE_MARKERS = {0x01, *range(0xD0, 0xDA)}
EXIF_ORIENTATION = 0x0112


class RasterBlocked(ValueError):
    def __init__(self, reason_code: str, finding_codes: tuple[str, ...] = ()) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.finding_codes = finding_codes


@dataclass(frozen=True)
class RasterConfig:
    max_width: int
    max_height: int
    max_pixels: int
    max_metadata_bytes: int
    max_icc_profile_bytes: int
    png_compress_level: int
    rendering_intent: int


@dataclass(frozen=True)
class ContainerFacts:
    format: str
    bit_depth: int
    color_type: int | None
    has_png_srgb: bool
    jpeg_size: tuple[int, int] | None = None


@dataclass(frozen=True)
class PillowBackend:
    Image: Any
    ImageCms: Any
    ImageFile: Any
    ImageOps: Any
    PngImagePlugin: Any
    UnidentifiedImageError: type[BaseException]
    version: str


def _positive_int(value: Any, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RasterBlocked("RASTER_CONFIG_INVALID")
    return value


def _parse_config(data: bytes) -> RasterConfig:
    value = strict_load_json_bytes(data)
    keys = {
        "max_width",
        "max_height",
        "max_pixels",
        "max_metadata_bytes",
        "max_icc_profile_bytes",
        "required_pillow_version",
        "png_compress_level",
        "rendering_intent",
    }
    if not isinstance(value, dict) or set(value) != keys or value["required_pillow_version"] != "12.1.0":
        raise RasterBlocked("RASTER_CONFIG_INVALID")
    return RasterConfig(
        max_width=_positive_int(value["max_width"], 1, 8192, "max_width"),
        max_height=_positive_int(value["max_height"], 1, 8192, "max_height"),
        max_pixels=_positive_int(value["max_pixels"], 1, 40_000_000, "max_pixels"),
        max_metadata_bytes=_positive_int(value["max_metadata_bytes"], 0, 4_194_304, "max_metadata_bytes"),
        max_icc_profile_bytes=_positive_int(value["max_icc_profile_bytes"], 0, 1_048_576, "max_icc_profile_bytes"),
        png_compress_level=_positive_int(value["png_compress_level"], 0, 9, "png_compress_level"),
        rendering_intent=_positive_int(value["rendering_intent"], 0, 3, "rendering_intent"),
    )


def _load_pillow() -> PillowBackend:
    try:
        import PIL
        from PIL import Image, ImageFile, ImageOps, PngImagePlugin, UnidentifiedImageError
        try:
            from PIL import ImageCms
        except ImportError:
            ImageCms = None
    except ImportError as exc:
        raise RasterBlocked("RASTER_DEPENDENCY_UNAVAILABLE") from exc
    if PIL.__version__ != "12.1.0":
        raise RasterBlocked("RASTER_RUNTIME_UNSUPPORTED")
    return PillowBackend(Image, ImageCms, ImageFile, ImageOps, PngImagePlugin, UnidentifiedImageError, PIL.__version__)


def _bounded_decompress(data: bytes, maximum: int) -> bytes:
    decompressor = zlib.decompressobj()
    try:
        result = decompressor.decompress(data, maximum + 1)
        if len(result) > maximum or decompressor.unconsumed_tail:
            raise RasterBlocked("RASTER_METADATA_LIMIT_EXCEEDED")
        result += decompressor.flush(maximum + 1 - len(result))
    except zlib.error as exc:
        raise RasterBlocked("RASTER_CONTAINER_INVALID") from exc
    if len(result) > maximum:
        raise RasterBlocked("RASTER_METADATA_LIMIT_EXCEEDED")
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        raise RasterBlocked("RASTER_CONTAINER_INVALID")
    return result


def _itxt_text_bytes(payload: bytes, maximum: int) -> int:
    first = payload.find(b"\x00")
    if first <= 0 or first + 3 > len(payload):
        raise RasterBlocked("RASTER_CONTAINER_INVALID")
    compression_flag = payload[first + 1]
    compression_method = payload[first + 2]
    if compression_flag not in {0, 1} or compression_method != 0:
        raise RasterBlocked("RASTER_CONTAINER_INVALID")
    language_end = payload.find(b"\x00", first + 3)
    translated_end = payload.find(b"\x00", language_end + 1) if language_end >= 0 else -1
    if language_end < 0 or translated_end < 0:
        raise RasterBlocked("RASTER_CONTAINER_INVALID")
    text = payload[translated_end + 1 :]
    prefix_bytes = translated_end + 1
    if prefix_bytes > maximum:
        raise RasterBlocked("RASTER_METADATA_LIMIT_EXCEEDED")
    if compression_flag == 1:
        return prefix_bytes + len(_bounded_decompress(text, maximum - prefix_bytes))
    if len(payload) > maximum:
        raise RasterBlocked("RASTER_METADATA_LIMIT_EXCEEDED")
    return len(payload)


def _preflight_png(data: bytes, config: RasterConfig) -> ContainerFacts:
    if not data.startswith(PNG_SIGNATURE):
        raise RasterBlocked("RASTER_CONTAINER_INVALID")
    offset = len(PNG_SIGNATURE)
    chunks: list[bytes] = []
    metadata_bytes = 0
    bit_depth = color_type = None
    has_srgb = False
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise RasterBlocked("RASTER_CONTAINER_INVALID")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise RasterBlocked("RASTER_CONTAINER_INVALID")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            raise RasterBlocked("RASTER_CONTAINER_INVALID")
        if chunk_type in PNG_ANIMATION_CHUNKS:
            raise RasterBlocked("RASTER_MULTIFRAME_UNSUPPORTED")
        if chunk_type == b"IHDR":
            if chunks or length != 13:
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            width, height = struct.unpack(">II", payload[:8])
            _check_dimensions((width, height), config)
            bit_depth, color_type = payload[8], payload[9]
        elif chunk_type == b"sRGB":
            has_srgb = True
        elif chunk_type == b"iCCP":
            metadata_bytes += length
            separator = payload.find(b"\x00")
            if separator <= 0 or separator + 2 > len(payload) or payload[separator + 1] != 0:
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            _bounded_decompress(payload[separator + 2 :], config.max_icc_profile_bytes)
        elif chunk_type == b"zTXt":
            separator = payload.find(b"\x00")
            if separator <= 0 or separator + 2 > len(payload) or payload[separator + 1] != 0:
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            remaining = max(0, config.max_metadata_bytes - metadata_bytes)
            prefix_bytes = separator + 2
            if prefix_bytes > remaining:
                raise RasterBlocked("RASTER_METADATA_LIMIT_EXCEEDED")
            metadata_bytes += prefix_bytes + len(_bounded_decompress(payload[separator + 2 :], remaining - prefix_bytes))
        elif chunk_type == b"iTXt":
            remaining = max(0, config.max_metadata_bytes - metadata_bytes)
            metadata_bytes += _itxt_text_bytes(payload, remaining)
        elif chunk_type not in {b"IDAT", b"IEND", b"PLTE", b"tRNS"}:
            metadata_bytes += length
        if metadata_bytes > config.max_metadata_bytes:
            raise RasterBlocked("RASTER_METADATA_LIMIT_EXCEEDED")
        chunks.append(chunk_type)
        offset = end
        if chunk_type == b"IEND":
            if length != 0 or offset != len(data):
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            saw_iend = True
            break
    if not saw_iend or not chunks or chunks[0] != b"IHDR" or chunks.count(b"IHDR") != 1 or chunks.count(b"IEND") != 1:
        raise RasterBlocked("RASTER_CONTAINER_INVALID")
    if bit_depth not in {1, 2, 4, 8} or color_type not in {0, 2, 3, 4, 6}:
        raise RasterBlocked("RASTER_MODE_OR_BIT_DEPTH_UNSUPPORTED")
    return ContainerFacts("PNG", int(bit_depth), int(color_type), has_srgb)


def _preflight_jpeg(data: bytes, config: RasterConfig) -> ContainerFacts:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise RasterBlocked("RASTER_CONTAINER_INVALID")
    offset = 2
    metadata_bytes = 0
    jpeg_size: tuple[int, int] | None = None
    in_scan = False
    while offset < len(data):
        if in_scan:
            marker_start = data.find(b"\xff", offset)
            if marker_start < 0:
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            offset = marker_start
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            marker = data[offset]
            offset += 1
            if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                continue
            in_scan = False
        else:
            if data[offset] != 0xFF:
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            marker = data[offset]
            offset += 1
        if marker == 0xD9:
            if offset != len(data) or jpeg_size is None:
                raise RasterBlocked("RASTER_CONTAINER_INVALID")
            return ContainerFacts("JPEG", 8, None, False, jpeg_size)
        if marker in JPEG_STANDALONE_MARKERS or marker == 0xD8:
            continue
        if offset + 2 > len(data):
            raise RasterBlocked("RASTER_CONTAINER_INVALID")
        segment_length = struct.unpack(">H", data[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(data):
            raise RasterBlocked("RASTER_CONTAINER_INVALID")
        payload = data[offset + 2 : offset + segment_length]
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if len(payload) < 6 or payload[0] != 8:
                raise RasterBlocked("RASTER_MODE_OR_BIT_DEPTH_UNSUPPORTED")
            height, width = struct.unpack(">HH", payload[1:5])
            _check_dimensions((width, height), config)
            jpeg_size = (width, height)
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            metadata_bytes += len(payload)
            if metadata_bytes > config.max_metadata_bytes:
                raise RasterBlocked("RASTER_METADATA_LIMIT_EXCEEDED")
            if marker == 0xE2 and payload.startswith(b"MPF\x00"):
                raise RasterBlocked("RASTER_MULTIFRAME_UNSUPPORTED")
        offset += segment_length
        if marker == 0xDA:
            in_scan = True
    raise RasterBlocked("RASTER_CONTAINER_INVALID")


def _preflight(data: bytes, media_type: str, config: RasterConfig) -> ContainerFacts:
    if media_type not in {"image/png", "image/jpeg"}:
        raise RasterBlocked("RASTER_FORMAT_UNSUPPORTED")
    actual: str | None = None
    if data.startswith(PNG_SIGNATURE):
        actual = "PNG"
    elif data.startswith(b"\xff\xd8"):
        actual = "JPEG"
    expected = {"image/png": "PNG", "image/jpeg": "JPEG"}[media_type]
    if actual is not None and actual != expected:
        raise RasterBlocked("RASTER_MEDIA_TYPE_MISMATCH")
    if expected == "PNG":
        return _preflight_png(data, config)
    return _preflight_jpeg(data, config)


def _check_dimensions(size: tuple[int, int], config: RasterConfig) -> None:
    width, height = size
    if width < 1 or height < 1 or width > config.max_width or height > config.max_height:
        raise RasterBlocked("RASTER_DIMENSIONS_UNSUPPORTED")
    if width * height > config.max_pixels:
        raise RasterBlocked("RASTER_DECOMPRESSION_LIMIT_EXCEEDED")


def _image_identity(image: Any, facts: ContainerFacts, media_type: str, config: RasterConfig) -> None:
    if image.format != facts.format or image.get_format_mimetype() != media_type:
        raise RasterBlocked("RASTER_MEDIA_TYPE_MISMATCH")
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
        raise RasterBlocked("RASTER_MULTIFRAME_UNSUPPORTED")
    try:
        image.seek(1)
    except EOFError:
        image.seek(0)
    else:
        raise RasterBlocked("RASTER_MULTIFRAME_UNSUPPORTED")
    _check_dimensions(image.size, config)


def _icc_bytes(image: Any, config: RasterConfig) -> bytes | None:
    value = image.info.get("icc_profile")
    if value is None:
        return None
    if not isinstance(value, bytes) or not value or len(value) > config.max_icc_profile_bytes:
        raise RasterBlocked("RASTER_ICC_PROFILE_INVALID")
    return value


def _orientation(image: Any) -> int:
    try:
        value = image.getexif().get(EXIF_ORIENTATION, 1)
    except MemoryError:
        raise
    except Exception as exc:
        raise RasterBlocked("RASTER_EXIF_INVALID") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value not in range(1, 9):
        raise RasterBlocked("RASTER_EXIF_INVALID")
    return value


def _color_convert(image: Any, icc_profile: bytes | None, facts: ContainerFacts, backend: PillowBackend, config: RasterConfig) -> tuple[Any, str, tuple[str, ...]]:
    if image.mode == "CMYK" and icc_profile is None:
        raise RasterBlocked("RASTER_ICC_PROFILE_REQUIRED")
    alpha = image.convert("RGBA").getchannel("A")
    if icc_profile is not None:
        if backend.ImageCms is None:
            raise RasterBlocked("RASTER_COLOR_MANAGEMENT_UNAVAILABLE")
        try:
            input_profile = backend.ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
            output_profile = backend.ImageCms.createProfile("sRGB")
            profile_space = str(input_profile.profile.xcolor_space).strip()
            expected_mode = {"RGB": "RGB", "CMYK": "CMYK", "GRAY": "L", "Gray": "L"}.get(profile_space)
            if expected_mode == "RGB" and image.mode in {"RGB", "RGBA", "P"}:
                color = image.convert("RGB")
            elif expected_mode == "L" and image.mode in {"1", "L", "LA"}:
                color = image.convert("L")
            elif expected_mode == "CMYK" and image.mode == "CMYK":
                color = image.copy()
            else:
                raise RasterBlocked("RASTER_ICC_PROFILE_INVALID")
            color = backend.ImageCms.profileToProfile(
                color,
                input_profile,
                output_profile,
                renderingIntent=config.rendering_intent,
                outputMode="RGB",
                inPlace=False,
            )
        except (MemoryError, RasterBlocked):
            raise
        except Exception as exc:
            raise RasterBlocked("RASTER_ICC_PROFILE_INVALID") from exc
        color.putalpha(alpha)
        return color, "embedded-icc-to-srgb", ()
    if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
        raise RasterBlocked("RASTER_MODE_OR_BIT_DEPTH_UNSUPPORTED")
    findings: tuple[str, ...] = ()
    policy = "png-srgb-declared" if facts.has_png_srgb else "untagged-assumed-srgb"
    if not facts.has_png_srgb:
        findings = ("RASTER_UNTAGGED_ASSUMED_SRGB",)
    return image.convert("RGBA"), policy, findings


def _write_normalized_png(image: Any, context: StageContext, config: RasterConfig, backend: PillowBackend) -> Any:
    output = backend.Image.new("RGBA", image.size)
    output.paste(image)
    pnginfo = backend.PngImagePlugin.PngInfo()
    pnginfo.add(b"sRGB", b"\x00")
    writer = context.sink.open_binary("normalized.png", role="normalized_raster", media_type="image/png")
    try:
        with writer:
            output.save(
                writer,
                format="PNG",
                optimize=False,
                compress_level=config.png_compress_level,
                pnginfo=pnginfo,
                icc_profile=None,
                exif=b"",
            )
    finally:
        output.close()
    artifact = writer.artifact
    _verify_output_png(artifact.path.read_bytes(), image.size)
    return artifact


def _verify_output_png(data: bytes, expected_size: tuple[int, int]) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise ResourceLimitExceeded("normalized PNG validation failed")
    offset = len(PNG_SIGNATURE)
    chunks: list[bytes] = []
    width = height = None
    srgb_intent: bytes | None = None
    while offset < len(data):
        if offset + 12 > len(data):
            raise ResourceLimitExceeded("normalized PNG validation failed")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data) or chunk_type not in PNG_ALLOWED_OUTPUT_CHUNKS:
            raise ResourceLimitExceeded("normalized PNG metadata policy failed")
        payload = data[offset + 8 : offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != crc:
            raise ResourceLimitExceeded("normalized PNG validation failed")
        if chunk_type == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", payload[:10])
            if depth != 8 or color_type != 6:
                raise ResourceLimitExceeded("normalized PNG mode validation failed")
        elif chunk_type == b"sRGB":
            srgb_intent = payload
        chunks.append(chunk_type)
        offset = end
        if chunk_type == b"IEND":
            break
    if offset != len(data) or (width, height) != expected_size:
        raise ResourceLimitExceeded("normalized PNG validation failed")
    if (
        chunks[0] != b"IHDR"
        or chunks[-1] != b"IEND"
        or chunks.count(b"IHDR") != 1
        or chunks.count(b"sRGB") != 1
        or chunks.count(b"IEND") != 1
        or srgb_intent != b"\x00"
        or not any(chunk == b"IDAT" for chunk in chunks)
    ):
        raise ResourceLimitExceeded("normalized PNG validation failed")


class PillowRasterNormalizeAdapter:
    adapter_id = "raster.normalize.pillow.v1"
    contract_id = "oc2d.spike.raster-normalize.v1"
    stage_type = "oc2d.spike.raster-normalize"
    implementation_version = "0.1.0"
    execution_profile = "python-pillow-12.1.0-in-process-v1"
    execution_provider = "pillow-12.1.0"
    producer_kind = ProducerKind.DETERMINISTIC
    determinism = Determinism.NUMERIC_TOLERANCE

    def execute(self, context: StageContext) -> StageOutcome:
        if len(context.spec.input_artifacts) != 1 or context.spec.input_artifacts[0].role != "source_raster":
            raise ValueError("raster stage requires one source_raster")
        context.cancellation.checkpoint()
        source = context.spec.input_artifacts[0]
        backend: PillowBackend | None = None
        try:
            config = _parse_config(context.spec.config_bytes)
            source_data = source.path.read_bytes()
            backend = _load_pillow()
            facts = _preflight(source_data, source.media_type, config)
            old_max_pixels = backend.Image.MAX_IMAGE_PIXELS
            old_truncated = backend.ImageFile.LOAD_TRUNCATED_IMAGES
            try:
                backend.Image.MAX_IMAGE_PIXELS = config.max_pixels
                backend.ImageFile.LOAD_TRUNCATED_IMAGES = False
                with warnings.catch_warnings():
                    warnings.simplefilter("error", backend.Image.DecompressionBombWarning)
                    with backend.Image.open(io.BytesIO(source_data), formats=("PNG", "JPEG")) as verify_image:
                        _image_identity(verify_image, facts, source.media_type, config)
                        verify_image.verify()
                    with backend.Image.open(io.BytesIO(source_data), formats=("PNG", "JPEG")) as decoded:
                        _image_identity(decoded, facts, source.media_type, config)
                        orientation = _orientation(decoded)
                        icc_profile = _icc_bytes(decoded, config)
                        decoded.load()
                        transformed = backend.ImageOps.exif_transpose(decoded)
                        try:
                            _check_dimensions(transformed.size, config)
                            normalized, color_policy, finding_codes = _color_convert(transformed, icc_profile, facts, backend, config)
                        finally:
                            transformed.close()
                        try:
                            context.cancellation.checkpoint()
                            png_artifact = _write_normalized_png(normalized, context, config, backend)
                            report = {
                                "format": "oneclick2d.raster-normalization-report",
                                "format_version": "0.1.0",
                                "scope": "disposable-gate-f-spike",
                                "adapter_id": self.adapter_id,
                                "adapter_version": self.implementation_version,
                                "contract_id": self.contract_id,
                                "input": {
                                    "format": facts.format,
                                    "media_type": source.media_type,
                                    "width": decoded.width,
                                    "height": decoded.height,
                                    "mode": decoded.mode,
                                    "bit_depth": facts.bit_depth,
                                    "frame_count": 1,
                                },
                                "orientation": {"value": orientation, "applied": orientation != 1},
                                "color_policy": color_policy,
                                "output": {
                                    "width": normalized.width,
                                    "height": normalized.height,
                                    "mode": "RGBA",
                                    "bit_depth": 8,
                                    "color_space": "srgb",
                                    "alpha_mode": "straight",
                                    "sha256": png_artifact.sha256,
                                    "byte_length": png_artifact.byte_length,
                                },
                                "metadata_removed": ["exif", "icc", "text", "comment", "dpi", "xmp"],
                                "finding_codes": list(finding_codes),
                                "runtime": {"pillow": backend.version},
                                "gate_f_feasibility_proven": False,
                            }
                            report_artifact = context.sink.write_bytes(
                                "normalization-report.json",
                                canonical_json_bytes(report),
                                role="raster_normalization_report",
                                media_type="application/vnd.oneclick2d.raster-normalization-report+json",
                            )
                        finally:
                            normalized.close()
            finally:
                backend.Image.MAX_IMAGE_PIXELS = old_max_pixels
                backend.ImageFile.LOAD_TRUNCATED_IMAGES = old_truncated
        except MemoryError as exc:
            raise ResourceLimitExceeded("raster memory limit exceeded") from exc
        except RasterBlocked as blocked:
            return StageOutcome(StageStatus.BLOCKED, reason_code=blocked.reason_code, finding_codes=blocked.finding_codes)
        except SyntaxError:
            return StageOutcome(StageStatus.BLOCKED, reason_code="RASTER_CONTAINER_INVALID")
        except Warning as warning:
            if backend is not None and isinstance(warning, backend.Image.DecompressionBombWarning):
                return StageOutcome(StageStatus.BLOCKED, reason_code="RASTER_DECOMPRESSION_LIMIT_EXCEEDED")
            raise
        except Exception as exc:
            if backend is not None and isinstance(exc, (backend.Image.DecompressionBombError, backend.UnidentifiedImageError)):
                return StageOutcome(StageStatus.BLOCKED, reason_code="RASTER_DECOMPRESSION_LIMIT_EXCEEDED" if isinstance(exc, backend.Image.DecompressionBombError) else "RASTER_CONTAINER_INVALID")
            raise
        return StageOutcome(StageStatus.SUCCEEDED, outputs=(png_artifact, report_artifact), finding_codes=finding_codes)


def build_raster_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(PillowRasterNormalizeAdapter())
    return registry
