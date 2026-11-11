"""``.rpyc`` inspection helpers.

A ``.rpyc`` file is a container of independently zlib-compressed slots::

    b"RENPY RPC2"                     (10 bytes)
    12 bytes * N: struct("<III", slot, start, length)   terminated by slot == 0
    <slot data>                       each zlib-compressed, or stored raw when
                                      it does not decompress
    ... 16 trailing bytes = md5 of the matching .rpy source

Because the payload holds a pickled AST, we deliberately do **not** decode it
into Ren'Py node objects (that would drag in Cython modules). Instead we
decompress the slots and pull out the string literals, which is enough to
identify a build, verify freshness, and grep for content.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import zlib
from dataclasses import dataclass, field

from .errors import ToolkitError
from . import safeio

RPC2_HEADER = b"RENPY RPC2"
SLOT_ENTRY = 12
SLOT_END = 0


class RpycError(ToolkitError):
    """Raised for files that are not readable .rpyc containers."""


@dataclass
class RpycFile:
    path: str
    slots: list[bytes] = field(default_factory=list)
    raw: bytes = b""
    version: int | None = None
    source_md5: bytes | None = None

    @property
    def joined(self) -> bytes:
        return b"\n".join(self.slots)

    def strings(self, min_len: int = 4) -> list[str]:
        """Extract printable ASCII/UTF-8 runs, like ``strings(1)``."""
        text = self.joined.decode("utf-8", "replace")
        return [s for s in re.findall(r"[\x20-\x7e\u4e00-\u9fff]{%d,}" % min_len, text)]

    def contains(self, needle: str) -> bool:
        return needle in self.joined.decode("utf-8", "replace")


def load_slots(path: str) -> RpycFile:
    """Read a .rpyc and decompress every slot.

    Slots that fail to decompress are kept verbatim so partial information is
    never silently dropped.
    """
    with open(path, "rb") as handle:
        data = handle.read()
    if not data.startswith(RPC2_HEADER):
        raise RpycError(f"{path}: missing {RPC2_HEADER!r} header")
    result = RpycFile(path=path, raw=data)

    pos = len(RPC2_HEADER)
    entries: list[tuple[int, int, int]] = []
    while True:
        if pos + SLOT_ENTRY > len(data):
            raise RpycError(f"{path}: truncated slot table at {pos}")
        slot, start, length = struct.unpack_from("<III", data, pos)
        pos += SLOT_ENTRY
        if slot == SLOT_END:
            break
        entries.append((slot, start, length))

    for slot, start, length in entries:
        chunk = data[start : start + length]
        if len(chunk) != length:
            raise RpycError(
                f"{path}: slot {slot} wants {length} bytes, only {len(chunk)} present"
            )
        try:
            result.slots.append(
                safeio.decompress_limited(chunk, what=f"{path}: slot {slot}")
            )
        except safeio.DecompressError as exc:
            if "corrupt zlib stream" not in str(exc):
                # A size/truncation problem is real damage, not "this slot is
                # stored uncompressed"; surface it instead of guessing.
                raise RpycError(str(exc)) from exc
            result.slots.append(chunk)

    result.source_md5 = data[-16:] if len(data) >= 16 else None
    return result


def source_md5(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read()).hexdigest()


def is_fresh(rpyc_path: str, rpy_path: str | None = None) -> bool:
    """True when the .rpyc was compiled from the current .rpy.

    Ren'Py compares the md5 embedded in the last 16 bytes against the source
    file; mtime is irrelevant.
    """
    if rpy_path is None:
        rpy_path = rpyc_path[:-1] if rpyc_path.endswith("c") else rpyc_path + ".rpy"
    if not os.path.exists(rpy_path):
        return False
    try:
        info = load_slots(rpyc_path)
    except (RpycError, OSError):
        return False
    if info.source_md5 is None:
        return False
    return info.source_md5.hex() == source_md5(rpy_path)


#: ``renpy.script_version`` values seen in Ren'Py 6.x–8.x (encoded as
#: ``major * 100000 + minor * 1000 + patch``). Only used to filter candidates
#: while scanning a slot for the marshalled int — the value is a hint, not a
#: certificate.
_KNOWN_SCRIPT_VERSIONS = frozenset(
    {
        600_000, 700_000, 710_000, 720_000, 730_000, 740_000, 750_000,
        760_000, 770_000, 800_000, 800_100, 800_200, 800_300, 810_000,
        810_100, 820_000, 820_100, 830_000, 831_000, 840_000, 850_000,
        870_000, 880_000, 899_999,
    }
)


def script_version(path: str) -> int | None:
    """Best-effort read of ``renpy.script_version`` embedded in the pickle.

    The value is marshalled as a 4-byte little-endian int inside one of the
    slots, so we scan for candidate patterns rather than executing the pickle
    (executing a .rpyc found in a game we are merely translating is not
    something this module will do).
    """
    try:
        info = load_slots(path)
    except (RpycError, OSError):
        return None
    blob = info.joined
    for ver in _KNOWN_SCRIPT_VERSIONS:
        if struct.pack("<I", ver) in blob:
            return ver
    return None


__all__ = [
    "RPC2_HEADER",
    "RpycError",
    "RpycFile",
    "is_fresh",
    "load_slots",
    "script_version",
    "source_md5",
]
