"""Ren'Py archive (.rpa / .rpi) reading and writing.

Format is fully reverse-engineered and documented in
``docs/rpa-format-spec.md``. Summary::

    RPA-3.0 %016x %08x\\n        <- header line, offset then XOR key
    <file data ...>              <- concatenated file blobs
    <zlib+pickle index>          <- {name: [(offset ^ key, length ^ key[, prefix])]}

Only RPAv2 and RPAv3 can be written. RPAv1 (.rpi) is detected by extension
and reported as read-unsupported rather than silently misparsed.
"""

from __future__ import annotations

import errno
import os
import pickle
import random
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from .errors import ToolkitError
from . import safeio

RPA1_MAGIC = b"RPA-1.0 "
RPA2_MAGIC = b"RPA-2.0 "
RPA3_MAGIC = b"RPA-3.0 "

DEFAULT_KEY = 0xDEADBEEF


class RPAError(ToolkitError):
    """Raised for malformed or unsupported archive files."""


class _IndexUnpickler(pickle.Unpickler):
    """Unpickle an RPA index without letting the archive run code.

    The RPA format stores its index as a pickle, and ``pickle.loads`` executes
    whatever a crafted archive tells it to -- opening a game downloaded from
    the internet would then be enough to run arbitrary code.  An index only ever
    contains ``dict``/``list``/``tuple``/``str``/``bytes``/``int``, so every
    other global is refused.

    This is a *reduction of the blast radius*, not a proof of safety: a value
    that reaches ``dict``/``list``/``tuple`` through a permitted global is still
    built by that callable.  Only the six builtins below are permitted, and none
    of them can reach the filesystem.
    """

    _ALLOWED = frozenset(
        {"dict", "list", "tuple", "set", "frozenset", "bytearray", "bytes", "str"}
    )

    def find_class(self, module: str, name: str) -> object:
        if module == "builtins" and name in self._ALLOWED:
            return super().find_class(module, name)
        # ``copyreg._reconstructor`` and ``__builtin__`` aliases appear in
        # pickles written by Python 2 (Ren'Py 7 archives).
        if module in ("copyreg", "__builtin__") and name in self._ALLOWED:
            return super().find_class("builtins", name)
        raise pickle.UnpicklingError(
            f"archive index references {module}.{name}; refusing to load it"
        )


def _load_index(blob: bytes) -> object:
    """Decode an RPA index with the restricted unpickler."""
    import io

    return _IndexUnpickler(io.BytesIO(blob), encoding="bytes").load()


@dataclass
class Entry:
    """One file inside an archive."""

    name: str
    offset: int = 0
    length: int = 0
    prefix: bytes = b""

    @property
    def stored_length(self) -> int:
        """Length actually occupied in the archive (prefix is not stored)."""
        return self.length - len(self.prefix)


def normalise_name(filename: str) -> str:
    """Convert an arbitrary local path to an in-archive key.

    Mirrors the reference implementation: drive letters are stripped, and
    separators become forward slashes.
    """
    path = os.path.normpath(filename).replace(os.sep, "/").replace("\\", "/")
    drive, path = os.path.splitdrive(path)
    return path.lstrip("/")


def _read_header(handle: BinaryIO, filename: str) -> tuple[int, int, int]:
    """Return ``(version, index_offset, key)`` read from *handle*.

    ``key`` is 0 for RPAv2, which has no key field.
    """
    handle.seek(0)
    first = handle.readline()
    if not first.endswith(b"\n"):
        raise RPAError(f"{filename}: archive header is not newline terminated")
    if first.startswith(RPA3_MAGIC):
        version = 3
    elif first.startswith(RPA2_MAGIC):
        version = 2
    elif first.startswith(RPA1_MAGIC):
        version = 1
    else:
        raise RPAError(
            f"{filename}: unsupported archive magic "
            f"{first[:16]!r}; only RPA-1.0/2.0/3.0 are supported"
        )
    if version == 1:
        fields = first[len(RPA1_MAGIC) : -1].split(b" ")
        if not fields or not fields[0]:
            raise RPAError(f"{filename}: RPA-1.0 header is missing the offset")
        offset = int(fields[0], 16)
        key = 0
        if offset >= os.path.getsize(filename):
            raise RPAError(f"{filename}: index offset {offset:#x} is beyond file size")
        return version, offset, key
    magic = RPA3_MAGIC if version == 3 else RPA2_MAGIC
    fields = first[len(magic) : -1].split(b" ")
    if version == 3:
        if len(fields) < 2:
            raise RPAError(f"{filename}: RPA-3.0 header needs offset and key")
        offset = int(fields[0], 16)
        key = int(fields[1], 16)
    else:
        if not fields or not fields[0]:
            raise RPAError(f"{filename}: RPA-2.0 header is missing the offset")
        offset = int(fields[0], 16)
        key = 0
    if offset >= os.path.getsize(filename):
        raise RPAError(f"{filename}: index offset {offset:#x} is beyond file size")
    return version, offset, key


def _read_rpa1_index(handle: BinaryIO, offset: int, filename: str) -> dict[str, list]:
    """Decode the RPA-1.0 ``.rpi`` index, which is stored in plain pickle form.

    RPAv1 predates the zlib wrapper and the XOR obfuscation, so the payload is
    a bare pickle whose values are ``(offset, length)`` pairs. It is read-only
    here: writing v1 is not a thing any modern Ren'Py build consumes.
    """
    handle.seek(offset)
    try:
        raw = _load_index(handle.read())
    except Exception as exc:
        raise RPAError(f"{filename}: cannot decode RPAv1 index ({exc})") from exc
    if not isinstance(raw, dict):
        raise RPAError(f"{filename}: RPAv1 index is {type(raw).__name__}, expected dict")
    return raw


def _deobfuscate(
    raw: dict, key: int
) -> dict[str, list[tuple[int, int, bytes]]]:
    out: dict[str, list[tuple[int, int, bytes]]] = {}
    for name, slots in raw.items():
        rebuilt = []
        for slot in slots:
            if len(slot) == 2:
                offset, length = slot
                rebuilt.append((offset ^ key, length ^ key, b""))
            elif len(slot) == 3:
                offset, length, prefix = slot
                rebuilt.append((offset ^ key, length ^ key, prefix))
            else:
                raise RPAError(f"index slot for {name!r} has {len(slot)} fields")
        out[name] = rebuilt
    return out


def _obfuscate(
    entries: dict[str, list[tuple[int, int, bytes]]], key: int
) -> dict:
    out = {}
    for name, slots in entries.items():
        rebuilt = []
        for offset, length, prefix in slots:
            if prefix:
                rebuilt.append((offset ^ key, length ^ key, prefix))
            else:
                rebuilt.append((offset ^ key, length ^ key))
        out[name] = rebuilt
    return out


class RenPyArchive:
    """Read (RPAv1/2/3) and write (RPAv2/3) Ren'Py archives."""

    def __init__(
        self,
        file: str | None = None,
        *,
        version: int = 3,
        padlength: int = 0,
        key: int = DEFAULT_KEY,
        prefixes: bool = False,
        override_key: int | None = None,
    ) -> None:
        self.file = file
        self.version = version
        self.padlength = padlength
        # ``key=None`` means "no explicit key given" (that is what CLI callers
        # pass when ``--key`` was omitted), not "XOR with nothing". Letting
        # None through here used to survive until save() failed on
        # ``offset ^ None`` with a TypeError after the archive was half built.
        if key is None:
            key = DEFAULT_KEY
        if not 0 <= key <= 0xFFFFFFFF:
            raise RPAError(
                f"XOR key {key:#x} does not fit in the 8 hex digits the RPA-3.0 "
                f"header reserves for it (0 .. 0xffffffff)"
            )
        self.key = key
        #: Store the first 16 bytes of every file *outside* the blob and keep
        #: only that prefix in the index slot. The reader must rebuild the file
        #: as ``prefix + stored_bytes``; ``length`` then counts prefix + stored.
        self.prefixes = prefixes
        self._handle: BinaryIO | None = None
        self._index: dict[str, list[tuple[int, int, bytes]]] = {}
        self._added: dict[str, bytes] = {}
        self._removed: set[str] = set()
        # ``key`` is both "the key to write with" and, for an existing file,
        # "the key to override the header with". Only the latter needs the
        # explicit spelling, so a plain open() trusts the header.
        self._override_key = override_key
        if file is not None:
            self.load(file, key=override_key)

    @classmethod
    def open(cls, file: str, *, key: int | None = None, **kwargs: object) -> "RenPyArchive":
        """Open an existing archive; ``key=`` forces an XOR key.

        Without ``key`` the key in the RPA-3.0 header is authoritative (that is
        the only correct behaviour, since the header is what Ren'Py itself
        reads).
        """
        return cls(file, **kwargs, override_key=key)  # type: ignore[arg-type]

    @classmethod
    def create(cls, file: str, **kwargs: object) -> "RenPyArchive":
        """Start a *new* archive: nothing is read and nothing is written yet.

        Add files with :meth:`add` and finish with :meth:`save`.
        """
        archive = cls(None, **kwargs)  # type: ignore[arg-type]
        archive.file = file
        return archive

    # ---------------------------------------------------------------- context
    def __enter__(self) -> "RenPyArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ load
    def load(self, filename: str, key: int | None = None) -> None:
        """Open *filename* and decode its index.

        *key* overrides the key stored in the header. Passing a **wrong** key
        does not corrupt anything on disk, but the XOR'd offsets come back as
        nonsense; we catch that and report it, because "silently returns
        garbage" is the worst failure mode for a translation tool.
        """
        self.close()
        self.file = filename
        self._added = {}
        self._removed = set()
        size = os.path.getsize(filename)
        with open(filename, "rb") as probe:
            version, offset, header_key = _read_header(probe, filename)
        self.version = version
        self.key = header_key if key is None else key
        self._handle = open(filename, "rb")
        if version == 1:
            self._index = self._load_v1(self._handle, offset, filename)
            return
        self._handle.seek(offset)
        compressed = self._handle.read(size - offset)
        try:
            raw = _load_index(safeio.decompress_limited(compressed, what=f"{filename}: index"))
        except Exception as exc:
            raise RPAError(f"{filename}: cannot decode index ({exc})") from exc
        if not isinstance(raw, dict):
            raise RPAError(f"{filename}: index is {type(raw).__name__}, expected dict")
        entries = _deobfuscate(raw, self.key if version == 3 else 0)
        self._validate_index(entries, filename, size, header_key)
        self._index = entries

    def _load_v1(self, handle: BinaryIO, offset: int, filename: str) -> dict:
        """Read an RPA-1.0 index and normalise it to the 3-tuple form."""
        raw = _read_rpa1_index(handle, offset, filename)
        out: dict[str, list[tuple[int, int, bytes]]] = {}
        for name, slots in raw.items():
            rebuilt = []
            for slot in slots:
                if len(slot) == 2:
                    off, length = slot
                    rebuilt.append((int(off), int(length), b""))
                elif len(slot) == 3:
                    off, length, prefix = slot
                    rebuilt.append((int(off), int(length), prefix))
                else:
                    raise RPAError(
                        f"{filename}: RPAv1 slot for {name!r} has {len(slot)} fields"
                    )
            out[name] = rebuilt
        return out

    def _validate_index(
        self,
        entries: dict[str, list[tuple[int, int, bytes]]],
        filename: str,
        size: int,
        header_key: int,
    ) -> None:
        """Reject an index whose offsets cannot possibly be right.

        With a wrong XOR key every offset and length becomes a huge random
        number, so a single bounds check tells the two cases apart and lets us
        name the real problem instead of decoding nonsense sizes.
        """
        for name, slots in entries.items():
            for offset, length, prefix in slots:
                if length < len(prefix):
                    raise RPAError(
                        f"{filename}: entry {name!r} claims length {length} "
                        f"shorter than its {len(prefix)}-byte prefix"
                    )
                if offset < 0 or length < 0 or offset + length > size:
                    hint = ""
                    if header_key and self.key != header_key:
                        hint = (
                            f" (opened with key {self.key:#010x} but the header "
                            f"says {header_key:#010x})"
                        )
                    raise RPAError(
                        f"{filename}: entry {name!r} points at "
                        f"[{offset:#x}, +{length:#x}) which is outside the "
                        f"{size:#x}-byte file; the archive is corrupt or the "
                        f"XOR key is wrong{hint}"
                    )

    # ------------------------------------------------------------- accessors
    @property
    def entries(self) -> dict[str, Entry]:
        """Current on-disk entries plus pending additions, minus removals."""
        merged = {
            name: Entry(name, slots[0][0], slots[0][1], slots[0][2])
            for name, slots in self._index.items()
            if name not in self._removed
        }
        for name, data in self._added.items():
            merged[name] = Entry(name, 0, len(data), b"")
        return merged

    def names(self) -> list[str]:
        return sorted(self.entries)

    def has(self, filename: str) -> bool:
        return normalise_name(filename) in self.entries

    def __contains__(self, filename: object) -> bool:
        if not isinstance(filename, str):
            return False
        return self.has(filename)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def read(self, filename: str) -> bytes:
        name = normalise_name(filename)
        if name in self._added:
            return self._added[name]
        if name in self._removed or name not in self._index:
            raise OSError(errno.ENOENT, f"{name} not in archive", self.file)
        if self._handle is None:
            raise RPAError(f"{self.file}: archive handle is closed")
        offset, length, prefix = self._index[name][0]
        self._handle.seek(offset)
        stored = self._handle.read(length - len(prefix))
        return prefix + stored

    def read_entry(self, filename: str) -> Entry:
        name = normalise_name(filename)
        if name in self._added:
            return Entry(name, 0, len(self._added[name]), b"")
        if name in self._removed or name not in self._index:
            raise OSError(errno.ENOENT, f"{name} not in archive", self.file)
        offset, length, prefix = self._index[name][0]
        return Entry(name, offset, length, prefix)

    # ------------------------------------------------------------- mutations
    def add(self, filename: str, contents: bytes) -> None:
        """Stage a file (or replace an existing one)."""
        name = normalise_name(filename)
        self._added[name] = bytes(contents)
        self._removed.discard(name)

    def remove(self, filename: str) -> None:
        name = normalise_name(filename)
        if name in self._added:
            del self._added[name]
        else:
            self._removed.add(name)

    def delete(self, filename: str) -> None:  # alias used by the CLI
        self.remove(filename)

    def extract(self, filename: str, dest: str) -> None:
        data = self.read(filename)
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)

    def extract_all(self, destdir: str, *, overwrite: bool = True) -> list[str]:
        """Write every entry under *destdir*.

        Entry names come from the archive index, which is attacker-controlled
        data: a crafted index can carry ``../../evil`` and escape *destdir*.
        :func:`safeio.safe_join` refuses those names instead of writing them.
        """
        written = []
        for name in self.names():
            target = safeio.safe_join(
                destdir, name, what=f"{self.file or 'archive'}: entry"
            )
            if os.path.exists(target) and not overwrite:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(self.read(name))
            written.append(name)
        return written

    # ------------------------------------------------------------------ save
    def save(
        self,
        filename: str | None = None,
        *,
        version: int | None = None,
        key: int | None = None,
    ) -> None:
        """Rebuild the archive with staged changes applied.

        The original file is preserved as ``<name>.bak`` (unless it did not
        exist yet).

        All source bytes are read *before* the target is opened for writing:
        on Windows you cannot have the same path open for reading and writing
        at the same time, and we do not want to depend on the file being
        closed by ``load()`` in time.
        """
        if filename is None:
            filename = self.file
        if filename is None:
            raise RPAError("no target file for save()")
        filename = os.path.abspath(filename)
        version = self.version if version is None else version
        if version not in (2, 3):
            raise RPAError(f"writing RPAv{version} is not supported")
        key = self.key if key is None else key

        if os.path.exists(filename):
            backup = filename + ".bak"
            with open(filename, "rb") as src, open(backup, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)

        index: dict[str, list[tuple[int, int, bytes]]] = {}
        header_len = 34 if version == 3 else 25

        # Snapshot the bytes we must copy over: the write handle below would
        # otherwise be reading the file we are truncating (and on Windows the
        # open-for-write alone blocks our own read).
        payloads: list[tuple[str, bytes]] = []
        for name in self.names_disk_only():
            payloads.append((name, self._read_raw(name)))
        for name, data in sorted(self._added.items()):
            payloads.append((name, data))

        with open(filename, "wb") as out:
            out.seek(header_len)
            offset = header_len

            def _emit(name: str, data: bytes) -> None:
                nonlocal offset
                if self.padlength > 0:
                    pad = bytes(random.randint(1, 255)
                                for _ in range(random.randint(1, self.padlength)))
                    out.write(pad)
                    offset += len(pad)
                if self.prefixes and len(data) > 16:
                    # Prefix lives in the index, not in the blob; ``length``
                    # still counts prefix + stored, which is what the reader
                    # subtracts from.
                    prefix, data = data[:16], data[16:]
                    index[name] = [(offset, len(prefix) + len(data), prefix)]
                else:
                    index[name] = [(offset, len(data), b"")]
                out.write(data)
                offset += len(data)

            # Existing bytes first, in original (sorted) order: keeps output
            # stable across runs, which matters for caching in Android builds.
            for name, data in payloads:
                _emit(name, data)

            out.write(zlib.compress(pickle.dumps(
                _obfuscate(index, key if version == 3 else 0),
                pickle.HIGHEST_PROTOCOL,
            )))

            out.seek(0)
            if version == 3:
                out.write(RPA3_MAGIC + b"%016x %08x\n" % (offset, key))
            else:
                out.write(RPA2_MAGIC + b"%016x\n" % (offset,))

        self.close()
        self.load(filename)

    def names_disk_only(self) -> list[str]:
        return [
            n
            for n in sorted(self._index)
            if n not in self._removed and n not in self._added
        ]

    def _read_raw(self, name: str) -> bytes:
        assert self._handle is not None
        offset, length, prefix = self._index[name][0]
        self._handle.seek(offset)
        return prefix + self._handle.read(length - len(prefix))

    # ------------------------------------------------------------- reporting
    def info(self) -> str:
        entries = self.entries
        total = sum(e.length for e in entries.values())
        head = f"{self.file}: RPAv{self.version}, {len(entries)} files, {total:,} bytes"
        if self._added:
            head += f", +{len(self._added)} staged"
        if self._removed:
            head += f", -{len(self._removed)} staged"
        return head

    def stats(self) -> dict[str, int]:
        entries = self.entries
        return {
            "files": len(entries),
            "bytes": sum(e.length for e in entries.values()),
            "staged_add": len(self._added),
            "staged_remove": len(self._removed),
            "version": self.version,
        }


def detect(path: str) -> int | None:
    """Return the archive version of *path*, or None if unrecognised.

    Detection is by **content** only. ``.rpi`` is an extension hint, not
    evidence: a file called ``x.rpi`` holding random bytes is not an archive.
    """
    try:
        with open(path, "rb") as fh:
            first = fh.readline(64)
    except OSError:
        return None
    if first.startswith(RPA3_MAGIC):
        return 3
    if first.startswith(RPA2_MAGIC):
        return 2
    if first.startswith(RPA1_MAGIC):
        return 1
    return None


def is_rpa(path: str) -> bool:
    """True when *path* really is a Ren'Py archive (byte check, not suffix)."""
    return detect(path) is not None


__all__ = [
    "DEFAULT_KEY",
    "Entry",
    "RenPyArchive",
    "RPAError",
    "RPA1_MAGIC",
    "RPA2_MAGIC",
    "RPA3_MAGIC",
    "detect",
    "is_rpa",
    "normalise_name",
]
