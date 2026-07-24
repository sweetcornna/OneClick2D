import assert from "node:assert/strict";
import test from "node:test";

import {
  NEUTRAL_PARAMETERS,
  calibrationFromSamples,
  classifyFrameStats,
  composeAffine,
  parametersFromRig,
  partAffine,
  shouldRelaxPose,
  versionedArtifactUrl,
} from "../../spikes/gate_f_runner/gui/live_preview.mjs";

test("live layer URLs are versioned by artifact content", () => {
  assert.equal(
    versionedArtifactUrl("/artifacts/layer.face", { sha256: "abc123" }),
    "/artifacts/layer.face?sha256=abc123",
  );
  assert.equal(
    versionedArtifactUrl("/artifacts/layer.face?preview=1", { sha256: "abc123" }),
    "/artifacts/layer.face?preview=1&sha256=abc123",
  );
});

test("calibration uses robust neutral samples", () => {
  const samples = Array.from({ length: 9 }, (_, index) => ({
    yaw: index === 8 ? 12 : 1,
    pitch: -2,
    eyeLeft: 0.8 + index * 0.01,
    eyeRight: 0.75 + index * 0.01,
    mouth: index === 0 ? 0 : 0.1,
  }));
  const calibration = calibrationFromSamples(samples);
  assert.equal(calibration.yaw, 1);
  assert.equal(calibration.pitch, -2);
  assert.equal(calibration.mouth, 0.1);
  assert.ok(calibration.eyeLeft > 0.85);
});

test("Kalidokit rig maps into the frozen five parameter ranges", () => {
  const parameters = parametersFromRig({
    head: { degrees: { x: 15, y: -22 } },
    eye: { l: 0.4, r: 0.9 },
    mouth: { y: 0.8 },
  }, { yaw: 0, pitch: 0, eyeLeft: 0.8, eyeRight: 0.9, mouth: 0.1 });
  assert.deepEqual(parameters, {
    "head.yaw": -15,
    "head.pitch": 10,
    "eye.left.open": 0.5,
    "eye.right.open": 1,
    "mouth.open": 1,
  });
});

test("neutral local transforms preserve eye and mouth geometry", () => {
  const identity = { a: 1, b: 0, c: 0, d: 0, e: 1, f: 0 };
  const eye = { motion_group: "eye", side: "left", box_ltrb: [10, 20, 40, 60] };
  const mouth = { motion_group: "mouth", side: "not-applicable", box_ltrb: [10, 70, 40, 90] };
  assert.deepEqual(partAffine(eye, NEUTRAL_PARAMETERS, identity), identity);
  assert.deepEqual(partAffine(mouth, NEUTRAL_PARAMETERS, identity), identity);
});

test("affine composition applies the inner transform first", () => {
  const outer = { a: 1, b: 0.2, c: 3, d: 0, e: 1, f: 4 };
  const inner = { a: 1, b: 0, c: 0, d: 0, e: 0.5, f: 10 };
  assert.deepEqual(composeAffine(outer, inner), { a: 1, b: 0.1, c: 5, d: 0, e: 0.5, f: 14 });
});

test("camera frame diagnostics distinguish a covered lens", () => {
  assert.equal(classifyFrameStats(38.2, 0.5), "blocked");
  assert.equal(classifyFrameStats(96, 22), "usable");
  assert.equal(classifyFrameStats(Number.NaN, 0), "unavailable");
});

test("brief tracking loss holds the last pose before relaxing", () => {
  assert.equal(shouldRelaxPose(450), false);
  assert.equal(shouldRelaxPose(1799), false);
  assert.equal(shouldRelaxPose(1800), true);
});
