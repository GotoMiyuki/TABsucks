# 开发日志：Windows 第一版桌面发行

**日期：** 2026-07-17  
**分支：** `end_main`

## 目标

将现有本地 Web 应用包装成普通 Windows 用户可安装、可双击启动的桌面发行版：

- Inno Setup 安装包
- Tkinter 启动状态窗口
- 后台 FastAPI/Uvicorn 服务
- 自动选择本地端口并在就绪后打开浏览器
- 窗口实时日志与 UTF-8 文件日志
- 用户数据写入 `%LOCALAPPDATA%\TABsucks`
- 随发行版携带静态资源、插件、模型和 FFmpeg

## 实现

1. `src/ui/desktop_launcher.py`
   - 管理用户目录、端口探测、HTTP 就绪轮询和浏览器启动。
   - 在后台线程运行 Uvicorn，并在退出时先停止服务再释放 Kernel。
   - 设置内置模型目录和用户模型缓存目录。

2. `src/ui/desktop_window.py`
   - 提供服务状态、访问地址、实时日志和常用操作。
   - 将 Python `logging`、标准输出和标准错误同时写入窗口与日志文件。

3. `scripts/tabsucks_launcher.py`
   - 作为 PyInstaller 入口。
   - 为打包后的 ISMIR2019 插件提供内部脚本子进程入口。

4. `packaging/tabsucks.spec`
   - 采用 `onedir` 模式。
   - 收集 Web 静态文件、manifest、ChordMini/ISMIR 插件、模型和 FFmpeg。

5. `packaging/tabsucks.iss`
   - 安装到当前用户的 `%LOCALAPPDATA%\Programs\TABsucks`。
   - 创建开始菜单快捷方式，可选桌面快捷方式。

6. Tab2 CPU/GPU 分离推理选择
   - 在分离模型下方增加 GPU/CPU 选择。
   - GPU 选择会在硬件可用且插件声明支持 GPU 时使用 CUDA。
   - CPU 选择会强制 `audio-separator` 使用 CPU 和
     `CPUExecutionProvider`。
   - 仅支持 CPU 的插件即使选择 GPU，后端也会实际使用 CPU。
   - 设备选择通过 API、Kernel、Orchestrator 传递到分离插件。
   - 分离事件记录用户请求设备和实际执行设备，便于识别 CPU 回退。
   - 仅在实际使用 GPU 时执行 CUDA 检查和显存准备；CPU 推理不会申请显存。

7. Bass Root Detector 双声道输入兼容
   - Bass 分离轨继续以双声道形式保存在 `ResourceController` 中，其他插件和播放流程不受影响。
   - 仅在运行 `chord_bass_root` 插件时创建临时单声道 `float32` 输入，并将其传给 `librosa.pyin`。
   - 同时兼容 `(samples, channels)` 和 `(channels, samples)` 两种音频数组布局。
   - 不调用 `rc.set_buffer("bass", ...)` 覆盖共享 Bass 缓冲区，插件完成后原始双声道数据保持不变。
   - 修复立体声 `(10213560, 2)` 被 `pyin` 错误解释为大量并行通道，进而尝试申请约 156 GiB 数组的问题。

## 数据与日志

```text
%LOCALAPPDATA%\TABsucks\cache
%LOCALAPPDATA%\TABsucks\logs
```

内置模型直接从安装目录读取；后续下载的模型写入用户缓存，避免依赖安装目录可写。

## 验证

- Ruff 静态检查
- 启动器与 SSE 关闭路径单元测试
- PyInstaller 构建
- 最终 EXE 启动、自动打开浏览器、关闭窗口和日志检查
- Inno Setup 安装包编译
- CPU/GPU 设备传递、CPU-only 插件回退和 CPU 推理提供程序聚焦测试
- Bass Root Detector 双声道两种布局转单声道及共享缓冲区保持不变测试

完整测试套件在当前沙箱中受到 pytest 临时目录 `WinError 5` 限制，发行相关的聚焦测试单独执行。

## 已知事项

- 当前便携目录约 7.27 GB，主要来自机器学习运行时依赖。
- PyInstaller 会报告部分可选 TensorRT/cuDNN DLL 缺失；这些不是当前默认功能的必需项。
- 后续公开发行前应评估拆分 CPU/GPU 安装包，以明显降低下载和安装体积。

## 2026-07-18：Windows 拆分发行

- 将 Windows 发行版拆分为 CPU 基础包、模型资源包和 GPU/CUDA 升级包。
- CPU 基础包仅包含程序本体、CPU 推理运行时、FFmpeg 和基础功能，默认分离设备改为 CPU。
- 模型资源从基础包和 GPU 包中移除，统一由独立模型安装包提供。
- GPU 升级包覆盖完整 GPU 运行环境和主程序，避免仅替换部分 Torch/CUDA 文件导致启动异常。
- GPU 升级包使用 Inno Setup 分片，确保每个 GitHub Release 资产小于 2 GiB。
- 新增打包运行时验证脚本，检查 PyTorch 版本、CUDA 可用性和 ONNX Runtime Provider。
- 安装顺序固定为：基础包、可选 GPU 升级包、模型资源包。
