"""Load ChordMini's BTC inference code without sharing TABsucks' ``src`` package.

Only the model, sliding-window inference and categorical smoothing function are
needed. Package initializers also import training, evaluation and plotting code,
so they are deliberately bypassed. The upstream source files remain unchanged.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.machinery
import importlib.util
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import numpy as np


REQUIRED_SOURCE_FILES = (
    "models/common/config.py",
    "models/common/transformer_modules.py",
    "models/common/temporal_smoothing.py",
    "models/btc_model.py",
    "evaluation/utils/common.py",
    "evaluation/utils/inference.py",
)


@dataclass(frozen=True)
class BTCRuntime:
    model_class: Any
    config_class: Any
    predict_sliding_windows: Callable


_runtimes: dict[Path, BTCRuntime] = {}
_load_lock = threading.RLock()


class _PrivateImports(ast.NodeTransformer):
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        if node.module and node.module.startswith("src."):
            node.module = self.namespace + node.module[3:]
        return node


def _package(name: str) -> None:
    package = ModuleType(name)
    package.__package__ = name
    package.__path__ = []
    package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    sys.modules[name] = package
    parent, _, child = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], child, package)


def _load_file(name: str, path: Path, namespace: str, *, function: str | None = None):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if function is not None:
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == function]
        if len(functions) != 1:
            raise ImportError(f"ChordMini {path.name} 缺少推理函数 {function}")
        # This upstream function depends only on numpy; omit the evaluation imports.
        tree = ast.Module(body=functions, type_ignores=[])
    tree = ast.fix_missing_locations(_PrivateImports(namespace).visit(tree))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if function is not None:
        module.__dict__["np"] = np
    sys.modules[name] = module
    parent, _, child = name.rpartition(".")
    setattr(sys.modules[parent], child, module)
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


def load_btc_runtime(source_dir: str | Path) -> BTCRuntime:
    """Return a cached, isolated runtime, publishing it only after all imports succeed."""
    source_dir = Path(source_dir).resolve()
    missing = [path for path in REQUIRED_SOURCE_FILES if not (source_dir / path).is_file()]
    if missing:
        raise FileNotFoundError(
            "BTC-SL 缺少 ChordMini 源码：" + ", ".join(missing)
            + "。请在项目目录执行 git submodule update --init -- "
            "src/plugins/chord/external/chordmini"
        )

    with _load_lock:
        if source_dir in _runtimes:
            return _runtimes[source_dir]
        suffix = hashlib.sha256(str(source_dir).encode("utf-8")).hexdigest()[:16]
        namespace = f"_tabsucks_chordmini_{suffix}"
        try:
            for package in ("", ".models", ".models.common", ".evaluation", ".evaluation.utils"):
                _package(namespace + package)
            modules = {}
            for module in ("models.common.config", "models.common.transformer_modules",
                           "models.common.temporal_smoothing", "models.btc_model"):
                modules[module] = _load_file(
                    namespace + "." + module, source_dir / (module.replace(".", "/") + ".py"),
                    namespace,
                )
            _load_file(
                namespace + ".evaluation.utils.common", source_dir / "evaluation/utils/common.py",
                namespace, function="majority_filter_indices",
            )
            inference = _load_file(
                namespace + ".evaluation.utils.inference", source_dir / "evaluation/utils/inference.py",
                namespace,
            )
            runtime = BTCRuntime(
                modules["models.btc_model"].BTC_model,
                modules["models.common.config"].ModelConfig,
                inference.predict_sliding_windows,
            )
        except Exception:
            # Failed loads must be retryable and must never leave partially loaded modules.
            for name in list(sys.modules):
                if name == namespace or name.startswith(namespace + "."):
                    del sys.modules[name]
            raise
        _runtimes[source_dir] = runtime
        return runtime
