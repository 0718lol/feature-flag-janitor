from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath

from janitor.validation import MAX_CODE_FILES, MAX_FILE_BYTES, MAX_TOTAL_CODE_BYTES, InputError

TEXT_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".java", ".json", ".yaml", ".yml", ".toml", ".md", ".mdx", ".txt", ".rst", ".css", ".html"}


def read_zip_files(data: bytes) -> list[dict[str, str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise InputError("上传的文件不是有效的 ZIP") from None
    files: list[dict[str, str]] = []
    total_bytes = 0
    with archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if member.is_dir() or path.is_absolute() or ".." in path.parts or path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            if member.file_size > MAX_FILE_BYTES:
                raise InputError(f"ZIP 内文件 {member.filename} 超过 1 MB 限制")
            total_bytes += member.file_size
            if total_bytes > MAX_TOTAL_CODE_BYTES:
                raise InputError("ZIP 内代码文件总大小不能超过 6 MB")
            if len(files) >= MAX_CODE_FILES:
                raise InputError(f"ZIP 内代码文件数量不能超过 {MAX_CODE_FILES}")
            try:
                content = archive.read(member).decode("utf-8")
            except UnicodeDecodeError:
                continue
            files.append({"path": str(path), "content": content})
    return files
