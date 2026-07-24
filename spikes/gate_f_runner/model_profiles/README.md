# Local model spike profiles

These profiles pin disposable Gate F candidates; they do not select a production stack.

## Selected candidates

- `see-through-v3-nf4.json`: semantic layer generation, hidden-region completion, depth/order proposal, deterministic alpha cleanup, top-visible source RGB preservation, and PSD output. Runs only through the isolated WSL2 worker. The postprocess clears alpha at or below 31/255, linearly remaps the retained range, assigns original RGB to the depth-frontmost cleaned semantic layer, and rebuilds the neutral reconstruction from the maximum cleaned semantic alpha so weak background noise cannot accumulate across layers. It does not validate semantic masks or hidden content. The main code is Apache-2.0; the exact NF4 and Marigold repositories still lack complete per-revision license metadata, so the profile is not approved for redistribution or production use.
- `hysts-anime-face-v0.1.0.json`: anime face detection and 28-point landmark proposals. Code and exact Safetensors are MIT, with Apache-2.0 vendored inference code. Landmark scores are uncalibrated heatmap peaks and left/right semantics require deterministic mapping.

## Local setup

The See-through worker expects this disposable WSL2 layout:

```bash
mkdir -p ~/oneclick2d-model-spikes
git clone https://github.com/shitagaki-lab/see-through.git ~/oneclick2d-model-spikes/see-through
git -C ~/oneclick2d-model-spikes/see-through checkout --detach 58a1cb11d13f85acec9bbddb8cd4b6487843d4cf
uv venv --python 3.12 ~/oneclick2d-model-spikes/see-through/.venv
```

Install `torch`, `torchvision` and `torchaudio` from the CUDA 12.8 index, then install `requirements-see-through-v3-nf4-wsl2.txt` plus upstream `common/` and `annotators/` as editable local packages. Download the two exact model revisions into `models/seethroughv0.0.2_layerdiff3d_nf4/` and `models/seethroughv0.0.1_marigold_nf4/`. The worker runs Hugging Face and Transformers in offline mode and checks every listed Safetensors byte length and SHA-256 before inference. Preserve `uv pip freeze` locally. Do not commit environments, caches, weights, user images, or generated PSDs.

Run a local model spike with:

```bash
python -m spikes.gate_f_runner model --source "C:/path/to/right-cleared.png" --run-id run.local-model
```

A successful worker result means only `LOCAL_MODEL_SPIKE_COMPLETED` and `GATE_F_NOT_EVALUATED`. The GUI reports neutral visible-pixel fidelity and always requires quality review because semantic correctness, hidden-region quality, external-editor interoperability, mesh generation, parameter binding, dynamic deformation, and `.oc2d` remain unproven or ungenerated. It does not authorize model redistribution.
