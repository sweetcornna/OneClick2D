"""Command-line entrypoint for the disposable Gate F runner."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .contracts import SpecValidationError, StageContractError, StageStatus
from .runner import PipelineRunner
from .runtime import ID_RE, canonical_json_bytes
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
    from .model_worker import run_model_worker

    if not ID_RE.fullmatch(args.run_id) or not 1 <= args.timeout_seconds <= 3600:
        print("model spike rejected: invalid run or timeout", file=sys.stderr)
        return 64
    output = args.workspace_root / args.run_id / "model-output"
    result_path = args.workspace_root / args.run_id / "model-result.json"
    if output.parent.exists():
        print("model spike rejected: invalid or existing run", file=sys.stderr)
        return 64
    try:
        output.parent.mkdir(parents=True)
        result = run_model_worker(args.source, output, timeout_seconds=args.timeout_seconds)
        result_path.write_bytes(canonical_json_bytes(result))
    except (OSError, ValueError, TypeError, StageContractError):
        shutil.rmtree(output.parent, ignore_errors=True)
        print("model spike failed: isolated model worker error", file=sys.stderr)
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


def _cancel(args: argparse.Namespace) -> int:
    if not ID_RE.fullmatch(args.run_id):
        print("cancel rejected: invalid run id", file=sys.stderr)
        return 64
    try:
        run_dir = args.workspace_root / args.run_id
        if not run_dir.is_dir() or run_dir.is_symlink():
            print("cancel rejected: unknown run", file=sys.stderr)
            return 64
        sentinel = run_dir / "cancel.request"
        try:
            sentinel.touch(exist_ok=False)
        except FileExistsError:
            pass
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

    model = subparsers.add_parser("model", help="run the pinned See-through model spike in isolated WSL2")
    model.add_argument("--source", type=Path, required=True)
    model.add_argument("--run-id", required=True)
    model.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    model.add_argument("--timeout-seconds", type=int, default=1800)
    model.set_defaults(func=_model)

    motion = subparsers.add_parser("motion", help="generate a deterministic motion draft for a validated model run")
    motion.add_argument("--run-id", required=True)
    motion.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE)
    motion.set_defaults(func=_motion)

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
