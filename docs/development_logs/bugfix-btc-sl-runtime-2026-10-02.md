# BTC-SL / ChordMini 运行修复（2026-10-02）

BTC-SL 已可通过正式 HTTP API 分析真实整曲的六条分轨，完成保存、服务重启恢复和 MIDI 导出。修复解决了 [排查记录](bugfix-btc-sl-readiness-diagnosis-2026-10-02.md) 中的子模块缺失、传递依赖和同名 Python 包冲突。

## 变更

- 初始化 ChordMini 到主仓库锁定的 `0e63ad44788278af25259458065f8bc4e4e975d6`，取得随附的 BTC Teacher 与 CL 权重。子模块指针未升级，内部源码未修改，Git 状态干净。
- 新增 `chordmini_runtime.py`，在独立私有包名下加载 BTC 模型和滑窗推理文件。仅改写导入目标，保留上游模型及推理函数；从 `common.py` 只加载依赖 numpy 的分类平滑函数，避免初始化训练、评估和绘图模块。既不修改 `sys.path` 或主程序 `src.__path__`，也不替换 TABsucks 的 `src.utils`。
- 加载受锁保护，并在全部导入成功后缓存；失败清理私有模块，可重试。两个包加载顺序均有回归覆盖。
- 权重使用严格参数匹配，防止不完整权重留下未训练的参数；加载成功记录设备和参数组数。默认仍优先使用 `btc_model_large_voca.pt`，其次为 CL 权重。模型加载先于 CQT，缺失资源能提前失败。
- manifest 声明六份推理源码和默认权重候选。插件列表返回资源状态，兼容性检查纳入缺失资源；界面停用未就绪选项并说明原因，允许选择其他已就绪模型。
- README 使用递归检出并补充已有仓库的初始化步骤，HTTP API 文档描述新增状态字段。无需向现有环境安装 ChordMini 的整套训练或评估依赖。

## 验证

当前项目环境使用 Python 3.12、PyTorch 2.7.1+cu128。两份随附 BTC checkpoint 均通过 CPU 严格加载，221 组参数完整匹配。

真实整曲验证复用之前 GPU 分离得到的六条音轨：每条为 182.352 秒、48 kHz、双声道、8,752,896 帧。通过正式 `/api/workshops/{wid}/analyze` 指定 `chord_btc_sl`，所有任务完成；后台记录 Teacher checkpoint 在 CUDA 上加载，221 组参数严格匹配。

| 音轨 | 和弦段数（含 N/X） | 非 N/X 段数 | 任务用时 |
| --- | ---: | ---: | ---: |
| 人声 | 27 | 14 | 3.07 秒 |
| 鼓 | 1 | 0 | 0.38 秒 |
| 贝斯 | 1 | 0 | 0.37 秒 |
| 钢琴 | 1 | 0 | 0.34 秒 |
| 吉他 | 39 | 23 | 0.34 秒 |
| 其他 | 134 | 117 | 0.35 秒 |

首条任务包含首次加载开销。仅返回 N 的音轨也是成功完成的分析结果。

- MIDI 导出为 3313 字节，包含人声 42、吉他 72、其他 387 个音符，可由 MIDI 解析器读取。
- 服务重启后六条分析结果的 ID、插件和和弦段数一致；重新导出的 MIDI SHA-256 与重启前一致。任务查询记录按现有设计属于进程内状态，本次恢复检查针对持久化分析结果。
- 单独隐藏 CUDA 后，CPU 在 1.19 秒内完成 12 秒真实原曲片段的分析，返回 12 个和弦段。
- 107 项相关 Python 测试、15 项 JavaScript 测试通过；BTC-SL 流程测试不再因缺少子模块而静默跳过。Ruff 的 CI 正确性规则与 `git diff --check` 通过。

此验证确认真实模型的执行、保存、恢复和导出，没有进行人工和弦准确率评估。原用户工作坊未修改；测试与服务使用独立的 `.workbench-preview-real-debug` 缓存。

原始证据位于被 Git 忽略的 `.workbench-preview-btc-diagnosis`：`real-btc-results.json`、`real-cpu-result.json`、`backend-run.log`、`backend-restart.log`、`btc-all-tracks.mid`。
