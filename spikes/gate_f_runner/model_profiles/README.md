# Local model spike profiles

These profiles pin disposable Gate F candidates; they do not select a production stack.

## Selected candidates

- `see-through-v3-nf4.json`: active host-neutral `source-preserve.v6` identity with a `native-linux` runtime. The native runtime is 无隔离边界、仅限本机 (`none-host-local`); it does not provide an isolation or security boundary. The profile covers semantic layer generation, hidden-region completion, depth/order proposal, deterministic alpha cleanup, top-visible source RGB preservation, and PSD output. The postprocess clears alpha at or below 31/255, linearly remaps the retained range, assigns original RGB to the depth-frontmost cleaned semantic layer, and rebuilds the neutral reconstruction from the maximum cleaned semantic alpha so weak background noise cannot accumulate across layers. A per-run challenge binds the source digest and final artifact-manifest digest, which the trusted parent recomputes; this is not cryptographic proof that the pinned entrypoint executed or a trusted-execution guarantee. It does not validate semantic masks or hidden content. The archived v4 and v5 profiles remain separately digest-pinned for historical read-only validation and retain their original WSL2 identities. The main code is Apache-2.0; the exact NF4 and Marigold repositories still lack complete per-revision license metadata, so the weights are not approved for redistribution, repository storage, or product use.
- `hysts-anime-face-v0.1.0.json`: anime face detection and 28-point landmark proposals. Code and exact Safetensors are MIT, with Apache-2.0 vendored inference code. Landmark scores are uncalibrated heatmap peaks and left/right semantics require deterministic mapping.

## Local setup

The active See-through worker expects this disposable native-Linux layout (the archived profiles record the historical WSL2 layout):

```bash
mkdir -p ~/oneclick2d-model-spikes
git clone https://github.com/shitagaki-lab/see-through.git ~/oneclick2d-model-spikes/see-through
git -C ~/oneclick2d-model-spikes/see-through checkout --detach 58a1cb11d13f85acec9bbddb8cd4b6487843d4cf
uv venv --python 3.12 ~/oneclick2d-model-spikes/see-through/.venv
```

Install `torch`, `torchvision` and `torchaudio` from the CUDA 12.8 index, then install `requirements-see-through-v3-nf4.txt`. The locked list includes `pycocotools==2.0.11`, which is imported by `common/utils/cv.py`. Make `<code_root>/common` effective on the virtual environment's `sys.path`; the runtime probe verifies the profile's `python_path_entries` before inference. Download the two exact model revisions into `models/seethroughv0.0.2_layerdiff3d_nf4/` and `models/seethroughv0.0.1_marigold_nf4/`. The hard-coded scheduler lookup resolves `frankjoshua/juggernautXL_version6Rundiffusion` with `subfolder=scheduler` through `models/hf-cache`; its cache must contain `refs/main` pointing to `aadab4c7cb252b83a0e2d6f3386b8c837af23932` and the corresponding pinned snapshot/blob. The worker runs Hugging Face and Transformers in offline mode and checks every listed Safetensors byte length and SHA-256 before inference. Preserve `uv pip freeze` locally. Do not commit environments, caches, weights, user images, or generated PSDs.

The GUI model workflow and the explicit `model` command expect a cut-out character image with a transparent background, normally a PNG. An opaque background is counted as source-visible by the fidelity measurement while semantic layers generally cover only the character. This is guidance rather than a new hard block, and transparency does not guarantee that the neutral-fidelity gate will pass.

Run a local model spike with:

```bash
python -m spikes.gate_f_runner model --source "/path/to/right-cleared.png" --run-id run.local-model
```

A successful worker result means only `LOCAL_MODEL_SPIKE_COMPLETED` and `GATE_F_NOT_EVALUATED`. The GUI reports neutral visible-pixel fidelity and always requires quality review because semantic correctness, hidden-region quality, external-editor interoperability, mesh generation, parameter binding, dynamic deformation, and `.oc2d` remain unproven or ungenerated. It does not authorize model redistribution.
