"""``python -m src.ui`` 入口。

把 CLI 的 main() 调用转发给 :py:mod:`src.ui.cli`。
"""

from src.ui.cli import main

if __name__ == "__main__":
    main()
