"""Replace text files only after their complete contents have been written."""

import os
import stat
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 atomically, following symlinks and preserving existing mode.

    A new file is private (0600). Existing ownership must match the current
    user: refuse rather than silently changing ownership during replacement.
    Other hard links continue to refer to the old file, as with atomic editors.
    """
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.stat() if target.exists() else None
    if existing is not None and existing.st_uid != os.geteuid():
        raise PermissionError(
            f"cannot atomically replace a file owned by another user: {path}"
        )
    fd, name = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            if existing is not None:
                os.fchown(stream.fileno(), existing.st_uid, existing.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(existing.st_mode))
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
