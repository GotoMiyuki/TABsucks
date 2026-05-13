# ismir2019_plugin.py (重构版)
import os
import sys
import subprocess
import tempfile
import soundfile as sf
from typing import Dict, Any

from src.plugins.base import BasePlugin
from src.core.resource_controller import ResourceController

# 指向外部仓库根目录
EXTERNAL_DIR = os.path.join(os.path.dirname(__file__), "external", "ismir2019")
CHORD_PY = os.path.join(EXTERNAL_DIR, "chord_recognition.py")

class ISMIR2019ChordPlugin(BasePlugin):
    """
    通过 subprocess 调用官方 chord_recognition.py 的和弦识别插件。
    """
    @property
    def name(self) -> str:
        return "chord_ismir2019"

    @property
    def version(self) -> str:
        return "1.0.0-legacy"

    def load_model(self, rc: ResourceController):
        # 由于通过子进程调用，无需在Python中加载模型，这里仅作校验
        if not os.path.exists(CHORD_PY):
            raise FileNotFoundError(f"Critical: {CHORD_PY} not found.")
        print(f"[{self.name}] Ready to call external chord recognition script.")

    def execute(self, rc: ResourceController, **kwargs) -> Dict[str, Any]:
        stem_name = kwargs.get("stem_name", "piano")
        audio = rc.get_buffer(stem_name)
        sr = rc.get_metadata("sample_rate") or 22050

        # 1. 将音频数据写入一个临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
            sf.write(tmp_wav.name, audio, sr)
            wav_path = tmp_wav.name

        # 2. 准备输出标签文件的临时路径
        with tempfile.NamedTemporaryFile(suffix=".lab", delete=False) as tmp_lab:
            lab_path = tmp_lab.name

        # 3. 构建并执行命令 (使用默认 'submission' chord_dict)
        cmd = ["python", CHORD_PY, wav_path, lab_path]
        print(f"[{self.name}] Running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, cwd=EXTERNAL_DIR)

        # 4. 读取并解析生成的标签文件
        chords = self._parse_lab(lab_path)

        # 5. 清理临时文件
        os.unlink(wav_path)
        os.unlink(lab_path)

        # 6. 回写结果
        rc.set_metadata(f"chord_raw_{stem_name}", chords)
        return {"status": "success", "stem": stem_name, "data": chords}

    def _parse_lab(self, lab_path: str) -> list:
        """
        解析 .lab 文件，格式通常为：起始时间 结束时间 和弦标签
        返回一个包含时间戳和和弦的列表。
        """
        results = []
        with open(lab_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    start, end, label = float(parts[0]), float(parts[1]), parts[2]
                    results.append({"start": start, "end": end, "chord": label})
        return results