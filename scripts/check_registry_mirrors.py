#!/usr/bin/env python3
"""Prove the canonical JSON registry mirrors match their YAML sources.

Registries are authored as YAML for review and mirrored to canonical JSON, which
is the form the CIR digests. This check fails closed when the two drift.

PyYAML is required to run the comparison and is *not* a production dependency:
``oneclick2d`` reads only the JSON mirrors. When PyYAML is unavailable the check
reports a skip so that the standard-library-only test command stays usable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_ROOT = ROOT / "registries"
PAIRS = (
    ("ontology-v0.1.yaml", "ontology-v0.1.json"),
    ("parameters-v0.1.yaml", "parameters-v0.1.json"),
    ("reason-codes.yaml", "reason-codes.json"),
)


def main() -> int:
    try:
        import yaml
    except ImportError:
        print("SKIP: PyYAML is unavailable; JSON mirrors were not compared to YAML sources")
        return 0

    errors: list[str] = []
    for yaml_name, json_name in PAIRS:
        yaml_path = REGISTRY_ROOT / yaml_name
        json_path = REGISTRY_ROOT / json_name
        if not yaml_path.is_file() or not json_path.is_file():
            errors.append(f"{yaml_name}: registry pair is incomplete")
            continue
        source = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        mirror = json.loads(json_path.read_text(encoding="utf-8"))
        if source != mirror:
            errors.append(f"{yaml_name}: JSON mirror {json_name} has drifted from its YAML source")

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        print(f"{len(errors)} registry mirror error(s).", file=sys.stderr)
        return 1
    print(f"{len(PAIRS)} registry mirrors match their YAML sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
