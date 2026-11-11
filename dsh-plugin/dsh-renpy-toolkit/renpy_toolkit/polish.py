"""读「润色批处理脚本」，直接应用到 TL 上。

逐行改 6 万块太慢，实践中会攒出一批一次性的 ``polish_script_blockNN_*.py``，
形态是一个模块级字典::

    translations_by_text_verified = {
        'mc "{size=-6}我保证，我最擅长保守秘密了。"': (599, 'mc "{size=-6}我保证，守秘密我最在行了。"'),
    }
    strings_translations = {
        "Load": "读取",
    }

键是**整行当前译文**（含角色名与前缀），值是 ``(源行号, 整行新译文)``。

这里**不用 exec**：用 ``ast.parse`` + ``ast.literal_eval`` 只把字面量读出来。
批处理脚本是数据，不是程序；能当数据读就不该执行它。

支持四种字典名（``polish_tool.py`` 定义的）：

=========================== =============================================
``translations_by_text``     ``{旧译文: 新译文}``，同文本会匹配多处
``translations_by_line``     ``{源行号: 新译文}``，只靠行号
``translations_verified``    ``{源行号: (旧译文, 新译文)}``
``translations_by_text_verified``  ``{旧译文: (源行号, 新译文)}`` ← 推荐
``strings_translations``     ``{old: new}``，作用于 strings 块
=========================== =============================================
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field

from . import tl as tlmod
from .errors import InvalidRequestError

__all__ = [
    "PolishBatch",
    "PolishResult",
    "apply_polish",
    "read_polish_script",
]

_TEXT_FIELDS = ("translations_by_text", "translations_by_text_verified")
_LINE_FIELDS = ("translations_by_line", "translations_verified")
_STRING_FIELDS = ("strings_translations",)


@dataclass
class PolishBatch:
    """The literals a polish script declared."""

    by_text: dict[str, str] = field(default_factory=dict)
    by_text_verified: dict[str, tuple[int, str]] = field(default_factory=dict)
    by_line: dict[int, str] = field(default_factory=dict)
    by_line_verified: dict[int, tuple[str, str]] = field(default_factory=dict)
    strings: dict[str, str] = field(default_factory=dict)
    path: str = ""

    @property
    def entries(self) -> int:
        return (
            len(self.by_text)
            + len(self.by_text_verified)
            + len(self.by_line)
            + len(self.by_line_verified)
            + len(self.strings)
        )


@dataclass
class PolishResult:
    replaced: int = 0
    not_found: int = 0
    already: int = 0
    files: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.replaced} line(s) replaced, {self.already} already correct, "
            f"{self.not_found} not found, in {len(self.files)} file(s)"
        )


def _literal(node: ast.AST, *, where: str) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError) as exc:
        raise InvalidRequestError(
            f"{where}: value is not a plain literal ({exc}); polish scripts must "
            f"contain data only, no expressions"
        ) from exc


def read_polish_script(path: str) -> PolishBatch:
    """Read one polish script. Never executes it."""
    if not os.path.isfile(path):
        raise InvalidRequestError(f"no such file: {path}")
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidRequestError(f"{path}: not valid UTF-8 ({exc})") from exc
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise InvalidRequestError(f"{path}: cannot parse ({exc})") from exc

    batch = PolishBatch(path=path)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        if name in _TEXT_FIELDS or name in _LINE_FIELDS or name in _STRING_FIELDS:
            data = _literal(node.value, where=f"{path}:{node.lineno} {name}")
            if not isinstance(data, dict):
                raise InvalidRequestError(f"{path}:{node.lineno} {name} is not a dict")
            _absorb(batch, name, data, where=f"{path}:{node.lineno} {name}")
    if not batch.entries:
        raise InvalidRequestError(
            f"{path}: no recognised dictionary (looked for "
            f"{', '.join(_TEXT_FIELDS + _LINE_FIELDS + _STRING_FIELDS)})"
        )
    return batch


def _absorb(batch: PolishBatch, name: str, data: dict, *, where: str) -> None:
    if name == "translations_by_text":
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise InvalidRequestError(f"{where}: expected str -> str")
            batch.by_text[key] = value
    elif name == "translations_by_text_verified":
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, tuple) or len(value) != 2:
                raise InvalidRequestError(f"{where}: expected str -> (line, str)")
            line, text = value
            if not isinstance(line, int) or not isinstance(text, str):
                raise InvalidRequestError(f"{where}: expected str -> (line, str)")
            batch.by_text_verified[key] = (line, text)
    elif name == "translations_by_line":
        for key, value in data.items():
            if not isinstance(key, int) or not isinstance(value, str):
                raise InvalidRequestError(f"{where}: expected int -> str")
            batch.by_line[key] = value
    elif name == "translations_verified":
        for key, value in data.items():
            if not isinstance(key, int) or not isinstance(value, tuple) or len(value) != 2:
                raise InvalidRequestError(f"{where}: expected int -> (str, str)")
            old, text = value
            if not isinstance(old, str) or not isinstance(text, str):
                raise InvalidRequestError(f"{where}: expected int -> (str, str)")
            batch.by_line_verified[key] = (old, text)
    else:  # strings
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise InvalidRequestError(f"{where}: expected str -> str")
            batch.strings[key] = value


def apply_polish(
    tl_dir: str,
    batch: PolishBatch,
    *,
    dry_run: bool = True,
    backup: bool = True,
    files: list[str] | None = None,
) -> PolishResult:
    """Apply *batch* to every TL file in *tl_dir*."""
    if not os.path.isdir(tl_dir):
        raise InvalidRequestError(f"no such directory: {tl_dir}")

    result = PolishResult()
    wanted = set(files) if files else None
    unmatched_text = set(batch.by_text) | set(batch.by_text_verified)
    unmatched_line = set(batch.by_line) | set(batch.by_line_verified)
    unmatched_strings = set(batch.strings)

    for path in tlmod.iter_tl_files(tl_dir):
        rel = os.path.relpath(path, tl_dir).replace(os.sep, "/")
        if wanted is not None and rel not in wanted:
            continue
        doc = tlmod.parse_tl_file(path)
        replaced_before = result.replaced

        for block in doc.blocks:
            if block.kind == "strings":
                for pair in block.strings:
                    new = batch.strings.get(pair.old)
                    if new is None:
                        continue
                    unmatched_strings.discard(pair.old)
                    if pair.new == new:
                        result.already += 1
                        continue
                    try:
                        tlmod._set_string_new(doc, pair, new)
                    except tlmod.UnsafeWriteError as exc:
                        result.problems.append(f"{rel}:{pair.lineno}: {exc}")
                        continue
                    result.replaced += 1
                continue

            if block.kind != "translate" or not block.identifier:
                continue
            units = list(block.aligned())
            for index, (source, line) in enumerate(units):
                # Polish scripts key on the **whole** say line (``k "……"``),
                # while ``SayLine.text`` is only the quoted payload. Match on
                # ``raw`` and pull the payload back out of the replacement.
                text = line.raw.strip()
                new = None
                if text in batch.by_text:
                    new = batch.by_text[text]
                    unmatched_text.discard(text)
                elif text in batch.by_text_verified:
                    expected, candidate = batch.by_text_verified[text]
                    unmatched_text.discard(text)
                    if expected != _source_lineno(block, source):
                        result.problems.append(
                            f"{rel}:{line.lineno}: '{text[:40]}' declared for source "
                            f"line {expected}, but this block's source is "
                            f"{_source_lineno(block, source)}"
                        )
                        continue
                    new = candidate
                elif _source_lineno(block, source) in batch.by_line_verified:
                    lineno = _source_lineno(block, source)
                    expected, candidate = batch.by_line_verified[lineno]
                    unmatched_line.discard(lineno)
                    if expected != text:
                        result.problems.append(
                            f"{rel}:{line.lineno}: source line {lineno} should read "
                            f"'{expected[:40]}' but reads '{text[:40]}'"
                        )
                        continue
                    new = candidate
                elif _source_lineno(block, source) in batch.by_line:
                    lineno = _source_lineno(block, source)
                    new = batch.by_line[lineno]
                    unmatched_line.discard(lineno)
                if new is None:
                    continue
                if text == new:
                    result.already += 1
                    continue
                payload = _payload(new)
                if payload is None:
                    result.problems.append(
                        f"{rel}:{line.lineno}: '{new[:40]}' is not a say statement"
                    )
                    continue
                try:
                    tlmod.apply_new_text(doc, block.identifier, payload, index=index)
                except (tlmod.UnsafeWriteError, KeyError, IndexError) as exc:
                    result.problems.append(f"{rel}:{line.lineno}: {exc}")
                    continue
                result.replaced += 1

        if result.replaced != replaced_before:
            result.files.append(rel)
            if not dry_run:
                doc.write(backup=backup)

    result.not_found = (
        len(unmatched_text) + len(unmatched_line) + len(unmatched_strings)
    )
    return result


def _source_lineno(block, source: str) -> int:
    """Line number of *source* in the game script, from the block's ``# game/...`` map."""
    return block.source_line or 0


def _payload(full_line: str) -> str | None:
    """Extract the decoded say payload from a whole say line.

    ``k "新台词。"`` -> ``新台词。``；``"旁白。"`` -> ``旁白。``；
    anything that is not a say statement -> ``None``.
    """
    split = tlmod._split_say(full_line)
    if split is None:
        return None
    return tlmod.unescape_value(split[1])
