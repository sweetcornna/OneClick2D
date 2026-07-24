"use strict";

const MEDIAPIPE_ROOT = "/vendor/mediapipe-tasks-vision-0.10.35";
const KALIDOKIT_URL = "/vendor/kalidokit-1.1.5/kalidokit.es.js";
const TRACK_INTERVAL_MS = 1000 / 30;
const CALIBRATION_SAMPLE_COUNT = 18;
const LOST_AFTER_MS = 450;
const RELAX_POSE_AFTER_MS = 1800;

export const NEUTRAL_PARAMETERS = Object.freeze({
  "head.yaw": 0,
  "head.pitch": 0,
  "eye.left.open": 1,
  "eye.right.open": 1,
  "mouth.open": 0,
});

export function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, Number(value)));
}

export function composeAffine(outer, inner) {
  return {
    a: outer.a * inner.a + outer.b * inner.d,
    b: outer.a * inner.b + outer.b * inner.e,
    c: outer.a * inner.c + outer.b * inner.f + outer.c,
    d: outer.d * inner.a + outer.e * inner.d,
    e: outer.d * inner.b + outer.e * inner.e,
    f: outer.d * inner.c + outer.e * inner.f + outer.f,
  };
}

function percentile(values, ratio) {
  const sorted = [...values].sort((left, right) => left - right);
  if (!sorted.length) return 0;
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * ratio)))];
}

export function calibrationFromSamples(samples) {
  if (!Array.isArray(samples) || !samples.length) throw new Error("CALIBRATION_SAMPLES_REQUIRED");
  return {
    yaw: percentile(samples.map((item) => item.yaw), 0.5),
    pitch: percentile(samples.map((item) => item.pitch), 0.5),
    eyeLeft: Math.max(0.2, percentile(samples.map((item) => item.eyeLeft), 0.9)),
    eyeRight: Math.max(0.2, percentile(samples.map((item) => item.eyeRight), 0.9)),
    mouth: percentile(samples.map((item) => item.mouth), 0.15),
  };
}

export function parametersFromRig(rig, calibration) {
  if (!rig?.head?.degrees || !rig.eye || !rig.mouth || !calibration) return { ...NEUTRAL_PARAMETERS };
  return {
    "head.yaw": clamp(rig.head.degrees.y - calibration.yaw, -15, 15),
    "head.pitch": clamp(rig.head.degrees.x - calibration.pitch, -10, 10),
    "eye.left.open": clamp(rig.eye.l / calibration.eyeLeft, 0, 1),
    "eye.right.open": clamp(rig.eye.r / calibration.eyeRight, 0, 1),
    "mouth.open": clamp((rig.mouth.y - calibration.mouth) * 1.65, 0, 1),
  };
}

export function classifyFrameStats(mean, standardDeviation) {
  if (!Number.isFinite(mean) || !Number.isFinite(standardDeviation)) return "unavailable";
  if (mean < 12 || (mean < 70 && standardDeviation < 2.5)) return "blocked";
  return "usable";
}

export function shouldRelaxPose(elapsedWithoutFaceMs) {
  return Number.isFinite(elapsedWithoutFaceMs) && elapsedWithoutFaceMs >= RELAX_POSE_AFTER_MS;
}

function rawSample(rig) {
  return {
    yaw: Number(rig.head.degrees.y),
    pitch: Number(rig.head.degrees.x),
    eyeLeft: Number(rig.eye.l),
    eyeRight: Number(rig.eye.r),
    mouth: Number(rig.mouth.y),
  };
}

function smoothParameters(current, target, elapsedMs, { relaxing = false } = {}) {
  const result = {};
  for (const id of Object.keys(NEUTRAL_PARAMETERS)) {
    const timeConstant = relaxing ? 180 : id.startsWith("eye.") ? 42 : id === "mouth.open" ? 58 : 88;
    const alpha = 1 - Math.exp(-Math.max(1, elapsedMs) / timeConstant);
    result[id] = current[id] + (target[id] - current[id]) * alpha;
  }
  return result;
}

function identityAffine() {
  return { a: 1, b: 0, c: 0, d: 0, e: 1, f: 0 };
}

function headAffine(parameters, pivotY, canvasSize) {
  const yaw = clamp(parameters["head.yaw"] / 15, -1, 1);
  const pitch = clamp(parameters["head.pitch"] / 10, -1, 1);
  const shear = 0.03 * yaw;
  return {
    a: 1,
    b: shear,
    c: (canvasSize / 50) * yaw - shear * pivotY,
    d: 0,
    e: 1,
    f: -(canvasSize / 64) * pitch,
  };
}

function scaleYAffine(scale, pivotY) {
  return { a: 1, b: 0, c: 0, d: 0, e: scale, f: (1 - scale) * pivotY };
}

export function partAffine(layer, parameters, head) {
  if (layer.motion_group === "static") return identityAffine();
  if (layer.motion_group === "head") return head;
  const [, top, , bottom] = layer.box_ltrb;
  const pivotY = (top + bottom) / 2;
  if (layer.motion_group === "eye") {
    const openness = clamp(parameters[`eye.${layer.side}.open`], 0, 1);
    return composeAffine(head, scaleYAffine(0.05 + 0.95 * openness, pivotY));
  }
  if (layer.motion_group === "mouth") {
    return composeAffine(head, scaleYAffine(1 + 0.5 * clamp(parameters["mouth.open"], 0, 1), pivotY));
  }
  throw new Error("LIVE_LAYER_MOTION_GROUP_INVALID");
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.addEventListener("load", () => resolve(image), { once: true });
    image.addEventListener("error", () => reject(new Error("LIVE_LAYER_LOAD_FAILED")), { once: true });
    image.src = url;
  });
}

export function versionedArtifactUrl(url, descriptor) {
  if (!descriptor?.sha256) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}sha256=${encodeURIComponent(descriptor.sha256)}`;
}

class AffineModelRenderer {
  constructor(canvas, report, resolveArtifactUrl) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d", { alpha: true });
    if (!this.context) throw new Error("CANVAS_2D_UNAVAILABLE");
    this.compositionCanvas = document.createElement("canvas");
    this.compositionContext = this.compositionCanvas.getContext("2d", { alpha: true });
    if (!this.compositionContext) throw new Error("CANVAS_2D_UNAVAILABLE");
    this.report = report;
    this.resolveArtifactUrl = resolveArtifactUrl;
    this.images = new Map();
    this.layers = [...report.motion_draft.layers].sort((left, right) => left.draw_order - right.draw_order || left.id.localeCompare(right.id));
    const [width, height] = report.motion_draft.input.canvas;
    if (width !== height || !Number.isInteger(width) || width <= 0) throw new Error("LIVE_CANVAS_INVALID");
    this.canvasSize = width;
    this.canvas.width = width;
    this.canvas.height = height;
    this.compositionCanvas.width = width;
    this.compositionCanvas.height = height;
    const moving = this.layers.filter((layer) => layer.motion_group !== "static").map((layer) => layer.box_ltrb);
    this.headPivotY = (Math.min(...moving.map((box) => box[1])) + Math.max(...moving.map((box) => box[3]))) / 2;
  }

  async load() {
    const loaded = await Promise.all(this.layers.map(async (layer) => {
      const descriptor = layer.artifact;
      if (!descriptor?.id) throw new Error("LIVE_LAYER_DESCRIPTOR_MISSING");
      const image = await loadImage(versionedArtifactUrl(this.resolveArtifactUrl(descriptor.id), descriptor));
      const [left, top, right, bottom] = layer.box_ltrb;
      if (image.naturalWidth !== right - left || image.naturalHeight !== bottom - top) throw new Error("LIVE_LAYER_SIZE_MISMATCH");
      return [layer.id, image];
    }));
    this.images = new Map(loaded);
    this.draw(NEUTRAL_PARAMETERS);
  }

  draw(parameters) {
    const context = this.compositionContext;
    context.save();
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, this.canvasSize, this.canvasSize);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.globalCompositeOperation = "source-over";
    const head = headAffine(parameters, this.headPivotY, this.canvasSize);
    for (const layer of this.layers) {
      const image = this.images.get(layer.id);
      if (!image) continue;
      const [left, top, right, bottom] = layer.box_ltrb;
      const transform = partAffine(layer, parameters, head);
      context.setTransform(transform.a, transform.d, transform.b, transform.e, transform.c, transform.f);
      context.drawImage(image, left, top, right - left, bottom - top);
    }
    context.restore();
    this.context.setTransform(1, 0, 0, 1, 0, 0);
    this.context.clearRect(0, 0, this.canvasSize, this.canvasSize);
    this.context.drawImage(this.compositionCanvas, 0, 0);
  }

  dispose() {
    for (const image of this.images.values()) image.removeAttribute("src");
    this.images.clear();
    this.compositionCanvas.width = 0;
    this.compositionCanvas.height = 0;
  }
}

function cameraErrorMessage(error) {
  if (error?.name === "NotAllowedError" || error?.name === "SecurityError") return "摄像头权限未开启";
  if (error?.name === "NotFoundError" || error?.name === "DevicesNotFoundError") return "未找到可用摄像头";
  if (error?.name === "NotReadableError" || error?.name === "TrackStartError") return "摄像头正在被其他程序使用";
  return "摄像头预览启动失败";
}

async function createFaceLandmarker() {
  const [{ FilesetResolver, FaceLandmarker }, { Face }] = await Promise.all([
    import(`${MEDIAPIPE_ROOT}/vision_bundle.mjs`),
    import(KALIDOKIT_URL),
  ]);
  const vision = await FilesetResolver.forVisionTasks(`${MEDIAPIPE_ROOT}/wasm`);
  const options = {
    baseOptions: { modelAssetPath: `${MEDIAPIPE_ROOT}/face_landmarker.task`, delegate: "GPU" },
    runningMode: "VIDEO",
    numFaces: 1,
    minFaceDetectionConfidence: 0.55,
    minFacePresenceConfidence: 0.55,
    minTrackingConfidence: 0.55,
    outputFaceBlendshapes: false,
    outputFacialTransformationMatrixes: false,
  };
  let landmarker;
  try {
    landmarker = await FaceLandmarker.createFromOptions(vision, options);
  } catch {
    options.baseOptions.delegate = "CPU";
    landmarker = await FaceLandmarker.createFromOptions(vision, options);
  }
  return { landmarker, Face };
}

export class LivePreviewController {
  constructor({ canvas, video, report, resolveArtifactUrl, onStatus, onParameters, onMetrics, onDevices }) {
    this.canvas = canvas;
    this.video = video;
    this.renderer = new AffineModelRenderer(canvas, report, resolveArtifactUrl);
    this.onStatus = onStatus;
    this.onParameters = onParameters;
    this.onMetrics = onMetrics;
    this.onDevices = onDevices;
    this.stream = null;
    this.landmarker = null;
    this.Face = null;
    this.animationFrame = 0;
    this.running = false;
    this.disposed = false;
    this.startEpoch = 0;
    this.prepared = false;
    this.calibration = null;
    this.calibrationSamples = [];
    this.parameters = { ...NEUTRAL_PARAMETERS };
    this.targetParameters = { ...NEUTRAL_PARAMETERS };
    this.lastLoopAt = performance.now();
    this.lastDetectAt = 0;
    this.lastFaceAt = 0;
    this.lastVideoTime = -1;
    this.frameCounter = 0;
    this.metricStartedAt = performance.now();
    this.lastFrameInspectionAt = 0;
    this.frameCondition = "unavailable";
    this.diagnosticCanvas = document.createElement("canvas");
    this.diagnosticCanvas.width = 48;
    this.diagnosticCanvas.height = 36;
    this.diagnosticContext = this.diagnosticCanvas.getContext("2d", { willReadFrequently: true });
    this.status = "";
  }

  setStatus(state, label) {
    if (this.status === `${state}:${label}`) return;
    this.status = `${state}:${label}`;
    this.onStatus?.({ state, label });
  }

  async prepare() {
    if (this.prepared) return;
    this.setStatus("loading", "正在加载动态部件");
    await this.renderer.load();
    this.prepared = true;
    await this.publishDevices();
    this.onParameters?.(this.parameters);
    this.setStatus("ready", "可以开启摄像头");
  }

  async publishDevices(activeDeviceId = "") {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devices = (await navigator.mediaDevices.enumerateDevices())
        .filter((device) => device.kind === "videoinput")
        .map((device) => ({ deviceId: device.deviceId, label: device.label }));
      this.onDevices?.({ devices, activeDeviceId });
    } catch {
      this.onDevices?.({ devices: [], activeDeviceId: "" });
    }
  }

  async start(deviceId = "") {
    if (this.running) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) {
      this.setStatus("error", "当前浏览器不支持本地摄像头访问");
      return;
    }
    await this.prepare();
    const epoch = ++this.startEpoch;
    this.setStatus("loading", "正在连接摄像头");
    try {
      const video = {
        width: { ideal: 640 },
        height: { ideal: 480 },
        frameRate: { ideal: 30, max: 30 },
        ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: "user" }),
      };
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video,
      });
      if (this.disposed || epoch !== this.startEpoch) {
        for (const track of stream.getTracks()) track.stop();
        return;
      }
      this.stream = stream;
      this.video.srcObject = this.stream;
      await this.video.play();
      await this.publishDevices(this.stream.getVideoTracks()[0]?.getSettings().deviceId || deviceId);
      this.setStatus("loading", "正在加载人脸追踪器");
      const tracker = await createFaceLandmarker();
      if (this.disposed || epoch !== this.startEpoch) {
        tracker.landmarker.close();
        this.stop();
        return;
      }
      this.landmarker = tracker.landmarker;
      this.Face = tracker.Face;
      this.running = true;
      this.calibrate();
      for (const track of this.stream.getVideoTracks()) track.addEventListener("ended", () => this.stop(), { once: true });
      this.lastLoopAt = performance.now();
      this.lastFaceAt = this.lastLoopAt;
      this.animationFrame = requestAnimationFrame((now) => this.loop(now));
    } catch (error) {
      this.stop({ preserveStatus: true });
      this.setStatus("error", cameraErrorMessage(error));
    }
  }

  async restart(deviceId = "") {
    this.stop();
    await this.start(deviceId);
  }

  calibrate() {
    this.calibration = null;
    this.calibrationSamples = [];
    this.targetParameters = { ...this.parameters };
    this.setStatus("calibrating", "正在校准");
  }

  solve(points) {
    const landmarks = points.map(({ x, y, z }) => ({ x, y, z }));
    return this.Face.solve(landmarks, {
      runtime: "mediapipe",
      imageSize: { width: this.video.videoWidth, height: this.video.videoHeight },
      smoothBlink: true,
    });
  }

  acceptRig(rig, now) {
    this.lastFaceAt = now;
    if (!this.calibration) {
      this.calibrationSamples.push(rawSample(rig));
      this.setStatus("calibrating", `正在校准 ${Math.min(100, Math.round(this.calibrationSamples.length / CALIBRATION_SAMPLE_COUNT * 100))}%`);
      if (this.calibrationSamples.length >= CALIBRATION_SAMPLE_COUNT) {
        this.calibration = calibrationFromSamples(this.calibrationSamples);
        this.setStatus("tracking", "追踪正常");
      }
    } else {
      this.setStatus("tracking", "追踪正常");
    }
  }

  inspectFrame() {
    if (!this.diagnosticContext || this.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return "unavailable";
    const context = this.diagnosticContext;
    context.drawImage(this.video, 0, 0, this.diagnosticCanvas.width, this.diagnosticCanvas.height);
    const pixels = context.getImageData(0, 0, this.diagnosticCanvas.width, this.diagnosticCanvas.height).data;
    let sum = 0;
    let squared = 0;
    const count = pixels.length / 4;
    for (let index = 0; index < pixels.length; index += 4) {
      const luminance = 0.2126 * pixels[index] + 0.7152 * pixels[index + 1] + 0.0722 * pixels[index + 2];
      sum += luminance;
      squared += luminance * luminance;
    }
    const mean = sum / count;
    const deviation = Math.sqrt(Math.max(0, squared / count - mean * mean));
    return classifyFrameStats(mean, deviation);
  }

  loop(now) {
    if (!this.running) return;
    const elapsed = Math.min(100, now - this.lastLoopAt);
    this.lastLoopAt = now;
    if (now - this.lastDetectAt >= TRACK_INTERVAL_MS && this.video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && this.video.currentTime !== this.lastVideoTime) {
      this.lastDetectAt = now;
      this.lastVideoTime = this.video.currentTime;
      const result = this.landmarker.detectForVideo(this.video, now);
      this.frameCounter += 1;
      const points = result.faceLandmarks?.[0];
      if (points) {
        const rig = this.solve(points);
        if (rig) {
          this.acceptRig(rig, now);
          if (this.calibration) this.targetParameters = parametersFromRig(rig, this.calibration);
        }
      }
    }
    if (now - this.lastFrameInspectionAt >= 500) {
      this.lastFrameInspectionAt = now;
      this.frameCondition = this.inspectFrame();
    }
    const elapsedWithoutFace = now - this.lastFaceAt;
    if (elapsedWithoutFace > LOST_AFTER_MS) {
      if (this.frameCondition === "blocked") this.setStatus("blocked", "摄像头画面过暗或被遮挡");
      else this.setStatus("lost", "未检测到人脸");
    }
    const relaxing = shouldRelaxPose(elapsedWithoutFace);
    if (relaxing) this.targetParameters = NEUTRAL_PARAMETERS;
    this.parameters = smoothParameters(this.parameters, this.targetParameters, elapsed, { relaxing });
    this.renderer.draw(this.parameters);
    this.onParameters?.(this.parameters);
    if (now - this.metricStartedAt >= 750) {
      const fps = this.frameCounter * 1000 / (now - this.metricStartedAt);
      this.onMetrics?.({ fps });
      this.metricStartedAt = now;
      this.frameCounter = 0;
    }
    this.animationFrame = requestAnimationFrame((next) => this.loop(next));
  }

  stop({ preserveStatus = false } = {}) {
    this.startEpoch += 1;
    this.running = false;
    cancelAnimationFrame(this.animationFrame);
    this.animationFrame = 0;
    if (this.stream) for (const track of this.stream.getTracks()) track.stop();
    this.stream = null;
    this.video.pause();
    this.video.srcObject = null;
    this.landmarker?.close();
    this.landmarker = null;
    this.Face = null;
    this.calibration = null;
    this.calibrationSamples = [];
    this.frameCondition = "unavailable";
    this.lastFrameInspectionAt = 0;
    this.parameters = { ...NEUTRAL_PARAMETERS };
    this.targetParameters = { ...NEUTRAL_PARAMETERS };
    if (this.prepared) this.renderer.draw(this.parameters);
    this.onParameters?.(this.parameters);
    this.onMetrics?.({ fps: 0 });
    if (!preserveStatus) this.setStatus(this.prepared ? "ready" : "idle", this.prepared ? "可以开启摄像头" : "尚未加载");
  }

  dispose() {
    this.disposed = true;
    this.stop();
    this.renderer.dispose();
  }
}

export function createLivePreviewController(options) {
  return new LivePreviewController(options);
}
