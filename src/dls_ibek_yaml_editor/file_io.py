"""Replace text files only after their complete contents have been written."""

import os
import stat
import tempfile
from pathlib import Path


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 atomically, following symlinks and preserving existing mode.

    A new file gets the usual permissions for the umask (0664 with umask 002),
    so colleagues on a shared area can read it, as with any editor. Other hard
    links continue to refer to the old file, as with atomic editors.

    Replacing a file renames a new one over it, which would make the current
    user its owner. A file owned by someone else, or in a directory we can't
    create files in (both happen on group-shared areas), is instead rewritten
    in place, as a plain editor would.
    """
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.stat() if target.exists() else None
    if existing is not None and existing.st_uid != os.geteuid():
        target.write_text(text, encoding="utf-8")
        return
    try:
        fd, name = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    except PermissionError:
        if existing is None:
            raise
        target.write_text(text, encoding="utf-8")
        return
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            if existing is not None:
                try:
                    os.fchown(stream.fileno(), existing.st_uid, existing.st_gid)
                except PermissionError:
                    pass  # not a member of the file's group: keep ours
                mode = stat.S_IMODE(existing.st_mode)
            else:
                mode = 0o666 & ~_umask()  # mkstemp made it 0600
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
