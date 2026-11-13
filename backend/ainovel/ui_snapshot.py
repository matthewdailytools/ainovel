"""Keep the running daily UI independent of subsequent development builds."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory


def freeze_ui(dist: Path, data_dir: Path) -> Path:
    root = dist.resolve()
    files = {}
    digest = hashlib.sha256()
    for path in sorted(dist.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("前端构建包含目录外引用，未建立运行快照。")
        if path.is_file():
            relative = path.relative_to(dist)
            data = path.read_bytes()
            files[relative] = data
            digest.update(relative.as_posix().encode() + b"\0" + hashlib.sha256(data).digest())
    if Path("index.html") not in files or not any(p.parts[0] == "assets" for p in files):
        raise ValueError("前端构建不完整，请先完成构建。")
    folder = data_dir / "runtime" / "ui"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / digest.hexdigest()
    if not target.exists():
        with TemporaryDirectory(prefix=".build-", dir=folder) as location:
            stage = Path(location)
            for relative, data in files.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            try:
                stage.rename(target)
            except FileExistsError:
                pass
    if any(not (target / p).is_file() or (target / p).read_bytes() != data for p, data in files.items()):
        raise ValueError("运行前端快照校验失败，未使用不完整文件。")
    return target
