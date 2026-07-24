from __future__ import annotations

import base64
import functools
import importlib
import json
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
    ENTRYPOINT_ROOT,
    MODEL_PART_NAMES,
    MODEL_SEMANTIC_NAMES,
    PROFILE_ID,
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
        import hashlib

        self.assertEqual(
            profile["runtime"]["dependencies_sha256"],
            hashlib.sha256(dependencies_path.read_bytes()).hexdigest(),
        )
        entrypoint_path = ENTRYPOINT_ROOT / profile["entrypoint"]["path"]
        self.assertEqual(
            profile["entrypoint"]["sha256"],
            hashlib.sha256(entrypoint_path.read_bytes()).hexdigest(),
        )
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
        ), mock.patch("spikes.gate_f_runner.model_worker._run_checked", side_effect=completed):
            root = Path(directory)
            (root / "output").mkdir()
            _invoke_wsl(root / "source.png", root / "output", profile, 30)
        command = calls[-1]
        self.assertIn("HF_HUB_OFFLINE=1", command)
        self.assertIn("TRANSFORMERS_OFFLINE=1", command)
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


if __name__ == "__main__":
    unittest.main()
