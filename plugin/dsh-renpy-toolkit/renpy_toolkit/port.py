"""把一个已有译本按标识符搬到新骨架上。

用途：游戏更新后重新生成 TL 骨架，旧译文不能丢。手工复制不可能（6 万块），
而「按 `old` 文本匹配」又会把译文放到错误的行上 —— 一句 ``"Okay."`` 在剧本里
可能出现几百次。

匹配规则（保守，宁可不搬也不搬错）：

* **对白块**：标识符必须相同，且镜像的 ``# e "..."`` 源文本逐行相同。
  标识符是 ``label + md5(code)``，源码没改就不可能变；源文本再校验一次，
  挡住「标识符撞车」这种极端情况。
* **strings 块**：按 ``old`` 文本匹配（``{#context}`` 已包含在 ``old`` 里），
  与 :func:`renpy_toolkit.tables.apply_rows` 的规则一致。

做不到的事：改过台词的那一句搬不过来（源文本对不上），会记进 ``skipped``。
这是刻意的 —— 对照表比错误译文容易补。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import tl as tlmod
from .errors import InvalidRequestError

__all__ = ["PortResult", "port_translations"]


@dataclass
class PortResult:
    """What :func:`port_translations` moved."""

    says: int = 0
    strings: int = 0
    files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """``file:identifier`` of blocks whose source text no longer matches."""

    def summary(self) -> str:
        return (
            f"{self.says} dialogue line(s), {self.strings} string(s) ported "
            f"into {len(self.files)} file(s); {len(self.skipped)} block(s) skipped"
        )


def _rel(path: str, root: str) -> str:
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")


def _load_reference(root: str) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """Index a reference TL directory.

    Returns ``({identifier: [(source, translation), ...]}, {old: new})``.
    """
    says: dict[str, list[tuple[str, str]]] = {}
    strings: dict[str, str] = {}
    for path in tlmod.iter_tl_files(root):
        doc = tlmod.parse_tl_file(path)
        for block in doc.blocks:
            if block.kind == "strings":
                for pair in block.strings:
                    if pair.new:
                        strings[pair.old] = pair.new
                continue
            if block.kind != "translate" or not block.identifier:
                continue
            sources = [src for src, _ in block.aligned()]
            texts = [line.text for line in block.say_lines if line.is_say]
            if not sources or len(sources) != len(texts):
                continue
            says[block.identifier] = list(zip(sources, texts))
    return says, strings


def port_translations(
    skeleton_dir: str,
    reference_dir: str,
    *,
    dry_run: bool = True,
    backup: bool = True,
    files: list[str] | None = None,
) -> PortResult:
    """Copy translations from *reference_dir* into *skeleton_dir*.

    ``skeleton_dir`` is modified in place (one file at a time, only when
    something actually changed). ``dry_run`` (the default) reports what would
    happen without touching the disk.
    """
    if not os.path.isdir(skeleton_dir):
        raise InvalidRequestError(f"no such directory: {skeleton_dir}")
    if not os.path.isdir(reference_dir):
        raise InvalidRequestError(f"no such reference directory: {reference_dir}")

    says, strings = _load_reference(reference_dir)
    if not says and not strings:
        raise InvalidRequestError(f"{reference_dir}: contains no translations")

    result = PortResult()
    wanted = set(files) if files else None

    for path in tlmod.iter_tl_files(skeleton_dir):
        rel = _rel(path, skeleton_dir)
        if wanted is not None and rel not in wanted:
            continue
        doc = tlmod.parse_tl_file(path)
        before_says, before_strings = result.says, result.strings

        for block in doc.blocks:
            if block.kind == "strings":
                for pair in block.strings:
                    new = strings.get(pair.old)
                    if not new or pair.new == new:
                        continue
                    try:
                        tlmod._set_string_new(doc, pair, new)
                    except tlmod.UnsafeWriteError as exc:
                        result.skipped.append(f"{rel}:{pair.lineno}: {exc}")
                        continue
                    result.strings += 1
                continue

            if block.kind != "translate" or not block.identifier:
                continue
            reference = says.get(block.identifier)
            if reference is None:
                continue
            mine = [(src, line) for src, line in block.aligned()]
            if len(mine) != len(reference):
                result.skipped.append(f"{rel}:{block.identifier}: line count differs")
                continue
            if [s.strip() for s, _ in mine] != [s.strip() for s, _ in reference]:
                # Source text changed: the identifier matched but the script
                # moved on. Writing here would put an old translation on a new
                # line, so skip it and let the translator look.
                result.skipped.append(f"{rel}:{block.identifier}: source text changed")
                continue
            for index, (_source, _line) in enumerate(mine):
                text = reference[index][1]
                if not text or _line.text == text:
                    continue
                try:
                    tlmod.apply_new_text(
                        doc, block.identifier, text, index=index
                    )
                except (tlmod.UnsafeWriteError, KeyError, IndexError) as exc:
                    result.skipped.append(f"{rel}:{block.identifier}: {exc}")
                    continue
                result.says += 1

        if result.says != before_says or result.strings != before_strings:
            result.files.append(rel)
            if not dry_run:
                doc.write(backup=backup)

    return result
