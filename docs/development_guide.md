## 开发指导

本文档面向 TABsucks 开发团队，涵盖从环境配置到代码合并的完整协作流程。

---

## 概览：三种角色

| 角色 | 职责 | 产出物 |
|------|------|--------|
| **开发者** | 负责功能实现、单元测试、提交 PR | 分支上的代码、PR 描述 |
| **测试者** | 执行测试、验证功能、报告缺陷 | 测试结果、PR 反馈 |
| **审查者** | 审核代码逻辑、设计合理性、代码质量 | Review 评论、Approval |

> 实践中，开发者与测试者通常为同一人（自己开发的功能自己先测），审查者为其他团队成员。

---

## 一、功能开发流程（开发者 / 测试者）

### 1.1 环境配置

**目标**：在一台新机器上，从零搭建可开发的环境。

**步骤**：

1. 安装 Python 3.11+
2. 克隆代码仓库
3. 创建虚拟环境
4. 安装依赖
5. 配置 pre-commit（可选但推荐）

**操作示例**：

```bash
# 1. 克隆仓库
git clone https://github.com/你的用户名/TABsucks.git
cd TABsucks

# 2. 创建虚拟环境（Python 环境管理详见 → § 基础概念/虚拟环境）
python -m venv venv
.\venv\Scripts\activate      # Windows
# source venv/bin/activate  # Linux/macOS

# 3. 安装依赖
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 开发/测试额外依赖

# 4. 配置 pre-commit 钩子（可选）（pre-commit 配置详见 → § 基础概念/pre-commit）
pip install pre-commit
pre-commit install
```

> **如果遇到问题**：参见 § 基础概念/Git 基础 和 § 基础概念/Python 环境

---

### 1.2 创建开发分支

**目标**：在独立分支上开发，不污染主分支。

**步骤**：

1. 确保主分支最新
2. 基于功能编号创建新分支
3. 切换到新分支开始开发

**操作示例**：

```bash
# 确保 main 最新
git checkout main
git pull origin main

# 创建并切换到新分支（分支命名规范 → § 基础概念/Git 分支）
git checkout -b feature/FR-01-audio-input
```

> **分支命名规则**：
> - `feature/FR-xx-描述` — 新功能
> - `bugfix/描述` — Bug 修复
> - `docs/描述` — 文档
> - `ci/描述` — CI/CD

---

### 1.3 编写代码

**目标**：实现功能，同时遵循代码规范。

**清单**：

- [ ] 使用 `snake_case`（Python）/ `camelCase`（JS）命名（命名规范 → § 基础概念/代码风格）
- [ ] 所有函数标注类型提示（类型提示 → § 基础概念/类型提示）
- [ ] 禁止硬编码 API Key，使用环境变量（环境变量 → § 基础概念/环境变量）
- [ ] 复杂逻辑添加 docstring（说明 *why*，不说明 *what*）
- [ ] 运行 `black . && ruff check .` 格式化后再提交

**Python 示例**（完整模块写法 → § 代码示例/Python）：

```python
from __future__ import annotations

from dataclasses import dataclass
import os

@dataclass(frozen=True)
class SeparationResult:
    vocals: str
    drums: str
    bass: str
    piano: str
    guitar: str
    other: str

def get_api_key() -> str:
    """从环境变量读取 API Key，避免硬编码。"""
    key = os.environ.get("AUDIO_API_KEY")
    if not key:
        raise RuntimeError("AUDIO_API_KEY 环境变量未设置")
    return key
```

---

### 1.4 编写测试

**目标**：为自己的代码编写测试用例，保证正确性。

**清单**（测试规范详解 → § 基础概念/测试基础）：

- [ ] 单元测试覆盖核心函数（`tests/unit/` 目录）
- [ ] 测试文件命名为 `test_<模块名>.py`
- [ ] 测试数据使用 `tests/fixtures/` 目录下的文件
- [ ] 测试函数命名：`test_<函数名>_<场景>`

**测试示例**：

```python
# tests/unit/test_audio_loader.py
import pytest
from src.audio.loader import load_audio, AudioLoaderError

def test_load_audio_raises_on_missing_file(tmp_path):
    """文件不存在时抛出 AudioLoaderError。"""
    missing = tmp_path / "not_exist.mp3"
    with pytest.raises(AudioLoaderError, match="文件不存在"):
        load_audio(missing)
```

**运行测试**：

```bash
pytest tests/ -v                  # 运行全部测试
pytest tests/unit/ -v             # 只运行单元测试
pytest tests/ --cov=src           # 运行 + 覆盖率报告
```

---

### 1.5 提交代码

**目标**：将代码提交到本地仓库，消息清晰可追溯。

**清单**（Conventional Commits 规范详解 → § 基础概念/Commit Message）：

- [ ] Subject 不超过 72 字符，以动词开头（Add/Fix/Update...）
- [ ] Type 正确（`feat`/`fix`/`docs`/`refactor`...）
- [ ] 关联功能编号（FR-xx）或 Bug 编号（BUG-xx）

**操作示例**：

```bash
# 查看修改了哪些文件
git status

# 添加到暂存区
git add src/audio/loader.py tests/unit/test_audio_loader.py

# 提交（消息格式：type(scope): subject）
git commit -m "feat(audio): 添加音频加载模块，支持 MP3/WAV/FLAC

- 使用 librosa 加载音频并转为单声道
- 异常处理保留原始堆栈

Closes #FR-01"
```

**Commit Message 示例**：

```
feat(audio): 添加基于 BS-RoFormer 的音轨分离功能

- 支持 MP3/WAV/FLAC/M4A 格式输入
- 分离为人声/鼓/贝斯/钢琴/吉他/其他六轨

Closes #FR-02
```

---

### 1.6 推送分支

**目标**：将本地分支推送到远程，创建 PR。

**操作示例**：

```bash
# 第一次推送需要设置上游分支
git push -u origin feature/FR-01-audio-input
```

> **如果遇到推送失败**：可能是远程有更新，先 `git pull --rebase`，解决冲突后重新推送。

---

## 二、Pull Request 流程（开发者）

### 2.1 创建 PR

**目标**：在 GitHub 上创建 Pull Request，请求审查。

**PR 内容清单**：

- [ ] **标题**：与 commit message 一致，格式 `feat(FR-xx): 功能名称`
- [ ] **描述（Description）**：包含以下内容（PR 模板可预先在 `.github/` 目录配置）

```markdown
## 变更内容
<!-- 简要说明这次改了什么 -->

## 关联功能
<!-- 关联的功能编号：Closes #FR-01 -->

## 测试计划
<!-- 描述你如何测试了这个功能 -->
- [ ] 测试了哪些场景
- [ ] 覆盖了哪些边界条件
- [ ] 附上测试截图/录屏（如有 UI 变更）

## 自查清单
- [ ] 代码符合格式化规范（已运行 black/ruff）
- [ ] 有新增功能则附带测试
- [ ] 无硬编码敏感信息
- [ ] commit message 符合规范
```

### 2.2 PR 描述示例

```
## 变更内容
实现了音频文件加载功能，支持 MP3、WAV、FLAC、M4A 四种格式。
使用 librosa 进行音频解码，统一转为单声道 44100Hz numpy 数组。

## 关联功能
Closes #FR-01

## 测试计划
- [x] 测试了四种格式的加载
- [x] 测试了文件不存在时的异常处理
- [x] 测试了不支持格式的异常处理
- [x] 使用 `tests/fixtures/sine_440hz.wav` 进行了集成测试

## 截图
<!-- 如有 UI 变更，附上截图 -->
```

---

## 三、测试与验证（测试者）

> 实践中通常由开发者自己执行初步测试，再由审查者进一步验证。

### 3.1 本地测试

**步骤**：

1. 拉取 PR 分支到本地
2. 运行完整测试套件
3. 人工功能验证（对照需求规格说明书）

**操作示例**：

```bash
# 方式一：通过 PR 编号拉取
git fetch origin
git checkout -b pr/PR-123 origin/pull/123

# 方式二：开发者已推送分支，直接切到该分支
git checkout feature/FR-01-audio-input

# 运行测试
pytest tests/ -v --cov=src

# 手动功能测试（对照 FR-01 需求）
# 音频输入 → 加载 MP3/WAV/FLAC/M4A → 确认采样率和时长正确
```

### 3.2 缺陷报告

如果在测试中发现问题，在 PR 下发布 Review 评论：

```markdown
## 缺陷报告

**位置**：`src/audio/loader.py:118`

**问题描述**：
当文件路径包含中文字符时，librosa.load 抛出 UnicodeDecodeError。

**复现步骤**：
1. 加载路径为 `C:\Users\用户\Music\歌曲.mp3` 的文件
2. 观察到异常

**期望行为**：
应正常加载或给出友好提示。

**建议修复**：
使用 `Path(path).resolve()` 处理路径编码问题。
```

---

## 四、审查流程（审查者）

### 4.1 审查清单

在 Review PR 时，检查以下方面（Code Review 详解 → § 基础概念/Code Review）：

**逻辑正确性**
- [ ] 代码逻辑是否符合需求规格（FR-xx）？
- [ ] 边界条件（空文件、超大文件、不支持格式）是否处理？
- [ ] 异常信息是否对用户友好？

**代码质量**
- [ ] 是否有硬编码的敏感信息？
- [ ] 是否遵循命名规范？
- [ ] 是否有无用的调试代码残留（`print`、`console.log`）？
- [ ] 是否有适当的类型提示？

**性能**
- [ ] 音频处理部分是否有明显性能问题？
- [ ] 是否有不必要的重复计算？

**测试**
- [ ] 新增逻辑是否配有测试？
- [ ] 测试是否覆盖了关键路径？

**安全性**
- [ ] 是否有 SQL 注入、路径遍历等安全隐患？
- [ ] 外部输入是否经过验证？

### 4.2 审查评论示例

```markdown
## 代码审查

**整体**：代码结构清晰，类型提示完整，异常处理得当。

**建议（可选，非阻塞）**：
1. `src/audio/loader.py:104` — 这里可以用 `pathlib` 的 `suffix` 属性替代 `split(".")[-1]`，更简洁：
   ```python
   suffix = path.suffix.lower().lstrip(".")  # 已有 .suffix，无须 split
   ```

**批准条件**：
- [x] 逻辑正确
- [x] 测试覆盖
- [x] 无安全隐患

**结论**：✅ Approve，可以合并。
```

### 4.3 审查结论

| 结论 | 含义 |
|------|------|
| **Approve** | 审查通过，可以合并 |
| **Request Changes** | 需要修改后再审（阻塞合并） |
| **Comment** | 仅评论，不阻塞合并 |

---

## 五、合入与收尾（开发者）

### 5.1 合并 PR

合并由审查者或项目维护者执行（通常在 GitHub 界面点击 **Squash and merge**）：

```bash
# 开发者通常不需要手动操作，以下仅作了解
git checkout main
git pull origin main
git branch -d feature/FR-01-audio-input  # 删除本地分支
```

### 5.2 分支清理

PR 合并后，GitHub 会自动关闭 PR 并可选地删除远程分支。建议同时清理本地分支：

```bash
# 切回 main 并拉取最新
git checkout main
git pull origin main

# 删除已合并的本地分支
git branch -d feature/FR-01-audio-input
```

---

## 六、附录：基础概念参考

以下章节为上述流程中所有名词、工具和操作的详细说明，可按需查阅。

---

### § 基础概念/虚拟环境

**什么是虚拟环境？**

每个项目需要的依赖版本可能不同（如项目 A 需要 Django 3.0，项目 B 需要 4.0）。虚拟环境为每个项目创建独立的"环境盒子"，避免版本冲突。

```bash
# 创建
python -m venv venv

# 激活
.\venv\Scripts\activate      # Windows
source venv/bin/activate      # Linux/macOS

# 退出
deactivate
```

**requirements.txt 管理**：

```bash
# 安装依赖
pip install -r requirements.txt

# 导出当前依赖
pip freeze > requirements.txt
```

---

### § 基础概念/Git 基础

**核心概念**：

```
工作区 (Working Directory)     你正在编辑的文件
        ↓ git add
暂存区 (Staging Area)         准备提交的文件快照
        ↓ git commit
本地仓库 (Local Repository)    .git 文件夹，存放所有历史版本
        ↓ git push
远程仓库 (Remote)              GitHub 上的仓库
```

**常用命令速查**：

| 命令 | 作用 |
|------|------|
| `git clone <url>` | 克隆仓库到本地 |
| `git checkout -b <分支名>` | 创建并切换新分支 |
| `git add <文件>` | 添加文件到暂存区 |
| `git commit -m "<消息>"` | 提交 |
| `git push -u origin <分支>` | 推送到远程 |
| `git pull origin main` | 拉取 main 最新代码 |
| `git merge <分支>` | 合并指定分支到当前分支 |
| `git status` | 查看当前状态 |
| `git log --oneline` | 查看提交历史 |

---

### § 基础概念/Git 分支

**命名规则**：

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feature/` | 新功能 | `feature/FR-01-audio-input` |
| `bugfix/` | Bug 修复 | `bugfix/playback-crash` |
| `docs/` | 文档 | `docs/readme` |
| `refactor/` | 重构 | `refactor/audio-pipeline` |
| `ci/` | CI/CD | `ci/github-actions` |

---

### § 基础概念/代码风格

**通用规范**：

- **缩进**：4 空格（Python）或 2 空格（前端/配置文件），禁止混用 Tab
- **行宽**：不超过 120 字符
- **文件编码**：`UTF-8`
- **换行符**：Unix 风格（`LF`）

**命名规范**：

| 语言 | 变量/函数 | 类名 | 常量 |
|------|-----------|------|------|
| Python | `snake_case` | `PascalCase` | `UPPER_SNAKE_CASE` |
| JS/TS | `camelCase` | `PascalCase` | `UPPER_SNAKE_CASE` |

**Python 格式化工具**：

```bash
pip install black isort ruff

black .           # 自动格式化
isort .           # 排序 import
ruff check .      # 检查问题
ruff check . --fix  # 自动修复
```

---

### § 基础概念/类型提示

**为什么需要类型提示？**

不加类型时，IDE 无法提前发现错误；加上后，写错类型会立即标红。

```python
# ❌ 无类型提示：运行前不知道错误
def add(a, b):
    return a + b
add(1, "2")  # 运行时报错：TypeError

# ✅ 有类型提示：写代码时 IDE 就标红
def add(a: int, b: int) -> int:
    return a + b
```

**常用类型语法**：

```python
from __future__ import annotations
from dataclasses import dataclass

# 基础类型
name: str
count: int
score: float
active: bool

# 容器
scores: list[int]
config: dict[str, int]

# 可选（有默认值或可能为 None）
def greet(name: str | None = None) -> str:
    return f"Hello, {name or 'Guest'}"

# 不可变数据类（创建后不可修改）
@dataclass(frozen=True)
class AudioData:
    samples: np.ndarray
    sample_rate: int
    duration: float
```

---

### § 基础概念/环境变量

**什么是环境变量？**

操作系统级别的全局变量，程序运行时从中读取配置，而不是写在代码里。

**为什么不能写死在代码里？**

```python
# ❌ 错误：API Key 会随代码上传到 GitHub
API_KEY = "sk-abc123xyz"

# ✅ 正确：从环境变量读取
import os
API_KEY = os.environ.get("AUDIO_API_KEY")
```

**`.env` 文件用法**：

```bash
# .env 文件（不要提交到 Git！）
AUDIO_API_KEY=sk-abc123xyz
```

```bash
pip install python-dotenv
```

```python
from dotenv import load_dotenv
load_dotenv()  # 读取 .env 文件

import os
API_KEY = os.getenv("AUDIO_API_KEY")
```

**`.env.example`**（必须提交到 Git，供队友参考格式）：

```
AUDIO_API_KEY=
DATABASE_URL=
```

---

### § 基础概念/测试基础

**什么是测试？**

一段验证代码是否正确工作的代码。

```python
# 待测函数
def add(a: int, b: int) -> int:
    return a + b

# 测试
def test_add_two_integers():
    result = add(1, 2)
    assert result == 3  # 不等于 3 则测试失败
```

**pytest 语法**：

```python
import pytest

# 断言相等
assert result == expected

# 断言抛出异常
def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(1, 0)

# 使用 fixture（tmp_path 自动创建临时目录）
def test_write_file(tmp_path):
    file = tmp_path / "test.txt"
    file.write_text("hello")
    assert file.read_text() == "hello"
```

**运行测试**：

```bash
pytest tests/ -v                  # 运行全部测试
pytest tests/unit/ -v             # 只运行单元测试
pytest tests/ --cov=src           # 运行 + 覆盖率
```

---

### § 基础概念/Commit Message

**格式**：

```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

**Type 分类**：

| type | 含义 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复 bug |
| `docs` | 文档变更 |
| `style` | 格式调整（不影响逻辑） |
| `refactor` | 重构（不修 bug、不加功能） |
| `perf` | 性能优化 |
| `test` | 添加/修改测试 |
| `chore` | 构建脚本、依赖更新、工具变动 |
| `ci` | CI/CD 配置变更 |

**好/坏示例**：

```
✅ feat(audio): 添加音频加载模块，支持 MP3/WAV/FLAC
✅ fix(chord): 修复弱拍位置的和弦误判
❌ fix bug
❌ update code
❌ WIP
```

---

### § 基础概念/pre-commit

**作用**：在 `git commit` 执行前自动运行代码检查，通过才允许提交。

```bash
pip install pre-commit
pre-commit install
```

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml

  - repo: https://github.com/psf/black
    rev: 24.1.0
    hooks:
      - id: black
```

---

### § 基础概念/Code Review

**审查者关注点**：

1. **逻辑正确性**：边界条件、异常处理
2. **可读性**：其他人能看懂吗
3. **性能**：音频处理部分有无明显性能问题
4. **安全性**：SQL 注入、XSS、路径遍历等
5. **测试覆盖**：新增逻辑有对应测试吗

**有效的 Review 评论**：

```markdown
<!-- ✅ 有效：指出问题 + 建议解决方案 -->
这里的异常捕获太宽泛，建议只捕获 SpecificError，方便调试。

<!-- ❌ 无效 -->
这里写得不好。
为什么不那样写？
```

---

### § 基础概念/CI/CD

**什么是 CI/CD？**

- **CI（持续集成）**：每次 push 或提 PR，自动运行测试/lint/构建
- **CD（持续交付）**：CI 通过后，自动部署

**GitHub Actions 是什么？**

GitHub 自带的免费 CI/CD 工具，云端虚拟机自动执行任务。

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install ruff black isort
      - run: ruff check .
      - run: black --check .

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements-dev.txt
      - run: pytest tests/ --cov=src
```

> 将上述文件推送到 GitHub 后，在仓库 **Actions** 标签页即可查看每次运行状态。

---

### § 基础概念/Git 常用操作图解

**场景 1：撤销操作**

```bash
# 撤销暂存（刚 add，还没 commit）
git reset HEAD src/audio/loader.py

# 撤销工作区修改（文件改错了）
git checkout -- src/audio/loader.py

# 撤销最后一次 commit（还没 push）
git reset --soft HEAD~1  # 保留修改在暂存区
git reset --hard HEAD~1  # 彻底撤销（危险！）

# 回退到某个历史 commit（撤销已 push 的 commit）
git revert <commit-hash>  # 安全：创建新 commit 撤销
```

**场景 2：解决冲突**

```bash
git checkout feature/FR-01-audio-input
git merge main
# Git 报告冲突，打开文件找到 <<<<<<< ======= >>>>>>> 手动解决
git add .
git commit -m "merge: 解决与 main 的冲突"
git push
```

---

## 七、工具速查

| 工具 | 安装 | 常用命令 |
|------|------|---------|
| `black` | `pip install black` | `black .` |
| `isort` | `pip install isort` | `isort .` |
| `ruff` | `pip install ruff` | `ruff check . --fix` |
| `pytest` | `pip install pytest` | `pytest tests/ -v` |
| `mypy` | `pip install mypy` | `mypy src/` |
| `pre-commit` | `pip install pre-commit` | `pre-commit install` |
| `python-dotenv` | `pip install python-dotenv` | `load_dotenv()` |
| `gh` (GitHub CLI) | 见 GitHub 文档 | `gh pr create` |

---

## 八、配置文件参考

所有配置文件统一放在项目根目录：

```
TABsucks/
├── .github/workflows/ci.yml     # GitHub Actions CI 配置
├── .pre-commit-config.yaml      # pre-commit 钩子配置
├── .commitlintrc.js             # commit message 校验规则
├── .editorconfig                # 编辑器统一配置
├── pyproject.toml               # Python 项目配置（black/isort/ruff）
├── requirements.txt             # 生产依赖
├── requirements-dev.txt        # 开发依赖
└── .env.example                 # 环境变量模板
```

> 这些文件的具体内容见下方「代码示例」章节。

---

## 九、代码示例参考

### § 代码示例/Python

**模块结构** `src/audio/loader.py`

```python
"""音频加载模块，支持多格式输入。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import librosa
import numpy as np


class AudioFormat(Enum):
    """支持的音频格式。"""

    MP3 = "mp3"
    WAV = "wav"
    FLAC = "flac"
    M4A = "m4a"


@dataclass(frozen=True)
class AudioData:
    """不可变的音频数据结构。"""

    samples: np.ndarray
    sample_rate: int
    duration: float

    @property
    def channels(self) -> int:
        return self.samples.ndim


class AudioLoaderError(Exception):
    """音频加载失败。"""
    pass


class IAudioLoader(Protocol):
    """音频加载器接口。"""

    def load(self, path: str | Path) -> AudioData: ...


def load_audio(path: str | Path, sr: int = 44100) -> AudioData:
    """加载音频文件并转为单声道 numpy 数组。

    Args:
        path: 音频文件路径
        sr: 目标采样率，默认 44100Hz

    Returns:
        AudioData 对象

    Raises:
        AudioLoaderError: 文件不存在或格式不支持
    """
    path = Path(path)

    if not path.exists():
        raise AudioLoaderError(f"文件不存在: {path}")

    suffix = path.suffix.lower().lstrip(".")
    if suffix not in [f.value for f in AudioFormat]:
        raise AudioLoaderError(f"不支持的格式: {suffix}")

    try:
        y, sr = librosa.load(path, sr=sr, mono=True)
        duration = len(y) / sr
        return AudioData(samples=y, sample_rate=sr, duration=duration)
    except Exception as e:
        raise AudioLoaderError(f"加载失败: {e}") from e


def get_api_key() -> str:
    """从环境变量读取 API Key，避免硬编码。"""
    key = os.environ.get("AUDIO_API_KEY")
    if not key:
        raise RuntimeError("AUDIO_API_KEY 环境变量未设置")
    return key
```

**测试** `tests/unit/test_audio_loader.py`

```python
"""音频加载模块测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.audio.loader import AudioLoaderError, load_audio


class TestAudioLoader:
    """音频加载器测试。"""

    def test_load_audio_raises_on_missing_file(self, tmp_path: Path) -> None:
        """文件不存在时抛出 AudioLoaderError。"""
        missing = tmp_path / "not_exist.mp3"
        with pytest.raises(AudioLoaderError, match="文件不存在"):
            load_audio(missing)

    def test_load_audio_raises_on_unsupported_format(self, tmp_path: Path) -> None:
        """不支持的格式时抛出 AudioLoaderError。"""
        dummy = tmp_path / "video.avi"
        dummy.touch()
        with pytest.raises(AudioLoaderError, match="不支持的格式"):
            load_audio(dummy)
```

**异常处理原则**

```python
# ✅ 正确：保留原始异常链
try:
    result = some_operation()
except SpecificError as e:
    raise AudioLoaderError("操作失败") from e

# ❌ 错误：吞掉原始异常
try:
    result = some_operation()
except SpecificError:
    raise AudioLoaderError("操作失败")
```

---

### § 代码示例/前端（React + TypeScript）

**目录结构约定**

```
src/
├── components/          # 展示组件
│   ├── AudioPlayer/
│   │   ├── AudioPlayer.tsx
│   │   ├── AudioPlayer.css
│   │   └── index.ts
├── hooks/              # 自定义 Hooks
├── pages/              # 页面组件
├── services/           # API 调用层
├── types/              # 共享类型定义
└── utils/              # 工具函数
```

**组件示例** `src/components/AudioPlayer/AudioPlayer.tsx`

```tsx
import { useRef, useState, useCallback, useEffect } from "react";
import "./AudioPlayer.css";

interface AudioPlayerProps {
  src: string;
  onEnded?: () => void;
}

export function AudioPlayer({ src, onEnded }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  const togglePlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      audio.play();
    }
    setIsPlaying((prev) => !prev);
  }, [isPlaying]);

  const handleTimeUpdate = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      setCurrentTime(audio.currentTime);
    }
  }, []);

  const handleLoadedMetadata = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      setDuration(audio.duration);
    }
  }, []);

  const seek = useCallback((time: number) => {
    const audio = audioRef.current;
    if (audio) {
      audio.currentTime = time;
      setCurrentTime(time);
    }
  }, []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    audio.addEventListener("ended", () => {
      setIsPlaying(false);
      onEnded?.();
    });

    return () => {
      audio.removeEventListener("ended", () => {});
    };
  }, [onEnded]);

  return (
    <div className="audio-player">
      <audio
        ref={audioRef}
        src={src}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
      />
      <button onClick={togglePlay} aria-label={isPlaying ? "暂停" : "播放"}>
        {isPlaying ? "暂停" : "播放"}
      </button>
      <span>
        {formatTime(currentTime)} / {formatTime(duration)}
      </span>
      <input
        type="range"
        min={0}
        max={duration}
        value={currentTime}
        onChange={(e) => seek(Number(e.target.value))}
      />
    </div>
  );
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}
```

**自定义 Hook 示例** `src/hooks/useAudioPlayer.ts`

```typescript
import { useState, useCallback, useRef, useEffect } from "react";

interface UseAudioPlayerOptions {
  src: string;
  autoPlay?: boolean;
}

interface AudioPlayerState {
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  playbackRate: number;
}

export function useAudioPlayer({
  src,
  autoPlay = false,
}: UseAudioPlayerOptions) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<AudioPlayerState>({
    isPlaying: false,
    currentTime: 0,
    duration: 0,
    playbackRate: 1,
  });

  useEffect(() => {
    const audio = new Audio(src);
    audioRef.current = audio;
    if (autoPlay) {
      audio.play().catch(() => {});
    }
    return () => {
      audio.pause();
      audio.src = "";
    };
  }, [src, autoPlay]);

  const play = useCallback(() => {
    audioRef.current?.play();
    setState((prev) => ({ ...prev, isPlaying: true }));
  }, []);

  const pause = useCallback(() => {
    audioRef.current?.pause();
    setState((prev) => ({ ...prev, isPlaying: false }));
  }, []);

  const seek = useCallback((time: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = time;
      setState((prev) => ({ ...prev, currentTime: time }));
    }
  }, []);

  const setPlaybackRate = useCallback((rate: number) => {
    if (audioRef.current) {
      audioRef.current.playbackRate = rate;
      setState((prev) => ({ ...prev, playbackRate: rate }));
    }
  }, []);

  return {
    ...state,
    play,
    pause,
    seek,
    setPlaybackRate,
    togglePlay: () => (state.isPlaying ? pause() : play()),
  };
}
```

**API 调用层示例** `src/services/api.ts`

```typescript
const API_BASE =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

interface SeparationResult {
  vocals: string;
  drums: string;
  bass: string;
  piano: string;
  guitar: string;
  other: string;
}

interface AnalyzeChordsRequest {
  audio_url: string;
  model?: string;
}

interface ChordResult {
  chords: Array<{
    root: string;
    quality: string;
    start: number;
    end: number;
  }>;
}

class ApiError extends Error {
  constructor(
    message: string,
    public statusCode: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(`请求失败: ${body}`, res.status);
  }
  return res.json() as Promise<T>;
}

export const audioApi = {
  async separate(audioUrl: string): Promise<SeparationResult> {
    const res = await fetch(`${API_BASE}/api/v1/separate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio_url: audioUrl }),
    });
    return handleResponse<SeparationResult>(res);
  },

  async analyzeChords(req: AnalyzeChordsRequest): Promise<ChordResult> {
    const res = await fetch(`${API_BASE}/api/v1/analyze/chords`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
    return handleResponse<ChordResult>(res);
  },
};
```

**类型定义示例** `src/types/audio.ts`

```typescript
export type TrackId =
  | "vocals"
  | "drums"
  | "bass"
  | "piano"
  | "guitar"
  | "other";

export interface Track {
  id: TrackId;
  label: string;
  url: string | null;
  muted: boolean;
  solo: boolean;
}

export interface Workspace {
  id: string;
  name: string;
  audioSrc: string | null;
  tracks: Track[];
  createdAt: number;
}

export const TRACK_LABELS: Record<TrackId, string> = {
  vocals: "人声",
  drums: "鼓",
  bass: "贝斯",
  piano: "钢琴",
  guitar: "吉他",
  other: "其他",
};

export const DEFAULT_PLAYBACK_RATES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0] as const;
```

**禁止的写法**

```typescript
// ❌ 硬编码 API 地址
const API_BASE = "http://127.0.0.1:8000";
// ✅ 环境变量
const API_BASE = import.meta.env.VITE_API_BASE_URL;

// ❌ any 类型滥用
function processData(data: any) {
  return data.foo.bar;
}
// ✅ 具体类型
function processData(data: KnownShape) {
  return data.foo.bar;
}

// ❌ 魔法数字
if (position > 44100) { ... }
// ✅ 具名常量
const SAMPLE_RATE = 44100;
if (position > SAMPLE_RATE) { ... }
```

---

### § 代码示例/配置文件

**`pyproject.toml`**（Python 项目配置）

```toml
[tool.black]
line-length = 100
target-version = ["py311"]

[tool.isort]
profile = "black"
line_length = 100

[tool.ruff]
line-length = 100
select = ["E", "F", "W", "I", "N", "UP", "B", "C4"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_ignores = true
```

**`.editorconfig`**

```ini
root = true

[*]
indent_style = space
indent_size = 4
end_of_line = lf
charset = utf-8
trim_trailing_whitespace = true
insert_final_newline = true

[*.{yml,yaml,json}]
indent_size = 2

[*.md]
trim_trailing_whitespace = false
```

**`.commitlintrc.js`**

```javascript
module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "type-enum": [
      2,
      "always",
      [
        "feat",
        "fix",
        "docs",
        "style",
        "refactor",
        "perf",
        "test",
        "chore",
        "ci",
      ],
    ],
  },
};
```

**`.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
        args: ["--maxkb=1024"]

  - repo: https://github.com/psf/black
    rev: 24.1.0
    hooks:
      - id: black

  - repo: https://github.com/pycqa/isort
    rev: 5.13.0
    hooks:
      - id: isort

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.2.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

**`.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install ruff black isort
      - run: ruff check .
      - run: black --check .
      - run: isort --check .

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements-dev.txt
      - run: pytest tests/ --cov=src

  build:
    needs: [lint, test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install build
      - run: python -m build
```

---

## 十、参考链接

| 资源 | 链接 |
|------|------|
| PEP 8 风格指南 | https://pep8.org/ |
| Python 类型提示文档 | https://docs.python.org/3/library/typing.html |
| Conventional Commits | https://www.conventionalcommits.org/ |
| GitHub Actions 文档 | https://docs.github.com/en/actions |
| pytest 文档 | https://docs.pytest.org/ |
| Black 格式化工具 | https://black.readthedocs.io/ |
| Ruff linter | https://docs.astral.sh/ruff/ |
| pre-commit 文档 | https://pre-commit.com/ |
