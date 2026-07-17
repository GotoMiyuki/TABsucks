# Windows 第一版发行

TABsucks 的 Windows 发行版采用以下结构：

- Inno Setup 安装包
- PyInstaller `onedir` 程序目录
- Tkinter 启动状态窗口
- 后台 FastAPI/Uvicorn 本地服务
- 服务就绪后自动打开默认浏览器
- 用户数据和日志写入 `%LOCALAPPDATA%\TABsucks`

## 用户体验

用户安装并启动 TABsucks 后：

1. 启动窗口立即出现并显示实时日志。
2. 程序优先监听 `127.0.0.1:8000`，端口被占用时自动选择后续可用端口。
3. HTTP 服务就绪后自动打开浏览器。
4. 窗口提供打开界面、复制地址、打开日志目录和停止退出操作。
5. 关闭窗口时，程序先停止服务并保存车间状态。

日志文件位于：

```text
%LOCALAPPDATA%\TABsucks\logs
```

车间和缓存位于：

```text
%LOCALAPPDATA%\TABsucks\cache
```

卸载程序不会自动删除用户车间和日志。

## 构建环境

当前 Windows 构建使用并验证了 64 位 Python 3.10.18。依赖文件按用途拆分：

```powershell
# 默认 CPU 源码运行环境
python -m pip install -r requirements.txt

# NVIDIA GPU 源码运行环境（二选一，不要再安装 requirements.txt）
python -m pip install -r requirements-gpu.txt -c constraints-windows.txt

# CPU 开发环境附加工具
python -m pip install -r requirements-dev.txt

# GPU 开发/EXE 构建环境附加工具
python -m pip install -r requirements-dev.txt -c constraints-windows.txt
```

`requirements-base.txt` 保存 CPU/GPU 共用的直接运行依赖；
`constraints-windows.txt` 记录当前 GPU 版 `pyq` 构建环境中已经验证的版本。
`requirements-dev.txt` 不会自动选择推理后端，避免同时安装 CPU 和 GPU 两套
ONNX Runtime。

只有直接调用 `src.audio.player` 的原生 Python 音频设备播放功能才需要：

```powershell
python -m pip install -r requirements-audio-device.txt
```

当前 Web UI 使用浏览器播放音频，不需要安装 `sounddevice`。节奏插件可选使用
`madmom` 增强分析，但缺少它时会使用内置回退，因此不属于必需运行依赖。
CPU 和 GPU requirements 分别锁定 PyTorch 2.6.0 的 CPU wheel 与 CUDA 12.4
wheel，避免 pip 静默选择错误后端。GPU 环境仍需 NVIDIA 驱动兼容 CUDA 12.4，
并确认 `torch.cuda.is_available()` 返回 `True`。显卡驱动和 Visual C++ 运行库
不是 pip 依赖，不能仅靠 requirements 文件安装。CPU 依赖组合尚未在干净的
Windows 环境完成验证，因此当前约束文件不用于 CPU 安装。

已经打包完成的 `dist\TABsucks` 便携版用户不需要安装 Python 或 requirements
文件；这些依赖清单只用于源码运行和重新构建 EXE。

构建前必须存在以下发行资源：

```text
models\BS-Roformer-SW.ckpt
models\BS-Roformer-SW.yaml
src\plugins\chord\external\chordmini\checkpoints\2e1d_model_best.pth
src\plugins\chord\external\chordmini\checkpoints\btc_model_best.pth
src\plugins\chord\external\chordmini\checkpoints\btc_model_large_voca.pt
```

这些模型目前部分被 `.gitignore` 排除，因此 CI 或新机器构建前需要单独准备。

构建脚本还会把 `ffmpeg.exe` 和 `ffprobe.exe` 放入发行目录。它优先使用构建机
`PATH` 中已有的程序；找不到时通过 `static-ffmpeg` 在构建阶段下载。最终用户
首次启动时不需要再下载 FFmpeg。

以下内容属于运行资源或系统依赖，不应写成普通 pip 包：

- `ffmpeg.exe` 和 `ffprobe.exe`
- BS-RoFormer 模型与 YAML 配置
- ChordMini/ISMIR2019 模型权重
- NVIDIA 驱动和可选 CUDA 运行环境
- Python 自带的 Tkinter（桌面启动状态窗口）

## 生成程序目录

```powershell
.\scripts\build_windows.ps1 -SkipInstaller
```

产物：

```text
dist\TABsucks\TABsucks.exe
```

不要只复制其中的 EXE，整个 `dist\TABsucks` 目录都是程序的一部分。
PyInstaller 6 会把运行资源放在其中的 `_internal` 子目录，这属于正常布局。

## 生成安装包

安装 Inno Setup 6，并确保 `iscc.exe` 位于 `PATH`，然后运行：

```powershell
.\scripts\build_windows.ps1
```

产物：

```text
dist\installer\TABsucks-Setup.exe
```

## 发布前检查

至少在一台未安装 Python 的 64 位 Windows 机器上验证：

- 安装、启动、卸载
- `8000` 被占用时仍能启动
- 浏览器自动打开
- 本地文件上传与播放
- BS-RoFormer 分离
- 默认 ISMIR 和弦分析
- ChordMini 两个和弦模型
- 日志目录和车间数据在重启后保留
- 无 NVIDIA GPU 时的提示与实际行为

当前依赖包含 `onnxruntime-gpu`。正式公开发布前，建议分别验证 NVIDIA GPU
机器与纯 CPU 机器，并决定是否拆分 CPU/GPU 安装包。
