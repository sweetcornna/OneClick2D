from __future__ import annotations

import ast
import importlib.util
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT_ROOT = ROOT / "spikes" / "gate_f_runner" / "model_entrypoints"
V5_ENTRYPOINT = ENTRYPOINT_ROOT / "see_through_v3_nf4_source_preserve_v5.py"
V6_ENTRYPOINT = ENTRYPOINT_ROOT / "see_through_v3_nf4_source_preserve_v6.py"
V6_DEVICE_POLICY = ENTRYPOINT_ROOT / "nf4_marigold_device_policy_v6.py"
RESOURCE_LIMIT_NAMES = {
    "MAX_ATTESTATION_SOURCE_BYTES",
    "MAX_ARTIFACT_MANIFEST_BYTES",
    "MAX_ARTIFACT_MANIFEST_ENTRIES",
    "MAX_ARTIFACT_MANIFEST_DIRECTORIES",
    "MAX_ARTIFACT_MANIFEST_NODES",
    "MAX_ARTIFACT_MANIFEST_DEPTH",
    "MAX_ARTIFACT_RELATIVE_PATH_BYTES",
}


def _load_device_policy():
    spec = importlib.util.spec_from_file_location("gate_f_nf4_device_policy_v6", V6_DEVICE_POLICY)
    if spec is None or spec.loader is None:
        raise RuntimeError("v6 device policy module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resource_limits(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected: list[ast.stmt] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if any(
            isinstance(target, ast.Name) and target.id in RESOURCE_LIMIT_NAMES
            for target in targets
        ):
            selected.append(node)
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return {name: namespace[name] for name in RESOURCE_LIMIT_NAMES if name in namespace}


class _FakeModule:
    def __init__(
        self,
        *,
        storage_device: str,
        hook: object | None = None,
        children: tuple[object, ...] = (),
    ) -> None:
        self._storage_device = storage_device
        self._children = children
        if hook is not None:
            self._hf_hook = hook

    def modules(self) -> list[object]:
        return [self, *self._children]

    def parameters(self, recurse: bool = True) -> list[object]:
        del recurse
        return [types.SimpleNamespace(device=self._storage_device)]

    def buffers(self, recurse: bool = True) -> list[object]:
        del recurse
        return []

    def to(self, *args: object, **kwargs: object) -> _FakeModule:
        del args, kwargs
        return self


class GateFModelEntrypointV6Tests(unittest.TestCase):
    def test_execution_device_uses_leaf_hook_when_root_hook_device_is_none(self) -> None:
        tree = ast.parse(V6_ENTRYPOINT.read_text(encoding="utf-8"), filename=str(V6_ENTRYPOINT))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_execution_aware_device"
        )
        fake_torch = types.SimpleNamespace(device=lambda value: value)
        namespace = {
            "ModelMixin": object,
            "torch": fake_torch,
            "_ORIGINAL_MODEL_DEVICE": lambda module: "meta",
        }
        exec(
            compile(ast.Module(body=[function], type_ignores=[]), str(V6_ENTRYPOINT), "exec"),
            namespace,
        )
        leaf = _FakeModule(
            storage_device="meta",
            hook=types.SimpleNamespace(execution_device="cuda:0"),
        )
        root = _FakeModule(
            storage_device="meta",
            hook=types.SimpleNamespace(execution_device=None),
            children=(leaf,),
        )

        device = namespace["_execution_aware_device"](root)

        self.assertEqual("cuda:0", device)

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = _load_device_policy()

    def _adapter(self, vae: _FakeModule):
        pipeline = types.SimpleNamespace(
            vae=vae,
            unet=_FakeModule(storage_device="cuda:0"),
            text_encoder=_FakeModule(storage_device="cpu"),
        )
        adapter = self.policy.Nf4MarigoldOffloadAdapter(
            pipeline,
            cpu_offload=lambda *args, **kwargs: None,
        )
        adapter._suppressed.update(("vae", "unet"))
        return adapter

    def _validation_exception(self, vae: _FakeModule) -> RuntimeError:
        with self.assertRaises(RuntimeError) as raised:
            self._adapter(vae)._validate_effective_policy()
        return raised.exception

    def _validation_error(self, vae: _FakeModule) -> str:
        return str(self._validation_exception(vae))

    def test_root_none_with_124_cuda_leaf_hooks_is_valid(self) -> None:
        leaves = tuple(
            _FakeModule(
                storage_device="meta",
                hook=types.SimpleNamespace(execution_device="cuda:0"),
            )
            for _ in range(124)
        )
        vae = _FakeModule(
            storage_device="meta",
            hook=types.SimpleNamespace(execution_device=None),
            children=leaves,
        )

        self.assertEqual([None, "cuda:0"], self.policy._hook_execution_devices(vae))
        self._adapter(vae)._validate_effective_policy()

    def test_hooks_without_execution_devices_fail_closed(self) -> None:
        leaf = _FakeModule(storage_device="meta", hook=object())
        vae = _FakeModule(
            storage_device="meta",
            hook=types.SimpleNamespace(execution_device=None),
            children=(leaf,),
        )

        self.assertEqual([None], self.policy._hook_execution_devices(vae))
        self.assertIsInstance(
            self._validation_exception(vae),
            self.policy.Nf4MarigoldExecutionDeviceMissingError,
        )
        self.assertEqual(
            "NF4 Marigold VAE execution hook device is missing",
            self._validation_error(vae),
        )

    def test_absent_hooks_fail_closed_as_missing_execution_device(self) -> None:
        vae = _FakeModule(storage_device="meta")

        self.assertEqual([], self.policy._hook_execution_devices(vae))
        exception = self._validation_exception(vae)
        self.assertIsInstance(
            exception,
            self.policy.Nf4MarigoldExecutionDeviceMissingError,
        )
        self.assertEqual("NF4 Marigold VAE execution hook is missing", str(exception))

    def test_non_cuda_hook_fails_closed_with_distinct_error(self) -> None:
        leaf = _FakeModule(
            storage_device="meta",
            hook=types.SimpleNamespace(execution_device="cpu"),
        )
        vae = _FakeModule(
            storage_device="meta",
            hook=types.SimpleNamespace(execution_device="cuda:0"),
            children=(leaf,),
        )

        self.assertIn("cpu", self.policy._hook_execution_devices(vae))
        exception = self._validation_exception(vae)
        self.assertIsInstance(exception, self.policy.Nf4MarigoldNonCudaExecutionDeviceError)
        self.assertNotIsInstance(exception, self.policy.Nf4MarigoldExecutionDeviceMissingError)
        message = self._validation_error(vae)
        self.assertEqual(
            "NF4 Marigold VAE execution hook points to a non-CUDA device",
            message,
        )
        self.assertNotEqual("NF4 Marigold VAE execution hook device is missing", message)

    def test_v6_resource_limits_match_v5(self) -> None:
        v5_limits = _resource_limits(V5_ENTRYPOINT)
        v6_limits = _resource_limits(V6_ENTRYPOINT)

        self.assertEqual(RESOURCE_LIMIT_NAMES, set(v5_limits))
        self.assertEqual(v5_limits, v6_limits)


if __name__ == "__main__":
    unittest.main()
