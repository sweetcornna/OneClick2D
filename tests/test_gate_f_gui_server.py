from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from spikes.gate_f_runner.gui_server import GuiServer, GuiState
from spikes.gate_f_runner.model_workbench import load_model_workbench_report
from spikes.gate_f_runner.runtime import canonical_json_bytes
from tests.test_gate_f_model_workbench import write_model_fixture
from tests.test_gate_f_simple_cutout import purpose_created_asymmetric_png


class GateFGuiServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.server = GuiServer(("127.0.0.1", 0), GuiState(self.root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        request_headers = dict(headers or {})
        if body is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=30)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_static_assets_have_local_security_headers(self) -> None:
        status, body, headers = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn(b"Image Workbench", body)
        self.assertIn(b'name="workflow"', body)
        self.assertIn(b'value="model"', body)
        self.assertIn(b'data-model-view="motion"', body)
        self.assertIn(b'data-model-view="camera"', body)
        self.assertIn(b'id="live-canvas"', body)
        self.assertIn(b'id="camera-device"', body)
        self.assertNotIn(b'value="run.', body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("'wasm-unsafe-eval'", headers["Content-Security-Policy"])
        self.assertIn("camera=(self)", headers["Permissions-Policy"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertNotIn(b'src="http://', body)
        self.assertNotIn(b'src="https://', body)
        self.assertNotIn(b'href="http://', body)
        self.assertNotIn(b'href="https://', body)

    def test_ipv6_loopback_uses_bracketed_authority(self) -> None:
        try:
            server = GuiServer(("::1", 0), GuiState(self.root))
        except OSError:
            self.skipTest("IPv6 loopback is unavailable")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("::1", server.server_address[1], timeout=30)
        try:
            connection.request("GET", "/", headers={"Host": f"[::1]:{server.server_address[1]}"})
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            self.assertIn(b"Image Workbench", response.read())
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_api_runs_preflight_lists_bundle_and_serves_frames(self) -> None:
        payload = json.dumps({"run_id": "run.gui-test"}).encode()
        status, body, _ = self.request("POST", "/api/preflight", payload)
        self.assertEqual(201, status)
        result = json.loads(body)
        self.assertEqual("LOCAL_TECHNICAL_PREFLIGHT_PASS", result["acceptance"]["local_technical_preflight_status"])
        self.assertEqual("GATE_F_NOT_EVALUATED", result["acceptance"]["gate_f_status"])

        status, body, _ = self.request("GET", "/api/bundles")
        bundles = json.loads(body)["bundles"]
        self.assertEqual("run.gui-test.bundle", bundles[0]["name"])

        status, body, headers = self.request("GET", "/api/frame/run.gui-test.bundle/candidate/0")
        self.assertEqual(200, status)
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual("image/png", headers["Content-Type"])

    def test_workbench_upload_reaches_completed_and_serves_allowlisted_outputs(self) -> None:
        source = purpose_created_asymmetric_png()
        status, body, headers = self.request(
            "POST",
            "/api/workbench/runs/run.gui-upload",
            source,
            {"Content-Type": "image/png", "Origin": f"http://127.0.0.1:{self.server.server_address[1]}"},
        )
        self.assertEqual(202, status)
        self.assertEqual("GATE_F_NOT_EVALUATED", json.loads(body)["gate_f_status"])
        self.assertIn("camera=(self)", headers["Permissions-Policy"])
        report = None
        for _ in range(200):
            status, body, _ = self.request("GET", "/api/workbench/runs/run.gui-upload")
            self.assertEqual(200, status)
            report = json.loads(body)
            if report["state"] in {"completed", "blocked", "failed"}:
                break
            time.sleep(0.02)
        self.assertIsNotNone(report)
        self.assertEqual("completed", report["state"])
        self.assertFalse(report["model_used"])
        self.assertFalse(report["oc2d_produced"])
        self.assertEqual(37, len(report["candidate"]["frames"]))

        status, frame, frame_headers = self.request("GET", "/api/workbench/runs/run.gui-upload/artifacts/frame-000")
        self.assertEqual(200, status)
        self.assertTrue(frame.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual("image/png", frame_headers["Content-Type"])
        status, psd, psd_headers = self.request("GET", "/api/workbench/runs/run.gui-upload/artifacts/output-psd")
        self.assertEqual(200, status)
        self.assertTrue(psd.startswith(b"8BPS"))
        self.assertEqual('attachment; filename="local-deterministic-baseline.psd"', psd_headers["Content-Disposition"])
        status, _, _ = self.request("GET", "/api/workbench/runs/run.gui-upload/artifacts/source")
        self.assertEqual(400, status)

        report_path = self.root / "run.gui-upload" / "workbench-report.json"
        legacy_report = json.loads(report_path.read_text(encoding="utf-8"))
        legacy_report.pop("workflow")
        report_path.write_bytes(canonical_json_bytes(legacy_report))
        discovered = GuiState(self.root).list_workbenches()
        self.assertEqual("run.gui-upload", discovered[0]["run_id"])
        self.assertFalse(discovered[0]["model_used"])

    def test_workbench_rejects_wrong_type_origin_and_duplicate_id(self) -> None:
        source = purpose_created_asymmetric_png()
        status, _, _ = self.request("POST", "/api/workbench/runs/run.bad-type", source, {"Content-Type": "application/octet-stream"})
        self.assertEqual(400, status)
        status, _, _ = self.request(
            "POST",
            "/api/workbench/runs/run.bad-origin",
            source,
            {"Content-Type": "image/png", "Origin": "https://example.invalid"},
        )
        self.assertEqual(400, status)
        status, _, _ = self.request("POST", "/api/workbench/runs/run.duplicate", source, {"Content-Type": "image/png"})
        self.assertEqual(202, status)
        for _ in range(200):
            status, body, _ = self.request("GET", "/api/workbench/runs/run.duplicate")
            if json.loads(body)["state"] in {"completed", "blocked", "failed"}:
                break
            time.sleep(0.02)
        status, body, _ = self.request("POST", "/api/workbench/runs/run.duplicate", source, {"Content-Type": "image/png"})
        self.assertEqual(409, status)
        self.assertEqual("RUN_ID_EXISTS", json.loads(body)["error"])

    def test_explicit_model_workflow_never_claims_model_before_runner_success(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def runner(workspace, run_id, source, media_type, callback):
            callback("PINNED_MODEL_INFERENCE", "running")
            started.set()
            release.wait(5)
            run_dir = workspace / run_id
            run_dir.mkdir()
            write_model_fixture(run_dir)
            report = load_model_workbench_report(run_dir)
            report_path = run_dir / "workbench-report.json"
            report_path.write_bytes(canonical_json_bytes(report))
            return report_path, report

        self.server.state.model_runner = runner
        status, body, _ = self.request(
            "POST",
            "/api/workbench/runs/run.gui-model",
            purpose_created_asymmetric_png(),
            {"Content-Type": "image/png", "X-OneClick2D-Workflow": "model"},
        )
        self.assertEqual(202, status)
        submitted = json.loads(body)
        self.assertEqual("model", submitted["workflow"])
        self.assertFalse(submitted["model_used"])
        self.assertTrue(started.wait(2))
        status, body, _ = self.request("GET", "/api/workbench/runs/run.gui-model")
        self.assertEqual(200, status)
        self.assertFalse(json.loads(body)["model_used"])
        release.set()
        for _ in range(100):
            status, body, _ = self.request("GET", "/api/workbench/runs/run.gui-model")
            report = json.loads(body)
            if report["state"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual("completed", report["state"])
        self.assertTrue(report["model_used"])
        self.assertFalse(report["oc2d_produced"])

    def test_model_runner_failure_preserves_phase_truth_and_model_false(self) -> None:
        def runner(workspace, run_id, source, media_type, callback):
            callback("RASTER_NORMALIZE", "completed")
            callback("PINNED_MODEL_INFERENCE", "running")
            raise StageContractError("private model failure")

        from spikes.gate_f_runner.contracts import StageContractError

        self.server.state.model_runner = runner
        status, _, _ = self.request(
            "POST",
            "/api/workbench/runs/run.gui-model-fail",
            purpose_created_asymmetric_png(),
            {"Content-Type": "image/png", "X-OneClick2D-Workflow": "model"},
        )
        self.assertEqual(202, status)
        for _ in range(100):
            status, body, _ = self.request("GET", "/api/workbench/runs/run.gui-model-fail")
            report = json.loads(body)
            if report["state"] == "failed":
                break
            time.sleep(0.01)
        self.assertEqual("failed", report["state"])
        self.assertFalse(report["model_used"])
        phases = {phase["id"]: phase["state"] for phase in report["phases"]}
        self.assertEqual("completed", phases["UPLOAD_RECEIVED"])
        self.assertEqual("completed", phases["RASTER_NORMALIZE"])
        self.assertEqual("failed", phases["PINNED_MODEL_INFERENCE"])
        self.assertEqual("unavailable", phases["MODEL_ARTIFACT_VALIDATE"])

    def test_invalid_routes_and_run_ids_are_bounded_json_errors(self) -> None:
        status, body, _ = self.request("POST", "/api/preflight", b'{"run_id":"../bad"}')
        self.assertEqual(400, status)
        self.assertIn("error", json.loads(body))
        status, body, _ = self.request("GET", "/api/frame/../bad/candidate/0")
        self.assertEqual(400, status)
        self.assertIn("error", json.loads(body))

    def test_local_tracker_assets_have_strict_types_and_immutable_cache(self) -> None:
        status, body, headers = self.request("GET", "/live_preview.mjs")
        self.assertEqual(200, status)
        self.assertIn(b"LivePreviewController", body)
        self.assertEqual("text/javascript; charset=utf-8", headers["Content-Type"])
        self.assertEqual("no-cache", headers["Cache-Control"])

        status, body, headers = self.request("GET", "/vendor/mediapipe-tasks-vision-0.10.35/face_landmarker.task")
        self.assertEqual(200, status)
        self.assertGreater(len(body), 3_000_000)
        self.assertEqual("application/octet-stream", headers["Content-Type"])
        self.assertEqual("private, max-age=31536000, immutable", headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
