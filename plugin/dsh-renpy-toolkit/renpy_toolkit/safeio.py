"""A bounded ``zlib.decompress``.

All three archive formats this toolkit reads (``.rpa``, ``.rpyc``) store a
zlib blob whose decompressed size is attacker-controlled: a few hundred bytes
of highly repetitive data expands to gigabytes.  ``zlib.decompress`` has no
output limit, so a malicious file can exhaust memory before any validation
runs.

``decompress_limited`` inflates incrementally and gives up once the output
passes *limit*, which turns a memory exhaustion into an ordinary error.
"""

from __future__ import annotations

import os
import zlib

from .errors import ToolkitError, UnsafeWriteError

#: Default ceiling for one decompressed blob (64 MiB).  Ren'Py script pickles
#: are a few megabytes at most; a legitimate file that exceeds this is far more
#: likely to be an archive of something else than a script.
DEFAULT_LIMIT = 64 * 1024 * 1024

#: Chunk size for the incremental inflate.  Large enough to keep the loop cheap,
#: small enough that an overshoot costs little.
_CHUNK = 1024 * 1024


class DecompressError(ToolkitError):
    """Raised when a zlib blob is corrupt or larger than the allowed size."""


class UnsafePathError(UnsafeWriteError):
    """Raised when an archive or TL entry would write outside its directory."""


def decompress_limited(
    blob: bytes, *, limit: int = DEFAULT_LIMIT, what: str = "data"
) -> bytes:
    """Inflate *blob*, refusing output larger than *limit* bytes.

    Raises :class:`DecompressError` for corrupt streams and for output that
    would exceed the limit; never returns a partial result.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    engine = zlib.decompressobj()
    out: list[bytes] = []
    total = 0
    pending = blob
    while True:
        try:
            chunk = engine.decompress(pending, _CHUNK)
        except zlib.error as exc:
            raise DecompressError(f"{what}: corrupt zlib stream ({exc})") from exc
        pending = engine.unconsumed_tail
        total += len(chunk)
        if total > limit:
            raise DecompressError(
                f"{what}: decompressed size exceeds {limit} bytes "
                f"(refusing to allocate more)"
            )
        if chunk:
            out.append(chunk)
        if engine.eof:
            break
        if not pending and not chunk:
            # Neither more input nor more output: the stream ended early.
            raise DecompressError(f"{what}: truncated zlib stream")
    if engine.unused_data:
        # Trailing bytes are not part of this member; Ren'Py never writes them.
        raise DecompressError(f"{what}: {len(engine.unused_data)} trailing byte(s)")
    return b"".join(out)


def safe_join(base: str, rel: str, *, what: str = "entry") -> str:
    """Join *rel* onto *base*, refusing anything that escapes *base*.

    Archive indexes and TL files carry their own path strings, and a crafted
    one (``../../evil``, ``C:/Windows/x``, ``/etc/x``) would otherwise write
    outside the destination directory -- the "Zip Slip" bug.  ``os.path.join``
    alone is not enough because it silently drops *base* for an absolute path
    and keeps ``..`` components.

    Raises :class:`UnsafePathError` when the result leaves *base*.
    """
    if not rel or rel.strip() in (".", ""):
        raise UnsafePathError(f"{what}: empty path")
    # Reject absolute/drive-qualified names outright: os.path.join would
    # discard ``base`` entirely.
    if os.path.isabs(rel) or os.path.splitdrive(rel)[0]:
        raise UnsafePathError(f"{what}: {rel!r} is an absolute path")
    root = os.path.realpath(base)
    target = os.path.realpath(os.path.join(root, rel))
    try:
        common = os.path.commonpath([root, target])
    except ValueError as exc:
        # Different drives on Windows; commonpath refuses to compare them.
        raise UnsafePathError(f"{what}: {rel!r} is on another drive") from exc
    if common != root:
        raise UnsafePathError(
            f"{what}: {rel!r} resolves outside {base} ({target})"
        )
    return target
