"""Create a compatibility-checked linear interpolation of two LoRA adapters."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from safetensors import safe_open
from safetensors.numpy import load_file, save_file


def _config(path: Path) -> dict:
    config = json.loads((path / "adapter_config.json").read_text(encoding="utf-8"))
    config["target_modules"] = sorted(config.get("target_modules") or [])
    return config


def interpolate_adapter(
    parent: Path, candidate: Path, output: Path, *, alpha: float,
) -> dict:
    """Interpolate compatible adapter tensors and return an audit summary."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be strictly between zero and one")
    if _config(parent) != _config(candidate):
        raise ValueError("adapter configurations are incompatible")

    parent_file = parent / "adapter_model.safetensors"
    candidate_file = candidate / "adapter_model.safetensors"
    parent_state = load_file(parent_file)
    candidate_state = load_file(candidate_file)
    if set(parent_state) != set(candidate_state):
        raise ValueError("adapter tensor keys differ")

    mixed: dict[str, np.ndarray] = {}
    for key in sorted(parent_state):
        before, after = parent_state[key], candidate_state[key]
        if before.shape != after.shape or before.dtype != after.dtype:
            raise ValueError(f"adapter tensor mismatch for {key}")
        if not np.issubdtype(before.dtype, np.floating):
            raise ValueError(f"adapter tensor is not floating point: {key}")
        mixed[key] = (before + alpha * (after - before)).astype(
            before.dtype, copy=False,
        )

    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    shutil.copytree(parent, output)
    metadata = None
    with safe_open(parent_file, framework="numpy") as handle:
        metadata = handle.metadata()
    save_file(mixed, output / "adapter_model.safetensors", metadata=metadata)
    summary = {
        "complete": True,
        "alpha": alpha,
        "tensor_count": len(mixed),
        "config": _config(parent),
    }
    (output / "interpolation_manifest.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    """Parse paths, build one interpolated adapter, and print its summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alpha", required=True, type=float)
    args = parser.parse_args(argv)
    summary = interpolate_adapter(
        args.parent, args.candidate, args.output, alpha=args.alpha,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
