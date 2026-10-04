# 真实模型全流程排查记录（2026-10-02）

本次使用之前失败项目中的原曲，通过正式 HTTP API 重跑了导入、真实 BS-RoFormer 六轨分离、音轨读取、波形、六轨 ISMIR 2019 分析、MIDI 导出和重启恢复。CPU + ISMIR 路径完成，未复现分离死锁；GPU 和 BTC-SL 路径各有明确的环境阻断。已修复两类分离插件错误处理问题。

后续已修正分离阶段和进度提示，并完成本机 GPU 配置与真实整曲验证，详见 [进度与 GPU 修复记录](bugfix-separation-progress-gpu-2026-10-02.md)。BTC-SL 后续也已恢复并完成真实六轨验证，见 [BTC-SL 修复记录](bugfix-btc-sl-runtime-2026-10-02.md)。本文保留初次排查的基线结果。

测试使用独立 `.workbench-preview-real-debug` 缓存。原项目 `cache/workshop_b209de6ae783450f` 仅作为输入来源，保留原状态。旧项目只保存了 `failed` 状态，没有当时的错误详情，因此不能断言本次发现就是历史失败的唯一原因。

## 实际运行结果

输入为 182.352 秒、48 kHz、双声道 MP3。运行环境为 Python 3.12.14、PyTorch 2.6.0+cpu、audio-separator 0.44.1、ONNX Runtime 1.30.0。

| 步骤 | 结果 | 证据 |
| --- | --- | --- |
| 原曲上传 | 完成 | HTTP 200；上传约 7.3 MB |
| GPU 分离 | 失败，约 3.2 秒 | `GPU was requested for separation_bs_roformer, but CUDA is unavailable.` |
| CPU 六轨分离 | 完成，409.688 秒（约 6 分 50 秒） | 真实 `BS-Roformer-SW.ckpt`；首次模型准备约 45 秒，底层分离约 6 分 2 秒 |
| 六轨文件保存 | 完成 | 每轨双声道、48 kHz、8,752,896 帧、182.352 秒；全曲检查六轨均有非零音频 |
| 原曲及六轨读取 | 完成 | 七个 HTTP Range 请求均为 206，返回正确媒体类型和 64 字节范围内容 |
| 原曲及六轨波形 | 完成 | 七条波形均为 182.352 秒、2000 个峰值 |
| BTC-SL 钢琴分析 | 失败，约 2.5 秒 | `No module named 'src.models'` |
| ISMIR 2019 六轨分析 | 完成 | 六个真实任务均 `done`，每轨约 6.1–6.6 秒 |
| MIDI 导出 | 完成 | HTTP 200，2370 字节；可由 MIDI 解析器读取 |
| 服务重启及恢复 | 完成 | 恢复六条分轨、七条分析记录（包含一次 BTC-SL 失败）；MIDI 再次导出成功 |
| 修复后真实模型加载与分离 | 完成 | 新服务进程运行原曲 12 秒片段，31.119 秒完成六轨分离 |

MIDI 中实际有音符的声部是吉他（42 个音符）和其他（318 个音符）。人声、鼓、贝斯、钢琴本次分析返回无和弦，不产生 MIDI 音符。此记录验证处理链路和数据结构；未进行试听、人工分离质量或和弦识别准确率验收。

原始任务 ID、时间戳、音源信息、媒体响应和结果引用保存在 [验证数据](../workbench-validation/real-full-flow-20261002.json)。

## 定位到的问题

### 1. 页面 95% 不是模型的真实进度（后续已修复）

`src/kernel/core/kernel_orchestrator.py::call_plugin_execute_async` 对未报告进度的同步插件，每 0.5 秒将进度增加 2%，达到 95% 后停止增加。BS-RoFormer 没有回传原生分片进度，因此约 24 秒就能显示 95%，而本次真实推理还需要数分钟。

模型下载、加载、推理在任务查询中也都归为 `running_plugin`。这会让页面看起来停住，却无法说明是在准备模型还是运行推理。本次后台日志中的分片计数持续从 1/23 推进到 23/23，没有出现死锁。

后续已接入真实片段和分轨计数，无可测量进度时显示阶段，并支持页面刷新恢复。详见上面的后续修复记录。

### 2. GPU 运行环境与实际硬件不匹配（后续已配置并验证）

`nvidia-smi` 能识别 NVIDIA GeForce RTX 5070 Laptop GPU，显存约 8 GB，驱动 596.13；当前虚拟环境安装的是 `torch==2.6.0+cpu`，`torch.version.cuda` 为 `None`，`torch.cuda.is_available()` 为 `False`。因此本次 GPU 失败是 Python 运行环境问题，不能解释为机器没有显卡。

初次排查采用已安装的 CPU 环境完成，没有替换依赖。后续已升级项目 `.venv` 至 PyTorch 2.7.1+cu128 和 ONNX Runtime GPU 1.23.2，同一整曲 GPU 分离在 39.235 秒内完成；完整验证数据见后续修复记录。

### 3. BTC-SL 所需的 ChordMini 子模块缺失（尚未修改）

`src/plugins/chord/external/chordmini` 目录为空。`btc_sl.py::_ensure_imports` 尝试从该目录加载 `src.models.btc_model` 时失败。manifest 仍把 BTC-SL 列入模型菜单，但模型可被发现不代表本地源码和权重已就绪。

本次显式测试 BTC-SL 并保留失败记录，然后选择现有的真实 ISMIR 2019 插件完成六轨分析。没有把 ISMIR 的成功算作 BTC-SL 成功。BTC-SL 需要补齐子模块和模型权重后重新验证；插件就绪检查和错误提示也有改进空间。

### 4. 首次模型加载失败后，重试跳过加载（已修复）

原 `_init_engine` 在调用 `load_model` 前就赋值 `_separator_instance`。首次加载异常时，实例存在，但设备和模型缓存标记仍为空；再次调用时不会进入加载分支，误复用未就绪引擎。切换模型失败时也会覆盖之前已加载的正常引擎。

已改为先在局部变量中构建和加载引擎，成功后才一起发布实例、设备及模型标记。加载失败可以重新尝试；切换失败保留之前的正常引擎。

### 5. 底层 `sys.exit()` 越过任务错误处理（已修复）

当前 audio-separator 的模型加载代码在部分 `RuntimeError` 情况下会调用 `sys.exit(1)`，产生 `SystemExit`，原来的 `except Exception` 无法捕获。已在模型加载和分离调用边界将 `SystemExit` 转为 `SeparatorError`，让上层记录任务失败。

该故障通过注入 `SystemExit` 复现并回归验证；没有故意破坏真实模型权重。

## 修复与验证范围

代码修改仅涉及 `src/plugins/separation/model_1/separator.py`，回归用例位于 `tests/unit/test_separation_plugin.py`。新增的四个用例在修复前全部失败，修复后全部通过，覆盖首次加载失败后的重试、底层加载退出、切换失败保留正常引擎以及推理退出后的临时文件清理。

以下 32 项测试通过，Ruff correctness 检查及修改内容空白检查通过：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/unit/test_separation_plugin.py tests/unit/test_kernel_orchestration.py tests/unit/test_p1_task_audio_contract.py -q --basetemp .pytest-workbench-real-flow
```

完整歌曲用于修复前的真实链路排查；重启加载修复代码后，又对同一原曲的 12 秒片段进行了真实分离复测。未将短片段复测描述为修复后整首歌曲再次运行。

独立结果页为 `http://127.0.0.1:8020/`，完整歌曲项目名为“真实全流程排查 2026-10-02”，另有“修复后真实模型复测（12秒）”项目。服务退出后可用以下命令重新打开这些结果：

```powershell
.venv\Scripts\python.exe -X utf8 -m src.ui --host 127.0.0.1 --port 8020 --cache-root .workbench-preview-real-debug
```
