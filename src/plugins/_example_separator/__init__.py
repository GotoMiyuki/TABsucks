"""Mock 6-stem 分离插件（用作 MVP 范例）。

**用途**：本插件 *不* 调用真正的 BS-RoFormer，只在内存里造 6 道静音 + 模拟
100 步进度。它服务于两个目的：

1. 跑通 MVP：UI 能在 Tab2 看到下拉列表、进度条、6 道 wav 列表。
2. 范本：给 PM/RC/AE 同事示范"如何写一个遵循 ``Plugin`` ABC 的插件"。

真实实现路线（不在本插件做）：
- 替换 :py:meth:`_run_separation` 的 `np.zeros` 为 ``audio_separator.separate(...)``。
- 通过 :py:attr:`progress_callback` 调外部 callback 上报进度。

不在 manifest 里登记：路径前缀 ``_example_`` 不会被 PM 的 ``_discover`` 扫到。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from src.plugins import BasePlugin

logger = logging.getLogger(__name__)

# 范例 plugin 输出的 6 道轨道名字（与会议一致）
EXAMPLE_STEMS: list[str] = ["vocals", "drums", "bass", "piano", "guitar", "other"]

# 每步模拟耗时（秒）；MVP 默认 3 秒跑完
EXAMPLE_STEP_DELAY: float = 0.03


@dataclass(frozen=True)
class MockSeparationResult:
    """mock 6 道分离的结果（frozen 数据类）。"""

    vocals: np.ndarray
    drums: np.ndarray
    bass: np.ndarray
    piano: np.ndarray
    guitar: np.ndarray
    other: np.ndarray
    sample_rate: int


class ExampleSeparatorPlugin(BasePlugin):
    """Mock 6-stem 分离器（不做真模型推理）。

    继承 :py:class:`src.plugins.BasePlugin`（= :py:class:`src.plugins.Plugin`）。

    **约定**：
    * ``name`` = ``"example_separator"``：业务层查表 key
    * ``execute(rc, ...)`` 同步写 6 道 buffer 到 RC + 返回标准 dict
    * 上报进度走 ``progress_callback``（不带回调则静默）
    """

    #: 范例插件名；下拉列表显示这条
    PLUGIN_NAME: str = "example_separator"
    PLUGIN_DISPLAY_NAME: str = "Mock 6-Stem Separator (范例)"
    PLUGIN_VERSION: str = "0.0.1"

    def __init__(self) -> None:
        """无配置范例。"""
        super().__init__()

    # ---------- BasePlugin 接口 ----------

    @property
    def name(self) -> str:
        return self.PLUGIN_NAME

    @property
    def version(self) -> str:
        return self.PLUGIN_VERSION

    def execute(
        self,
        rc,  # ResourceController（不 import 类型以避免循环）
        **kwargs,
    ) -> dict[str, Any]:
        """Mock 化执行，按"100 步 × EXAMPLE_STEP_DELAY"模拟进度。

        Args:
            rc: 资源控制器。从 ``rc.get_buffer("raw")`` 读原始混音，向
                ``rc.set_buffer(track_name, np.ndarray)`` 写 6 道 stem，
                通过 ``rc.set_metadata("separation_*", ...)`` 写元数据。
            **kwargs:
                * ``progress_callback``: ``Callable[[float], None]``，0~1 进度
                * ``durations_sec``: float，模拟总时长（默认 3 秒）
                * ``sample_rate_hint``: int，原始音频的 sr（默认 22050）

        Returns:
            标准 dict，含 ``"status"`` 与 ``"data"``。
        """
        cb = kwargs.get("progress_callback")
        duration = float(kwargs.get("durations_sec", 3.0))
        sr_hint = int(kwargs.get("sample_rate_hint", 22050))

        # 0. 读原始混音
        try:
            raw = rc.get_buffer("raw")
        except Exception as e:
            logger.warning(
                "RC 中没有 'raw' buffer（%s），使用空数组生成静 mock", e
            )
            raw = np.zeros(sr_hint * 3, dtype=np.float32)

        if isinstance(raw, np.ndarray) and raw.ndim == 2:
            n_samples = raw.shape[1]
        else:
            n_samples = len(raw)
        sample_rate = rc.get_metadata("sample_rate") or sr_hint

        # 1. 模拟进度 0 → 1，每步 ~EXAMPLE_STEP_DELAY
        steps = 100
        started = time.time()
        for i in range(steps + 1):
            if cb is not None:
                try:
                    cb(i / steps)
                except Exception as e:  # 不让调用方崩
                    logger.debug("progress_callback 抛出: %s", e)
            # 实际跨进程走 asyncio.sleep，避免阻塞
            if i < steps:
                # time.sleep 是同步阻塞；按情况选择真实 sleep（同步）
                # 或 noop（异步在外层 sleep）
                # 本范例全走同步，让 PM 调用方在外层包 asyncio.to_thread
                time.sleep(duration / steps)
        elapsed = time.time() - started

        # 2. 在内存生成 6 道静音 np.ndarray（shape=(n_samples,)，float32）
        stems = {
            name: np.zeros(n_samples, dtype=np.float32)
            for name in EXAMPLE_STEMS
        }

        # 3. 写回 RC buffer
        for name, arr in stems.items():
            rc.set_buffer(name, arr)
        rc.set_metadata(
            "separated_stems",
            EXAMPLE_STEMS,
        )
        rc.set_metadata(
            "separation_model",
            self.PLUGIN_NAME,
        )

        # 4. 返回标准化 dict
        return {
            "status": "success",
            "data": {
                "plugin": self.PLUGIN_NAME,
                "version": self.PLUGIN_VERSION,
                "stems": EXAMPLE_STEMS,
                "sample_rate": sample_rate,
                "elapsed_sec": round(elapsed, 3),
                "mock": True,
            },
        }


# ---- module API ----
def get_manifest() -> dict[str, Any]:
    """不通过 manifest.json 注册（属于\"_example_\"路径，PM 不扫它）。

    但仍保留 ``get_manifest()`` 供动态发现 / 单测使用。
    """
    return {
        "name": ExampleSeparatorPlugin.PLUGIN_NAME,
        "display_name": ExampleSeparatorPlugin.PLUGIN_DISPLAY_NAME,
        "version": ExampleSeparatorPlugin.PLUGIN_VERSION,
        "phase": "separation",
        "mock": True,
        "requirements": {
            "gpu_memory_mb": 0,
            "ram_mb_min": 128,
            "python_packages": ["numpy"],
        },
        "input_stems": [],
        "output": "separated_stems",
    }


async def run_async(
    rc,
    *,
    progress_callback=None,
    durations_sec: float = 3.0,
) -> dict[str, Any]:
    """异步版本：``asyncio.sleep`` 替代 ``time.sleep``，便于挂 FastAPI。

    与 :py:meth:`ExampleSeparatorPlugin.execute` 等价，但 ``time.sleep`` → ``asyncio.sleep``，
    这样可以在 FastAPI BackgroundTasks 里跑而不阻塞。

    Args:
        rc: ResourceController 句柄。
        progress_callback: 形如 ``Callable[[float], None]``，0~1 进度。
        durations_sec: 模拟总时长。
    """
    steps = 100
    try:
        raw = rc.get_buffer("raw")
    except Exception:
        raw = None
    n_samples = (raw.shape[1] if isinstance(raw, np.ndarray) and raw.ndim == 2
                else (len(raw) if raw is not None else 22050 * 3))

    for i in range(steps + 1):
        if progress_callback is not None:
            try:
                progress_callback(i / steps)
            except Exception:
                pass
        if i < steps:
            await asyncio.sleep(durations_sec / steps)

    for name in EXAMPLE_STEMS:
        rc.set_buffer(name, np.zeros(n_samples, dtype=np.float32))
    rc.set_metadata("separated_stems", EXAMPLE_STEMS)
    rc.set_metadata("separation_model", "example_separator")
    return {
        "status": "success",
        "data": {
            "plugin": "example_separator",
            "stems": EXAMPLE_STEMS,
            "mock": True,
        },
    }


__all__ = [
    "EXAMPLE_STEMS",
    "EXAMPLE_STEP_DELAY",
    "MockSeparationResult",
    "ExampleSeparatorPlugin",
    "get_manifest",
    "run_async",
]
