"""Command-line entrypoint for the disposable Gate F runner."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .contracts import SpecValidationError, StageContractError, StageStatus
from .runner import PipelineRunner
from .runtime import (
    ID_RE,
    contained_run_path,
    create_regular_run_file,
    read_bounded_file,
    require_regular_workspace_root,
)
from .synthetic import build_synthetic_registry

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = ROOT / "examples" / "gate-f-spike-smoke"
DEFAULT_WORKSPACE = ROOT / "workspaces" / "gate-f-spike"
EXIT_CODES = {
    StageStatus.SUCCEEDED: 0,
    StageStatus.REVIEW: 10,
    StageStatus.FALLBACK: 11,
    StageStatus.BLOCKED: 20,
    StageStatus.FAILED: 70,
    StageStatus.CANCELLED: 130,
}
WINDOWS_SOURCE_PATH_REQUIRES_WINDOWS_HOST_SHELL = "WINDOWS_SOURCE_PATH_REQUIRES_WINDOWS_HOST_SHELL"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8"


def _is_windows_drive_absolute_path(path: Path) -> bool:
    value = str(path)
    return (
        len(value) >= 3
        and value[0].isascii()
        and value[0].isalpha()
        and value[1] == ":"
        and value[2] in ("/", "\\")
    )


def _model_source_media_type(source: bytes) -> str:
    if source.startswith(PNG_SIGNATURE):
        return "image/png"
    if source.startswith(JPEG_SIGNATURE):
        return "image/jpeg"
    raise ValueError("unsupported model source container")


def _run(args: argparse.Namespace) -> int:
    runner = PipelineRunner(args.registry_factory(), args.workspace_root)
    try:
        status, manifest = runner.run(
            spec_path=args.spec,
            source_path=args.source,
            run_id=args.run_id,
            source_revision=args.source_revision,
            build_id=args.build_id,
        )
    except SpecValidationError:
        print("run rejected: invalid specification or input", file=sys.stderr)
        return 64
    except (OSError, ValueError, TypeError, RecursionError):
        print("run failed: local runner error", file=sys.stderr)
        return 70
    print(f"run_id={args.run_id} status={status.value} manifest={manifest.parent.name}/run-manifest.json")
    return EXIT_CODES[status]


def _preflight(args: argparse.Namespace) -> int:
    from .local_preflight import run_local_preflight

    try:
        index, report = run_local_preflight(args.workspace_root, args.run_id)
    except (OSError, ValueError, TypeError, StageContractError):
        print("preflight failed: local technical preflight error", file=sys.stderr)
        return 70
    print(
        f"run_id={args.run_id} status={report['local_technical_preflight_status']} "
        f"gate_f={report['gate_f_status']} bundle={index.parent.name}/bundle-index.json"
    )
    return 0 if report["local_technical_preflight_status"] == "LOCAL_TECHNICAL_PREFLIGHT_PASS" else 70


def _verify_bundle(args: argparse.Namespace) -> int:
    from .acceptance import verify_bundle

    try:
        report = verify_bundle(args.bundle)
    except (OSError, ValueError, TypeError, LookupError, StageContractError):
        print("bundle verification failed", file=sys.stderr)
        return 70
    print(f"bundle={args.bundle.name} status={report['local_technical_preflight_status']} gate_f={report['gate_f_status']}")
    return 0 if report["local_technical_preflight_status"] == "LOCAL_TECHNICAL_PREFLIGHT_PASS" else 70


def _gui(args: argparse.Namespace) -> int:
    from .gui_server import serve_gui

    try:
        serve_gui(args.host, args.port, args.workspace_root, open_browser=not args.no_open)
    except (OSError, ValueError, StageContractError):
        print("gui failed: local server could not start", file=sys.stderr)
        return 70
    return 0


def _model(args: argparse.Namespace) -> int:
    if not ID_RE.fullmatch(args.run_id) or not 1 <= args.timeout_seconds <= 3600:
        print("model spike rejected: invalid run or timeout", file=sys.stderr)
        return 64
    if os.name == "posix" and _is_windows_drive_absolute_path(args.source):
        print(WINDOWS_SOURCE_PATH_REQUIRES_WINDOWS_HOST_SHELL, file=sys.stderr)
        return 64

    from .model_workbench import MAX_SOURCE_BYTES, run_normalized_model_workbench
    from .model_worker import run_model_worker

    try:
        workspace_root = require_regular_workspace_root(args.workspace_root, create=True)
        run_dir = workspace_root / args.run_id
        if run_dir.exists() or run_dir.is_symlink():
            print("model spike rejected: invalid or existing run", file=sys.stderr)
            return 64
        source_bytes = read_bounded_file(args.source, MAX_SOURCE_BYTES)
        media_type = _model_source_media_type(source_bytes)
    except FileExistsError:
        print("model spike rejected: invalid or existing run", file=sys.stderr)
        return 64
    except (OSError, ValueError):
        print("model spike failed: local model worker error", file=sys.stderr)
        return 70
    try:
        _, _, result = run_normalized_model_workbench(
            workspace_root,
            args.run_id,
            source_bytes,
            media_type,
            run_model_worker,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, ValueError, TypeError, SpecValidationError, StageContractError):
        shutil.rmtree(run_dir, ignore_errors=True)
        print("model spike failed: local model worker error", file=sys.stderr)
        return 70
    print(
        f"run_id={args.run_id} status=LOCAL_MODEL_SPIKE_COMPLETED "
        f"model={result['profile_id']} gate_f={result['gate_f_status']}"
    )
    return 0


def _motion(args: argparse.Namespace) -> int:
    from .model_motion_draft import generate_model_motion_draft

    if not ID_RE.fullmatch(args.run_id):
        print("motion draft rejected: invalid run id", file=sys.stderr)
        return 64
    try:
        _, report = generate_model_motion_draft(args.workspace_root / args.run_id)
    except (OSError, ValueError, TypeError, StageContractError):
        print("motion draft failed: validated model motion error", file=sys.stderr)
        return 70
    print(
        f"run_id={args.run_id} status=LOCAL_MODEL_MOTION_DRAFT_COMPLETED "
        f"frames={len(report['frames'])} gate_f=GATE_F_NOT_EVALUATED"
    )
    return 0


def _diagnose_fidelity(args: argparse.Namespace) -> int:
    from .model_fidelity_diagnosis import diagnose_model_fidelity

    if not ID_RE.fullmatch(args.run_id):
        print("fidelity diagnosis rejected: invalid run id", file=sys.stderr)
        return 64
    try:
        run_dir = contained_run_path(args.workspace_root, args.run_id, kind="directory")
        report = diagnose_model_fidelity(run_dir)
    except (OSError, ValueError, TypeError, StageContractError):
        print("fidelity diagnosis failed: local diagnosis error", file=sys.stderr)
        return 70
    print(json.dumps(report, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


def _model_candidate(args: argparse.Namespace) -> int:
    from .model_candidate import generate_model_candidate_preflight

    if not ID_RE.fullmatch(args.run_id):
        print("model candidate rejected: invalid run id", file=sys.stderr)
        return 64
    try:
        _, report = generate_model_candidate_preflight(args.workspace_root / args.run_id)
    except (OSError, ValueError, TypeError, LookupError, StageContractError):
        print("model candidate failed: validated local preflight error", file=sys.stderr)
        return 70
    print(
        f"run_id={args.run_id} status={report['local_status']} "
        f"gate_f={report['gate_f_status']}"
    )
    return 0


def _verify_model_candidate(args: argparse.Namespace) -> int:
    from .model_candidate import load_model_candidate_preflight_report

    if not ID_RE.fullmatch(args.run_id):
        print("model candidate verification rejected: invalid run id", file=sys.stderr)
        return 64
    try:
        report = load_model_candidate_preflight_report(args.workspace_root / args.run_id)
    except (OSError, ValueError, TypeError, LookupError, StageContractError):
        print("model candidate verification failed", file=sys.stderr)
        return 70
    print(
        f"run_id={args.run_id} status={report['local_status']} "
        f"gate_f={report['gate_f_status']}"
    )
    return 0


def _cancel(args: argparse.Namespace) -> int:
    if not ID_RE.fullmatch(args.run_id):
        print("cancel rejected: invalid run id", file=sys.stderr)
        return 64
    try:
        create_regular_run_file(args.workspace_root, args.run_id, "cancel.request")
    except ValueError:
        print("cancel rejected: unknown or unsafe run", file=sys.stderr)
        return 64
    except OSError:
        print("cancel failed: local runner error", file=sys.stderr)
        return 70
    print(f"run_id={args.run_id} cancel=requested")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    smoke = subparsers.add_parser("smoke", help="run the purpose-created orchestration smoke fixture")
    smoke.add_argument("--run-id", required=True)
    smoke.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    smoke.set_defaults(
        func=_run,
        spec=DEFAULT_FIXTURE / "run-spec.json",
        source=DEFAULT_FIXTURE / "source.synthetic.json",
        source_revision="source.local-smoke",
        build_id="build.local-smoke",
        registry_factory=build_synthetic_registry,
    )

    run = subparsers.add_parser("run", help="run a specification using built-in synthetic adapters only")
    run.add_argument("--spec", type=Path, required=True)
    run.add_argument("--source", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--source-revision", required=True)
    run.add_argument("--build-id", required=True)
    run.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    run.set_defaults(func=_run, registry_factory=build_synthetic_registry)

    raster = subparsers.add_parser("raster", help="run the Pillow 12.1.0 raster spike adapters")
    raster.add_argument("--spec", type=Path, required=True)
    raster.add_argument("--source", type=Path, required=True)
    raster.add_argument("--run-id", required=True)
    raster.add_argument("--source-revision", required=True)
    raster.add_argument("--build-id", required=True)
    raster.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    from .candidate_baseline import build_gate_f_registry

    raster.set_defaults(func=_run, registry_factory=build_gate_f_registry)

    preflight = subparsers.add_parser("preflight", help="run the purpose-created local Gate F technical preflight")
    preflight.add_argument("--run-id", required=True)
    preflight.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    preflight.set_defaults(func=_preflight)

    verify = subparsers.add_parser("verify-bundle", help="verify a local technical-preflight bundle")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.set_defaults(func=_verify_bundle)

    gui = subparsers.add_parser("gui", help="serve the loopback-only local preflight workbench")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    gui.add_argument("--no-open", action="store_true")
    gui.set_defaults(func=_gui)

    model = subparsers.add_parser(
        "model",
        help="run the pinned host-neutral v6 model spike with the native Linux worker (no isolation; host-local only)",
    )
    model.add_argument("--source", type=Path, required=True)
    model.add_argument("--run-id", required=True)
    model.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    model.add_argument("--timeout-seconds", type=int, default=1800)
    model.set_defaults(func=_model)

    motion = subparsers.add_parser("motion", help="generate a deterministic motion draft for a validated model run")
    motion.add_argument("--run-id", required=True)
    motion.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    motion.set_defaults(func=_motion)

    diagnose_fidelity = subparsers.add_parser(
        "diagnose-fidelity",
        help="diagnose H1/H2 neutral-fidelity omissions without changing a completed model run",
    )
    diagnose_fidelity.add_argument("--run-id", required=True)
    diagnose_fidelity.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    diagnose_fidelity.set_defaults(func=_diagnose_fidelity)

    model_candidate = subparsers.add_parser(
        "model-candidate",
        help="generate a single-item preflight for a validated model and motion run",
    )
    model_candidate.add_argument("--run-id", required=True)
    model_candidate.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    model_candidate.set_defaults(func=_model_candidate)

    verify_model_candidate = subparsers.add_parser(
        "verify-model-candidate",
        help="verify a published single-item model candidate preflight",
    )
    verify_model_candidate.add_argument("--run-id", required=True)
    verify_model_candidate.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    verify_model_candidate.set_defaults(func=_verify_model_candidate)

    cancel = subparsers.add_parser("cancel", help="request cooperative cancellation")
    cancel.add_argument("--run-id", required=True)
    cancel.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    cancel.set_defaults(func=_cancel)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
