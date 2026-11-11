"""硬编码文本的「身份」记录：清单文件 + 注释标记。

硬编码文本指**源码里直接写、没有 ``_()`` 包裹**的可见文本，例如::

    text "Are you sure?"                       # screen 里的字面量
    $ msg = "Saved."                           # 运行时才显示的串
    stack_notify("已保存")                      # 自定义函数的参数

它们能翻的**唯一依据**是运行时会过一次 ``renpy.translation.translate_string``：
``Text.set_text()`` → ``renpy.substitutions.substitute(translate=True)`` →
``translate_string(s)``。这条链与源码有没有 ``_()`` 无关，所以只要 TL 侧有一条
逐字符相同的 ``old``，它就生效。

**关键取舍：本工具一律不改源码。** 早期做法是「先把源码里的字面量手工包上
``_()``，再用清单记身份」，那样每份补丁都绑死在一个游戏版本上，游戏一升级
就得重新改源码。现在改为**纯 TL 侧**：

* ``old`` 就是源码里的原始字面量，一字不改；
* 补丁 = ``tl/<lang>/*.rpy`` + ``tl/<lang>/hardcoded.json``，与源码完全解耦；
* 游戏更新后只要 ``old`` 还对得上，译文继续生效，``tl hardcoded scan`` 会报出
  哪几条已经找不到源文（``removed: true``）。

因而引入一套**双轨**记录：

1. ``tl/<lang>/hardcoded.json`` —— 权威清单。记录每条 ``old`` 的源位置、
   原句、上下文、加入时间。可以校验（源文还在不在）、可以反向生成报告。
2. 注释标记 —— TL 文件里紧挨 ``# game/x.rpy:NNN`` 的 ``# hardcoded: <context>``
   行。它只为人眼和 grep 服务，**解析时不依赖它**；清单丢了可以用它恢复大部分。

为什么不靠「源码里有没有 ``_()``」判别：用户明确回答「不能，因为有些是原生
strings 块」—— 原生 strings 同样走 ``_()`` 路径，两者在源码侧无法区分。清单
靠的是**生成时就记录**（``tl gen`` 知道每条字面量的 ``wrapped`` 标志），而不是
事后猜。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence

from .errors import InvalidRequestError

#: A language directory name must be a plain identifier.  Ren'Py itself allows
#: any directory name, but this value is used as a path component here, so
#: ``..`` and drive letters have to be refused.
LANGUAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")

__all__ = [
    "HardcodedEntry",
    "HardcodedLedger",
    "MARKER_PREFIX",
    "LEDGER_NAME",
    "annotate_blocks",
    "extract_from_dir",
    "from_rows",
    "marker_line",
    "merge_ledger",
    "parse_marker",
    "load_ledger",
    "save_ledger",
    "ledger_path",
    "scan_markers",
    "source_anchor",
]

#: File name of the ledger inside ``tl/<lang>/``.
LEDGER_NAME = "hardcoded.json"

#: Comment prefix written directly above / below the ``# game/...`` anchor.
MARKER_PREFIX = "# hardcoded:"

_MARKER_RE = re.compile(
    r"^\s*#\s*hardcoded\s*:(?P<context>.*?)\s*$", re.IGNORECASE
)
_SOURCE_RE = re.compile(r"^\s*#\s*(?P<file>game/[\w./\\-]+\.rpy):(?P<line>\d+)\s*$")


@dataclass
class HardcodedEntry:
    """One literal the game shows but never marked with ``_()``."""

    old: str
    """The source literal, exactly as it appears in ``old "..."``."""
    new: str = ""
    """The current translation (kept in sync by :meth:`HardcodedLedger.sync`)."""
    source: str = ""
    """``game/screens.rpy:100`` -- where the literal lives, or ``""``."""
    context: str = ""
    """Free-form origin note, e.g. ``stack_notify`` or ``main_menu label``."""
    added: str = ""
    removed: bool = False
    """Set when a verification pass no longer finds the literal at ``source``."""

    kind: str = "hardcoded"

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class HardcodedLedger:
    """The ``hardcoded.json`` document."""

    language: str
    entries: list[HardcodedEntry] = field(default_factory=list)
    version: int = 1
    path: str = ""

    # ------------------------------------------------------------- accessors
    def by_old(self) -> dict[str, HardcodedEntry]:
        return {entry.old: entry for entry in self.entries}

    def get(self, old: str) -> HardcodedEntry | None:
        return self.by_old().get(old)

    def add(self, entry: HardcodedEntry) -> None:
        existing = self.get(entry.old)
        if existing is not None:
            # Same literal seen twice: keep the first source anchor, but adopt
            # any newly supplied translation (an empty ``new`` must not wipe it).
            if entry.new and not existing.new:
                existing.new = entry.new
            if entry.source and not existing.source:
                existing.source = entry.source
            if entry.context and not existing.context:
                existing.context = entry.context
            existing.removed = False
            return
        self.entries.append(entry)

    def remove(self, old: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.old != old]
        return len(self.entries) != before

    def contexts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.entries:
            out[entry.context] = out.get(entry.context, 0) + 1
        return out

    # --------------------------------------------------------------- syncing
    def sync(self, pairs: Iterable[tuple[str, str]]) -> tuple[int, int]:
        """Refresh ``new`` from the TL files; mark vanished entries.

        ``pairs`` is ``(old, new)`` from the current ``.rpy`` files.  Returns
        ``(updated, missing)``: how many translations were refreshed and how
        many ledger entries no longer appear in any TL file.
        """
        current = dict(pairs)
        updated = missing = 0
        for entry in self.entries:
            if entry.old in current:
                new = current[entry.old]
                if new and new != entry.new:
                    entry.new = new
                    updated += 1
                entry.removed = False
            else:
                entry.removed = True
                missing += 1
        return updated, missing

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "language": self.language,
            "entries": [entry.to_json() for entry in self.entries],
        }


def marker_line(context: str = "") -> str:
    """The comment written next to a hardcoded entry."""
    context = context.strip()
    return MARKER_PREFIX if not context else f"{MARKER_PREFIX} {context}"


def parse_marker(line: str) -> str | None:
    """Return the context of a ``# hardcoded: ...`` line, or None."""
    match = _MARKER_RE.match(line)
    if match is None:
        return None
    return match.group("context")


def ledger_path(tl_dir: str, language: str | None = None) -> str:
    """Path of ``hardcoded.json`` for a language directory.

    ``tl_dir`` is normally the *language* directory (``game/tl/Chinese``), which
    is what every CLI command takes, so the language name is never appended to
    it — doing so produced ``tl/Chinese/Chinese/hardcoded.json``.  When the
    caller does pass the parent ``game/tl`` directory, the language subdirectory
    is used instead.

    *language* becomes a path component, so it is validated against
    :data:`LANGUAGE_RE`; ``..`` or a drive letter would otherwise let the
    ledger be written outside the language tree.
    """
    root = tl_dir
    if language and os.path.basename(os.path.normpath(tl_dir)).lower() != language.lower():
        if not LANGUAGE_RE.match(language):
            raise InvalidRequestError(
                f"invalid language name {language!r}: expected a plain identifier"
            )
        candidate = os.path.join(tl_dir, language)
        if os.path.isdir(tl_dir):
            from . import tl as tlmod

            candidate = os.path.join(tl_dir, tlmod.resolve_language(tl_dir, language))
        if os.path.isdir(candidate) or not os.path.isdir(tl_dir):
            root = candidate
    return os.path.join(root, LEDGER_NAME)


def load_ledger(path: str, *, language: str = "") -> HardcodedLedger:
    """Load a ledger; a missing file yields an empty one (never an error)."""
    if not os.path.exists(path):
        return HardcodedLedger(language=language, path=path)
    with open(path, "rb") as handle:
        raw = handle.read().decode("utf-8-sig")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidRequestError(f"{path}: invalid JSON ({exc.msg} at {exc.pos})") from exc
    if not isinstance(data, dict):
        raise InvalidRequestError(f"{path}: expected a JSON object")
    entries: list[HardcodedEntry] = []
    for item in data.get("entries", []):
        if not isinstance(item, dict) or "old" not in item:
            continue
        entries.append(
            HardcodedEntry(
                old=str(item.get("old", "")),
                new=str(item.get("new", "")),
                source=str(item.get("source", "")),
                context=str(item.get("context", "")),
                added=str(item.get("added", "")),
                removed=bool(item.get("removed", False)),
                kind=str(item.get("kind", "hardcoded")),
            )
        )
    return HardcodedLedger(
        language=str(data.get("language") or language),
        version=int(data.get("version", 1)),
        entries=entries,
        path=path,
    )


def save_ledger(ledger: HardcodedLedger, path: str | None = None) -> str:
    """Write the ledger as UTF-8 JSON with a trailing newline.

    Entries are sorted by ``old`` so repeated saves are byte-identical and a
    diff only shows real changes.
    """
    target = path or ledger.path
    if not target:
        raise InvalidRequestError("ledger has no path; pass an explicit path")
    payload = ledger.to_json()
    payload["entries"] = sorted(payload["entries"], key=lambda item: item["old"])
    directory = os.path.dirname(os.path.abspath(target))
    if directory:
        os.makedirs(directory, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(target, "wb") as handle:
        handle.write(text.encode("utf-8"))
    ledger.path = target
    return target


def source_anchor(line: str) -> str | None:
    """``game/screens.rpy:100`` from a mirrored comment line, else None."""
    match = _SOURCE_RE.match(line)
    return f"{match.group('file')}:{match.group('line')}" if match else None


def annotate_blocks(text: str, ledger: HardcodedLedger) -> str:
    """Insert ``# hardcoded: ...`` markers above matching ``translate ... strings``.

    Only tuples present in ``ledger`` are annotated, so a file with no ledger
    hits stays byte-identical.  The marker goes *above* the block, after any
    bundled ``# game/x.rpy:NNN`` source comment, so the anchor stays first.

    Runs are idempotent: a block that already carries a marker is left alone.

    This is a pure text transform: it never parses the TL grammar, so it cannot
    reorder or drop a block even if the file is hand-mangled.
    """
    wanted = ledger.by_old()
    if not wanted:
        return text
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    # Walk blocks: a block starts at a ``translate`` header and runs until the
    # next line at column 0 that is not a comment.
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if not line.strip().startswith("translate ") or line[:1].isspace():
            out.append(line)
            index += 1
            continue
        block_end = index + 1
        while block_end < total:
            probe = lines[block_end]
            if probe.strip() and not probe[:1].isspace():
                break
            block_end += 1
        body = lines[index:block_end]
        olds = [
            match.group("body")
            for match in (
                re.match(r'^\s*old\s+"(?P<body>.*)"\s*$', item) for item in body
            )
            if match is not None
        ]
        entry = next((wanted[old] for old in olds if old in wanted), None)
        # ``body`` starts *at* the header, so any marker written by a previous
        # run lives in the tail already accumulated in ``out``.  Scanning only
        # ``body`` made this function append a second marker on every run.
        already = any(parse_marker(item) is not None for item in body) or any(
            parse_marker(item) is not None for item in out[_last_blank(out) :]
        )
        if entry is None or already:
            out.extend(body)
            index = block_end
            continue
        # Place the marker between any ``# game/...`` anchor and the header,
        # but never above the blank line that separates blocks.
        cut = len(out)
        while cut > 0 and out[cut - 1].lstrip().startswith("#"):
            if parse_marker(out[cut - 1]) is not None:
                break
            cut -= 1
        pending = out[cut:]
        del out[cut:]
        out.append(marker_line(entry.context))
        out.extend(pending)
        out.extend(body)
        index = block_end
    return eol.join(out)


def _last_blank(lines: list[str]) -> int:
    """Index just past the last blank line, i.e. where the current block starts."""
    for position in range(len(lines) - 1, -1, -1):
        if not lines[position].strip():
            return position + 1
    return 0


# ---------------------------------------------------------------------------
# building a ledger from the generator's probe dump
# ---------------------------------------------------------------------------
def from_rows(rows: Iterable[dict], *, language: str,
              keep: Iterable[str] | None = None) -> HardcodedLedger:
    """Build a ledger from ``rtk_strings.json`` rows, keeping only hardcoded ones.

    The probe records, per literal, whether the source wrapped it in ``_()``
    (``wrapped``).  Anything **not** wrapped reached the TL purely through the
    runtime ``translate_string`` path, which is exactly the definition of a
    hardcoded string -- no source edit is implied or required.

    ``context`` becomes the owning screen name when the probe knows it, else the
    discovery route (``pycode`` / ``pyexpr`` / ``showtext`` / ``additional`` /
    ``sl``), so a reader can tell a screen label from a data-table string.

    Engine-owned rows are skipped: they live in ``common.rpy`` and are not the
    game author's hardcoding.

    *keep*, when given, is the set of texts that survived
    :func:`renpy_toolkit.tlgen.filter_strings`.  Passing it keeps the ledger in
    lockstep with the TL files actually written -- without it the ledger would
    list every unwrapped literal in the dump, including image paths and colour
    codes that never became a translation.
    """
    allowed = set(keep) if keep is not None else None
    ledger = HardcodedLedger(language=language)
    for row in rows:
        if row.get("engine"):
            continue
        if row.get("wrapped"):
            continue
        text = str(row.get("text") or "")
        if not text:
            continue
        if allowed is not None and text not in allowed:
            continue
        rel = str(row.get("file") or "").replace("\\", "/")
        if not rel or rel.startswith("tl/"):
            continue
        line = row.get("line")
        source = f"game/{rel}:{line}" if line else f"game/{rel}"
        screen = str(row.get("screen") or "").strip()
        context = screen or str(row.get("how") or "hardcoded")
        ledger.add(
            HardcodedEntry(old=text, source=source, context=context)
        )
    return ledger


def merge_ledger(old: HardcodedLedger, fresh: HardcodedLedger) -> HardcodedLedger:
    """Fold *fresh* into *old*, preserving hand-edited fields.

    A rescan must not throw away what a person typed: ``added`` dates stay as
    they were, a translation already in the ledger survives an empty rescan
    value, and entries that vanished from the dump are kept but flagged
    ``removed`` instead of being deleted -- otherwise one bad run silently
    destroys the record of what used to be hardcoded.
    """
    merged = HardcodedLedger(language=fresh.language or old.language, path=old.path)
    fresh_by_old = fresh.by_old()
    for entry in old.entries:
        found = fresh_by_old.pop(entry.old, None)
        if found is None:
            entry.removed = True
            merged.entries.append(entry)
            continue
        entry.removed = False
        if found.source and found.source != entry.source:
            entry.source = found.source
        if found.context and found.context != entry.context:
            entry.context = found.context
        merged.entries.append(entry)
    for entry in fresh_by_old.values():
        merged.entries.append(entry)
    merged.entries.sort(key=lambda item: item.old)
    return merged
