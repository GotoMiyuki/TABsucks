# BTC-SL / ChordMini 不可用原因排查（2026-10-02）

后续已完成修复与真实音频验证，见 [BTC-SL 修复记录](bugfix-btc-sl-runtime-2026-10-02.md)。下文保留修复前的诊断事实。

本机 BTC-SL 在模型导入阶段失败，尚未进入权重加载或 GPU 推理。直接原因是 ChordMini 子模块未初始化；进一步隔离检查发现，补齐源码后还有传递依赖缺失和 Python 命名空间冲突。这次完成原因定位，没有修改正式插件源码、安装清单、子模块指针或现有虚拟环境。

## 本地检出与安装流程

- `.gitmodules` 正确声明了 `src/plugins/chord/external/chordmini`，Git HEAD 记录为 `160000 commit 0e63ad44788278af25259458065f8bc4e4e975d6`。
- 本地该目录文件数量为零，没有 `.git/modules`，本地 Git 配置也没有已初始化的子模块配置。
- `README.md` 的源码安装流程使用普通 `git clone`，随后安装 Python 依赖，没有 `--recurse-submodules` 或 `git submodule update --init`。照此流程检出时不会获得 ChordMini 的内容；`pip install -r requirements.txt` 也不会初始化 Git 子模块。
- ISMIR2019 在 HEAD 中是普通 `040000 tree`，其源码及权重实际包含在主仓库。这解释了两种分析选项在同一环境中表现不同。
- ChordMini 锁定的提交在上游公开仓库中存在，其树包含 `btc_model_large_voca.pt` 和 `btc_model_best.pth`，分别约 12.2 MB 和 35.9 MB。两份本地默认权重均不存在，与源码缺失属于同一子模块检出问题。

这些事实确认当前检出缺少初始化，安装说明遗漏足以重现问题；不能据此追溯本机最初是哪次检出或文件操作造成目录为空。

## 分层复现

使用项目当前 `.venv`，将锁定版本的相关 Python 源码下载到独立、被 Git 忽略的 `.workbench-preview-btc-diagnosis/snapshot`。没有将它们写入正式 ChordMini 目录，也没有下载模型权重。`mir_eval==0.8.2` 仅解压到诊断目录，通过单独进程的 `sys.path` 使用，没有安装进项目虚拟环境。

| 条件 | 复现结果 | 原因 |
| --- | --- | --- |
| 当前正式路径 | `ModuleNotFoundError: No module named 'src.models'` | `btc_sl.py::_ensure_imports` 需要的源码不存在 |
| 隔离使用完整锁定版本源码，保持当前依赖 | `ModuleNotFoundError: No module named 'mir_eval'` | 上游 `inference.py` 导入 `evaluation.utils.common`，该文件在模块顶层导入 `mir_eval` |
| 同上，诊断进程额外使用 `mir_eval`，未预加载主程序 `src.utils` | `ModuleNotFoundError: No module named 'matplotlib'` | `evaluation/utils/__init__.py` 同时导入 `quality_analysis`，引入绘图依赖；该文件还导入 `seaborn` 和 `sklearn` |
| 同上，先加载主程序 `src.utils` | `ImportError: cannot import name '_parse_chord_string' from 'src.utils'` | 上游要求 ChordMini 的工具函数，却命中了 TABsucks 的同名包 |

`btc_sl.py` 直接加载 `inference.py` 的做法没有完全绕过包初始化：该文件自身的绝对导入仍会执行 `evaluation/utils/__init__.py`。因此仅补齐子模块、仅增加 `mir_eval` 都不足以完成修复。

命名空间冲突与正常流程相关：`Kernel` 创建工作坊时会导入 `src.utils.naming`，上传接口也使用其中的标题清理函数。它们会提前加载 TABsucks 的 `src.utils`。之后即使向 `src.__path__` 插入 ChordMini 路径，Python 仍复用已经缓存的同名工具包。反过来，上游工具包先被加载时，也会占用主程序需要的包名。

上游完整 `requirements.txt` 含训练、评估和绘图依赖，并固定 `torch==2.9.1`、`numpy==1.26.4` 等版本，与当前项目 GPU 环境和依赖约束不同。修复应明确推理所需依赖或隔离不需要的评估导入，避免直接覆盖现有环境。

## 为什么界面和测试没有提前发现

- `src/plugins/chord/manifest.json` 的 BTC-SL 依赖只声明 `torch`、`librosa`、`numpy`，没有声明源码、权重或上述传递依赖。
- `PluginManager.get_available_plugins()` 按 manifest 列表返回菜单项，没有资产就绪检查。
- 本机实测 `check_compatibility('chord_btc_sl')` 返回 `compatible: true`，`missing_packages: []`，插件实例也能正常构造；执行时的延迟导入仍然失败。这个检查不能代表实际可执行。
- `tests/unit/test_btc_sl.py` 的执行测试在无法导入 ChordMini 时明确 `pytest.skip`，随后使用 mock 权重和 mock 推理函数。因此这些测试没有覆盖本机完整真实推理链路。
- CI 已为 Python 测试配置递归子模块检出，但这不会替开发者初始化本地检出，也不能代替真实模型就绪检查。

## 修复范围与验证边界

建议按顺序恢复锁定子模块及其模型资产、处理同名包导入冲突和不必要的评估导入、补齐实际推理依赖、增加源码/权重就绪检查，再用真实音频验证 BTC-SL。就绪检查应返回可读的缺失原因，避免将 manifest 被发现等同于可执行。

本次只验证加载与依赖问题，没有运行 BTC-SL 的真实权重推理，因此不能宣称其恢复后已可用或评价和弦准确率。正式源文件和运行中的服务均未改动。

诊断证据保存在 Git 忽略目录中：

- `.workbench-preview-btc-diagnosis/upstream-tree.json`：锁定上游版本的完整树与文件尺寸。
- `.workbench-preview-btc-diagnosis/layered-import-results.json`：完整源码下的传递依赖报错。
- `.workbench-preview-btc-diagnosis/namespace-import-result.json`：主程序工具包预加载后的命名空间冲突。

上游来源：[锁定提交](https://github.com/ptnghia-j/ChordMini/commit/0e63ad44788278af25259458065f8bc4e4e975d6)、[锁定版本源码与资产树](https://api.github.com/repos/ptnghia-j/ChordMini/git/trees/0e63ad44788278af25259458065f8bc4e4e975d6?recursive=1)。
