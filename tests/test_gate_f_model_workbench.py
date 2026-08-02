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
    "eNrVXFtv2zoS/isLPceuqLv6lqbpOcHmJEVO2n1YFAJFjmxuJFFHFyfZIv99h5Qs2YnSuDgytkKRIhFnePnmmyE19vC7wWhBY5GK"
    "WkBlvP9u8MecZoJFRQkbAffGeyOXdbSCHEpaAzdODHioocxpGgEXtSyjDU0Fp7WQeScM+KTphNeCc8ijElbYHv3VoGz9OCKXQbXe"
    "jjL0tDusZBaPCsru6ApGmgta0gxwZlEsci7y1ZhMxSORd7MvgfIYu0M5uqEipXEKKFNBRvMaAWCyLIHVOVTVyHx7MQ5FvX6tj3IV"
    "0+dtsikZYO8ZzlhUeqlD+9OJoTuMcKrKGvFjDVEK+UqNQTw78H3npQWqmtbN2CzXIFbrGjWtwDwxBHZpyKYumnqhR1moUU6MOwQM"
    "WzLJId1rSOkj4slkk2MfljISFzSqHwtlAJGhId5tcr6kXMawLNayltVaFmqRa2q5HgrZFvNiYMzhvuP5zDMZriAEz7J9CDnEECYB"
    "hCYlgecnlu9a3CY0cXkQeFYMgUa6LhtWN+WOyZAGyijYACdGU4p+8u3i3om8/7+1z7Jdz73gGkdEA4FOZJnRWkGSA0sFu7P4MpWM"
    "pgu9tMW9LO9iyNl6UUIhy9rYqkQbKKuWpObSXprYsELAo2QwxG+nt+fRp+jq+jY6/3p6+QX//KgAVd0PUpfXZ6eX0b+ub/754fzq"
    "7Pfo7PqPz5fnrahekHZIzQdNW41/Db1FbGVTQKYpl/pusKKJZJKkkvItOKgMOYow9O6otwqnzLOcOCaJ7TmObzsJAyfxfEqc2HXi"
    "xHUsh9DAJyGlVpIEVhgzG9BwHkmAcNfYsrSESqZN67C+FyAz87p8LCTOdhjNsykJLRetG4acx67nu0FIPZe43PdsYuPTxIoJ4UgQ"
    "03RY4jBGgjAwTTOxqBsoeEvZFMPaEppWuDiRJ1Di2gARhQIBtZHkaEjIKxhArppCWQ8jQnSv3SHCMEExYFEEVblhilEjyiUuhgsk"
    "m4jbBWG4kFVdlJJhBIhoupKlqNdZ52W0KFLRBpVSJiKFSHtXBbCo1zjb1Xq5sZd54iwV25b3VWotN9aOeA8PcVx0ajdkzALbpjG1"
    "mQm+k3BiJ8R2KLCE0sRx4zigjALagFgksCgNLDsEWxkDwyqS4L995EwcfLhrm9b/cXI4RwcduSlwoUAzFYYyoXzADShhygg4auBS"
    "BiyMY7RWwLgTe07gB47N0TYqQumwgND++7tBEdiEsvpFrApt3x6NP62j6i4WpjlEn23MXOiYORprCgzqrWPgo5vfPpzuRhofYtOy"
    "SRyHoZlQDoyHph/4LEhMLyROkPgBp7EXWBDbgRcj60ybWIg9JWZgEma8FUneJaXM63+sqSiX7Uz2w0nrEa8Cgi7l/gCQNuyOAtLu"
    "MG8jcrkLBw88ToiX+ARCEiSubaL3BqblcotZgRPbrueEDoCDoZYlNk9oQpzQBTNhhDKX/gwc2xg7AgouBR6M97hWPFWoSQ5ayKS/"
    "zR8yJ/6onevI9CHzoU+PxtvsIT17eqUpyGPNiTxrPPocxJu8SdMeOatHTulPAZo9N9DugR7R4ez5ONwWjLf9zd5jjdKZgjnOrPZ6"
    "PAEdjzXOjHZ5BOJtxjjD/o7yU7DFnRNb4BHiUt4fjzDufAjTYfE2Z9yeM53KFLTxZkablFbr49HGmxVtFBZv08bbpY1SmYI2/pxo"
    "I0rBoToea/z5sKaF4m3S+D1pWo0pOBPMLNTcr0V9xDNNMKtYo8F4mzfBbrDROlMwJ5wbc476ChXOizgHvUGFe7yZ5gWKzCpZims+"
    "3hZFZpQmVUAckOIaMqRKYQq6kJnR5ahRhpBZMeawKEPILmmmCjOzSovmsjremWYUil+UNAqIAxgzJISVwhR0mVVCOMNnx3vfJjPK"
    "BmskDiDMkAvWGlMwZlaJ4BzY3fEIM6NEsALiAL4MmWClMAVd3LnR5bgHGXdelDnwJOPu0Waqo8ysssG1LI7LnBllgzssDiDOkA7u"
    "dKbgzazSwWuaH/dDbjKjhPAWjAOYM+SEt0pTUGdWWeFY1rXMjkueGeWFBzgOoM+QGh7UpiDQrJLDKayOy54ZJYc7LA6gzpAd7nQm"
    "4I01r6/SSlkflTjWnL5I24HxNnOsne/RdkpTUGdWmeKaivR4tJlRmlgBcQBlhhyxUpiCLrNKEN+LfHW8D6KsGWWINRIHEGZIEWuN"
    "KRgzqxyxjP8DrD4iZ2aUJe6wOIA1Q5640zGevqkaJCbztoBP1yH9HHueafeovXj+65Fof4qjyPVFoqOlfc62UvRnQeu0nlV16lkt"
    "YKPKBXXrrwdZVSIU2Wocq7ZodsrZqPpAXfCsYxQXSRLVIsMx2g/jM1qKlUx5/xDDYgH0LtqUNItWsfHeW1puW1Lc63WVeFGHYVuH"
    "V8uapoOMjctBXlQ18OhnLdzpLcYt/Urr/83SY/MZse5TV+saNRX0dattsXcpecNgKPgs1rSCtvBQ4/Hl8+X16cfo5vzs/OKrrppV"
    "VlWL2tZ1tjUvWvjzxdXV+cfoj+uP55fRxdWn85vzq7PzH+u0wqc3txefTs9uo6+nlxcfT28PUro5//PL5W30+cuHy4s/fx/X+Kb5"
    "88L+nus6ZuBNUuc9UuE9VW23xcA2WWCGthfEnktMomgRJBTs0GMxWE4CJmWM+17shxCHvhWC54Fv2y4JqfO3a7tHq7r7Kwa+Gzk0"
    "teo6wai3fUZTJFFUr0vA5aS8/ehBDTk801Ghizh4SongAff6SN9LEGUiF1mTGe/NZRi6J7tiGQX8edg2D40bUYk4VaF9AyXi+rwr"
    "8qQ2SlrJHEW45veLrXPohOYbWrVd6IjzilwhHtClOvOqiwMc09ybLlvTPEcRnDYOaJ7gv28nryxbD7S/VF3T2T3Sjj6QUxvw5eqf"
    "T/yH6LwqgVHzh0vbCstMVKpGfyv3UuKVbp6t4tXp/6ibVwyq7tOIRA2ZejJ+0cTzCzO6YNEec56HA31wkAXCpi/weBy5RmPsXoyT"
    "/o4PDu2FBq3k3gUb3/atq68XoLnMBUP3aivN92ri0dHyCseCvF4wUBNbFJQvCpGm8n6h5pcDLRdNsYjlw4LL+3y5US/b2yNJhD2t"
    "RK5jjyrXRzuXgG+sOf7Sbg/a5TUKmpf4+oA8rNl6GytaqS1LYXs1gBbsYxYLTWAuhiwbTDwcB2ZsAVCPcc/D8OOEjDmO7ycs8E0S"
    "h9TyLWoBbmseWH7oxOp1L1eQIQb9/t333pZevrT8c07tb/9Tz/BpZ8COcyX81YhS71FG2eTtVQX4y7JQF1noTXpj2gt9P0HFpN4C"
    "uKgKWalLSRajl2IMHo52UndQ6KsHtkvbMVlE8+3fHEqxwdY2lFeDhdF5hApFeaN3gkzi3ja6ZWK0xwkkyKntvmA8/Q8jmIos"
)

LEGACY_V3_WORKBENCH_REPORT_V03_ZLIB_BASE64 = (
    "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"
    "yKntvmA8/Q8qVZfa"
)


LEGACY_V4_WORKBENCH_REPORT_V04_ZLIB_BASE64 = (
    "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"
)


def _legacy_workbench_report_v03_bytes(encoded: str) -> bytes:
    return zlib.decompress(base64.b64decode(encoded))


_GRAYSCALE_PSD = base64.b64decode(
    "OEJQUwABAAAAAAAAAAEAAAACAAAAAgAIAAEAAAAAAAAAXjhCSU0EIQAAAAAAUQAAAAEBAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAEABwAHMAZAAtAHQAbwBvAGwAcwAgADEALgAxADQALgAyAAAAAQAAAADwAAAA6AACAAAAAAAAAAAAAAABAAAAAQAC//8AAAAGAAAAAAAGOEJJTW5vcm3/AAgAAAAAOAAAAAAAAAAoAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wRmYWNlAAAAAAAAAQAAAAEAAAACAAAAAgAC//8AAAAGAAAAAAAGOEJJTW5vcm3/AAgAAAAAOAAAAAAAAAAoAAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wVtb3V0aAAAAAEAAgD/AAEAAgBQAAEAAgD/AAEAAgCgAAAAAAAAAABQAACg"
)


@cache
def _png(mode: str) -> bytes:
    from PIL import Image

    color = (30, 90, 160, 220) if mode == "RGBA" else 120
    with Image.new(mode, (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE), color) as image:
        stream = BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()


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
            self.assertEqual("0.5.0", report["format_version"])
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

                self.assertEqual("0.5.0", report["format_version"])
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
            result["entrypoint_attestation"].pop("binding")
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
            self.assertEqual("0.5.0", report["format_version"])
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
            result["entrypoint_attestation"].pop("binding")
            (run_dir / "model-result.json").write_bytes(canonical_json_bytes(result))
            report = build_model_workbench_report(run_dir, run_dir.name, result)
            (run_dir / "workbench-report.json").write_bytes(canonical_json_bytes(report))

            with self.assertRaisesRegex(
                StageContractError,
                "format version 0.5.0 is unsupported for this profile",
            ):
                load_model_workbench_report(run_dir)

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
                "requires a fidelity-passing active model profile",
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
            result["entrypoint_attestation"].pop("binding")

            report = build_model_workbench_report(run_dir, run_dir.name, result)
            identity = report["model"]["identity"]
            archived_v4 = json.loads(
                (PROFILE_ROOT / "see-through-v3-nf4.source-preserve-v4.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual("0.5.0", report["format_version"])
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
