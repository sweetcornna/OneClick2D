# Browser tracker inventory

These files are pinned local-only dependencies for the disposable webcam preview. The browser makes no runtime request to a third-party origin.

## MediaPipe Tasks Vision

- Package: `@mediapipe/tasks-vision@0.10.35`
- npm integrity: `sha512-HOvadwVRE6JC+45nyYhmnywnr5h/J8KZvOeUNVOG9q/0875pZgItznFB9bRTvLc264YSJqiZ1NsIpCStJw/egg==`
- Upstream: `google-ai-edge/mediapipe` (Apache-2.0)
- Face Landmarker model: `face_landmarker/float16/1`
- Model source: `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`

| File | SHA-256 |
|---|---|
| `vision_bundle.mjs` | `55d7ab624fbb70dcc5adc4ae6d7ea9cfcb569139d3dbfbf2b1deafcb966bc0fe` |
| `wasm/vision_wasm_internal.js` | `e7fd9858e8e8f221d9b96eddc11f8e077f263e0b7bbd79d3cbe882b134274f8c` |
| `wasm/vision_wasm_internal.wasm` | `6a5c64584c2ab61c763b6e204afbdbc7ce1caf7f5216187322bca8df94f646bc` |
| `wasm/vision_wasm_nosimd_internal.js` | `438d1fe8ff7f4d946025bc211c291543c037d8a3785ed4eee60f1f521b236296` |
| `wasm/vision_wasm_nosimd_internal.wasm` | `8a3092d34c79d3f57e6ba8592105e8a90f6b07c27891ffecd14cca428bfd3e31` |
| `face_landmarker.task` | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |

## Kalidokit

- Package: `kalidokit@1.1.5`
- npm integrity: `sha512-cLaPfCK5UB1QUesFSF12s1/ZsOz4FMcaZDqfFoIYYAzouAjzreishAKIMuoN4zhz2KLuJudGKYWVjI+VVb0W1Q==`
- Upstream: `yeemachine/kalidokit` (MIT)
- Purpose: established MediaPipe landmark to Live2D head/eye/mouth solving

| File | SHA-256 |
|---|---|
| `kalidokit.es.js` | `6ec0fad85b79678ba587efb24f474b04e65ecc871bd4c6b2f6c626f3b3a2abc8` |
| `LICENSE.md` | `5f87543ab2826461fed78bcaa686478d0c3c17e702e638b20379ab2dd9dc300a` |

The standalone tracker model's training-data provenance has not been approved for product redistribution. These files remain limited to disposable local evaluation until the project license gate is closed.
