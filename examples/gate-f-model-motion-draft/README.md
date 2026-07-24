# Gate F model motion research draft

This v2 fixed configuration connects a previously validated local `source-preserve.v4` model run to deterministic semantic bounding-box quads, five affine research bindings, neutral-frame passthrough, and the shared 37-frame Gate F sequence.

The configuration and implementation are repository-authored. No user image, model output, generated frame, PSD, camera data, or private path is committed. Runtime outputs remain under the ignored local workspace.

The result is a disposable visual diagnostic. It is not CIR, `.oc2d`, `.moc3`, mesh-delta deformation, an external-editor result, or Gate F evidence.

Run it only after the active v4 model workbench report passes neutral fidelity validation:

```bash
python -m spikes.gate_f_runner motion --run-id run.local-model
```
