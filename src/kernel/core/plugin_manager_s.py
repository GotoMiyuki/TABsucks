"""
SeparationPluginManager – 音轨分离专用插件管理器。

在 PluginManager 基类之上扩展了：
- Manifest 自动发现（扫描 model_*/manifest.json）
- 动态插件实例化（从 entrypoint 动态 import）
- 硬件兼容性探针（GPU 显存 / RAM / Python 包）
- VRAM 准备与清理（执行前释放缓存模型，检查显存余量）

与 AnalysisEngine 的协作流程::

    rc = ResourceController()
    pm = SeparationPluginManager(rc)

    # 1. UI 获取可用插件列表 → 渲染下拉菜单
    plugins = pm.get_available_plugins()

    # 2. 用户选择插件 + 参数后，AnalysisEngine 动态实例化
    pm.instantiate_plugin("separation_bs_roformer", {"model_name": "BS-Roformer-SW.ckpt"})

    # 3. 环境探针：检查硬件是否满足 manifest 声明的阈值
    compat = pm.check_compatibility("separation_bs_roformer")
    if not compat["compatible"]:
        show_warning(compat["warnings"])

    # 4. 显存准备：释放 RC 中缓存的其他模型，确保显存充裕
    vram_status = pm.prepare_vram("separation_bs_roformer")

    # 5. 执行分离（插件内部从 RC 读取 raw → 分离 → 回写各 stem buffer）
    result = pm.execute("separation_bs_roformer")

    # 6. 下游插件（和弦、节奏）通过 rc.get_buffer("piano") 等读取分离结果
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from src.kernel.core.plugin_manager import PluginManager, PluginManagerError
from src.kernel.core.resource_controller import ResourceController
from src.plugins import Plugin


class SeparationPluginManagerError(PluginManagerError):
    """分离插件管理器操作失败时抛出。"""
    pass


# ------------------------------------------------------------------
# 辅助：包名 → 导入名映射
# 部分 pip 包名与 import 名不一致，在此维护映射表
# ------------------------------------------------------------------
_PKG_IMPORT_MAP: dict[str, str] = {
    "audio-separator": "audio_separator",
    "onnxruntime-gpu": "onnxruntime",
    "scikit-learn": "sklearn",
}


def _try_import(package_name: str) -> bool:
    """尝试导入一个 Python 包，返回是否成功。"""
    import_name = _PKG_IMPORT_MAP.get(package_name, package_name.replace("-", "_"))
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


# ------------------------------------------------------------------
# SeparationPluginManager
# ------------------------------------------------------------------


class SeparationPluginManager(PluginManager):
    """音轨分离专用插件管理器。

    继承自 PluginManager，新增 manifest 发现、动态实例化、
    硬件兼容性检查、VRAM 准备等功能。

    所有分离模型（如 BS-RoFormer）以 manifest.json 形式注册在
    ``src/plugins/separation/model_*/`` 目录下，本管理器在构造时
    自动扫描并登记。
    """

    # manifest 搜索目录（相对于本文件的路径）
    _MANIFEST_SEARCH_SUBDIR: tuple[str, ...] = ("plugins", "separation")

    def __init__(self, rc: ResourceController) -> None:
        super().__init__(rc)
        self._manifests: dict[str, dict] = {}
        self._discover()

    # ------------------------------------------------------------------
    # Manifest 发现
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """自动扫描分离插件 manifest 并登记到内部注册表。

        遍历 ``src/plugins/separation/model_*/manifest.json``，
        解析 ``{"plugins": [...]}`` 数组，每个条目以 ``name``
        字段为键存入 ``_manifests``。
        """
        # 从本文件位置推算项目 src 目录
        # plugin_manager_s.py → src/kernel/core → src/kernel → src
        src_dir = Path(__file__).resolve().parents[2]
        search_dir = src_dir.joinpath(*self._MANIFEST_SEARCH_SUBDIR)

        if not search_dir.is_dir():
            return

        for manifest_path in sorted(search_dir.glob("*/manifest.json")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            plugins_list: list[dict] = data.get("plugins", [])
            for entry in plugins_list:
                name = entry.get("name")
                if not name:
                    continue
                # 附加 manifest 所在目录，供 instantiate 使用
                entry["_manifest_dir"] = str(manifest_path.parent)
                self._manifests[name] = entry

    # ------------------------------------------------------------------
    # UI 查询接口
    # ------------------------------------------------------------------

    def get_available_plugins(self) -> list[dict]:
        """返回所有已发现分离插件的 manifest 元数据列表。

        供 UI 层渲染下拉菜单：显示名称、版本、输入/输出 stem、
        硬件需求、可选参数等信息。

        Returns:
            manifest 条目列表，每个 dict 包含:
            ``name``, ``display_name``, ``version``,
            ``requirements``, ``input_stems``, ``output``,
            ``phase``, ``class``, ``entrypoint`` 等字段。
        """
        # 返回副本，避免外部意外修改内部状态
        return [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for m in self._manifests.values()
        ]

    def get_manifest(self, name: str) -> dict | None:
        """获取指定插件的 manifest 条目。

        Args:
            name: 插件名称（对应 manifest 中 ``"name"`` 字段）。

        Returns:
            manifest dict，不存在时返回 None。
        """
        return self._manifests.get(name)

    # ------------------------------------------------------------------
    # 动态实例化
    # ------------------------------------------------------------------

    def instantiate_plugin(
        self,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> Plugin:
        """根据 manifest 的 entrypoint 动态导入并实例化插件。

        插件实例化后自动调用 ``self.register()`` 注册到父类
        PluginManager 中，后续可直接通过 ``pm.execute(name)`` 执行。

        实现细节:
        1. 从 manifest 读取 ``entrypoint``（模块路径）和 ``class``（类名）
        2. ``importlib.import_module(entrypoint)`` 动态加载模块
        3. ``getattr(module, class_name)`` 获取插件类
        4. 以 ``config`` 为构造参数实例化
        5. 调用 ``self.register(instance)`` 注册

        Args:
            name: 插件名称。
            config: 可选配置字典，传递给插件类的 ``__init__``。
                   例如 ``{"model_name": "BS-Roformer-SW.ckpt"}``。

        Returns:
            已注册的 Plugin 实例。

        Raises:
            SeparationPluginManagerError: manifest 不存在、导入失败、
                                         类不存在或构造参数不匹配。
        """
        manifest = self._manifests.get(name)
        if manifest is None:
            available = list(self._manifests.keys())
            raise SeparationPluginManagerError(
                f"插件 '{name}' 未在 manifest 中找到。"
                f"可用插件: {available}"
            )

        entrypoint = manifest.get("entrypoint")
        if not entrypoint:
            raise SeparationPluginManagerError(
                f"插件 '{name}' 的 manifest 缺少 'entrypoint' 字段。"
            )

        class_name: str = manifest.get("class", "")
        if not class_name:
            raise SeparationPluginManagerError(
                f"插件 '{name}' 的 manifest 缺少 'class' 字段。"
            )

        # 1. 动态导入模块
        try:
            module = importlib.import_module(entrypoint)
        except ImportError as e:
            raise SeparationPluginManagerError(
                f"无法导入插件 '{name}' 的入口模块 '{entrypoint}': {e}"
            )

        # 2. 获取插件类
        try:
            plugin_cls = getattr(module, class_name)
        except AttributeError:
            raise SeparationPluginManagerError(
                f"模块 '{entrypoint}' 中未找到类 '{class_name}'。"
            )

        # 3. 实例化
        config = config or {}
        try:
            instance = plugin_cls(**config)
        except TypeError as e:
            raise SeparationPluginManagerError(
                f"实例化 '{class_name}' 失败，config={config}: {e}"
            )

        # 4. 注册到父类
        self.register(instance)
        return instance

    # ------------------------------------------------------------------
    # 硬件兼容性探针
    # ------------------------------------------------------------------

    def check_compatibility(self, name: str) -> dict[str, Any]:
        """探测当前系统硬件是否满足插件的运行要求。

        检查项:
        - GPU 显存（通过 torch.cuda）
        - 系统 RAM（通过 psutil，可选）
        - Python 依赖包是否已安装

        供 UI 层在用户点击"运行"前展示兼容性警告，
        也可供 AnalysisEngine 在调度前做最终校验。

        Args:
            name: 插件名称。

        Returns:
            兼容性报告字典::

                {
                    "compatible": bool,       # 总体是否兼容
                    "gpu_available": bool,    # CUDA 是否可用
                    "gpu_free_mb": float|None,  # 当前空闲显存 (MB)
                    "gpu_required_mb": float,    # 声明所需显存 (MB)
                    "gpu_ok": bool,              # 显存是否达标
                    "ram_ok": bool,              # 内存是否达标
                    "missing_packages": [str, ...],  # 缺失的包
                    "warnings": [str, ...],      # 警告信息
                    "errors": [str, ...],        # 错误信息
                }
        """
        manifest = self._manifests.get(name)
        if manifest is None:
            return {
                "compatible": False,
                "errors": [f"插件 '{name}' 未在 manifest 中找到。"],
                "warnings": [],
            }

        reqs: dict = manifest.get("requirements", {})
        gpu_required_mb: float = float(reqs.get("gpu_memory_mb", 0))
        ram_required_mb: float = float(reqs.get("ram_mb_min", 0))
        required_packages: list[str] = reqs.get("python_packages", [])

        warnings: list[str] = []
        errors: list[str] = []

        # ---- GPU 显存探针 ----
        gpu_available = False
        gpu_free_mb: float | None = None
        gpu_ok = True

        try:
            import torch

            if torch.cuda.is_available():
                gpu_available = True
                free_bytes = torch.cuda.mem_get_info()[0]
                gpu_free_mb = free_bytes / (1024**2)

                if gpu_free_mb < gpu_required_mb:
                    gpu_ok = False
                    warnings.append(
                        f"GPU 空闲显存 ({gpu_free_mb:.0f} MB) 低于"
                        f" 插件要求 ({gpu_required_mb:.0f} MB)，"
                        f" 可能导致 OOM 崩溃。"
                    )
            elif gpu_required_mb > 0:
                warnings.append(
                    f"插件需要 {gpu_required_mb:.0f} MB 显存，"
                    f" 但 CUDA 不可用，将回退到 CPU 推理（速度较慢）。"
                )
        except ImportError:
            if gpu_required_mb > 0:
                warnings.append("未安装 torch，无法探测 GPU 状态。")

        # ---- 系统 RAM 探针 ----
        ram_ok = True
        try:
            import psutil

            avail_mb = psutil.virtual_memory().available / (1024**2)
            if avail_mb < ram_required_mb:
                ram_ok = False
                warnings.append(
                    f"系统可用内存 ({avail_mb:.0f} MB) 低于"
                    f" 插件要求 ({ram_required_mb:.0f} MB)。"
                )
        except ImportError:
            # psutil 未安装时跳过 RAM 检查，不阻塞
            pass

        # ---- Python 依赖包检查 ----
        missing_packages: list[str] = []
        for pkg in required_packages:
            if not _try_import(pkg):
                missing_packages.append(pkg)

        if missing_packages:
            warnings.append(
                f"缺少 Python 依赖包: {missing_packages}，"
                f" 请使用 pip install 安装。"
            )

        compatible = gpu_ok and ram_ok and len(errors) == 0

        return {
            "compatible": compatible,
            "gpu_available": gpu_available,
            "gpu_free_mb": gpu_free_mb,
            "gpu_required_mb": gpu_required_mb,
            "gpu_ok": gpu_ok,
            "ram_ok": ram_ok,
            "missing_packages": missing_packages,
            "warnings": warnings,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # VRAM 管理
    # ------------------------------------------------------------------

    def prepare_vram(self, name: str) -> dict[str, Any]:
        """在分离运算前准备显存。

        执行步骤:
        1. 调用 ``rc.release_all_models()`` 清空 RC 中缓存的
           其他模型，释放被占用的显存。
        2. 探测当前 GPU 空闲显存，与插件 manifest 声明的
           ``gpu_memory_mb`` 阈值对比。
        3. 返回就绪状态与显存信息。

        这是防止 OOM 的关键步骤 —— 在加载 BS-RoFormer 等
        重量级模型之前先"清场"，确保显存充裕。

        Args:
            name: 插件名称。

        Returns:
            {
                "ready": bool,              # 显存是否就绪
                "gpu_free_mb": float|None,  # 当前空闲显存
                "gpu_required_mb": float,   # 插件所需显存
                "models_released": bool,    # 是否已释放缓存模型
                "message": str,             # 人类可读的状态描述
            }
        """
        manifest = self._manifests.get(name)
        gpu_required_mb: float = 0.0
        if manifest is not None:
            gpu_required_mb = float(
                manifest.get("requirements", {}).get("gpu_memory_mb", 0)
            )

        # Step 1: 释放 RC 中所有已缓存的模型，腾出显存
        self._rc.release_all_models()
        models_released = True

        # Step 2: 探测 GPU
        gpu_free_mb: float | None = None
        ready = True
        message = "VRAM 准备完成。"

        try:
            import torch

            if torch.cuda.is_available():
                free_bytes = torch.cuda.mem_get_info()[0]
                gpu_free_mb = free_bytes / (1024**2)

                if gpu_required_mb > 0 and gpu_free_mb < gpu_required_mb:
                    ready = False
                    message = (
                        f"显存不足: 空闲 {gpu_free_mb:.0f} MB，"
                        f" 需要 {gpu_required_mb:.0f} MB。"
                        f" 建议关闭其他应用程序或启用 CPU 模式。"
                    )
                else:
                    message = (
                        f"GPU 就绪: 空闲 {gpu_free_mb:.0f} MB，"
                        f" 需求 {gpu_required_mb:.0f} MB。"
                    )
            else:
                message = "CUDA 不可用，将在 CPU 上运行。"
        except ImportError:
            message = "未安装 torch，无法探测 GPU 状态，假定 CPU 运行。"

        return {
            "ready": ready,
            "gpu_free_mb": gpu_free_mb,
            "gpu_required_mb": gpu_required_mb,
            "models_released": models_released,
            "message": message,
        }

    # ------------------------------------------------------------------
    # 批量操作
    # ------------------------------------------------------------------

    def refresh_manifests(self) -> int:
        """重新扫描 manifest 目录，返回发现的插件数量。

        用于用户添加新模型目录后的热刷新。
        """
        self._manifests.clear()
        self._discover()
        return len(self._manifests)
