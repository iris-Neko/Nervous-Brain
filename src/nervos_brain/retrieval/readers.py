from pathlib import Path


def read_text_file(path: str) -> str:
    """读取 UTF-8 文本文件，返回字符串内容。"""
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    if not file_path.is_file():
        raise ValueError(f"不是文件: {path}")
    return file_path.read_text(encoding="utf-8")
