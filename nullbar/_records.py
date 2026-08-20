"""Reading a record file the library was pointed at, safely.

Every path this package opens is, in the case that matters, supplied by
somebody else: you clone a repository and run ``nullbar report`` or
``nullbar verify`` against the records it ships. A registration, a stamp, a
ledger and an anchor sidecar are all just paths in that tree, and a path can
be a symlink to something that is not a record.

``Path.exists()`` is true of a character device, and reading ``/dev/zero``
returns an endless stream: it allocates until the machine dies. That is not
a thought experiment. It happened here twice in one day — first through an
anchor ENTRY path, which was fixed, and then straight through the anchor
sidecar itself, which the fix had not covered because it guarded the paths
the code derived and not the path it was handed.

So this is one function rather than a check at each call site, because the
first version was a check at each call site and it missed the front door.

Two rules, both cheap:

  * the path must resolve to an ORDINARY FILE — not a device, a FIFO, a
    socket or a directory. This is what stops the unbounded read;
  * it must not be larger than ``MAX_RECORD_BYTES``. A record is a small
    JSON document or an append-only JSONL; a gigabyte of it is a mistake or
    an attack, and either way refusing beats swallowing it.

``RecordReadError`` subclasses ``OSError`` deliberately: callers already
catch ``OSError`` around record reads, so a refusal degrades to the handling
they have instead of becoming a traceback out of a new exception type.
"""
from __future__ import annotations

from pathlib import Path

#: Generous by design — a 20k-cell ledger is a few megabytes. The cap is a
#: backstop against a planted file, not a budget anyone should ever meet.
MAX_RECORD_BYTES = 256 * 1024 * 1024


class RecordReadError(OSError):
    """A path nullbar was asked to read is not a readable record file."""


def check_record(path: Path, what: str = "record") -> Path:
    """Raise unless ``path`` is an ordinary file of a sane size.

    Returns the path, so it can be used inline. Symlinks are FOLLOWED —
    a symlink to a real file is a perfectly ordinary way to lay out a
    repository; what is refused is what it points AT.
    """
    try:
        if not path.is_file():
            raise RecordReadError(
                f"the {what} at {path} is not an ordinary file — reading "
                "whatever a path happens to point at (a device, a FIFO) is "
                "how a record takes the machine with it")
        size = path.stat().st_size
    except OSError as exc:
        if isinstance(exc, RecordReadError):
            raise
        raise RecordReadError(f"cannot inspect the {what} at {path}: {exc}") \
            from exc
    if size > MAX_RECORD_BYTES:
        raise RecordReadError(
            f"the {what} at {path} is {size} bytes, over the "
            f"{MAX_RECORD_BYTES}-byte limit — a record is a small document, "
            "and this one is not being read whole")
    return path


def record_bytes(path: Path, what: str = "record") -> bytes:
    """The file's bytes, once it has been established to be a file."""
    return check_record(path, what).read_bytes()


def record_text(path: Path, what: str = "record") -> str:
    """The file's text, once it has been established to be a file."""
    return check_record(path, what).read_text()
