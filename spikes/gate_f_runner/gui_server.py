"""Loopback-only standard-library GUI server for local Gate F spike workbenches."""

from __future__ import annotations

import argparse
import copy
import ipaddress
import mimetypes
import socket
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from .acceptance import verified_bundle_artifact_bytes, verify_bundle
from .contracts import StageContractError
from .local_preflight import run_local_preflight
from .local_workbench import MAX_SOURCE_BYTES, PHASES, run_uploaded_workbench
from .model_psd_validator import MAX_PSD_BYTES
from .model_workbench import MODEL_PHASES, load_model_workbench_report, run_uploaded_model_workbench
from .runtime import (
    ID_RE,
    canonical_json_bytes,
    contained_run_path,
    contained_workspace_path,
    read_bounded_file,
    sha256_file,
    strict_load_json_bytes,
)

GUI_ROOT = Path(__file__).with_name("gui")
MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_STATIC_BYTES = 16 * 1024 * 1024
WorkbenchRunner = Callable[..., tuple[Path, dict[str, object]]]


def _http_authority(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ipaddress.ip_address(host).version == 6 else f"{host}:{port}"


class GuiState:
    def __init__(self, workspace_root: Path, *, model_runner: WorkbenchRunner = run_uploaded_model_workbench) -> None:
        self.workspace_root = workspace_root
        self.model_runner = model_runner
        self._lock = threading.Lock()
        self._active_run_id: str | None = None
        self._jobs: dict[str, dict[str, object]] = {}
        self.running = False

    def run_preflight(self, run_id: str) -> dict[str, object]:
        with self._lock:
            if self.running or self._active_run_id is not None:
                raise StageContractError("a local run is already running")
            self.running = True
        try:
            index, acceptance = run_local_preflight(self.workspace_root, run_id)
            return {"run_id": run_id, "bundle": index.parent.name, "acceptance": acceptance}
        finally:
            with self._lock:
                self.running = False

    def submit_workbench(
        self,
        run_id: str,
        source: bytes,
        media_type: str,
        workflow: str = "baseline",
    ) -> dict[str, object]:
        self._validate_run_id(run_id)
        if workflow not in {"baseline", "model"}:
            raise StageContractError("WORKFLOW_UNSUPPORTED")
        with self._lock:
            if self.running or self._active_run_id is not None:
                raise StageContractError("WORKBENCH_BUSY")
            if run_id in self._jobs or (self.workspace_root / run_id).exists():
                raise StageContractError("RUN_ID_EXISTS")
            phase_ids = MODEL_PHASES if workflow == "model" else PHASES
            phases = [{"id": phase, "state": "completed" if phase == "UPLOAD_RECEIVED" else "pending"} for phase in phase_ids]
            self._jobs[run_id] = {
                "run_id": run_id,
                "workflow": workflow,
                "state": "submitted",
                "local_status": "LOCAL_WORKBENCH_RUNNING",
                "model_used": False,
                "oc2d_produced": False,
                "gate_f_status": "GATE_F_NOT_EVALUATED",
                "phases": phases,
            }
            self._active_run_id = run_id
        worker = threading.Thread(target=self._run_workbench, args=(run_id, source, media_type, workflow), daemon=True)
        worker.start()
        return self.workbench_status(run_id)

    def _run_workbench(self, run_id: str, source: bytes, media_type: str, workflow: str) -> None:
        def phase_callback(phase_id: str, state: str) -> None:
            with self._lock:
                job = self._jobs.get(run_id)
                if job is None:
                    return
                job["state"] = "running"
                phases = job["phases"]
                if isinstance(phases, list):
                    for phase in phases:
                        if isinstance(phase, dict) and phase.get("id") == phase_id:
                            phase["state"] = state
                            break

        try:
            runner = self.model_runner if workflow == "model" else run_uploaded_workbench
            _, report = runner(self.workspace_root, run_id, source, media_type, phase_callback)
        except Exception:
            with self._lock:
                current = self._jobs.get(run_id, {})
                current_phases = current.get("phases")
                phases = [dict(phase) for phase in current_phases if isinstance(phase, dict)] if isinstance(current_phases, list) else []
            failed = False
            for phase in phases:
                state = phase.get("state")
                if state == "running" and not failed:
                    phase["state"] = "failed"
                    failed = True
                elif state == "pending":
                    phase["state"] = "unavailable"
            report = {
                "run_id": run_id,
                "workflow": workflow,
                "state": "failed",
                "local_status": "LOCAL_WORKBENCH_NOT_COMPLETED",
                "reason_code": "STAGE_INTERNAL_ERROR",
                "model_used": False,
                "oc2d_produced": False,
                "gate_f_status": "GATE_F_NOT_EVALUATED",
                "phases": phases,
            }
        finally:
            source = b""
        with self._lock:
            if report.get("state") == "completed":
                self._jobs.pop(run_id, None)
            else:
                self._jobs[run_id] = report
            if self._active_run_id == run_id:
                self._active_run_id = None

    def list_workbenches(self) -> list[dict[str, object]]:
        discovered: dict[str, dict[str, object]] = {}
        model_run_ids: set[str] = set()
        if self.workspace_root.exists():
            for result_path in self.workspace_root.glob("*/model-result.json"):
                try:
                    run_id = result_path.parent.name
                    run_dir = self._contained_run_directory(run_id)
                    self._contained_run_file(run_id, "model-result.json")
                    model_run_ids.add(run_id)
                    discovered[run_id] = self._model_report(run_dir)
                except (OSError, ValueError, TypeError, StageContractError):
                    continue
            for report_path in self.workspace_root.glob("*/workbench-report.json"):
                try:
                    run_id = report_path.parent.name
                    if run_id in model_run_ids:
                        continue
                    run_dir = self._contained_run_directory(run_id)
                    self._contained_run_file(run_id, "workbench-report.json")
                    value = self._baseline_report(run_dir)
                    discovered[run_id] = value
                except (OSError, ValueError, TypeError, StageContractError):
                    continue
        with self._lock:
            jobs = {
                name: copy.deepcopy(value)
                for name, value in self._jobs.items()
                if value.get("state") != "completed"
            }
        discovered.update(jobs)
        return sorted(discovered.values(), key=lambda item: str(item.get("run_id", "")), reverse=True)

    def workbench_status(self, run_id: str) -> dict[str, object]:
        self._validate_run_id(run_id)
        with self._lock:
            job = self._jobs.get(run_id)
            if job is not None and job.get("state") != "completed":
                return copy.deepcopy(job)
        run_dir = self._contained_run_directory(run_id)
        result = run_dir / "model-result.json"
        if result.exists() or result.is_symlink():
            self._contained_run_file(run_id, "model-result.json")
            value = self._model_report(run_dir)
        else:
            self._contained_run_file(run_id, "workbench-report.json")
            value = self._baseline_report(run_dir)
        if value.get("run_id") != run_id:
            raise StageContractError("invalid workbench report")
        return value

    def workbench_artifact(self, run_id: str, artifact_id: str) -> tuple[bytes, str, str | None]:
        report = self.workbench_status(run_id)
        if report.get("state") != "completed":
            raise StageContractError("workbench output is not published")
        descriptor = self._artifact_descriptor(report, artifact_id)
        media_type = descriptor.get("media_type")
        uri = descriptor.get("uri")
        length = descriptor.get("byte_length")
        digest = descriptor.get("sha256")
        if not isinstance(media_type, str) or not isinstance(uri, str) or isinstance(length, bool) or not isinstance(length, int) or not isinstance(digest, str):
            raise StageContractError("workbench artifact descriptor is invalid")
        try:
            path = self._contained_run_file(run_id, uri)
        except StageContractError as exc:
            raise StageContractError("workbench artifact is invalid") from exc
        if path.stat().st_size != length or sha256_file(path) != digest:
            raise StageContractError("workbench artifact is invalid")
        maximum = MAX_PSD_BYTES if media_type == "image/vnd.adobe.photoshop" else MAX_FRAME_BYTES
        filename = None
        if media_type == "image/vnd.adobe.photoshop":
            if report.get("workflow") == "model":
                filename = "local-see-through-depth.psd" if artifact_id == "output-depth-psd" else "local-see-through-layers.psd"
            else:
                filename = "local-deterministic-baseline.psd"
        return read_bounded_file(path, maximum), media_type, filename

    @staticmethod
    def _artifact_descriptor(report: dict[str, object], artifact_id: str) -> dict[str, object]:
        normalization = report.get("normalization")
        candidate = report.get("candidate")
        model = report.get("model")
        psd = report.get("psd")
        depth_psd = report.get("depth_psd")
        descriptors: list[dict[str, object]] = []
        if isinstance(normalization, dict) and isinstance(normalization.get("artifact"), dict):
            descriptors.append(normalization["artifact"])
        if isinstance(candidate, dict):
            for key in ("layers", "frames"):
                values = candidate.get(key)
                if isinstance(values, list):
                    for item in values:
                        if isinstance(item, dict) and isinstance(item.get("artifact"), dict):
                            descriptors.append(item["artifact"])
        if isinstance(model, dict):
            source = model.get("source")
            if isinstance(source, dict):
                descriptors.append(source)
            reconstruction = model.get("reconstruction")
            if isinstance(reconstruction, dict):
                descriptors.append(reconstruction)
            layers = model.get("layers")
            if isinstance(layers, list):
                for item in layers:
                    if not isinstance(item, dict):
                        continue
                    for key in ("artifact", "depth_artifact"):
                        descriptor = item.get(key)
                        if isinstance(descriptor, dict):
                            descriptors.append(descriptor)
        motion = report.get("motion_draft")
        if isinstance(motion, dict):
            layers = motion.get("layers")
            if isinstance(layers, list):
                for item in layers:
                    if isinstance(item, dict) and isinstance(item.get("artifact"), dict):
                        descriptors.append(item["artifact"])
            frames = motion.get("frames")
            if isinstance(frames, list):
                for item in frames:
                    if isinstance(item, dict) and isinstance(item.get("artifact"), dict):
                        descriptors.append(item["artifact"])
        if isinstance(psd, dict):
            descriptors.append(psd)
        if isinstance(depth_psd, dict):
            descriptors.append(depth_psd)
        matches = [item for item in descriptors if item.get("id") == artifact_id]
        if len(matches) != 1:
            raise StageContractError("unknown workbench artifact")
        return matches[0]

    def list_bundles(self) -> list[dict[str, object]]:
        if not self.workspace_root.exists():
            return []
        candidates: list[Path] = []
        for entry in self.workspace_root.glob("*.bundle"):
            try:
                candidates.append(self.resolve_bundle(entry.name))
            except StageContractError:
                continue
        bundles = []
        for directory in sorted(candidates, key=lambda item: item.lstat().st_mtime_ns, reverse=True):
            try:
                report = verify_bundle(directory)
            except (OSError, ValueError, TypeError, StageContractError):
                continue
            bundles.append({"name": directory.name, "report": report})
        return bundles

    def bundle_summary(self, bundle_name: str) -> dict[str, object]:
        directory = self.resolve_bundle(bundle_name)
        acceptance = verify_bundle(directory)
        candidate = self._json(self._contained_bundle_file(bundle_name, "candidate-report.json"))
        comparator = self._json(self._contained_bundle_file(bundle_name, "comparator-report.json"))
        statistics = self._json(self._contained_bundle_file(bundle_name, "paired-statistics.json"))
        psd = self._json(self._contained_bundle_file(bundle_name, "psd-readback.json"))
        return {"bundle": bundle_name, "acceptance": acceptance, "candidate": candidate, "comparator": comparator, "statistics": statistics, "psd": psd}

    def resolve_bundle(self, bundle_name: str) -> Path:
        if not bundle_name.endswith(".bundle") or not ID_RE.fullmatch(bundle_name[:-7]):
            raise StageContractError("invalid bundle name")
        try:
            return contained_workspace_path(self.workspace_root, bundle_name, kind="directory")
        except ValueError as exc:
            raise StageContractError("unknown bundle") from exc

    def frame_bytes(self, bundle_name: str, arm: str, index_text: str) -> bytes:
        if arm not in {"candidate", "comparator"} or not index_text.isdigit():
            raise StageContractError("invalid frame request")
        index = int(index_text)
        if not 0 <= index < 37:
            raise StageContractError("frame index is outside the sequence")
        directory = self.resolve_bundle(bundle_name)
        return verified_bundle_artifact_bytes(directory, f"{arm}-frame-{index:03d}.png", MAX_FRAME_BYTES)

    def _contained_bundle_file(self, bundle_name: str, relative: str) -> Path:
        try:
            return contained_workspace_path(self.workspace_root, f"{bundle_name}/{relative}", kind="file")
        except ValueError as exc:
            raise StageContractError("bundle artifact is invalid") from exc

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not ID_RE.fullmatch(run_id):
            raise StageContractError("invalid run ID")

    def _contained_run_directory(self, run_id: str) -> Path:
        try:
            return contained_run_path(self.workspace_root, run_id, kind="directory")
        except ValueError as exc:
            raise StageContractError("invalid workbench run directory") from exc

    def _contained_run_file(self, run_id: str, relative: str) -> Path:
        try:
            return contained_run_path(self.workspace_root, run_id, relative, kind="file")
        except ValueError as exc:
            raise StageContractError("invalid workbench artifact path") from exc

    def _model_report(self, run_dir: Path) -> dict[str, object]:
        run_dir = self._contained_run_directory(run_dir.name)
        return load_model_workbench_report(run_dir)

    def _baseline_report(self, run_dir: Path) -> dict[str, object]:
        run_dir = self._contained_run_directory(run_dir.name)
        report_path = self._contained_run_file(run_dir.name, "workbench-report.json")
        value = self._json(report_path)
        if (
            value.get("run_id") != run_dir.name
            or value.get("workflow") not in {None, "baseline"}
            or value.get("model_used") is not False
            or value.get("oc2d_produced") is not False
            or value.get("gate_f_status") != "GATE_F_NOT_EVALUATED"
        ):
            raise StageContractError("invalid workbench report")
        return value

    @staticmethod
    def _json(path: Path) -> dict[str, object]:
        value = strict_load_json_bytes(read_bounded_file(path))
        if not isinstance(value, dict):
            raise StageContractError("GUI evidence JSON is invalid")
        return value


class GuiRequestHandler(BaseHTTPRequestHandler):
    server_version = "OneClick2DGui/0.2"

    @property
    def state(self) -> GuiState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        try:
            self._validate_local_request(state_changing=False)
            if path == "/api/workbench/runs":
                self._json_response({"runs": self.state.list_workbenches(), "running": self.state._active_run_id is not None})
            elif path.startswith("/api/workbench/runs/"):
                parts = path.split("/")
                if len(parts) == 5:
                    self._json_response(self.state.workbench_status(parts[4]))
                elif len(parts) == 7 and parts[5] == "artifacts":
                    data, media_type, filename = self.state.workbench_artifact(parts[4], parts[6])
                    self._bytes_response(data, media_type, cache="private, max-age=31536000, immutable", filename=filename)
                else:
                    raise StageContractError("invalid workbench route")
            elif path == "/api/bundles":
                self._json_response({"bundles": self.state.list_bundles(), "running": self.state.running})
            elif path.startswith("/api/bundle/"):
                self._json_response(self.state.bundle_summary(path.removeprefix("/api/bundle/")))
            elif path.startswith("/api/frame/"):
                parts = path.split("/")
                if len(parts) != 6:
                    raise StageContractError("invalid frame route")
                self._bytes_response(self.state.frame_bytes(parts[3], parts[4], parts[5]), "image/png", cache="private, max-age=31536000, immutable")
            else:
                self._static_response(path)
        except (OSError, ValueError, TypeError, LookupError, StageContractError):
            self._json_response({"error": "LOCAL_EVIDENCE_UNAVAILABLE"}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        path = unquote(urlsplit(self.path).path)
        try:
            self._validate_local_request(state_changing=False)
            if path.startswith("/api/workbench/runs/"):
                parts = path.split("/")
                if len(parts) != 5:
                    raise StageContractError("invalid workbench route")
                body = self._read_body(MAX_SOURCE_BYTES)
                self._validate_local_request(state_changing=True)
                content_type = self.headers.get("Content-Type", "").lower()
                if content_type not in {"image/png", "image/jpeg"}:
                    raise StageContractError("unsupported image media type")
                workflow = self.headers.get("X-OneClick2D-Workflow", "baseline").lower()
                if workflow not in {"baseline", "model"}:
                    raise StageContractError("unsupported workbench workflow")
                self._json_response(self.state.submit_workbench(parts[4], body, content_type, workflow), HTTPStatus.ACCEPTED)
            elif path == "/api/preflight":
                exact = self._read_body(4096)
                self._validate_local_request(state_changing=True)
                body = strict_load_json_bytes(exact)
                run_id = body.get("run_id") if isinstance(body, dict) else None
                if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
                    raise StageContractError("invalid run ID")
                self._json_response(self.state.run_preflight(run_id), HTTPStatus.CREATED)
            else:
                self._json_response({"error": "UNKNOWN_ACTION"}, HTTPStatus.NOT_FOUND)
        except StageContractError as exc:
            code = str(exc) if str(exc) in {"WORKBENCH_BUSY", "RUN_ID_EXISTS"} else "LOCAL_RUN_REJECTED"
            status = HTTPStatus.CONFLICT if code in {"WORKBENCH_BUSY", "RUN_ID_EXISTS"} else HTTPStatus.BAD_REQUEST
            self._json_response({"error": code}, status)
        except (OSError, ValueError, TypeError):
            self._json_response({"error": "LOCAL_RUN_REJECTED"}, HTTPStatus.BAD_REQUEST)

    def _read_body(self, maximum: int) -> bytes:
        if self.headers.get("Transfer-Encoding") is not None:
            raise StageContractError("transfer encoding is unsupported")
        values = self.headers.get_all("Content-Length", failobj=[])
        if len(values) != 1:
            raise StageContractError("content length is invalid")
        try:
            length = int(values[0])
        except ValueError as exc:
            raise StageContractError("content length is invalid") from exc
        if not 1 <= length <= maximum:
            raise StageContractError("content length is invalid")
        data = self.rfile.read(length)
        if len(data) != length:
            raise StageContractError("request body is truncated")
        return data

    def _validate_local_request(self, *, state_changing: bool) -> None:
        try:
            peer = ipaddress.ip_address(self.client_address[0])
        except ValueError as exc:
            raise StageContractError("invalid peer") from exc
        if not peer.is_loopback:
            raise StageContractError("non-loopback peer")
        host = self.headers.get("Host", "")
        bound_host = str(self.server.server_address[0])
        port = int(self.server.server_address[1])
        allowed_hosts = {_http_authority(bound_host, port), f"localhost:{port}"}
        if host.lower() not in allowed_hosts:
            raise StageContractError("invalid host")
        if state_changing:
            origin = self.headers.get("Origin")
            if origin is not None and origin.lower() not in {f"http://{item}" for item in allowed_hosts}:
                raise StageContractError("invalid origin")

    def _static_response(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = GUI_ROOT / relative
        if candidate.is_symlink() or not candidate.is_file() or GUI_ROOT.resolve() not in candidate.resolve().parents:
            self._json_response({"error": "NOT_FOUND"}, HTTPStatus.NOT_FOUND)
            return
        media_type = {
            ".mjs": "text/javascript; charset=utf-8",
            ".wasm": "application/wasm",
            ".task": "application/octet-stream",
        }.get(candidate.suffix.lower()) or mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        cache = "private, max-age=31536000, immutable" if "vendor" in candidate.relative_to(GUI_ROOT).parts else "no-cache"
        self._bytes_response(read_bounded_file(candidate, MAX_STATIC_BYTES), media_type, cache=cache)

    def _json_response(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._bytes_response(canonical_json_bytes(value), "application/json; charset=utf-8", status=status, cache="no-store")

    def _bytes_response(
        self,
        data: bytes,
        media_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        cache: str,
        filename: str | None = None,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        if filename is not None:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], state: GuiState) -> None:
        if address[0] == "::1":
            self.address_family = socket.AF_INET6
        super().__init__(address, GuiRequestHandler)
        self.state = state


def serve_gui(host: str, port: int, workspace_root: Path, *, open_browser: bool) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise StageContractError("GUI server is loopback-only")
    server = GuiServer((host, port), GuiState(workspace_root))
    display_host = "127.0.0.1" if host == "localhost" else host
    url = f"http://{_http_authority(display_host, server.server_address[1])}/"
    print(f"gui={url}")
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_gui_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace-root", type=Path, default=Path(__file__).resolve().parents[2] / "workspaces" / "gate-f-spike")
    parser.add_argument("--no-open", action="store_true")
    return parser
