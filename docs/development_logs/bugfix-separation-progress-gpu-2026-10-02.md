# 分离进度修正与 RTX 5070 GPU 验证（2026-10-02）

分离任务已使用真实阶段与计数，取消按时间递增至 95% 的提示。当前项目 `.venv` 已升级为 CUDA 12.8 环境；同一首真实歌曲在 RTX 5070 Laptop GPU 上完成六轨分离，用时 39.235 秒。六轨文件、波形、ISMIR 2019 分析和 MIDI 导出通过验证。

本次继续使用之前失败项目中的 182.352 秒、48 kHz、双声道歌曲。所有测试结果放在独立 `.workbench-preview-real-debug`，原项目保持原状态。

## 进度行为

- 下载、加载、音频准备没有可测总量时仅显示阶段；下载有长度时报告实际字节数，长度未知时显示已下载大小。
- BS-RoFormer 按实际完成的模型 forward 计数。本曲 23 个片段，依次上报 0/23 至 23/23；CUDA 运算同步后才计入完成数。
- 推理结束后继续显示生成分轨、校验分轨、保存结果，每个阶段独立计数 0/6 至 6/6。全部持久化完成后才发送 `separation_done`。
- 百分比明确表示当前阶段。未测量阶段不编造百分比；失败、取消、中断不保留前一阶段的 100%。
- 任务 API 保留最新阶段、真实计数和实际设备，刷新/重连后可以恢复。前端过滤不匹配的任务及旧时间戳，使用持续状态文字而非反复弹出进度消息。
- 运行中设备选项与实际任务一致且不可更改；刷新恢复 GPU 选择，任务结束后可以选择下一次使用的设备。
- 分轨持久化在提交线程执行，使保存期间的任务查询和 SSE 仍可响应。

进度适配器只包装当前 audio-separator 引擎实例的方法，不修改第三方安装目录或全局 tqdm。计数实现针对已验证的 audio-separator 0.44.1 BS-RoFormer；无法识别的推理布局保留阶段提示。

## GPU 环境

| 组件 | 已安装并验证 |
| --- | --- |
| Python | 3.12.14，项目 `.venv` |
| 显卡 / 驱动 | RTX 5070 Laptop GPU，8151 MiB，596.13 |
| PyTorch / torchaudio | 2.7.1+cu128 |
| torchvision | 0.22.1+cu128 |
| CUDA / cuDNN | 12.8 / 9.7.1 |
| ONNX Runtime GPU | 1.23.2 |
| audio-separator | 0.44.1 |

PyTorch 架构列表包含本机所需的 `sm_120`；真实 CUDA 矩阵计算通过。ONNX 会话禁止 CPU 回退后完成 MatMul，运行 profile 确认节点使用 `CUDAExecutionProvider`。矩阵精度检查关闭该会话的默认 TF32，未改变应用的模型推理设置。

卸载了原 CPU `onnxruntime`，再安装 GPU 包，避免两个发行包覆盖同一个 Python 模块。`pip check` 无依赖冲突。升级前后依赖清单保存在测试目录。

可复用安装入口为 [requirements-gpu-blackwell.txt](../../requirements-gpu-blackwell.txt)，用法已写入 README；原 CUDA 12.4 发行构建配置保留。此次配置的是源码环境，没有重新打包或替换已安装 EXE。

版本组合依据 [PyTorch 官方配套命令](https://pytorch.org/get-started/previous-versions/)、
[Blackwell 支持说明](https://pytorch.org/blog/pytorch-2-7/)与
[ONNX Runtime CUDA/DLL 说明](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)。

## 实际验证

| 验证 | 结果 |
| --- | --- |
| CPU 真实 12 秒片段 | 31.367 秒；真实 0/2、1/2、2/2 片段和六轨保存事件 |
| 页面中途刷新 | 恢复“分离音轨 · 1/2 片段（50%） · CPU” |
| GPU 整首真实歌曲 | 39.235 秒；23/23 片段；六轨成功保存 |
| GPU 采样观测 | 利用率最高 100%；全卡已用显存最高采样值 4085 MiB，包含桌面及其他进程 |
| GPU 页面刷新 | 恢复保存阶段；随后显示“分离完成 · 6/6 音轨 · GPU”，设备选择与任务一致 |
| 六轨数据 | 每轨 48 kHz、双声道、8,752,896 帧；有限数值、非全零，时长均为 182.352 秒 |
| 原曲及六轨媒体/波形 | 七个 Range 请求均为 206；波形时长和峰值数正常 |
| ISMIR 2019 六轨分析 | 全部完成，每轨约 4.9–5.4 秒 |
| MIDI 导出 | HTTP 200，2370 字节；解析得到吉他 42、其他 318 个音符 |
| 自动化回归 | Python 102 项、前端 12 项通过；Ruff correctness、依赖和空白检查通过 |

初次 CPU 整曲基线为 409.688 秒，包含首次模型下载；本次 GPU 使用已缓存模型，因此两个总耗时不能直接作为严格的加速倍率。

测试中刷新媒体连接时出现 Windows Proactor `WinError 10054` 日志；对应浏览器旧连接被关闭，任务继续推进并完成。没有把这些日志归为模型失败，未在本次修改网络连接处理。

原有 BTC-SL 的 ChordMini 源码缺失问题仍在，未纳入本次修复。这里只验证真实处理链路和产物完整性，没有进行人工试听或和弦准确率验收。

原始阶段事件、任务 ID、GPU 采样、音轨数据和分析记录见 [验证数据](../workbench-validation/progress-gpu-20261002.json)。页面截图与 SSE/后台日志在独立测试目录中。

## 查看结果

当前结果服务在 `http://127.0.0.1:8020/`，完整 GPU 项目为“进度验证 GPU（真实音频）”。关闭后可以重新打开：

```powershell
.venv\Scripts\python.exe -X utf8 -m src.ui --host 127.0.0.1 --port 8020 --cache-root .workbench-preview-real-debug
```

正常项目仍可使用 `.venv\Scripts\python.exe -m src.ui` 启动，进入分离设置后选择 GPU 加速。
