"use strict";

const state = {
  file: null,
  objectUrl: "",
  runId: "",
  report: null,
  workflow: "baseline",
  frame: 0,
  artifactId: "normalized",
  showDepth: false,
  modelView: "reconstruction",
  busy: false,
  activeRunId: "",
  runEpoch: 0,
  fileSerial: 0,
  loadSerial: 0,
  pollTimer: 0,
  playTimer: 0,
  playRequest: 0,
  frameRenderSerial: 0,
  frameCache: new Map(),
  framePreloadKey: "",
  framePreloadPromise: null,
  livePreview: null,
  livePreviewSerial: 0,
};
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const phaseText = {
  pending: "待处理",
  running: "处理中",
  completed: "完成",
  blocked: "已阻止",
  failed: "失败",
  unavailable: "不可用",
};
const phaseLabels = {
  UPLOAD_RECEIVED: "接收图片",
  RASTER_NORMALIZE: "栅格标准化",
  DETERMINISTIC_BASELINE_37_FRAMES: "固定区域与 37 帧",
  PSD_WRITE: "PSD 写出",
  PSD_READBACK: "PSD 独立回读",
  PINNED_MODEL_INFERENCE: "固定模型校验与推理",
  MODEL_ARTIFACT_VALIDATE: "模型产物校验",
  MODEL_RESULT_PUBLISH: "结果发布",
};
const workflowPhases = {
  baseline: ["UPLOAD_RECEIVED", "RASTER_NORMALIZE", "DETERMINISTIC_BASELINE_37_FRAMES", "PSD_WRITE", "PSD_READBACK"],
  model: ["UPLOAD_RECEIVED", "RASTER_NORMALIZE", "PINNED_MODEL_INFERENCE", "MODEL_ARTIFACT_VALIDATE", "MODEL_RESULT_PUBLISH"],
};
const semanticLabels = {
  "front hair": "前发",
  "back hair": "后发",
  head: "头部合成",
  headwear: "头饰",
  face: "面部",
  eyebrow: "眉毛",
  eyelash: "睫毛",
  irides: "虹膜",
  eyewhite: "眼白",
  eyewear: "眼部配饰",
  ears: "耳朵",
  earwear: "耳饰",
  nose: "鼻子",
  mouth: "嘴部",
  neck: "颈部",
  neckwear: "颈饰",
  topwear: "上装",
  handwear: "手部配饰",
  bottomwear: "下装",
  legwear: "腿部配饰",
  footwear: "鞋袜",
  tail: "尾部",
  wings: "翅膀",
  objects: "其他物体",
};
const capabilityLabels = {
  source_comparison: "输入与重建对照",
  semantic_rgba: "语义 RGBA 图层",
  semantic_depth: "逐层深度",
  psd_internal_readback: "PSD 内部结构回读",
  semantic_correctness: "语义正确性",
  hidden_region_quality: "遮挡区域补全质量",
  external_editor_validation: "外部编辑器验证",
  mesh_generation: "网格生成",
  parameter_binding: "参数绑定",
  dynamic_preview: "动态参数预览",
  oc2d_package: ".oc2d 项目",
};
const capabilityStateText = {
  available: "可用",
  research_draft: "研究初稿",
  not_evaluated: "未评估",
  not_generated: "未生成",
};

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("oc2d-theme", theme);
}

function initialTheme() {
  const saved = localStorage.getItem("oc2d-theme");
  if (saved === "light" || saved === "dark") return saved;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function workflowOf(report = state.report) {
  if (report?.workflow === "model" || report?.model_used === true) return "model";
  return "baseline";
}

function hasMotionDraft(report = state.report) {
  return workflowOf(report) === "model" && Array.isArray(report?.motion_draft?.frames) && report.motion_draft.frames.length > 0;
}

function defaultModelView(report = state.report) {
  return hasMotionDraft(report) ? "motion" : "reconstruction";
}

function previewFrames() {
  if (!state.report || state.report.state !== "completed") return [];
  if (workflowOf() === "baseline") return Array.isArray(state.report.candidate?.frames) ? state.report.candidate.frames : [];
  if (state.modelView === "motion" && hasMotionDraft()) return state.report.motion_draft.frames;
  return [];
}

function modelCompletionStatus(report = state.report) {
  return hasMotionDraft(report) ? "动态研究初稿可预览，仍需质量复核。" : "静态语义层候选已生成，需质量复核。";
}

function syncWorkflow(workflow) {
  state.workflow = workflow === "model" ? "model" : "baseline";
  const radio = $(`input[name="workflow"][value="${state.workflow}"]`);
  if (radio) radio.checked = true;
  const model = state.workflow === "model";
  $("#workflow-boundary").textContent = model ? "See-through V3 · 语义层候选" : "快速基线 · 非模型";
  $("#workflow-profile").textContent = model ? "NF4 · seed 42 · 1280 px" : "See-through 模型可选";
  $("#model-notice").hidden = !model;
  $("#run-button-label").textContent = model ? "生成语义层候选" : "生成 37 帧预览";
  updateRunButton();
}

function newRunId() {
  const now = new Date();
  const date = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((value, index) => String(value).padStart(index === 0 ? 4 : 2, "0")).join("");
  const time = [now.getHours(), now.getMinutes(), now.getSeconds(), now.getMilliseconds()]
    .map((value, index) => String(value).padStart(index === 3 ? 3 : 2, "0")).join("");
  return `run.${state.workflow}-${date}-${time}`;
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("Content-Type") || "";
  const body = contentType.startsWith("application/json") ? await response.json() : await response.blob();
  if (!response.ok) throw new Error(body.error || "LOCAL_REQUEST_FAILED");
  return body;
}

function artifactUrl(id) {
  return `/api/workbench/runs/${encodeURIComponent(state.runId)}/artifacts/${encodeURIComponent(id)}`;
}

function resetFrameCache() {
  for (const entry of state.frameCache.values()) entry.image.removeAttribute("src");
  state.frameCache.clear();
  state.frameRenderSerial += 1;
  state.framePreloadKey = "";
  state.framePreloadPromise = null;
}

function cachedFrame(url) {
  const existing = state.frameCache.get(url);
  if (existing) return existing;
  const image = new Image();
  image.decoding = "async";
  const entry = { image, ready: false, promise: null };
  entry.promise = new Promise((resolve, reject) => {
    image.addEventListener("load", () => { entry.ready = true; resolve(image); }, { once: true });
    image.addEventListener("error", () => reject(new Error("PREVIEW_FRAME_LOAD_FAILED")), { once: true });
  });
  image.src = url;
  state.frameCache.set(url, entry);
  return entry;
}

function preloadPreviewFrames() {
  const frames = previewFrames();
  if (!frames.length) return Promise.resolve();
  const key = `${state.runId}:${frames.map((frame) => frame.artifact.id).join(",")}`;
  if (state.framePreloadKey === key && state.framePreloadPromise) return state.framePreloadPromise;
  state.framePreloadKey = key;
  state.framePreloadPromise = Promise.all(frames.map((frame) => cachedFrame(artifactUrl(frame.artifact.id)).promise));
  return state.framePreloadPromise;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.hidden = true; }, 2800);
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

function shortHash(value) {
  return typeof value === "string" && value.length > 16 ? `${value.slice(0, 12)}…${value.slice(-4)}` : String(value || "—");
}

function formatSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return "—";
  return seconds >= 60 ? `${(seconds / 60).toFixed(1)} min` : `${seconds.toFixed(1)} s`;
}

function formatPercent(value) {
  const ratio = Number(value);
  return Number.isFinite(ratio) ? `${(ratio * 100).toFixed(2)}%` : "—";
}

async function imageFacts(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => { resolve({ width: image.naturalWidth, height: image.naturalHeight }); URL.revokeObjectURL(url); };
    image.onerror = () => { reject(new Error("无法读取图片。")); URL.revokeObjectURL(url); };
    image.src = url;
  });
}

async function selectFile(file) {
  if (state.busy) return;
  stopPlayback();
  const fileSerial = ++state.fileSerial;
  resetSelectedFile();
  clearWorkbench();
  $("#run-error").hidden = true;
  if (!file || !["image/png", "image/jpeg"].includes(file.type)) {
    showError("只接受 PNG/JPEG。", false);
    $("#run-status").textContent = "请选择有效的 PNG/JPEG。";
    return;
  }
  if (file.size < 1 || file.size > 25 * 1024 * 1024) {
    showError("图片必须小于 25 MiB。", false);
    $("#run-status").textContent = "请选择符合大小限制的图片。";
    return;
  }
  $("#run-status").textContent = "正在读取图片。";
  try {
    const facts = await imageFacts(file);
    if (fileSerial !== state.fileSerial) return;
    if (facts.width > 2048 || facts.height > 2048 || facts.width * facts.height > 4_194_304) {
      showError("当前本地预研限制为单边 2048 px、总像素 4 MP。", false);
      $("#run-status").textContent = "请选择符合尺寸限制的图片。";
      return;
    }
    state.file = file;
    state.objectUrl = URL.createObjectURL(file);
    $("#source-image").src = state.objectUrl;
    $("#source-meta").textContent = `${file.type.replace("image/", "").toUpperCase()} · ${facts.width} × ${facts.height} · ${formatBytes(file.size)}`;
    $("#drop-zone").hidden = true;
    $("#source-preview").hidden = false;
    $("#run-status").textContent = "图片已就绪。";
    updateRunButton();
  } catch (error) {
    if (fileSerial !== state.fileSerial) return;
    showError(error.message, false);
    $("#run-status").textContent = "请选择可读取的图片。";
  }
}

function resetSelectedFile() {
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
  state.file = null;
  state.objectUrl = "";
  $("#source-file").value = "";
  $("#source-image").removeAttribute("src");
  $("#source-meta").textContent = "";
  $("#drop-zone").hidden = false;
  $("#source-preview").hidden = true;
  updateRunButton();
}

function clearFile() {
  if (state.busy) return;
  state.fileSerial += 1;
  resetSelectedFile();
  $("#run-error").hidden = true;
  $("#run-status").textContent = "选择一张图片。";
}

function updateRunButton() {
  const busy = state.busy;
  $("#run-button").disabled = !state.file || !$("#rights-confirmation").checked || busy;
  $$('input[name="workflow"]').forEach((radio) => { radio.disabled = busy; });
  $("#source-file").disabled = busy;
  $("#replace-file").disabled = busy;
  $("#rights-confirmation").disabled = busy;
  $("#run-select").disabled = busy;
  $("#drop-zone").tabIndex = busy ? -1 : 0;
  $("#drop-zone").setAttribute("aria-disabled", String(busy));
}

function showError(message, focus = true) {
  const error = $("#run-error");
  error.textContent = message;
  error.hidden = false;
  if (focus) error.focus();
}

function clearWorkbench() {
  stopPlayback();
  disposeLivePreview();
  resetFrameCache();
  $("#workbench").hidden = true;
  $("#result-frame").removeAttribute("src");
  $("#artifact-image").removeAttribute("src");
  $("#download-psd").removeAttribute("href");
  $("#download-depth-psd").removeAttribute("href");
}

function resetLiveReadout() {
  const values = {
    "head.yaw": 0,
    "head.pitch": 0,
    "eye.left.open": 1,
    "eye.right.open": 1,
    "mouth.open": 0,
  };
  updateLiveParameters(values);
  $("#tracking-fps").textContent = "0.0 FPS";
}

function updateLiveParameters(parameters) {
  const yaw = Number(parameters["head.yaw"] || 0);
  const pitch = Number(parameters["head.pitch"] || 0);
  const leftEye = Number(parameters["eye.left.open"] ?? 1);
  const rightEye = Number(parameters["eye.right.open"] ?? 1);
  const mouth = Number(parameters["mouth.open"] || 0);
  $("#param-head-yaw").textContent = `${yaw.toFixed(1)}°`;
  $("#param-head-pitch").textContent = `${pitch.toFixed(1)}°`;
  $("#param-eye-left").textContent = `${Math.round(leftEye * 100)}%`;
  $("#param-eye-right").textContent = `${Math.round(rightEye * 100)}%`;
  $("#param-mouth").textContent = `${Math.round(mouth * 100)}%`;
  $("#meter-head-yaw").value = yaw;
  $("#meter-head-pitch").value = pitch;
  $("#meter-eye-left").value = leftEye;
  $("#meter-eye-right").value = rightEye;
  $("#meter-mouth").value = mouth;
}

function updateCameraVisibility() {
  const visible = state.modelView === "camera" && Boolean(state.livePreview?.running) && $("#camera-preview-toggle").checked;
  $("#camera-pip").hidden = !visible;
}

function updateCameraDevices({ devices, activeDeviceId = "" }) {
  const select = $("#camera-device");
  const previous = select.value;
  select.replaceChildren(new Option("默认摄像头", ""));
  devices.forEach((device, index) => {
    select.add(new Option(device.label || `摄像头 ${index + 1}`, device.deviceId));
  });
  const preferred = activeDeviceId || previous;
  select.value = [...select.options].some((option) => option.value === preferred) ? preferred : "";
}

function updateLiveStatus({ state: status, label }) {
  const stage = $("#live-stage-status");
  const readout = $("#tracking-state");
  stage.dataset.state = status;
  stage.querySelector("span").textContent = label;
  readout.dataset.state = status;
  readout.textContent = label;
  const running = Boolean(state.livePreview?.running);
  const loading = status === "loading";
  $("#camera-start").disabled = running || loading;
  $("#camera-start span").textContent = loading ? "正在启动" : "开启摄像头";
  $("#camera-stop").disabled = !running;
  $("#camera-calibrate").disabled = !running;
  $("#camera-device").disabled = loading;
  updateCameraVisibility();
}

function disposeLivePreview() {
  state.livePreviewSerial += 1;
  if (state.livePreview) state.livePreview.dispose();
  state.livePreview = null;
  $("#camera-pip").hidden = true;
  resetLiveReadout();
}

async function ensureLivePreview() {
  if (state.livePreview) return state.livePreview;
  if (state.modelView !== "camera" || !hasMotionDraft()) return null;
  const serial = ++state.livePreviewSerial;
  const runId = state.runId;
  const report = state.report;
  updateLiveStatus({ state: "loading", label: "正在加载动态部件" });
  try {
    const { createLivePreviewController } = await import("/live_preview.mjs");
    if (serial !== state.livePreviewSerial || state.modelView !== "camera" || runId !== state.runId) return null;
    let controller;
    controller = createLivePreviewController({
      canvas: $("#live-canvas"),
      video: $("#camera-video"),
      report,
      resolveArtifactUrl: (id) => `/api/workbench/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(id)}`,
      onStatus: (value) => { if (state.livePreview === controller) updateLiveStatus(value); },
      onParameters: (value) => { if (state.livePreview === controller) updateLiveParameters(value); },
      onMetrics: ({ fps }) => { if (state.livePreview === controller) $("#tracking-fps").textContent = `${fps.toFixed(1)} FPS`; },
      onDevices: (value) => { if (state.livePreview === controller) updateCameraDevices(value); },
    });
    state.livePreview = controller;
    await controller.prepare();
    if (serial !== state.livePreviewSerial || state.livePreview !== controller) {
      controller.dispose();
      return null;
    }
    return controller;
  } catch (error) {
    if (serial === state.livePreviewSerial) updateLiveStatus({ state: "error", label: "实时模型加载失败" });
    console.error(error);
    return null;
  }
}

function beginActiveRun(runId) {
  clearInterval(state.pollTimer);
  state.pollTimer = 0;
  state.activeRunId = runId;
  state.busy = true;
  const epoch = ++state.runEpoch;
  updateRunButton();
  return epoch;
}

function isActiveRun(runId, epoch) {
  return state.busy && state.activeRunId === runId && state.runEpoch === epoch;
}

function finishActiveRun(runId, epoch) {
  if (!isActiveRun(runId, epoch)) return false;
  clearInterval(state.pollTimer);
  state.pollTimer = 0;
  state.activeRunId = "";
  state.busy = false;
  state.runEpoch += 1;
  updateRunButton();
  return true;
}

function schedulePolling(runId, epoch, workflow) {
  clearInterval(state.pollTimer);
  const delay = workflow === "model" ? 1000 : 250;
  state.pollTimer = window.setInterval(() => { void pollRun(runId, epoch); }, delay);
}

function updatePhases(report) {
  const workflow = report ? workflowOf(report) : state.workflow;
  const values = Array.isArray(report?.phases) ? report.phases : [];
  const ids = values.length ? values.map((item) => typeof item === "string" ? item : item.id) : workflowPhases[workflow];
  const byId = new Map(values.map((item) => typeof item === "string" ? [item, "pending"] : [item.id, item.state]));
  const list = $("#phase-list");
  list.style.setProperty("--phase-count", String(Math.max(1, ids.length)));
  list.replaceChildren();
  ids.forEach((id, index) => {
    const item = document.createElement("li");
    const number = document.createElement("i");
    const label = document.createElement("span");
    const status = document.createElement("b");
    const value = byId.get(id) || "pending";
    item.dataset.phase = id;
    item.dataset.state = value;
    number.textContent = String(index + 1).padStart(2, "0");
    label.textContent = phaseLabels[id] || id;
    status.textContent = phaseText[value] || value;
    item.append(number, label, status);
    list.append(item);
  });
  const qualityReview = report?.state === "completed" && workflowOf(report) === "model" && report?.quality?.status === "review_required";
  const stateLabel = qualityReview ? "待质量复核" : report?.state === "completed" ? "完成" : report?.state === "blocked" ? "已阻止" : report?.state === "failed" ? "失败" : ["running", "submitted"].includes(report?.state) ? "处理中" : "待运行";
  $("#pipeline-state").textContent = stateLabel;
}

async function loadRuns(preferred = "") {
  const listSerial = ++state.loadSerial;
  const payload = await request("/api/workbench/runs");
  if (listSerial !== state.loadSerial) return;
  const select = $("#run-select");
  const running = payload.runs.find((run) => ["submitted", "running"].includes(run.state));
  const current = preferred || state.runId || (payload.running ? running?.run_id : "") || select.value;
  select.replaceChildren();
  if (!payload.runs.length) {
    select.add(new Option("暂无运行", ""));
    return;
  }
  for (const run of payload.runs) {
    const kind = workflowOf(run) === "model" ? "模型" : "基线";
    select.add(new Option(`${run.run_id} · ${kind} · ${run.local_status}`, run.run_id));
  }
  select.value = payload.runs.some((run) => run.run_id === current) ? current : payload.runs[0].run_id;
  await loadRun(select.value);
}

async function loadRun(runId) {
  if (!runId) return;
  stopPlayback();
  disposeLivePreview();
  const loadSerial = ++state.loadSerial;
  const report = await request(`/api/workbench/runs/${encodeURIComponent(runId)}`);
  if (loadSerial !== state.loadSerial) return;
  if (state.runId !== runId) resetFrameCache();
  state.runId = runId;
  state.report = report;
  state.modelView = defaultModelView(report);
  $("#run-error").hidden = true;
  syncWorkflow(workflowOf(state.report));
  updatePhases(state.report);
  if (state.report.state === "completed") {
    state.frame = 0;
    state.showDepth = false;
    state.artifactId = workflowOf(state.report) === "model" ? state.report.model.layers[0]?.artifact.id : "normalized";
    renderWorkbench();
    $("#run-status").textContent = workflowOf(state.report) === "model" ? modelCompletionStatus() : "37 帧与 PSD 已生成。";
  } else if (["submitted", "running"].includes(state.report.state)) {
    clearWorkbench();
    const workflow = workflowOf(state.report);
    $("#run-status").textContent = workflow === "model" ? "本地模型处理中。" : "本地处理中。";
    const epoch = beginActiveRun(runId);
    schedulePolling(runId, epoch, workflow);
  } else if (["blocked", "failed", "cancelled"].includes(state.report.state)) {
    clearWorkbench();
    $("#run-status").textContent = state.report.state === "blocked" ? "运行已阻止。" : state.report.state === "cancelled" ? "运行已取消。" : "运行失败。";
    showError(`${state.report.reason_code || "LOCAL_WORKBENCH_NOT_COMPLETED"}`, false);
  } else {
    clearWorkbench();
  }
}

async function pollRun(runId, epoch) {
  if (!isActiveRun(runId, epoch)) return;
  try {
    const report = await request(`/api/workbench/runs/${encodeURIComponent(runId)}`);
    if (!isActiveRun(runId, epoch)) return;
    state.runId = runId;
    state.report = report;
    updatePhases(report);
    const model = workflowOf(report) === "model";
    $("#run-status").textContent = ["submitted", "running"].includes(report.state) ? (model ? "本地模型处理中。" : "本地处理中。") : report.state === "completed" ? (model ? modelCompletionStatus(report) : "37 帧与 PSD 已生成。") : report.state === "blocked" ? "运行已阻止。" : report.state === "cancelled" ? "运行已取消。" : "运行失败。";
    if (["completed", "blocked", "failed", "cancelled"].includes(report.state)) {
      if (!finishActiveRun(runId, epoch)) return;
      if (report.state === "completed") {
        state.frame = 0;
        state.modelView = defaultModelView(report);
        state.showDepth = false;
        state.artifactId = model ? report.model.layers[0]?.artifact.id : "normalized";
        renderWorkbench();
        await loadRuns(runId);
      } else {
        clearWorkbench();
        showError(report.reason_code || "LOCAL_WORKBENCH_NOT_COMPLETED");
      }
    }
  } catch (error) {
    if (!finishActiveRun(runId, epoch)) return;
    clearWorkbench();
    $("#run-status").textContent = "无法继续获取运行状态。";
    showError(error.message);
  }
}

function renderWorkbench() {
  $("#workbench").hidden = false;
  if (workflowOf() === "model") renderModelWorkbench();
  else renderBaselineWorkbench();
  $("#workbench").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

function renderBaselineWorkbench() {
  disposeLivePreview();
  $("#result-title").textContent = "最终预览";
  $("#result-subtitle").textContent = "固定区域变形 · 非模型";
  $("#model-view-switch").hidden = true;
  $("#model-quality").hidden = true;
  $("#model-identity").hidden = true;
  $("#playback-controls").hidden = false;
  $("#filmstrip").hidden = false;
  $("#live-controls").hidden = true;
  $("#live-canvas").hidden = true;
  $("#result-frame").hidden = false;
  $("#live-stage-status").hidden = true;
  $("#depth-control").hidden = true;
  renderFilmstrip();
  renderFrame();
  renderArtifactTabs();
  renderArtifact();
  $("#download-psd").href = artifactUrl("output-psd");
  $("#download-psd").download = "local-deterministic-baseline.psd";
  $("#download-psd").textContent = "下载 PSD";
  $("#download-depth-psd").hidden = true;
  $("#output-title").textContent = "输出";
  $("#output-status").textContent = "结构回读通过 · 未经外部编辑器验证";
  $("#output-boundary").textContent = ".oc2d 尚未实现";
}

function renderModelWorkbench() {
  stopPlayback();
  const model = state.report.model;
  const motionAvailable = hasMotionDraft();
  const viewSwitch = $("#model-view-switch");
  const motionButton = $('[data-model-view="motion"]');
  const cameraButton = $('[data-model-view="camera"]');
  motionButton.hidden = !motionAvailable;
  cameraButton.hidden = !motionAvailable;
  viewSwitch.style.setProperty("--model-view-count", motionAvailable ? "4" : "2");
  viewSwitch.hidden = false;
  $("#model-quality").hidden = false;
  $("#depth-control").hidden = false;
  renderModelFrame();
  renderModelQuality();
  renderModelIdentity();
  renderArtifactTabs();
  renderArtifact();
  $("#download-psd").href = artifactUrl("output-psd");
  $("#download-psd").download = "local-see-through-layers.psd";
  $("#download-psd").textContent = "语义图层 PSD";
  $("#download-depth-psd").href = artifactUrl("output-depth-psd");
  $("#download-depth-psd").textContent = "逐层深度 PSD";
  $("#download-depth-psd").hidden = false;
  $("#output-title").textContent = "研究产物";
  $("#output-status").textContent = motionAvailable
    ? `${state.report.psd.layer_count} 层主 PSD · ${state.report.motion_draft.frames.length} 帧本地动态预览 · 外部编辑器未评估`
    : `${state.report.psd.layer_count} 层主 PSD · 主/深度 PSD 内部结构回读通过 · 外部编辑器未评估`;
  $("#output-boundary").textContent = motionAvailable
    ? "四边形网格与仿射绑定仅为研究初稿 · 未生成 .oc2d"
    : "网格、参数绑定、动态预览与 .oc2d 均未生成";
}

function renderModelFrame() {
  const model = state.report.model;
  const views = hasMotionDraft() ? ["source", "reconstruction", "motion", "camera"] : ["source", "reconstruction"];
  if (!views.includes(state.modelView)) state.modelView = defaultModelView();
  if (state.modelView === "camera") {
    stopPlayback();
    $("#result-title").textContent = "摄像头驱动预览";
    $("#result-subtitle").textContent = "MediaPipe · Kalidokit · 本地实时合成";
    $("#playback-controls").hidden = true;
    $("#filmstrip").hidden = true;
    $("#live-controls").hidden = false;
    $("#result-frame").hidden = true;
    $("#live-canvas").hidden = false;
    $("#live-stage-status").hidden = false;
    $("#frame-source").textContent = "LIVE · LOCAL";
    $("#frame-id").textContent = "camera.live";
    $("#frame-count").textContent = "478 LANDMARKS · 5 PARAMS";
    $$('[data-model-view]').forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.modelView === state.modelView)));
    void ensureLivePreview();
    return;
  }
  disposeLivePreview();
  $("#live-controls").hidden = true;
  $("#live-canvas").hidden = true;
  $("#result-frame").hidden = false;
  $("#live-stage-status").hidden = true;
  if (state.modelView === "motion") {
    $("#result-title").textContent = "动态模型初稿";
    $("#result-subtitle").textContent = `${model.identity.profile_id} · 四边形 / 仿射`;
    $("#playback-controls").hidden = false;
    $("#filmstrip").hidden = false;
    state.frame = Math.min(state.frame, previewFrames().length - 1);
    void preloadPreviewFrames().catch(() => {});
    renderFilmstrip();
    renderFrame();
    $$("[data-model-view]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.modelView === state.modelView)));
    return;
  }
  stopPlayback();
  $("#result-title").textContent = "静态模型重建";
  $("#result-subtitle").textContent = model.identity.profile_id;
  $("#playback-controls").hidden = true;
  $("#filmstrip").hidden = true;
  const source = state.modelView === "source";
  const descriptor = source ? model.source : model.reconstruction;
  $("#frame-source").textContent = source ? "SOURCE" : "MODEL";
  $("#result-frame").src = artifactUrl(descriptor.id);
  $("#result-frame").alt = source ? "模型规范化输入证据" : "See-through 静态重建";
  $("#frame-id").textContent = source ? "source" : "reconstruction";
  $("#frame-count").textContent = source ? `${descriptor.width} × ${descriptor.height}` : `${model.semantic_intermediate_count} RGBA · ${model.depth_intermediate_count} DEPTH`;
  $$("[data-model-view]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.modelView === state.modelView)));
}

function renderModelQuality() {
  const quality = state.report.quality;
  const fidelity = quality.neutral_fidelity;
  const qualityState = $("#model-quality-state");
  qualityState.textContent = quality.status === "review_required" ? "待人工复核" : "通过";
  qualityState.dataset.state = quality.status;
  const facts = [
    ["可见画布覆盖", formatPercent(fidelity.visible_canvas_ratio)],
    ["原图 RGB 完全一致", formatPercent(fidelity.source_rgb_exact_ratio)],
    ["RGB 平均误差", Number(fidelity.source_rgb_mae).toFixed(2)],
    ["中性保真", fidelity.status === "pass" ? "通过阈值" : "需要复核"],
  ];
  const factList = $("#model-quality-facts");
  factList.replaceChildren();
  facts.forEach(([label, value]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const definition = document.createElement("dd");
    term.textContent = label;
    definition.textContent = value;
    row.append(term, definition);
    factList.append(row);
  });
  const capabilityList = $("#model-capabilities");
  capabilityList.replaceChildren();
  Object.entries(state.report.capabilities).forEach(([id, capabilityState]) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    const value = document.createElement("b");
    label.textContent = capabilityLabels[id] || id;
    value.textContent = capabilityStateText[capabilityState] || capabilityState;
    value.dataset.state = capabilityState;
    item.append(label, value);
    capabilityList.append(item);
  });
}

function renderModelIdentity() {
  const model = state.report.model;
  const identity = model.identity;
  const stats = model.stats;
  const values = [
    ["Profile", identity.profile_id],
    ["Upstream", shortHash(identity.upstream_commit)],
    ["入口摘要", shortHash(identity.entrypoint_sha256)],
    ["固定推理", `${String(identity.quantization).toUpperCase()} · seed ${identity.seed} · ${identity.resolution}px · ${identity.inference_steps} steps`],
    ["可见像素保真", identity.postprocess_algorithm === "not_applied" ? "旧 profile · 未应用" : identity.postprocess_algorithm],
    ["总耗时", formatSeconds(stats.total_time_s)],
    ["记录峰值", `${Number(stats.peak_vram_gb).toFixed(2)} GiB`],
  ];
  const list = $("#model-identity");
  list.replaceChildren();
  values.forEach(([label, value]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const definition = document.createElement("dd");
    term.textContent = label;
    definition.textContent = value;
    row.append(term, definition);
    list.append(row);
  });
  list.hidden = false;
}

function renderFilmstrip() {
  const strip = $("#filmstrip");
  strip.replaceChildren();
  const frames = previewFrames();
  const motion = workflowOf() === "model";
  strip.setAttribute("aria-label", `${frames.length} 帧${motion ? "动态研究初稿" : "基线预览"}`);
  frames.forEach((frame, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "frame-cell";
    button.role = "option";
    button.dataset.frame = String(index);
    button.setAttribute("aria-label", `${motion ? "动态初稿" : "基线"}帧 ${index + 1}：${frame.id}`);
    button.setAttribute("aria-selected", String(index === state.frame));
    button.tabIndex = index === state.frame ? 0 : -1;
    button.textContent = String(index + 1).padStart(2, "0");
    button.addEventListener("click", () => selectFrame(index));
    button.addEventListener("keydown", (event) => {
      let target = null;
      if (event.key === "ArrowLeft") target = index - 1;
      else if (event.key === "ArrowRight") target = index + 1;
      else if (event.key === "Home") target = 0;
      else if (event.key === "End") target = frames.length - 1;
      if (target === null) return;
      event.preventDefault();
      selectFrame(target, true);
    });
    strip.append(button);
  });
  $("#frame-slider").max = String(frames.length - 1);
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  $("#play-toggle").disabled = reducedMotion;
  $("#play-toggle").title = reducedMotion ? "系统已启用减少动态效果，可逐帧查看" : "";
}

function renderFrame() {
  const frames = previewFrames();
  const frame = frames[state.frame];
  if (!frame) return;
  const frameIndex = state.frame;
  const frameUrl = artifactUrl(frame.artifact.id);
  const renderSerial = ++state.frameRenderSerial;
  const resultFrame = $("#result-frame");
  const entry = cachedFrame(frameUrl);
  const applyFrame = () => {
    if (renderSerial === state.frameRenderSerial && frameIndex === state.frame) resultFrame.src = frameUrl;
  };
  if (entry.ready) applyFrame();
  else entry.promise.then(applyFrame).catch(() => {});
  resultFrame.alt = workflowOf() === "model" ? `模型动态研究初稿第 ${state.frame + 1} 帧：${frame.id}` : `固定区域生成帧 ${state.frame + 1}：${frame.id}`;
  $("#frame-id").textContent = frame.id;
  $("#frame-count").textContent = `${String(state.frame + 1).padStart(3, "0")} / ${String(frames.length).padStart(3, "0")}`;
  $("#frame-source").textContent = frame.source.replace("seeded-", "").toUpperCase();
  $("#frame-slider").value = String(state.frame);
  $$(".frame-cell").forEach((button, index) => {
    const selected = index === state.frame;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  $(`.frame-cell[data-frame="${state.frame}"]`)?.scrollIntoView({ behavior: "auto", inline: "center", block: "nearest" });
}

function selectFrame(index, focus = false) {
  const length = previewFrames().length;
  if (!length) return;
  state.frame = (index + length) % length;
  renderFrame();
  if (focus) $(`.frame-cell[data-frame="${state.frame}"]`)?.focus({ preventScroll: true });
}

async function startPlayback() {
  if (!previewFrames().length || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const request = ++state.playRequest;
  const button = $("#play-toggle");
  button.disabled = true;
  button.querySelector("span").textContent = "正在准备";
  try {
    await preloadPreviewFrames();
  } catch {
    if (request === state.playRequest) toast("动态帧加载失败");
    stopPlayback();
    return;
  }
  if (request !== state.playRequest || state.modelView === "camera") return;
  button.disabled = false;
  $("#play-toggle").setAttribute("aria-pressed", "true");
  $("#play-toggle span").textContent = "暂停";
  state.playTimer = setInterval(() => selectFrame(state.frame + 1), 240);
}

function stopPlayback() {
  state.playRequest += 1;
  clearInterval(state.playTimer);
  state.playTimer = 0;
  $("#play-toggle")?.setAttribute("aria-pressed", "false");
  if ($("#play-toggle")) $("#play-toggle").disabled = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if ($("#play-toggle span")) $("#play-toggle span").textContent = "播放";
}

function artifacts() {
  if (!state.report) return [];
  if (workflowOf() === "model") {
    const result = [];
    if (state.report.normalization?.artifact) {
      const normalization = state.report.normalization;
      result.push({
        id: normalization.artifact.id,
        label: "标准化输入",
        caption: "sRGB · RGBA8 · 元数据已移除",
        stage: "栅格标准化",
        artifact: normalization.artifact,
        depthArtifact: null,
        facts: [
          ["输入", `${normalization.input.format} · ${normalization.input.width} × ${normalization.input.height}`],
          ["输出", `${normalization.output.width} × ${normalization.output.height} · RGBA8`],
        ],
      });
    }
    state.report.model.layers.forEach((layer) => {
      result.push({
        id: layer.artifact.id,
        label: semanticLabels[layer.name] || layer.name,
        caption: layer.name,
        stage: "See-through 语义 RGBA",
        artifact: layer.artifact,
        depthArtifact: layer.depth_artifact,
        facts: [
          ["语义", layer.name],
          ["画布", `${layer.artifact.width} × ${layer.artifact.height}`],
          ["格式", layer.artifact.mode],
          ["逐层深度", layer.depth_artifact ? "可用" : "无"],
        ],
      });
    });
    return result;
  }
  const normalized = { id: "normalized", label: "标准化", caption: "sRGB · RGBA8 · 元数据已移除", stage: "栅格标准化", artifact: state.report.normalization.artifact, depthArtifact: null, facts: [
    ["输入", `${state.report.normalization.input.format} · ${state.report.normalization.input.width} × ${state.report.normalization.input.height}`],
    ["输出", `${state.report.normalization.output.width} × ${state.report.normalization.output.height} · RGBA8`],
    ["色彩", state.report.normalization.color_policy],
    ["方向", state.report.normalization.orientation.applied ? `已应用 EXIF ${state.report.normalization.orientation.value}` : "未变换"],
  ] };
  const labels = {
    "layer.torso-base": "底图与填补",
    "layer.head": "头部固定区域",
    "layer.eye.screen-left": "角色右眼区域",
    "layer.eye.screen-right": "角色左眼区域",
    "layer.mouth": "嘴部固定区域",
  };
  const layers = state.report.candidate.layers.map((layer) => ({
    id: layer.artifact.id,
    label: labels[layer.id] || layer.id,
    caption: `${layer.slot_id} · ${layer.side}`,
    stage: "固定区域基线",
    artifact: layer.artifact,
    depthArtifact: null,
    facts: [["区域", layer.box_ltrb.join(", ")], ["生成填补", layer.generated_fill ? "是" : "否"], ["来源", layer.generated_fill ? "固定角点均值" : "源像素"]],
  }));
  return [normalized, ...layers];
}

function renderArtifactTabs() {
  const tabs = $("#artifact-tabs");
  const values = artifacts();
  tabs.replaceChildren();
  if (!values.some((item) => item.id === state.artifactId)) state.artifactId = values[0]?.id || "";
  if (workflowOf() === "model") {
    const select = document.createElement("select");
    select.setAttribute("aria-label", "模型语义层");
    values.forEach((artifact) => select.add(new Option(artifact.label, artifact.id)));
    select.value = state.artifactId;
    select.addEventListener("change", () => { state.artifactId = select.value; renderArtifact(); });
    tabs.append(select);
    return;
  }
  for (const artifact of values) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = artifact.label;
    button.dataset.artifact = artifact.id;
    button.setAttribute("aria-pressed", String(artifact.id === state.artifactId));
    button.addEventListener("click", () => { state.artifactId = artifact.id; renderArtifactTabs(); renderArtifact(); });
    tabs.append(button);
  }
}

function renderArtifact() {
  const values = artifacts();
  const artifact = values.find((item) => item.id === state.artifactId) || values[0];
  if (!artifact) return;
  const useDepth = workflowOf() === "model" && state.showDepth && Boolean(artifact.depthArtifact);
  const descriptor = useDepth ? artifact.depthArtifact : artifact.artifact;
  $("#artifact-image").src = artifactUrl(descriptor.id);
  $("#artifact-image").alt = `${artifact.label}${useDepth ? "深度图" : ""}`;
  $("#artifact-caption").textContent = useDepth ? `${artifact.caption} · 单通道深度` : artifact.caption;
  $("#artifact-stage").textContent = useDepth ? "Marigold 逐层深度" : artifact.stage;
  const toggle = $("#depth-toggle");
  toggle.disabled = !artifact.depthArtifact;
  toggle.checked = useDepth;
  const facts = $("#artifact-facts");
  facts.replaceChildren();
  const rows = [...artifact.facts];
  if (workflowOf() === "model") rows.push(["当前视图", useDepth ? "深度" : "RGBA"]);
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const definition = document.createElement("dd");
    term.textContent = label;
    definition.textContent = value;
    row.append(term, definition);
    facts.append(row);
  }
}

$("#theme-toggle").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
$("#source-file").addEventListener("change", (event) => selectFile(event.target.files?.[0]));
$("#replace-file").addEventListener("click", () => { if (!state.busy) { clearFile(); $("#source-file").click(); } });
$("#rights-confirmation").addEventListener("change", updateRunButton);
$$('input[name="workflow"]').forEach((radio) => radio.addEventListener("change", () => {
  if (!radio.checked || state.busy) return;
  stopPlayback();
  state.loadSerial += 1;
  state.report = null;
  state.runId = "";
  state.showDepth = false;
  state.modelView = "reconstruction";
  syncWorkflow(radio.value);
  updatePhases(null);
  clearWorkbench();
}));
$("#drop-zone").addEventListener("keydown", (event) => { if (!state.busy && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); $("#source-file").click(); } });
for (const type of ["dragenter", "dragover"]) {
  $("#drop-zone").addEventListener(type, (event) => { event.preventDefault(); if (!state.busy) $("#drop-zone").classList.add("is-dragging"); });
}
for (const type of ["dragleave", "drop"]) {
  $("#drop-zone").addEventListener(type, (event) => { event.preventDefault(); $("#drop-zone").classList.remove("is-dragging"); });
}
$("#drop-zone").addEventListener("drop", (event) => { if (!state.busy) void selectFile(event.dataTransfer?.files?.[0]); });
$("#source-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy || !state.file || !$("#rights-confirmation").checked) return;
  const file = state.file;
  const workflow = state.workflow;
  const runId = newRunId();
  state.loadSerial += 1;
  state.report = null;
  state.runId = runId;
  state.showDepth = false;
  state.modelView = "reconstruction";
  clearWorkbench();
  updatePhases(null);
  $("#run-error").hidden = true;
  const epoch = beginActiveRun(runId);
  $("#run-status").textContent = "正在接收图片。";
  try {
    const report = await request(`/api/workbench/runs/${encodeURIComponent(runId)}`, {
      method: "POST",
      headers: { "Content-Type": file.type, "X-OneClick2D-Workflow": workflow },
      body: file,
    });
    if (!isActiveRun(runId, epoch)) return;
    state.report = report;
    updatePhases(state.report);
    schedulePolling(runId, epoch, workflow);
    await pollRun(runId, epoch);
  } catch (error) {
    if (!finishActiveRun(runId, epoch)) return;
    clearWorkbench();
    showError(error.message);
    state.runId = "";
    state.report = null;
    $("#run-status").textContent = "运行未开始。";
  }
});
$("#run-select").addEventListener("change", (event) => loadRun(event.target.value).catch((error) => { clearWorkbench(); showError(error.message); }));
$("#prev-frame").addEventListener("click", () => selectFrame(state.frame - 1));
$("#next-frame").addEventListener("click", () => selectFrame(state.frame + 1));
$("#frame-slider").addEventListener("input", (event) => selectFrame(Number(event.target.value)));
$("#play-toggle").addEventListener("click", () => state.playTimer ? stopPlayback() : void startPlayback());
$("#depth-toggle").addEventListener("change", (event) => { state.showDepth = event.target.checked; renderArtifact(); });
$$("[data-model-view]").forEach((button) => button.addEventListener("click", () => {
  if (workflowOf() !== "model" || state.report?.state !== "completed") return;
  stopPlayback();
  if (state.modelView === "camera" && button.dataset.modelView !== "camera") disposeLivePreview();
  state.frame = 0;
  state.modelView = button.dataset.modelView;
  renderModelFrame();
}));
$("#camera-start").addEventListener("click", async () => {
  const controller = await ensureLivePreview();
  if (controller && state.modelView === "camera") await controller.start($("#camera-device").value);
});
$("#camera-stop").addEventListener("click", () => state.livePreview?.stop());
$("#camera-calibrate").addEventListener("click", () => state.livePreview?.calibrate());
$("#camera-device").addEventListener("change", async (event) => {
  if (state.livePreview?.running) await state.livePreview.restart(event.target.value);
});
$("#camera-preview-toggle").addEventListener("change", updateCameraVisibility);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) return;
  stopPlayback();
  state.livePreview?.stop();
});
document.addEventListener("keydown", (event) => {
  if (!previewFrames().length || ["INPUT", "SELECT", "BUTTON", "A"].includes(document.activeElement?.tagName)) return;
  if (event.key === "ArrowLeft") { event.preventDefault(); selectFrame(state.frame - 1); }
  if (event.key === "ArrowRight") { event.preventDefault(); selectFrame(state.frame + 1); }
});
window.addEventListener("beforeunload", () => {
  state.livePreview?.dispose();
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
});

setTheme(initialTheme());
syncWorkflow("baseline");
updatePhases(null);
loadRuns().catch((error) => toast(error.message));
