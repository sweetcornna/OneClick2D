"""Read-only H1/H2 diagnosis for a completed local model run."""

from __future__ import annotations

import ast
import importlib
import io
import math
import stat
import statistics
import warnings
from collections import deque
from pathlib import Path
from typing import Iterable

from .model_workbench import MODEL_CANVAS_SIZE
from .raster import _load_pillow, _temporary_max_image_pixels
from .runtime import MAX_JSON_BYTES, contained_run_path, read_bounded_file, strict_load_json_bytes

ROOT = Path(__file__).resolve().parents[2]
PART_NAMES_ENTRYPOINT = (
    ROOT
    / "spikes"
    / "gate_f_runner"
    / "model_entrypoints"
    / "see_through_v3_nf4_source_preserve_v6.py"
)
MODEL_OUTPUT_DIRECTORY = Path("model-output") / "input" / "input"
MAX_DIAGNOSIS_PART_ENTRIES = 23
MAX_DIAGNOSIS_ENTRYPOINT_BYTES = 1024 * 1024
MAX_DIAGNOSIS_PNG_BYTES = 64 * 1024 * 1024
EXPECTED_CANVAS = (MODEL_CANVAS_SIZE, MODEL_CANVAS_SIZE)


class FidelityDiagnosisError(ValueError):
    """A bounded, content-free failure from local fidelity diagnosis."""


def _read_required(path: Path, maximum: int, label: str) -> bytes:
    try:
        return read_bounded_file(path, maximum)
    except (OSError, ValueError, TypeError) as exc:
        raise FidelityDiagnosisError(f"fidelity diagnosis {label} is missing or invalid") from exc


def _read_run_file(run_dir: Path, relative: str, maximum: int, label: str) -> bytes:
    try:
        path = contained_run_path(run_dir.parent, run_dir.name, relative, kind="file")
    except (OSError, ValueError, TypeError) as exc:
        raise FidelityDiagnosisError(f"fidelity diagnosis {label} is missing or invalid") from exc
    return _read_required(path, maximum, label)


def _part_names() -> tuple[str, ...]:
    source = _read_required(
        PART_NAMES_ENTRYPOINT,
        MAX_DIAGNOSIS_ENTRYPOINT_BYTES,
        "part-name source",
    )
    try:
        tree = ast.parse(source, filename="fixed-model-entrypoint")
        assignments = [
            node
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and (
                any(isinstance(target, ast.Name) and target.id == "PART_NAMES" for target in node.targets)
                if isinstance(node, ast.Assign)
                else isinstance(node.target, ast.Name) and node.target.id == "PART_NAMES"
            )
        ]
        if len(assignments) != 1:
            raise ValueError("PART_NAMES assignment count")
        value = ast.literal_eval(assignments[0].value)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise FidelityDiagnosisError("fidelity diagnosis part-name source is invalid") from exc
    if (
        not isinstance(value, tuple)
        or len(value) != MAX_DIAGNOSIS_PART_ENTRIES
        or any(not isinstance(name, str) or not name or Path(name).name != name for name in value)
        or len(set(value)) != len(value)
    ):
        raise FidelityDiagnosisError("fidelity diagnosis part-name inventory is invalid")
    return value


def _decode_rgba_alpha(data: bytes, label: str) -> bytes:
    backend = _load_pillow()
    try:
        with _temporary_max_image_pixels(backend, MODEL_CANVAS_SIZE * MODEL_CANVAS_SIZE):
            with warnings.catch_warnings():
                warnings.simplefilter("error", backend.Image.DecompressionBombWarning)
                with backend.Image.open(io.BytesIO(data), formats=("PNG",)) as image:
                    if (
                        image.format != "PNG"
                        or image.mode != "RGBA"
                        or image.size != EXPECTED_CANVAS
                        or getattr(image, "n_frames", 1) != 1
                    ):
                        raise FidelityDiagnosisError(
                            f"fidelity diagnosis {label} canvas is outside its profile"
                        )
                    image.load()
                    with image.getchannel("A") as alpha:
                        return alpha.tobytes()
    except FidelityDiagnosisError:
        raise
    except Exception as exc:
        raise FidelityDiagnosisError(f"fidelity diagnosis {label} PNG is invalid") from exc


def _load_report(run_dir: Path) -> tuple[int, dict[str, int | float], dict[str, int | float]]:
    encoded = _read_run_file(run_dir, "workbench-report.json", MAX_JSON_BYTES, "report")
    try:
        report = strict_load_json_bytes(encoded)
        neutral = report["quality"]["neutral_fidelity"]
        threshold = neutral["alpha_threshold"]
        report_metrics = {
            "source_visible": neutral["source_visible_pixel_count"],
            "reconstruction_visible": neutral["reconstruction_visible_pixel_count"],
            "covered": neutral["source_visible_covered_pixel_count"],
            "omitted": neutral["source_visible_omission_count"],
            "exact_ratio": neutral["source_rgb_exact_ratio"],
            "rgb_mae": neutral["source_rgb_mae"],
        }
        pass_thresholds = neutral["pass_thresholds"]
        thresholds = {
            "coverage_minimum": pass_thresholds["source_visible_coverage_ratio_minimum"],
            "exact_ratio_minimum": pass_thresholds["source_rgb_exact_ratio_minimum"],
            "rgb_mae_maximum": pass_thresholds["source_rgb_mae_maximum"],
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise FidelityDiagnosisError("fidelity diagnosis report is invalid") from exc
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold < 255:
        raise FidelityDiagnosisError("fidelity diagnosis report alpha_threshold is invalid")
    for key in ("source_visible", "reconstruction_visible", "covered", "omitted"):
        value = report_metrics[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MODEL_CANVAS_SIZE**2:
            raise FidelityDiagnosisError("fidelity diagnosis report metrics are invalid")
    for key in ("exact_ratio", "rgb_mae"):
        value = report_metrics[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise FidelityDiagnosisError("fidelity diagnosis report metrics are invalid")
    if not 0 <= report_metrics["exact_ratio"] <= 1 or report_metrics["rgb_mae"] < 0:
        raise FidelityDiagnosisError("fidelity diagnosis report metrics are invalid")
    for value in thresholds.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise FidelityDiagnosisError("fidelity diagnosis report thresholds are invalid")
    if (
        not 0 <= thresholds["coverage_minimum"] <= 1
        or not 0 <= thresholds["exact_ratio_minimum"] <= 1
        or thresholds["rgb_mae_maximum"] < 0
    ):
        raise FidelityDiagnosisError("fidelity diagnosis report thresholds are invalid")
    return threshold, report_metrics, thresholds


def _union_alpha(layer_alphas: Iterable[bytes]) -> bytes:
    backend = _load_pillow()
    image_chops = importlib.import_module(f"{backend.Image.__package__}.ImageChops")
    union = backend.Image.new("L", EXPECTED_CANVAS, 0)
    try:
        for encoded in layer_alphas:
            layer = backend.Image.frombytes("L", EXPECTED_CANVAS, encoded)
            try:
                combined = image_chops.lighter(union, layer)
            finally:
                layer.close()
            union.close()
            union = combined
        return union.tobytes()
    finally:
        union.close()


def _normalized_median(values: list[int]) -> int | float:
    if not values:
        return 0
    value = statistics.median(values)
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _source_alpha_facts(values: list[int], threshold: int) -> dict[str, object]:
    ranges = (
        (threshold + 1, 63),
        (max(threshold + 1, 64), 127),
        (max(threshold + 1, 128), 191),
        (max(threshold + 1, 192), 254),
        (255, 255),
    )
    bins = [
        {
            "minimum": lower,
            "maximum": upper,
            "count": sum(lower <= value <= upper for value in values),
        }
        for lower, upper in ranges
        if lower <= upper
    ]
    return {
        "bins": bins,
        "alpha_255_count": sum(value == 255 for value in values),
        "median": _normalized_median(values),
    }


def _component_facts(
    groups: bytearray,
    width: int,
    target: int | None,
) -> tuple[dict[str, int | float], dict[str, int] | None]:
    visited = bytearray(len(groups))
    sizes: list[int] = []
    largest_composition: dict[str, int] | None = None
    for start, group in enumerate(groups):
        if visited[start] or group == 0 or (target is not None and group != target):
            continue
        visited[start] = 1
        pending = deque([start])
        size = 0
        composition = {"H1": 0, "H2": 0}
        while pending:
            index = pending.popleft()
            size += 1
            composition["H1" if groups[index] == 1 else "H2"] += 1
            x = index % width
            if index >= width:
                neighbor = index - width
                if (
                    not visited[neighbor]
                    and groups[neighbor]
                    and (target is None or groups[neighbor] == target)
                ):
                    visited[neighbor] = 1
                    pending.append(neighbor)
            if index + width < len(groups):
                neighbor = index + width
                if (
                    not visited[neighbor]
                    and groups[neighbor]
                    and (target is None or groups[neighbor] == target)
                ):
                    visited[neighbor] = 1
                    pending.append(neighbor)
            if x:
                neighbor = index - 1
                if (
                    not visited[neighbor]
                    and groups[neighbor]
                    and (target is None or groups[neighbor] == target)
                ):
                    visited[neighbor] = 1
                    pending.append(neighbor)
            if x + 1 < width:
                neighbor = index + 1
                if (
                    not visited[neighbor]
                    and groups[neighbor]
                    and (target is None or groups[neighbor] == target)
                ):
                    visited[neighbor] = 1
                    pending.append(neighbor)
        sizes.append(size)
        if largest_composition is None or size > sum(largest_composition.values()):
            largest_composition = composition
    return (
        {
            "count": len(sizes),
            "median_size": _normalized_median(sizes),
            "maximum_size": max(sizes, default=0),
        },
        largest_composition,
    )


def _preimage_facts(histogram: list[int], threshold: int) -> dict[str, list[int]]:
    preimages: dict[str, list[int]] = {}
    for cleaned, count in enumerate(histogram):
        if cleaned == 0 or count == 0:
            continue
        preimages[str(cleaned)] = [
            raw
            for raw in range(256)
            if raw > threshold
            and round((raw - threshold) * 255 / (255 - threshold)) == cleaned
        ]
    return preimages


def diagnose_model_fidelity(run_dir: Path) -> dict[str, object]:
    """Diagnose one completed run without modifying it or evaluating Gate F."""

    try:
        info = run_dir.lstat()
    except (OSError, ValueError) as exc:
        raise FidelityDiagnosisError("fidelity diagnosis run directory is invalid") from exc
    if run_dir.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise FidelityDiagnosisError("fidelity diagnosis run directory is invalid")

    threshold, report_metrics, pass_thresholds = _load_report(run_dir)
    part_names = _part_names()
    source_alpha = _decode_rgba_alpha(
        _read_run_file(
            run_dir,
            "trusted-model-source.png",
            MAX_DIAGNOSIS_PNG_BYTES,
            "source",
        ),
        "source",
    )
    reconstruction_alpha = _decode_rgba_alpha(
        _read_run_file(
            run_dir,
            (MODEL_OUTPUT_DIRECTORY / "reconstruction.png").as_posix(),
            MAX_DIAGNOSIS_PNG_BYTES,
            "reconstruction",
        ),
        "reconstruction",
    )
    layer_alphas = [
        _decode_rgba_alpha(
            _read_run_file(
                run_dir,
                (MODEL_OUTPUT_DIRECTORY / f"{name}.png").as_posix(),
                MAX_DIAGNOSIS_PNG_BYTES,
                "semantic layer",
            ),
            "semantic layer",
        )
        for name in part_names
    ]
    if len(layer_alphas) > MAX_DIAGNOSIS_PART_ENTRIES:
        raise FidelityDiagnosisError("fidelity diagnosis semantic layer entry limit exceeded")

    union_alpha = _union_alpha(layer_alphas)
    difference_count = sum(left != right for left, right in zip(union_alpha, reconstruction_alpha))
    if difference_count:
        raise FidelityDiagnosisError(
            "fidelity diagnosis union alpha does not match reconstruction alpha"
        )

    source_visible = 0
    reconstruction_visible = 0
    covered = 0
    groups = bytearray(len(source_alpha))
    h1_source_alpha: list[int] = []
    h2_source_alpha: list[int] = []
    union_histogram = [0] * (threshold + 1)
    h1_indices: list[int] = []
    for index, (source, reconstruction, union) in enumerate(
        zip(source_alpha, reconstruction_alpha, union_alpha)
    ):
        source_is_visible = source > threshold
        reconstruction_is_visible = reconstruction > threshold
        source_visible += source_is_visible
        reconstruction_visible += reconstruction_is_visible
        covered += source_is_visible and reconstruction_is_visible
        if not source_is_visible or reconstruction_is_visible:
            continue
        if union > threshold:
            raise FidelityDiagnosisError("fidelity diagnosis omitted-pixel union is invalid")
        union_histogram[union] += 1
        if union == 0:
            groups[index] = 2
            h2_source_alpha.append(source)
        else:
            groups[index] = 1
            h1_source_alpha.append(source)
            h1_indices.append(index)

    omitted = len(h1_source_alpha) + len(h2_source_alpha)
    calculated_metrics = {
        "source_visible": source_visible,
        "reconstruction_visible": reconstruction_visible,
        "covered": covered,
        "omitted": omitted,
    }
    if any(report_metrics[key] != value for key, value in calculated_metrics.items()):
        raise FidelityDiagnosisError("fidelity diagnosis report metrics do not match artifacts")
    if source_visible <= 0:
        raise FidelityDiagnosisError("fidelity diagnosis has no source-visible pixels")

    overall_components, largest_composition = _component_facts(groups, MODEL_CANVAS_SIZE, None)
    h1_components, _ = _component_facts(groups, MODEL_CANVAS_SIZE, 1)
    h2_components, _ = _component_facts(groups, MODEL_CANVAS_SIZE, 2)
    largest_h1 = largest_composition["H1"] if largest_composition else 0
    largest_h2 = largest_composition["H2"] if largest_composition else 0
    if largest_composition is None:
        dominant_group = "none"
        composition = "none"
    else:
        dominant_group = (
            "tie" if largest_h1 == largest_h2 else ("H1" if largest_h1 > largest_h2 else "H2")
        )
        composition = "mixed" if largest_h1 and largest_h2 else "single_group"

    argmax_distribution = {name: 0 for name in part_names}
    argmax_tie_count = 0
    for index in h1_indices:
        maximum = union_alpha[index]
        winners = [layer_index for layer_index, alpha in enumerate(layer_alphas) if alpha[index] == maximum]
        if len(winners) > 1:
            argmax_tie_count += 1
        argmax_distribution[part_names[winners[0]]] += 1

    h1_count = len(h1_source_alpha)
    h2_count = len(h2_source_alpha)
    projected_numerator = covered + h1_count
    projected_ratio = projected_numerator / source_visible
    exact_ratio = report_metrics["exact_ratio"]
    rgb_mae = report_metrics["rgb_mae"]
    threshold_checks = {
        "source_visible_coverage_ratio": {
            "operator": ">=",
            "threshold": pass_thresholds["coverage_minimum"],
            "value": round(projected_ratio, 6),
            "passes": projected_ratio >= pass_thresholds["coverage_minimum"],
        },
        "source_rgb_exact_ratio": {
            "operator": ">=",
            "threshold": pass_thresholds["exact_ratio_minimum"],
            "value": exact_ratio,
            "passes": exact_ratio >= pass_thresholds["exact_ratio_minimum"],
        },
        "source_rgb_mae": {
            "operator": "<=",
            "threshold": pass_thresholds["rgb_mae_maximum"],
            "value": rgb_mae,
            "passes": rgb_mae <= pass_thresholds["rgb_mae_maximum"],
        },
    }

    return {
        "alpha_threshold": threshold,
        "connected_components_4": {
            "H1": h1_components,
            "H2": h2_components,
            "overall": overall_components,
            "overall_largest_component": {
                "composition": composition,
                "dominant_group": dominant_group,
                "H1_pixel_count": largest_h1,
                "H2_pixel_count": largest_h2,
            },
        },
        "counts": calculated_metrics,
        "format": "oneclick2d.local-fidelity-diagnosis",
        "format_version": "0.1.0",
        "gate_f_status": "GATE_F_NOT_EVALUATED",
        "groups": {
            "H1": {
                "argmax_layer_distribution": argmax_distribution,
                "argmax_tie_pixel_count": argmax_tie_count,
                "criterion": "1 <= union_alpha <= alpha_threshold",
                "omitted_percentage": round(h1_count * 100 / omitted, 6) if omitted else 0,
                "omitted_ratio": round(h1_count / omitted, 6) if omitted else 0,
                "pixel_count": h1_count,
                "pre_cleanup_alpha_preimages_by_cleaned_alpha": _preimage_facts(
                    union_histogram, threshold
                ),
                "source_alpha": _source_alpha_facts(h1_source_alpha, threshold),
            },
            "H2": {
                "criterion": "union_alpha == 0",
                "omitted_percentage": round(h2_count * 100 / omitted, 6) if omitted else 0,
                "omitted_ratio": round(h2_count / omitted, 6) if omitted else 0,
                "pixel_count": h2_count,
                "source_alpha": _source_alpha_facts(h2_source_alpha, threshold),
            },
        },
        "h1_covered_projection": {
            "coverage": {
                "denominator": source_visible,
                "numerator": projected_numerator,
                "ratio": round(projected_ratio, 6),
            },
            "exact_ratio_unaffected": True,
            "rgb_mae_unaffected": True,
            "threshold_checks": threshold_checks,
            "all_thresholds_pass": all(item["passes"] for item in threshold_checks.values()),
        },
        "local_status": "LOCAL_FIDELITY_DIAGNOSIS_COMPLETED",
        "part_count": len(part_names),
        "union_alpha_histogram_0_to_threshold": union_histogram,
        "union_reconstruction_alpha": {
            "difference_pixel_count": difference_count,
            "equal": True,
        },
    }
