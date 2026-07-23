"""Command-line entrypoint for the disposable Gate F runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .contracts import SpecValidationError, StageStatus
from .runner import PipelineRunner
from .runtime import ID_RE
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
    from .simple_cutout import build_simple_cutout_registry

    raster.set_defaults(func=_run, registry_factory=build_simple_cutout_registry)

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
