"""Isolated subprocess bridge for disposable local model spikes."""

from __future__ import annotations

import argparse
import hmac
import io
import json
import math
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from .contracts import StageContractError
from .model_psd_validator import ModelPsdStructure, validate_model_psd
from .runtime import canonical_json_bytes, read_bounded_file, sha256_bytes, strict_load_json_bytes

PROFILE_ROOT = Path(__file__).with_name("model_profiles")
ENTRYPOINT_ROOT = Path(__file__).with_name("model_entrypoints")
DEVICE_POLICY_PATH = ENTRYPOINT_ROOT / "nf4_marigold_device_policy.py"
PROFILE_ID = "see-through.v3.nf4.1280.wsl2.source-preserve.v5"
PROFILE_PATH = PROFILE_ROOT / "see-through-v3-nf4.json"
LEGACY_PROFILE_ID = "see-through.v3.nf4.1280.wsl2.v2"
LEGACY_PROFILE_SHA256 = "14577459cc2e33aba3c0e74fd13f134aecfaaf45bb8acae96112182aa8239e35"
LEGACY_ENTRYPOINT_SHA256 = "63a192527599ddb567589a6515d7631399df2b11d67c004cf4cc1898000f2a58"
LEGACY_DEPENDENCIES_SHA256 = "dac624bb1f3644734fce4f67a14b54bf54241a8719aa2ff829bc3e77961fe1d5"
LEGACY_UPSTREAM_COMMIT = "58a1cb11d13f85acec9bbddb8cd4b6487843d4cf"
LEGACY_SOURCE_PRESERVE_PROFILE_ID = "see-through.v3.nf4.1280.wsl2.source-preserve.v3"
LEGACY_SOURCE_PRESERVE_PROFILE_SHA256 = "990b2561bb2067e3838aedf1751d9a86a06e6dfc985aad92189ec9fda387ec83"
LEGACY_SOURCE_PRESERVE_ENTRYPOINT_SHA256 = "6b625faa99022f6edfa5faba97b23054331b9276501e2b02953cb783f357ec71"
LEGACY_SOURCE_PRESERVE_V4_PROFILE_ID = "see-through.v3.nf4.1280.wsl2.source-preserve.v4"
LEGACY_SOURCE_PRESERVE_V4_PROFILE_SHA256 = "d24de59690e0db2c64828e580eed8b00f939d5327b255ef59f1826f8cf582ae3"
LEGACY_SOURCE_PRESERVE_V4_ENTRYPOINT_SHA256 = "ae4d26b042b8b15e7bdcfdacd11c50b16d97c1ccf19aad94162dd67046e1642f"
LEGACY_V4_NF4_MARIGOLD_DEVICE_POLICY_ID = "see-through.v4.nf4-marigold-bounded-offload.v1"
LEGACY_V4_PSD_PIXEL_PROJECTION_ALGORITHM_ID = (
    "source-visible-rgb-by-depth-mask-clean.v2.psd-postcorrect.v1"
)
LEGACY_V4_ENTRYPOINT_COMPONENT_DISPOSITIONS = (
    ("vae", "sequential-cpu-offload"),
    ("unet", "resident-quantized"),
    ("text_encoder", "cached-and-released"),
)
SOURCE_PRESERVE_ALGORITHM_ID = "source-visible-rgb-by-depth-mask-clean.v2"
NF4_MARIGOLD_DEVICE_POLICY_ID = "see-through.v4.nf4-marigold-bounded-offload.v1"
PSD_PIXEL_PROJECTION_ALGORITHM_ID = f"{SOURCE_PRESERVE_ALGORITHM_ID}.psd-postcorrect.v1"
ENTRYPOINT_COMPONENT_DISPOSITIONS = (
    ("vae", "sequential-cpu-offload"),
    ("unet", "resident-quantized"),
    ("text_encoder", "cached-and-released"),
)
MAX_MODEL_STDIO_BYTES = 2 * 1024 * 1024
MAX_MODEL_RESULT_BYTES = 512 * 1024 * 1024
MAX_MODEL_PNG_BYTES = 64 * 1024 * 1024
MAX_MODEL_ARTIFACT_MANIFEST_ENTRIES = 256
MAX_MODEL_ARTIFACT_MANIFEST_DIRECTORIES = 64
MAX_MODEL_ARTIFACT_MANIFEST_NODES = 320
MAX_MODEL_ARTIFACT_MANIFEST_DEPTH = 8
MAX_MODEL_ARTIFACT_RELATIVE_PATH_BYTES = 512
MODEL_PART_NAMES = (
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
MODEL_SEMANTIC_NAMES = (*MODEL_PART_NAMES[:2], "head", *MODEL_PART_NAMES[2:])
MODEL_STATS_KEYS = (
    "peak_vram_gb",
    "layerdiff_time_s",
    "marigold_time_s",
    "psd_time_s",
    "total_time_s",
)
RUNTIME_PACKAGE_NAMES = (
    "accelerate",
    "bitsandbytes",
    "diffusers",
    "einops",
    "huggingface-hub",
    "kornia",
    "matplotlib",
    "numpy",
    "opencv-python",
    "pillow",
    "psd-tools",
    "pyyaml",
    "safetensors",
    "scikit-image",
    "scikit-learn",
    "scipy",
    "timm",
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
)
RUNTIME_ATTESTATION_CODE = """import importlib.metadata as m,json,platform,torch
names=json.loads(__import__('os').environ['ONECLICK2D_PACKAGE_NAMES'])
result={'python':platform.python_version(),'torch':torch.__version__,'cuda':torch.version.cuda,'cuda_available':torch.cuda.is_available(),'packages':{name:m.version(name) for name in names}}
try:
 result['timm_direct_url']=json.loads(m.distribution('timm').read_text('direct_url.json') or 'null')
except (FileNotFoundError,TypeError,ValueError):
 result['timm_direct_url']=None
print(json.dumps(result,sort_keys=True,separators=(',',':')))
"""


def _load_profile() -> tuple[dict[str, object], bytes]:
    exact = read_bounded_file(PROFILE_PATH, 256 * 1024)
    profile = strict_load_json_bytes(exact)
    if not isinstance(profile, dict) or profile.get("profile_id") != PROFILE_ID:
        raise StageContractError("model spike profile is invalid")
    return profile, exact


def _wsl_path(path: Path, distribution: str) -> str:
    completed = _run_checked(
        ["wsl.exe", "-d", distribution, "--", "wslpath", "-a", path.resolve().as_posix()],
        timeout=30,
        output_limit=32 * 1024,
    )
    if completed.returncode != 0 or len(completed.stdout) > 32 * 1024:
        raise StageContractError("model worker path translation failed")
    value = completed.stdout.decode("utf-8", errors="strict").strip()
    if not value.startswith("/mnt/"):
        raise StageContractError("model worker path is outside the mounted workspace")
    return value


def _runtime(profile: dict[str, object]) -> tuple[str, str, str]:
    runtime = profile.get("runtime")
    if not isinstance(runtime, dict):
        raise StageContractError("model worker runtime profile is invalid")
    distribution = runtime.get("distribution")
    root = runtime.get("code_root_relative_to_home")
    python = runtime.get("python_relative_to_code_root")
    if not all(isinstance(value, str) and value for value in (distribution, root, python)):
        raise StageContractError("model worker runtime identity is invalid")
    if any(".." in Path(value).parts for value in (root, python)):
        raise StageContractError("model worker runtime path is invalid")
    return distribution, root, python


def _validated_profile_entrypoint(
    profile: dict[str, object],
    *,
    expected_device_policy_name: str | None,
) -> Path:
    entrypoint = profile.get("entrypoint")
    if not isinstance(entrypoint, dict) or set(entrypoint) != {
        "path",
        "sha256",
        "upstream_script",
        "device_policy",
    }:
        raise StageContractError("model entrypoint profile is invalid")
    relative = entrypoint.get("path")
    expected_digest = entrypoint.get("sha256")
    upstream_script = entrypoint.get("upstream_script")
    if (
        not isinstance(relative, str)
        or Path(relative).name != relative
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
        or upstream_script != "inference/scripts/inference_psd_quantized.py"
    ):
        raise StageContractError("model entrypoint identity is invalid")
    path = ENTRYPOINT_ROOT / relative
    exact = read_bounded_file(path, 256 * 1024)
    if sha256_bytes(exact) != expected_digest:
        raise StageContractError("model entrypoint digest mismatch")

    device_policy = entrypoint.get("device_policy")
    if not isinstance(device_policy, dict) or set(device_policy) != {"path", "sha256"}:
        raise StageContractError("model entrypoint device policy profile is invalid")
    policy_relative = device_policy.get("path")
    policy_digest = device_policy.get("sha256")
    if (
        not isinstance(policy_relative, str)
        or Path(policy_relative).name != policy_relative
        or not policy_relative
        or (expected_device_policy_name is not None and policy_relative != expected_device_policy_name)
        or not isinstance(policy_digest, str)
        or len(policy_digest) != 64
        or any(character not in "0123456789abcdef" for character in policy_digest)
    ):
        raise StageContractError("model entrypoint device policy identity is invalid")
    policy_exact = read_bounded_file(ENTRYPOINT_ROOT / policy_relative, 256 * 1024)
    if sha256_bytes(policy_exact) != policy_digest:
        raise StageContractError("model entrypoint device policy digest mismatch")
    return path


def _validated_entrypoint(profile: dict[str, object]) -> Path:
    return _validated_profile_entrypoint(
        profile,
        expected_device_policy_name=DEVICE_POLICY_PATH.name,
    )


def _validated_archived_entrypoint(profile: dict[str, object]) -> Path:
    return _validated_profile_entrypoint(profile, expected_device_policy_name=None)


def _run_checked(
    command: list[str],
    *,
    timeout: int,
    output_limit: int = 4096,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    streams = ((process.stdout, bytearray()), (process.stderr, bytearray()))
    exceeded = threading.Event()

    def drain(stream: object, buffer: bytearray) -> None:
        while True:
            chunk = stream.read(64 * 1024)  # type: ignore[attr-defined]
            if not chunk:
                return
            if len(buffer) + len(chunk) > output_limit:
                remaining = max(0, output_limit + 1 - len(buffer))
                buffer.extend(chunk[:remaining])
                exceeded.set()
                process.kill()
                return
            buffer.extend(chunk)

    threads = [threading.Thread(target=drain, args=item, daemon=True) for item in streams]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        for thread in threads:
            thread.join()
        for stream, _ in streams:
            stream.close()  # type: ignore[attr-defined]
        raise StageContractError("isolated model command timed out") from exc
    for thread in threads:
        thread.join()
    for stream, _ in streams:
        stream.close()  # type: ignore[attr-defined]
    if exceeded.is_set():
        raise StageContractError("isolated model command output exceeded its bound")
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout=bytes(streams[0][1]),
        stderr=bytes(streams[1][1]),
    )


def _runtime_versions(runtime: dict[str, object]) -> dict[str, str]:
    versions = runtime.get("versions")
    if not isinstance(versions, dict) or set(versions) != set(RUNTIME_PACKAGE_NAMES):
        raise StageContractError("model dependency identity is invalid")
    result: dict[str, str] = {}
    for name in RUNTIME_PACKAGE_NAMES:
        version = versions.get(name)
        if not isinstance(version, str) or not version:
            raise StageContractError("model dependency version is invalid")
        result[name] = version
    return result


def _verify_runtime(profile: dict[str, object], dependencies_sha256: str) -> None:
    distribution, root, python = _runtime(profile)
    runtime = profile.get("runtime")
    if not isinstance(runtime, dict):
        raise StageContractError("model worker runtime profile is invalid")
    if dependencies_sha256 != runtime.get("dependencies_sha256"):
        raise StageContractError("model dependency profile mismatch")
    expected = {
        "python": runtime.get("python_version"),
        "torch": runtime.get("torch_version"),
        "cuda": runtime.get("cuda_version"),
        "packages": _runtime_versions(runtime),
    }
    if not all(isinstance(expected[key], str) and expected[key] for key in ("python", "torch", "cuda")):
        raise StageContractError("model runtime version identity is invalid")
    command = [
        "wsl.exe",
        "-d",
        distribution,
        "--cd",
        f"~/{root}",
        "--",
        "env",
        f"ONECLICK2D_PACKAGE_NAMES={json.dumps(RUNTIME_PACKAGE_NAMES, separators=(',', ':'))}",
        f"./{python}",
        "-c",
        RUNTIME_ATTESTATION_CODE,
    ]
    completed = _run_checked(command, timeout=120, output_limit=64 * 1024)
    if completed.returncode != 0:
        raise StageContractError("model runtime attestation failed")
    try:
        actual = strict_load_json_bytes(completed.stdout)
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise StageContractError("model runtime attestation is invalid") from exc
    if not isinstance(actual, dict) or actual.get("cuda_available") is not True:
        raise StageContractError("model CUDA runtime is unavailable")
    for key, value in expected.items():
        if actual.get(key) != value:
            raise StageContractError("model runtime version mismatch")
    timm_direct_url = actual.get("timm_direct_url")
    if not isinstance(timm_direct_url, dict):
        raise StageContractError("model timm provenance is missing")
    vcs = timm_direct_url.get("vcs_info")
    if (
        timm_direct_url.get("url") != "https://github.com/huggingface/pytorch-image-models"
        or not isinstance(vcs, dict)
        or vcs.get("vcs") != "git"
        or vcs.get("commit_id") != runtime.get("timm_commit")
    ):
        raise StageContractError("model timm provenance mismatch")


def _verify_wsl_models(profile: dict[str, object]) -> None:
    distribution, root, _ = _runtime(profile)
    models = profile.get("models")
    if not isinstance(models, list) or len(models) != 3:
        raise StageContractError("model inventory is invalid")
    code = profile.get("code")
    if not isinstance(code, dict) or not isinstance(code.get("commit"), str):
        raise StageContractError("model code identity is invalid")
    revision = _run_checked(
        ["wsl.exe", "-d", distribution, "--cd", f"~/{root}", "--", "git", "rev-parse", "HEAD"],
        timeout=30,
    )
    if revision.returncode != 0 or revision.stdout.decode("ascii", errors="strict").strip() != code["commit"]:
        raise StageContractError("model code revision mismatch")
    tracked_status = _run_checked(
        [
            "wsl.exe",
            "-d",
            distribution,
            "--cd",
            f"~/{root}",
            "--",
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
        ],
        timeout=30,
        output_limit=64 * 1024,
    )
    if tracked_status.returncode != 0 or tracked_status.stdout.strip():
        raise StageContractError("model code checkout has tracked changes")

    entrypoint = profile.get("entrypoint")
    upstream_script = entrypoint.get("upstream_script") if isinstance(entrypoint, dict) else None
    if not isinstance(upstream_script, str) or upstream_script != "inference/scripts/inference_psd_quantized.py":
        raise StageContractError("model upstream entrypoint identity is invalid")
    committed_entrypoint = _run_checked(
        [
            "wsl.exe",
            "-d",
            distribution,
            "--cd",
            f"~/{root}",
            "--",
            "git",
            "rev-parse",
            f"{code['commit']}:{upstream_script}",
        ],
        timeout=30,
        output_limit=128,
    )
    actual_entrypoint = _run_checked(
        [
            "wsl.exe",
            "-d",
            distribution,
            "--cd",
            f"~/{root}",
            "--",
            "git",
            "hash-object",
            "--",
            upstream_script,
        ],
        timeout=30,
        output_limit=128,
    )
    try:
        committed_digest = committed_entrypoint.stdout.decode("ascii", errors="strict").strip()
        actual_digest = actual_entrypoint.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise StageContractError("model upstream entrypoint digest is invalid") from exc
    if (
        committed_entrypoint.returncode != 0
        or actual_entrypoint.returncode != 0
        or len(committed_digest) != 40
        or any(character not in "0123456789abcdef" for character in committed_digest)
        or actual_digest != committed_digest
    ):
        raise StageContractError("model upstream entrypoint does not match the pinned commit")
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("local_dir_relative_to_code_root"), str):
            raise StageContractError("model local inventory is invalid")
        local_dir = model["local_dir_relative_to_code_root"]
        configs = model.get("config_files")
        weights = model.get("weights")
        if not isinstance(configs, list) or not isinstance(weights, list):
            raise StageContractError("model file inventory is invalid")
        for descriptor in (*configs, *weights):
            if not isinstance(descriptor, dict):
                raise StageContractError("model file identity is invalid")
            relative = descriptor.get("path")
            if not isinstance(relative, str) or ".." in Path(relative).parts:
                raise StageContractError("model file path is invalid")
            path = f"{local_dir}/{relative}"
            if "sha256" in descriptor:
                digest_command = ["sha256sum", path]
                expected_digest = descriptor.get("sha256")
            else:
                digest_command = ["git", "hash-object", path]
                expected_digest = descriptor.get("git_blob_sha1")
            if not isinstance(expected_digest, str):
                raise StageContractError("model file digest is invalid")
            completed = _run_checked(
                ["wsl.exe", "-d", distribution, "--cd", f"~/{root}", "--", *digest_command],
                timeout=300,
            )
            if completed.returncode != 0:
                raise StageContractError("model file verification failed")
            digest = completed.stdout.decode("ascii", errors="strict").split(maxsplit=1)[0]
            if digest != expected_digest:
                raise StageContractError("model file digest mismatch")
            if "byte_length" not in descriptor:
                continue
            expected_length = descriptor.get("byte_length")
            if isinstance(expected_length, bool) or not isinstance(expected_length, int):
                raise StageContractError("model weight size identity is invalid")
            size = _run_checked(
                ["wsl.exe", "-d", distribution, "--cd", f"~/{root}", "--", "stat", "-c", "%s", path],
                timeout=30,
                output_limit=64,
            )
            if size.returncode != 0:
                raise StageContractError("model weight size verification failed")
            try:
                actual_length = int(size.stdout.decode("ascii", errors="strict").strip())
            except ValueError as exc:
                raise StageContractError("model weight size is invalid") from exc
            if actual_length != expected_length:
                raise StageContractError("model weight size mismatch")


def _invoke_wsl(
    source: Path,
    output: Path,
    profile: dict[str, object],
    timeout_seconds: int,
) -> tuple[subprocess.CompletedProcess[bytes], Mapping[str, object] | None]:
    distribution, root, python = _runtime(profile)
    _verify_wsl_models(profile)
    inference = _validated_inference(profile)
    _validated_postprocess(profile)
    entrypoint = _validated_entrypoint(profile)
    models = profile.get("models")
    if not isinstance(models, list) or len(models) != 3:
        raise StageContractError("model inference profile is invalid")
    local_models: dict[str, str] = {}
    for model in models:
        if not isinstance(model, dict):
            raise StageContractError("model identity is invalid")
        role = model.get("role")
        local_dir = model.get("local_dir_relative_to_code_root")
        if not isinstance(role, str) or not isinstance(local_dir, str) or ".." in Path(local_dir).parts:
            raise StageContractError("model local directory is invalid")
        if role in local_models:
            raise StageContractError("model role is duplicated")
        local_models[role] = local_dir
    if set(local_models) != {
        "scheduler_configuration",
        "semantic_layer_generation",
        "semantic_layer_depth",
    }:
        raise StageContractError("model role inventory is invalid")
    source_wsl = _wsl_path(source, distribution)
    attestation_challenge = secrets.token_hex(32)
    entrypoint_wsl = _wsl_path(entrypoint, distribution)
    inference_output = output / "input"
    inference_output.mkdir()
    output_wsl = _wsl_path(inference_output, distribution)
    attestation_path = inference_output / ".entrypoint-attestation.json"
    command = [
        "wsl.exe", "-d", distribution, "--cd", f"~/{root}", "--",
        f"./{python}", entrypoint_wsl,
        "--save_dir", output_wsl,
        "--save_to_psd",
        "--tblr_split",
        "--quant_mode", str(inference.get("quantization")),
        "--repo_id_layerdiff", local_models["semantic_layer_generation"],
        "--repo_id_depth", local_models["semantic_layer_depth"],
        "--seed", str(inference.get("seed")),
        "--resolution", str(inference.get("resolution")),
        "--resolution_depth", str(inference.get("depth_resolution")),
        "--num_inference_steps", str(inference.get("inference_steps")),
    ]
    if inference.get("cpu_offload") is True:
        command.append("--cpu_offload")
    if inference.get("group_offload") is True:
        command.append("--group_offload")
    else:
        command.append("--no_group_offload")
    command[command.index("--") + 1 : command.index("--") + 1] = [
        "env",
        "HF_HUB_DISABLE_TELEMETRY=1",
        "HF_HUB_DISABLE_PROGRESS_BARS=1",
        f"HF_HOME=~/{root}/models/hf-cache",
        "HF_HUB_OFFLINE=1",
        "TRANSFORMERS_OFFLINE=1",
        "TRANSFORMERS_NO_ADVISORY_WARNINGS=1",
        "DO_NOT_TRACK=1",
        f"PYTORCH_CUDA_ALLOC_CONF={inference.get('cuda_allocator')}",
    ]
    process_env = os.environ.copy()
    forwarded = {
        "ONECLICK2D_ENTRYPOINT_ATTESTATION": f"{output_wsl}/.entrypoint-attestation.json",
        "ONECLICK2D_ATTESTATION_CHALLENGE": attestation_challenge,
        "ONECLICK2D_ATTESTATION_SOURCE": source_wsl,
    }
    forwarded_names = set(forwarded)
    wslenv_entries = [
        entry
        for entry in process_env.get("WSLENV", "").split(":")
        if entry and entry.split("/", 1)[0] not in forwarded_names
    ]
    process_env.update(forwarded)
    process_env["WSLENV"] = ":".join([*wslenv_entries, *forwarded])
    completed = _run_checked(
        command,
        timeout=timeout_seconds,
        output_limit=MAX_MODEL_STDIO_BYTES,
        env=process_env,
    )
    attestation = (
        _consume_entrypoint_attestation(
            attestation_path,
            expected_challenge=attestation_challenge,
            source=source,
        )
        if completed.returncode == 0
        else None
    )
    return completed, attestation


_ENTRYPOINT_ATTESTATION_SUMMARY_KEYS = {
    "policy_id",
    "requested_cpu_offload",
    "execution_device",
    "components",
    "psd_pixel_projection_algorithm_id",
    "psd_projection_verified",
}


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validated_device_attestation_summary(
    value: object,
    *,
    expected_keys: set[str],
    policy_id: str,
    psd_projection_algorithm_id: str,
    component_dispositions: tuple[tuple[str, str], ...],
) -> Mapping[str, object]:
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_keys
        or value.get("policy_id") != policy_id
        or value.get("requested_cpu_offload") is not True
        or value.get("execution_device") != "cuda:0"
        or value.get("psd_pixel_projection_algorithm_id") != psd_projection_algorithm_id
        or value.get("psd_projection_verified") is not True
    ):
        raise StageContractError("model entrypoint attestation is invalid")
    execution_device = str(value["execution_device"])
    components = value.get("components")
    expected_dispositions = dict(component_dispositions)
    if not isinstance(components, Mapping) or set(components) != set(expected_dispositions):
        raise StageContractError("model entrypoint device attestation is invalid")
    frozen_components: dict[str, Mapping[str, object]] = {}
    for name, disposition in component_dispositions:
        component = components.get(name)
        if not isinstance(component, Mapping) or set(component) != {
            "storage_devices",
            "execution_hook_devices",
            "upstream_cuda_move_suppressed",
            "disposition",
        }:
            raise StageContractError("model entrypoint device attestation is invalid")
        storage = component.get("storage_devices")
        hooks = component.get("execution_hook_devices")
        if (
            not isinstance(storage, (list, tuple))
            or not isinstance(hooks, (list, tuple))
            or any(not isinstance(device, str) or not device for device in (*storage, *hooks))
            or list(storage) != sorted(set(storage))
            or list(hooks) != sorted(set(hooks))
            or not isinstance(component.get("upstream_cuda_move_suppressed"), bool)
            or component.get("disposition") != disposition
        ):
            raise StageContractError("model entrypoint device attestation is invalid")
        frozen_components[name] = MappingProxyType(
            {
                "storage_devices": tuple(storage),
                "execution_hook_devices": tuple(hooks),
                "upstream_cuda_move_suppressed": component["upstream_cuda_move_suppressed"],
                "disposition": disposition,
            }
        )
    vae = frozen_components["vae"]
    unet = frozen_components["unet"]
    text_encoder = frozen_components["text_encoder"]
    is_cuda = lambda device: device == "cuda" or device.startswith("cuda:")
    if (
        vae["upstream_cuda_move_suppressed"] is not True
        or any(is_cuda(device) for device in vae["storage_devices"])
        or not vae["execution_hook_devices"]
        or not all(device == execution_device for device in vae["execution_hook_devices"])
        or unet["upstream_cuda_move_suppressed"] is not True
        or not unet["storage_devices"]
        or not all(device == execution_device for device in unet["storage_devices"])
        or any(is_cuda(device) for device in text_encoder["storage_devices"])
    ):
        raise StageContractError("model entrypoint device attestation is invalid")
    return MappingProxyType(
        {
            "policy_id": policy_id,
            "requested_cpu_offload": True,
            "execution_device": execution_device,
            "components": MappingProxyType(frozen_components),
            "psd_pixel_projection_algorithm_id": psd_projection_algorithm_id,
            "psd_projection_verified": True,
        }
    )


def _validated_entrypoint_attestation_summary(value: object) -> Mapping[str, object]:
    summary = _validated_device_attestation_summary(
        value,
        expected_keys=_ENTRYPOINT_ATTESTATION_SUMMARY_KEYS | {"binding"},
        policy_id=NF4_MARIGOLD_DEVICE_POLICY_ID,
        psd_projection_algorithm_id=PSD_PIXEL_PROJECTION_ALGORITHM_ID,
        component_dispositions=ENTRYPOINT_COMPONENT_DISPOSITIONS,
    )
    if not isinstance(value, Mapping):
        raise StageContractError("model entrypoint attestation is invalid")
    binding = value.get("binding")
    if (
        not isinstance(binding, Mapping)
        or set(binding) != {"source_sha256", "artifact_manifest_digest"}
        or not _is_sha256(binding.get("source_sha256"))
        or not _is_sha256(binding.get("artifact_manifest_digest"))
    ):
        raise StageContractError("model entrypoint attestation binding is invalid")
    return MappingProxyType(
        {
            **summary,
            "binding": MappingProxyType(
                {
                    "source_sha256": binding["source_sha256"],
                    "artifact_manifest_digest": binding["artifact_manifest_digest"],
                }
            ),
        }
    )


def _validated_legacy_v4_entrypoint_attestation_summary(value: object) -> Mapping[str, object]:
    return _validated_device_attestation_summary(
        value,
        expected_keys=_ENTRYPOINT_ATTESTATION_SUMMARY_KEYS,
        policy_id=LEGACY_V4_NF4_MARIGOLD_DEVICE_POLICY_ID,
        psd_projection_algorithm_id=LEGACY_V4_PSD_PIXEL_PROJECTION_ALGORITHM_ID,
        component_dispositions=LEGACY_V4_ENTRYPOINT_COMPONENT_DISPOSITIONS,
    )


def _entrypoint_attestation_dict_from_summary(
    summary: Mapping[str, object],
    *,
    component_dispositions: tuple[tuple[str, str], ...],
) -> dict[str, object]:
    components = summary["components"]
    if not isinstance(components, Mapping):
        raise StageContractError("model entrypoint device attestation is invalid")
    return {
        "policy_id": summary["policy_id"],
        "requested_cpu_offload": summary["requested_cpu_offload"],
        "execution_device": summary["execution_device"],
        "components": {
            name: {
                "storage_devices": list(components[name]["storage_devices"]),
                "execution_hook_devices": list(components[name]["execution_hook_devices"]),
                "upstream_cuda_move_suppressed": components[name]["upstream_cuda_move_suppressed"],
                "disposition": components[name]["disposition"],
            }
            for name, _ in component_dispositions
        },
        "psd_pixel_projection_algorithm_id": summary["psd_pixel_projection_algorithm_id"],
        "psd_projection_verified": summary["psd_projection_verified"],
    }


def _entrypoint_attestation_dict(value: object) -> dict[str, object]:
    summary = _validated_entrypoint_attestation_summary(value)
    result = _entrypoint_attestation_dict_from_summary(
        summary,
        component_dispositions=ENTRYPOINT_COMPONENT_DISPOSITIONS,
    )
    binding = summary["binding"]
    if not isinstance(binding, Mapping):
        raise StageContractError("model entrypoint attestation binding is invalid")
    result["binding"] = {
        "source_sha256": binding["source_sha256"],
        "artifact_manifest_digest": binding["artifact_manifest_digest"],
    }
    return result


def _legacy_v4_entrypoint_attestation_dict(value: object) -> dict[str, object]:
    return _entrypoint_attestation_dict_from_summary(
        _validated_legacy_v4_entrypoint_attestation_summary(value),
        component_dispositions=LEGACY_V4_ENTRYPOINT_COMPONENT_DISPOSITIONS,
    )


def _validated_declared_artifact_manifest(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise StageContractError("model entrypoint artifact manifest is invalid")
    manifest: list[dict[str, object]] = []
    previous_path: str | None = None
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "byte_length"}:
            raise StageContractError("model entrypoint artifact manifest is invalid")
        relative = item.get("path")
        byte_length = item.get("byte_length")
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or Path(relative).is_absolute()
            or "." in Path(relative).parts
            or ".." in Path(relative).parts
            or Path(relative).as_posix() != relative
            or (previous_path is not None and relative <= previous_path)
            or not _is_sha256(item.get("sha256"))
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length < 0
        ):
            raise StageContractError("model entrypoint artifact manifest is invalid")
        manifest.append(
            {
                "path": relative,
                "sha256": item["sha256"],
                "byte_length": byte_length,
            }
        )
        previous_path = relative
    if not any(str(item["path"]).endswith(".psd") for item in manifest):
        raise StageContractError("model entrypoint artifact manifest is invalid")
    return manifest


def _bounded_artifact_files(
    root: Path,
    excluded_path: Path | None = None,
) -> list[tuple[str, Path, int]]:
    if root.is_symlink() or not root.is_dir():
        raise StageContractError("model entrypoint artifact manifest root is invalid")
    files: list[tuple[str, Path, int]] = []
    directories = 0
    nodes = 0
    stack: list[tuple[Path, int]] = [(root, 0)]
    try:
        while stack:
            directory, depth = stack.pop()
            if depth > MAX_MODEL_ARTIFACT_MANIFEST_DEPTH:
                raise StageContractError("model entrypoint artifact manifest depth exceeded its bound")
            with os.scandir(directory) as entries:
                for entry in entries:
                    candidate = Path(entry.path)
                    if excluded_path is not None and candidate == excluded_path:
                        continue
                    nodes += 1
                    if nodes > MAX_MODEL_ARTIFACT_MANIFEST_NODES:
                        raise StageContractError(
                            "model entrypoint artifact manifest node count exceeded its bound"
                        )
                    relative = candidate.relative_to(root).as_posix()
                    if len(relative.encode("utf-8")) > MAX_MODEL_ARTIFACT_RELATIVE_PATH_BYTES:
                        raise StageContractError(
                            "model entrypoint artifact manifest path length exceeded its bound"
                        )
                    if entry.is_symlink():
                        raise StageContractError("model entrypoint artifact manifest contains a symlink")
                    if entry.is_dir(follow_symlinks=False):
                        directories += 1
                        if directories > MAX_MODEL_ARTIFACT_MANIFEST_DIRECTORIES:
                            raise StageContractError(
                                "model entrypoint artifact manifest directory count exceeded its bound"
                            )
                        stack.append((candidate, depth + 1))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        raise StageContractError(
                            "model entrypoint artifact manifest contains a non-regular node"
                        )
                    if len(files) >= MAX_MODEL_ARTIFACT_MANIFEST_ENTRIES:
                        raise StageContractError(
                            "model entrypoint artifact manifest entry count exceeded its bound"
                        )
                    size = entry.stat(follow_symlinks=False).st_size
                    if size < 0:
                        raise StageContractError(
                            "model entrypoint artifact manifest file size is invalid"
                        )
                    files.append((relative, candidate, size))
    except OSError as exc:
        raise StageContractError("model entrypoint artifact manifest could not be enumerated") from exc
    return sorted(files, key=lambda item: item[0])


def _artifact_manifest(root: Path, attestation_path: Path) -> list[dict[str, object]]:
    manifest: list[dict[str, object]] = []
    total = 0
    for relative, candidate, size in _bounded_artifact_files(root, attestation_path):
        if size > MAX_MODEL_RESULT_BYTES - total:
            raise StageContractError(
                "model entrypoint artifact manifest byte count exceeded its bound"
            )
        try:
            exact = read_bounded_file(candidate, MAX_MODEL_RESULT_BYTES - total)
        except (OSError, ValueError) as exc:
            raise StageContractError("model entrypoint artifact manifest file is unreadable") from exc
        if len(exact) != size:
            raise StageContractError("model entrypoint artifact manifest file changed while hashing")
        total += len(exact)
        manifest.append(
            {
                "path": relative,
                "sha256": sha256_bytes(exact),
                "byte_length": len(exact),
            }
        )
    if not any(str(item["path"]).endswith(".psd") for item in manifest):
        raise StageContractError("model entrypoint artifact manifest contains no PSD")
    return manifest


def _artifact_manifest_digest(manifest: list[dict[str, object]]) -> str:
    exact = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(exact)


def _consume_entrypoint_attestation(
    path: Path,
    *,
    expected_challenge: str,
    source: Path,
) -> Mapping[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise StageContractError("model entrypoint attestation is missing")
        try:
            value = strict_load_json_bytes(read_bounded_file(path, 64 * 1024))
        except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise StageContractError("model entrypoint attestation is invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "format",
                "format_version",
                "policy_id",
                "requested_cpu_offload",
                "execution_device",
                "components",
                "psd_pixel_projection_algorithm_id",
                "psd_projection_verified",
                "binding",
            }
            or value.get("format") != "oneclick2d.model-entrypoint-attestation"
            or value.get("format_version") != "0.1.0"
            or value.get("policy_id") != NF4_MARIGOLD_DEVICE_POLICY_ID
            or value.get("requested_cpu_offload") is not True
            or value.get("execution_device") != "cuda:0"
            or value.get("psd_pixel_projection_algorithm_id") != PSD_PIXEL_PROJECTION_ALGORITHM_ID
            or value.get("psd_projection_verified") is not True
        ):
            raise StageContractError("model entrypoint attestation is invalid")
        binding = value.get("binding")
        if (
            not isinstance(binding, dict)
            or set(binding)
            != {
                "challenge",
                "source_sha256",
                "artifact_manifest_digest",
                "artifact_manifest",
            }
            or not isinstance(binding.get("challenge"), str)
            or len(str(binding["challenge"])) != 64
            or any(character not in "0123456789abcdef" for character in str(binding["challenge"]))
            or not _is_sha256(binding.get("source_sha256"))
            or not _is_sha256(binding.get("artifact_manifest_digest"))
        ):
            raise StageContractError("model entrypoint attestation binding is invalid")
        if not hmac.compare_digest(str(binding["challenge"]), expected_challenge):
            raise StageContractError("model entrypoint attestation challenge mismatch")
        try:
            worker_source_sha256 = sha256_bytes(read_bounded_file(source, 25 * 1024 * 1024))
        except (OSError, ValueError) as exc:
            raise StageContractError("model entrypoint attestation source verification failed") from exc
        if not hmac.compare_digest(str(binding["source_sha256"]), worker_source_sha256):
            raise StageContractError("model entrypoint attestation source digest mismatch")
        declared_manifest = _validated_declared_artifact_manifest(binding["artifact_manifest"])
        actual_manifest = _artifact_manifest(path.parent, path)
        if declared_manifest != actual_manifest:
            raise StageContractError(
                "model entrypoint artifact manifest changed after entrypoint attestation"
            )
        actual_manifest_digest = _artifact_manifest_digest(actual_manifest)
        if not hmac.compare_digest(
            str(binding["artifact_manifest_digest"]),
            actual_manifest_digest,
        ):
            raise StageContractError("model entrypoint artifact manifest digest mismatch")
        return _validated_entrypoint_attestation_summary(
            {
                "policy_id": value["policy_id"],
                "requested_cpu_offload": value["requested_cpu_offload"],
                "execution_device": value["execution_device"],
                "components": value["components"],
                "psd_pixel_projection_algorithm_id": value["psd_pixel_projection_algorithm_id"],
                "psd_projection_verified": value["psd_projection_verified"],
                "binding": {
                    "source_sha256": binding["source_sha256"],
                    "artifact_manifest_digest": binding["artifact_manifest_digest"],
                },
            }
        )
    finally:
        path.unlink(missing_ok=True)


def _validated_inference(profile: dict[str, object]) -> dict[str, object]:
    inference = profile.get("inference")
    keys = {
        "quantization",
        "seed",
        "resolution",
        "depth_resolution",
        "inference_steps",
        "cpu_offload",
        "group_offload",
        "cuda_allocator",
        "left_right_split",
    }
    if not isinstance(inference, dict) or set(inference) != keys:
        raise StageContractError("model inference profile is invalid")
    if (
        inference["quantization"] != "nf4"
        or isinstance(inference["seed"], bool)
        or not isinstance(inference["seed"], int)
        or not 0 <= inference["seed"] <= 2**63 - 1
        or inference["resolution"] != 1280
        or inference["depth_resolution"] != 768
        or inference["inference_steps"] != 30
        or inference["cpu_offload"] is not True
        or inference["group_offload"] is not False
        or inference["cuda_allocator"] != "expandable_segments:True"
        or inference["left_right_split"] is not True
    ):
        raise StageContractError("model inference settings are invalid")
    return inference


def _validated_postprocess(profile: dict[str, object]) -> dict[str, object]:
    postprocess = profile.get("postprocess")
    if (
        not isinstance(postprocess, dict)
        or set(postprocess) != {"algorithm_id", "visible_alpha_threshold", "neutral_reconstruction"}
        or postprocess.get("algorithm_id") != SOURCE_PRESERVE_ALGORITHM_ID
        or postprocess.get("visible_alpha_threshold") != 31
        or postprocess.get("neutral_reconstruction") != "source-rgb-with-max-cleaned-semantic-alpha"
    ):
        raise StageContractError("model postprocess profile is invalid")
    return postprocess


def _strict_model_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = strict_load_json_bytes(read_bounded_file(path))
    except (OSError, ValueError, TypeError) as exc:
        raise StageContractError(f"model worker {label} is invalid") from exc
    if not isinstance(value, dict):
        raise StageContractError(f"model worker {label} is invalid")
    return value


def _validated_png(
    path: Path,
    *,
    expected_mode: str,
    expected_size: tuple[int, int],
    require_alpha: bool = False,
) -> None:
    from PIL import Image

    try:
        exact = read_bounded_file(path, MAX_MODEL_PNG_BYTES)
        with Image.open(io.BytesIO(exact), formats=("PNG",)) as image:
            if (
                image.format != "PNG"
                or image.mode != expected_mode
                or image.size != expected_size
                or getattr(image, "n_frames", 1) != 1
            ):
                raise StageContractError("model worker PNG is outside the fixed output profile")
            image.load()
            if require_alpha:
                extrema = image.getextrema()
                if not isinstance(extrema, tuple) or len(extrema) != 4 or extrema[3][1] == 0:
                    raise StageContractError("model worker required semantic layer is empty")
    except StageContractError:
        raise
    except Exception as exc:
        raise StageContractError("model worker PNG is invalid") from exc


def _expected_output_uris() -> set[str]:
    semantic_root = "input/input"
    return {
        *(f"{semantic_root}/{name}.png" for name in MODEL_SEMANTIC_NAMES),
        *(f"{semantic_root}/{name}_depth.png" for name in MODEL_PART_NAMES),
        f"{semantic_root}/reconstruction.png",
        f"{semantic_root}/src_head.png",
        f"{semantic_root}/src_img.png",
        f"{semantic_root}/info.json",
        f"{semantic_root}/stats.json",
        "input/input.psd",
        "input/input.psd.json",
        "input/input_depth.psd",
    }


def _validate_model_output(directory: Path, profile: dict[str, object]) -> tuple[ModelPsdStructure, ModelPsdStructure]:
    inference = _validated_inference(profile)
    resolution = inference["resolution"]
    if isinstance(resolution, bool) or not isinstance(resolution, int):
        raise StageContractError("model worker output resolution is invalid")
    expected_size = (resolution, resolution)
    result_root = directory / "input"
    semantic_root = result_root / "input"
    if (
        result_root.is_symlink()
        or semantic_root.is_symlink()
        or not result_root.is_dir()
        or not semantic_root.is_dir()
    ):
        raise StageContractError("model worker output layout is invalid")

    info = _strict_model_json(semantic_root / "info.json", "semantic metadata")
    parts = info.get("parts")
    if (
        set(info) != {"parts"}
        or not isinstance(parts, dict)
        or tuple(parts) != MODEL_PART_NAMES
        or any(value != {} for value in parts.values())
    ):
        raise StageContractError("model worker semantic ontology is invalid")

    _validated_png(
        semantic_root / "reconstruction.png",
        expected_mode="RGBA",
        expected_size=expected_size,
        require_alpha=True,
    )
    _validated_png(
        semantic_root / "src_img.png",
        expected_mode="RGBA",
        expected_size=expected_size,
        require_alpha=True,
    )
    _validated_png(
        semantic_root / "src_head.png",
        expected_mode="RGBA",
        expected_size=expected_size,
        require_alpha=True,
    )
    for name in MODEL_SEMANTIC_NAMES:
        _validated_png(
            semantic_root / f"{name}.png",
            expected_mode="RGBA",
            expected_size=expected_size,
            require_alpha=name in {"face", "head", "mouth"},
        )
    for name in MODEL_PART_NAMES:
        _validated_png(
            semantic_root / f"{name}_depth.png",
            expected_mode="L",
            expected_size=expected_size,
        )

    stats = _strict_model_json(semantic_root / "stats.json", "statistics")
    if set(stats) != {"quant_mode", *MODEL_STATS_KEYS} or stats.get("quant_mode") != inference["quantization"]:
        raise StageContractError("model worker statistics are invalid")
    for key in MODEL_STATS_KEYS:
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise StageContractError("model worker statistics are invalid")
    if float(stats["total_time_s"]) < sum(
        float(stats[key]) for key in ("layerdiff_time_s", "marigold_time_s", "psd_time_s")
    ):
        raise StageContractError("model worker statistics are invalid")

    try:
        main_psd = validate_model_psd(result_root / "input.psd")
    except (OSError, ValueError, TypeError, StageContractError) as exc:
        raise StageContractError("model worker PSD is invalid") from exc
    try:
        depth_psd = validate_model_psd(result_root / "input_depth.psd", profile="grayscale")
    except (OSError, ValueError, TypeError, StageContractError) as exc:
        raise StageContractError("model worker depth PSD is invalid") from exc
    if (main_psd.width, main_psd.height) != expected_size or (depth_psd.width, depth_psd.height) != expected_size:
        raise StageContractError("model worker PSD canvas does not match the fixed output profile")
    main_layers = tuple((layer.name, layer.top, layer.left, layer.bottom, layer.right) for layer in main_psd.layers)
    depth_layers = tuple((layer.name, layer.top, layer.left, layer.bottom, layer.right) for layer in depth_psd.layers)
    if main_layers != depth_layers:
        raise StageContractError("model worker PSD semantic structures do not match")
    allowed_psd_names = set(MODEL_SEMANTIC_NAMES)
    allowed_psd_names.update(f"{name}-{side}" for name in MODEL_PART_NAMES for side in ("l", "r"))
    if any(layer.name not in allowed_psd_names for layer in main_psd.layers):
        raise StageContractError("model worker PSD semantic name is outside the fixed ontology")

    psd_metadata = _strict_model_json(result_root / "input.psd.json", "PSD metadata")
    psd_parts = psd_metadata.get("parts")
    if (
        set(psd_metadata) != {"parts", "frame_size"}
        or psd_metadata.get("frame_size") != [resolution, resolution]
        or not isinstance(psd_parts, dict)
        or set(psd_parts) != {layer.name for layer in main_psd.layers}
        or any(not isinstance(value, dict) for value in psd_parts.values())
    ):
        raise StageContractError("model worker PSD metadata is invalid")
    for layer in main_psd.layers:
        value = psd_parts[layer.name]
        base_name = layer.name[:-2] if layer.name.endswith(("-l", "-r")) else layer.name
        median = value.get("depth_median")
        part_id = value.get("part_id")
        if (
            not {"xyxy", "tag", "depth_median"}.issubset(value)
            or not set(value).issubset({"xyxy", "tag", "depth_median", "part_id"})
            or value.get("tag") != layer.name
            or base_name not in MODEL_PART_NAMES
            or value.get("xyxy") != [layer.left, layer.top, layer.right, layer.bottom]
            or isinstance(median, bool)
            or not isinstance(median, (int, float))
            or not math.isfinite(float(median))
            or not 0 <= float(median) <= 1
            or (part_id is not None and (isinstance(part_id, bool) or not isinstance(part_id, int) or part_id < 0))
        ):
            raise StageContractError("model worker PSD metadata is invalid")
    return main_psd, depth_psd


def _inventory(directory: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    total = 0
    for relative, path, size in _bounded_artifact_files(directory):
        if size > MAX_MODEL_RESULT_BYTES - total:
            raise StageContractError("model worker result exceeded its bound")
        try:
            exact = read_bounded_file(path, MAX_MODEL_RESULT_BYTES - total)
        except (OSError, ValueError) as exc:
            raise StageContractError("model worker output file is unreadable") from exc
        if len(exact) != size:
            raise StageContractError("model worker output file changed while hashing")
        total += len(exact)
        files.append({
            "uri": relative,
            "byte_length": len(exact),
            "sha256": sha256_bytes(exact),
        })
    if not files:
        raise StageContractError("model worker produced no files")
    return files


def run_model_worker(source: Path, output_root: Path, *, timeout_seconds: int = 1800) -> dict[str, object]:
    if source.is_symlink() or not source.is_file():
        raise StageContractError("model worker source is invalid")
    source_bytes = read_bounded_file(source, 25 * 1024 * 1024)
    source_sha256 = sha256_bytes(source_bytes)
    profile, profile_bytes = _load_profile()
    runtime = profile.get("runtime")
    if not isinstance(runtime, dict):
        raise StageContractError("model worker runtime profile is invalid")
    dependencies_profile = runtime.get("dependencies_profile")
    if (
        not isinstance(dependencies_profile, str)
        or Path(dependencies_profile).name != dependencies_profile
        or not dependencies_profile
    ):
        raise StageContractError("model dependency profile path is invalid")
    dependencies_bytes = read_bounded_file(PROFILE_ROOT / dependencies_profile, 256 * 1024)
    dependencies_sha256 = sha256_bytes(dependencies_bytes)
    _verify_runtime(profile, dependencies_sha256)
    output_root.mkdir(parents=True, exist_ok=False)
    local_source = Path(tempfile.mkdtemp(prefix="source-", dir=output_root.parent)) / "input.png"
    local_source.write_bytes(source_bytes)
    try:
        completed, entrypoint_attestation = _invoke_wsl(local_source, output_root, profile, timeout_seconds)
    finally:
        shutil.rmtree(local_source.parent, ignore_errors=True)
    if completed.returncode != 0:
        raise StageContractError("model worker process failed")
    candidates = [item for item in output_root.iterdir() if item.is_dir() and not item.is_symlink()]
    if len(candidates) != 1 or candidates[0].name != "input":
        raise StageContractError("model worker output identity is ambiguous")
    validated_psd, validated_depth_psd = _validate_model_output(output_root, profile)
    files = _inventory(output_root)
    indexed = {str(item["uri"]): item for item in files}
    if set(indexed) != _expected_output_uris():
        raise StageContractError("model worker output inventory does not match the fixed profile")
    attestation = _entrypoint_attestation_dict(entrypoint_attestation)
    binding = attestation["binding"]
    if not hmac.compare_digest(str(binding["source_sha256"]), source_sha256):
        raise StageContractError("model worker entrypoint source binding does not match")
    published_manifest = _artifact_manifest(
        candidates[0],
        candidates[0] / ".entrypoint-attestation.json",
    )
    published_manifest_digest = _artifact_manifest_digest(published_manifest)
    if not hmac.compare_digest(
        str(binding["artifact_manifest_digest"]),
        published_manifest_digest,
    ):
        raise StageContractError("model worker published artifact manifest digest mismatch")
    for uri, structure in (
        ("input/input.psd", validated_psd),
        ("input/input_depth.psd", validated_depth_psd),
    ):
        descriptor = indexed[uri]
        if descriptor["byte_length"] != structure.byte_length or descriptor["sha256"] != structure.sha256:
            raise StageContractError("model worker PSD changed during publication")
    return {
        "format": "oneclick2d.model-worker-result",
        "format_version": "0.1.0",
        "scope": "disposable-local-model-spike",
        "state": "completed",
        "profile_id": PROFILE_ID,
        "profile_sha256": sha256_bytes(profile_bytes),
        "dependencies_sha256": dependencies_sha256,
        "source_sha256": source_sha256,
        "model_used": True,
        "oc2d_produced": False,
        "gate_f_status": "GATE_F_NOT_EVALUATED",
        "entrypoint_attestation": attestation,
        "files": files,
        "psd": indexed["input/input.psd"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.timeout_seconds <= 3600 or args.result.exists():
        return 2
    try:
        result = run_model_worker(args.source, args.output, timeout_seconds=args.timeout_seconds)
        args.result.write_bytes(canonical_json_bytes(result))
    except (OSError, ValueError, TypeError, StageContractError):
        if args.output.exists():
            shutil.rmtree(args.output, ignore_errors=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
