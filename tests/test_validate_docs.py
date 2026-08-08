from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_docs  # noqa: E402


class DocumentationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(
            (ROOT / "examples/cir-minimal/manifest.json").read_text(encoding="utf-8")
        )

    def test_repository_documents_are_valid(self) -> None:
        self.assertEqual([], validate_docs.run_checks())

    def test_minimal_cir_rejects_occlusion_cycle(self) -> None:
        manifest = deepcopy(self.manifest)
        second_layer = deepcopy(manifest["layers"][0])
        second_layer["id"] = "layer.front-hair"
        second_layer["semantic"] = "oc2d.hair.front"
        manifest["layers"].append(second_layer)
        manifest["occlusion_edges"] = [
            {
                "behind_layer_id": "layer.face-base",
                "in_front_layer_id": "layer.front-hair",
                "source": "semantic_rule",
                "confidence": 1.0,
                "evidence": [],
            },
            {
                "behind_layer_id": "layer.front-hair",
                "in_front_layer_id": "layer.face-base",
                "source": "user",
                "confidence": 1.0,
                "evidence": [],
            },
        ]

        errors = validate_docs.validate_minimal_cir(manifest)

        self.assertTrue(any("occlusion graph contains a cycle" in error for error in errors))

    def test_minimal_cir_rejects_missing_artifact_reference(self) -> None:
        manifest = deepcopy(self.manifest)
        manifest["layers"][0]["texture_artifact_id"] = "sha256:" + "f" * 64

        errors = validate_docs.validate_minimal_cir(manifest)

        self.assertTrue(any("missing texture_artifact_id" in error for error in errors))

    def test_minimal_cir_rejects_parameter_default_outside_range(self) -> None:
        manifest = deepcopy(self.manifest)
        manifest["parameters"][0]["default"] = 90.0

        errors = validate_docs.validate_minimal_cir(manifest)

        self.assertTrue(any("range/default invalid" in error for error in errors))

    def test_document_index_covers_governing_baseline(self) -> None:
        self.assertEqual([], validate_docs.validate_index_coverage())

    def test_product_requirements_cover_each_stable_prefix(self) -> None:
        errors = validate_docs.validate_product_boundary_and_requirements()

        self.assertFalse(any("missing requirement headings" in error for error in errors))

    def test_active_model_commands_use_native_linux_source_paths(self) -> None:
        expected_command = (
            'python -m spikes.gate_f_runner model --source "/path/to/right-cleared.png" '
            "--run-id run.local-model"
        )
        documents = (
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "CLAUDE.md",
            ROOT / "spikes/gate_f_runner/model_profiles/README.md",
        )
        for document in documents:
            with self.subTest(document=document.relative_to(ROOT)):
                text = document.read_text(encoding="utf-8")
                self.assertIn(expected_command, text)
                self.assertNotIn('"C:/path/to/right-cleared.png"', text)
                self.assertIn("native-linux", text)
                self.assertIn("none-host-local", text)
                self.assertTrue(
                    "已抠背景" in text
                    or "cut-out character image with a transparent background" in text
                )

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("必须从 Windows PowerShell 或 cmd 运行", readme)
        self.assertNotIn("不支持直接从 WSL shell 调用", readme)

    def test_phase_1_docs_contain_no_prohibited_brand_claim(self) -> None:
        errors = validate_docs.validate_product_boundary_and_requirements()

        self.assertFalse(any("prohibited unqualified Phase 1 claim" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
