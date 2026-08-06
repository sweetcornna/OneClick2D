"""Command-line entry point for the OneClick2D production path.

Local, single-user and offline: this exposes the generation pipeline and the
independent verifiers. It is not a server and opens no network listener.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import OneClick2DError
from .generate import generate
from .registries import load_registries

EXIT_OK = 0
EXIT_USAGE = 64
EXIT_REJECTED = 65
EXIT_FAILED = 70


def _media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    raise ValueError("source must be a .png, .jpg or .jpeg file")


def _generate(arguments: argparse.Namespace) -> int:
    try:
        media_type = _media_type_for(arguments.source)
        upload = arguments.source.read_bytes()
    except (OSError, ValueError) as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        result = generate(
            upload=upload,
            declared_media_type=media_type,
            account_id=arguments.account_id,
            project_id=arguments.project_id,
            revision_id=arguments.revision_id,
            run_id=arguments.run_id,
            release_id=arguments.release_id,
            created_at=arguments.created_at,
            root_seed=arguments.root_seed,
            workspace=arguments.output,
        )
    except OneClick2DError as exc:
        # Reason codes are bounded and carry no content, path or free text.
        print(f"blocked: {exc.reason_code}", file=sys.stderr)
        return EXIT_REJECTED

    record = result.release.record
    print(f"release={record['release_id']} status={record['status']}")
    print(f"payload_sha256={record['project_payload_sha256']}")
    print(f"oc2d_sha256={record['oc2d']['sha256']} bytes={record['oc2d']['byte_length']}")
    print(f"psd_sha256={record['layered_psd']['sha256']} bytes={record['layered_psd']['byte_length']}")
    print(f"validation_status={result.validation_report['status']}")
    findings = result.validation_report["findings"]
    if findings:
        print(f"findings={len(findings)} (review required before use)")
    return EXIT_OK


def _verify(arguments: argparse.Namespace) -> int:
    from .export.oc2d import open_package
    from .export.psd import parse_layered_psd
    from .validation import validate_project

    try:
        opened = open_package(arguments.package.read_bytes())
        report = validate_project(opened.manifest, opened.artifacts, load_registries())
        if not report.export_ready:
            print(f"verification failed: status={report.status}", file=sys.stderr)
            return EXIT_FAILED
        print(f"package_ok payload_sha256={report.project_payload_sha256}")
        print(f"archive_sha256={opened.archive_sha256}")
        print(f"validation_status={report.status} findings={len(report.findings)}")
        if arguments.psd is not None:
            parsed = parse_layered_psd(arguments.psd.read_bytes())
            print(f"psd_ok layers={len(parsed.layers)} canvas={parsed.width}x{parsed.height}")
    except (OneClick2DError, OSError) as exc:
        print(f"verification failed: {type(exc).__name__}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def _registries(arguments: argparse.Namespace) -> int:
    registries = load_registries()
    for snapshot in (registries.ontology, registries.parameters, registries.reason_codes):
        reference = snapshot.as_reference()
        print(f"{reference['id']} version={reference['version']} sha256={reference['sha256']}")
    if arguments.show_parameters:
        for parameter in registries.parameters_list:
            print(
                f"  {parameter['id']:16s} {parameter['capability']:20s} "
                f"range={parameter['template_range']} neutral={parameter['neutral']}"
            )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oneclick2d", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("generate", help="run the full path and write both outputs")
    run.add_argument("--source", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--account-id", default="account.local")
    run.add_argument("--project-id", default="project.local")
    run.add_argument("--revision-id", default="revision.0001")
    run.add_argument("--run-id", default="run.local")
    run.add_argument("--release-id", default="release.local")
    run.add_argument("--created-at", default="2026-01-01T00:00:00Z")
    run.add_argument("--root-seed", type=int, default=0)
    run.set_defaults(func=_generate)

    verify = subparsers.add_parser("verify", help="independently re-verify published artifacts")
    verify.add_argument("--package", type=Path, required=True)
    verify.add_argument("--psd", type=Path)
    verify.set_defaults(func=_verify)

    registries = subparsers.add_parser("registries", help="show bound registry identities")
    registries.add_argument("--show-parameters", action="store_true")
    registries.set_defaults(func=_registries)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    return int(arguments.func(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
