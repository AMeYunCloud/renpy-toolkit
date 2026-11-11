"""从一个只有编译产物的游戏里，造出一份全新的 ``tl/<lang>/`` 骨架.

发行版游戏的结构是::

    game/
      *.rpyc            编译后的游戏本体（英文原文）
      tl/None/common.rpym   引擎内置串的英文原文，通常只有这一个文件
      tl/Spanish/*.rpyc     别人的翻译（可选，可当参考，不能当原文）

要汉化就得新建 ``game/tl/chinese/``。有 SDK 时最好的做法是
``renpy <basedir> translate chinese`` —— 标识符、来源行号、引号转义全由引擎
自己算。但没有 SDK（或手头只有一份发行版）时，这条命令跑不了，于是要么放弃，
要么自己拼标识符。自己拼标识符是这套工具里最危险的事：标识符由
``md5(who + encode_say_string(what))`` 派生，规则错一位就是「译文全部显示成
英文，而且没有任何报错」。

所以这里的做法是：**先反编译，再从反编译出的源码里读标识符**。反编译产物里
的 ``translate`` 块（如果原游戏自带其他语言）和 ``Say`` 语句都带着引擎当初算好
的标识符，我们只做搬运，绝不重算。

三种来源，优先级从高到低：

1. ``--from`` 指定的**已有翻译目录**（哪怕语言不同）。块和标识符都是现成的，
   把语言名换掉、译文清空即可。最稳。
2. 原游戏自带的 ``tl/None``。只有引擎内置串（``common.rpym``），覆盖不到剧情。
3. 直接扫 ``game/**/*.rpyc`` 的 ``Say`` / ``Menu`` 语句，自己算标识符。
   这是唯一的兜底，也是唯一有风险的一条——算出来的标识符与引擎是否一致，
   必须靠 :func:`renpy_toolkit.verify.verify_fresh_tl` 上机验证。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .errors import ToolkitError
from . import tl as tlmod
from .tl import TlBlock, TlFile, dump_tl_file, parse_tl_file, parse_tl_text


__all__ = [
    "FreshTlResult",
    "identifier_for",
    "plan_fresh_tl",
    "translate_dir_for",
]


# ---------------------------------------------------------------------------
# 标识符派生
# ---------------------------------------------------------------------------
def _encode_say_string(text: str) -> str:
    r"""Mirror ``renpy.translation.encode_say_string``.

    Ren'Py's own implementation::

        s = s.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        s = re.sub(r"(?<= ) ", "\\ ", s)
        return '"' + s + '"'

    The trailing ``re.sub`` exists because Ren'Py's lexer collapses runs of
    spaces inside a quoted string; escaping the second space of every pair
    keeps the source text's spacing stable through a round trip.  Getting the
    lookbehind wrong here is silent: identifiers still look plausible, they
    just never match, and every translation falls back to English.
    """
    s = text.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    # ``re.sub`` scans left to right and does NOT rescan what it inserted, so
    # ``"a   b"`` (three spaces) becomes ``a \  b``: the 2nd space is escaped,
    # the 3rd is not, and the next pair starts after it.
    s = re.sub(r"(?<= ) ", r"\\ ", s)
    return '"' + s + '"'


def identifier_for(label: str, who: str | None, what: str, *, arguments: str = "") -> str:
    """Derive the translation identifier Ren'Py would give for one say line.

    ``identifier = label.replace(".", "_") + "_" + md5(code + "\\r\\n").hexdigest()[:8]``
    where ``code`` is the say statement's source text without its trailing
    newline.  *arguments* is the literal ``(multiple=2)``-style tail, which is
    part of ``code`` and therefore part of the hash.
    """
    import hashlib

    head = f"{who} " if who else ""
    code = f"{head}{_encode_say_string(what)}"
    if arguments:
        code += arguments
    label_part = (label or "").replace(".", "_")
    digest = hashlib.md5((code + "\r\n").encode("utf-8")).hexdigest()[:8]
    return f"{label_part}_{digest}"


# ---------------------------------------------------------------------------
# 目录约定
# ---------------------------------------------------------------------------
def translate_dir_for(basedir: str, language: str) -> str:
    """Target ``game/tl/<language>``, spelled the way an existing folder is.

    Language names are case-sensitive: ``tl/Chinese`` and ``tl/chinese`` are two
    different languages to Ren'Py, while Windows treats them as one directory.
    """
    """``<basedir>/game/tl/<language>``, accepting either the root or ``game``.

    *basedir* is the directory a player would launch the game from -- the one
    holding ``game/``.  Passing ``game/`` itself also works, because people do
    it constantly and answering "not a project" is not helpful.
    """
    base = os.path.abspath(basedir)
    game = base if os.path.basename(base).lower() == "game" else os.path.join(base, "game")
    if not os.path.isdir(game):
        raise ToolkitError(
            f"{basedir}: no game/ directory (pass the folder containing game/, "
            "or game/ itself)"
        )
    tls = os.path.join(game, "tl")
    return os.path.join(tls, tlmod.resolve_language(tls, language))


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------
@dataclass
class FreshTlResult:
    """What :func:`plan_fresh_tl` worked out."""

    language: str
    target_dir: str
    source: str = ""
    files: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    blocks: int = 0
    strings: int = 0
    skipped_existing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"tl/{self.language}: {self.blocks} translate block(s), "
            f"{self.strings} string(s) in {len(self.files)} file(s)",
            f"  source: {self.source}",
            f"  target: {self.target_dir}",
        ]
        if not self.files and self.planned:
            lines.append(f"  dry run -- {len(self.planned)} file(s) would be written")
        if self.skipped_existing:
            lines.append(
                f"  kept existing: {len(self.skipped_existing)} file(s)"
                " (never overwritten -- use --force)"
            )
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------
def _existing_languages(tl_root: str) -> list[str]:
    if not os.path.isdir(tl_root):
        return []
    return sorted(
        name
        for name in os.listdir(tl_root)
        if os.path.isdir(os.path.join(tl_root, name))
    )


def _template_files(tl_dir: str) -> list[str]:
    """``.rpy``/``.rpym`` files under *tl_dir* (skipping backups)."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(tl_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("_"))
        for name in sorted(filenames):
            if name.endswith((".rpy", ".rpym")) and not name.endswith(".bak"):
                found.append(os.path.join(dirpath, name))
    return found


def _decompiled_template(tl_dir: str, result: "FreshTlResult") -> tuple[str, list[str]]:
    """Decompile *tl_dir*'s ``.rpyc`` into a scratch dir.

    Returns ``(scratch_root, files)`` -- the caller must use *scratch_root* as
    the base for relative paths, because the decoded files live there rather
    than under *tl_dir*.

    Used only when the template ships compiled-only.  The scratch tree is a
    temp directory keyed by the source path so repeated runs reuse the same
    decode, and nothing is ever written next to the game's own files.
    """
    import hashlib
    import tempfile

    from .decompile import decompile_dir

    key = hashlib.sha1(os.path.abspath(tl_dir).encode("utf-8")).hexdigest()[:12]
    scratch = os.path.join(tempfile.gettempdir(), "renpy-toolkit-tl", key)
    os.makedirs(scratch, exist_ok=True)
    try:
        outcome = decompile_dir(tl_dir, dest=scratch, overwrite=True)
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same news
        result.warnings.append(f"decompile failed: {exc}")
        return scratch, []
    if outcome.failed:
        result.warnings.append(
            f"{len(outcome.failed)} .rpyc could not be decompiled "
            "(those files will be missing from the new tl/ tree)"
        )
    result.warnings.append(f"template was compiled-only; decoded into {scratch}")
    return scratch, _template_files(scratch)


def _retarget(tl: TlFile, language: str) -> tuple[int, int]:
    """Rewrite one parsed template file to *language* with empty translations.

    Returns ``(blocks, strings)``.  The header is rewritten in place so the
    template's indentation and blank-line layout survive untouched -- a
    reviewer diffing the new file against the template should see only the
    language name and the emptied text.

    Emptying goes through :func:`tl.apply_new_text` rather than poking at
    ``SayLine.text``: the writer renders from the parsed text *through* that
    path (it re-derives the raw line and its quoting), so assigning the field
    directly would silently write the template's original translation back.
    """
    from .tl import _set_string_new, apply_new_text

    blocks = strings = 0
    header = re.compile(r"^(\s*translate\s+)(\S+)(\s)")
    for block in list(tl.blocks):
        if block.kind not in ("translate", "strings"):
            continue
        raw = tl.lines[block.lineno - 1]
        match = header.match(raw.text)
        if match:
            rewritten = (
                match.group(1) + language + match.group(3) + raw.text[match.end():]
            )
            tl.lines[block.lineno - 1] = type(raw)(rewritten, raw.eol)
        if block.kind == "translate":
            if block.identifier and not block.deleted:
                # A duplicated identifier appears several times in one file and
                # ``apply_new_text`` defaults to the first occurrence, so pass
                # this block's own rank -- otherwise every copy after the first
                # silently keeps the template's translation.
                siblings = tl.find(block.identifier)
                rank = next(
                    (i for i, other in enumerate(siblings) if other is block), 0
                )
                apply_new_text(tl, block.identifier, "", index=rank)
                blocks += 1
        elif block.kind == "strings":
            for pair in block.strings:
                if pair.old and pair.new_lineno is not None:
                    _set_string_new(tl, pair, "")
                    strings += 1
    return blocks, strings


def plan_fresh_tl(
    basedir: str,
    language: str,
    *,
    source: str | None = None,
    force: bool = False,
    dry_run: bool = True,
) -> FreshTlResult:
    """Build ``game/tl/<language>/`` from the best available template.

    *source* is an explicit ``tl/<lang>`` directory to copy the shape from.
    When omitted the choice is automatic: another language's TL directory if
    the game ships one, else ``tl/None``.  Only *.rpy*/*.rpym* text is used --
    a ``.rpyc`` template is decompiled first if nothing else is available.

    Existing files in the target directory are **never** overwritten: a
    half-finished translation is the most expensive thing in the project, and
    re-running this command after a game update must be safe.  *force* is the
    explicit override.
    """
    target_dir = translate_dir_for(basedir, language)
    result = FreshTlResult(language=language, target_dir=target_dir)
    tl_root = os.path.dirname(target_dir)

    if source is None:
        candidates = [
            d
            for d in _existing_languages(tl_root)
            if d.lower() != language.lower() and os.path.isdir(os.path.join(tl_root, d))
        ]
        # Prefer a real translation over the None pseudo-language, and prefer
        # one with the most files: that is the one most likely to be complete.
        candidates.sort(key=lambda d: (d == "None", -len(_template_files(os.path.join(tl_root, d)))))
        if candidates:
            source = os.path.join(tl_root, candidates[0])
            result.source = f"tl/{candidates[0]}"
    else:
        source = os.path.abspath(source)
        result.source = source

    if source is None or not os.path.isdir(source):
        raise ToolkitError(
            f"{basedir}: no tl/ template to clone from. Install the Ren'Py SDK "
            f"and run `renpy <basedir> translate {language}`, or pass --source."
        )

    template_root = source
    sources = _template_files(source)
    if not sources:
        # A shipped game usually keeps only ``.rpyc``.  Decompiling into the
        # game tree would litter it with files the game then tries to load, so
        # the text is materialised in a scratch directory instead and used as
        # the template from there.  ``template_root`` moves with it, otherwise
        # the relative paths below would come out as ``../../..``.
        template_root, sources = _decompiled_template(source, result)
    if not sources:
        raise ToolkitError(
            f"{source}: no .rpy/.rpym text to clone, and nothing decompilable. "
            "Pass --source pointing at a real tl/ tree."
        )

    for path in sources:
        rel = os.path.relpath(path, template_root)
        target = os.path.join(target_dir, rel)
        exists = os.path.exists(target)
        if exists and not force:
            result.skipped_existing.append(target)
            continue
        tl = parse_tl_file(path)
        blocks, strings = _retarget(tl, language)
        if blocks == 0 and strings == 0:
            # ``translate python``/``style`` blocks carry no text; cloning them
            # would copy another language's code into ours.  Skip.
            continue
        # ``files`` lists what a *write* run actually wrote.  A dry run
        # reports the same list minus the writes, so callers can log a real
        # plan without mistaking it for work already done; ``planned`` keeps
        # both runs comparable.
        result.planned.append(target)
        if dry_run:
            result.blocks += blocks
            result.strings += strings
            continue
        result.files.append(target)
        result.blocks += blocks
        result.strings += strings
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # ``tl`` was parsed from the *template*, not from *target*; retarget
        # its path so ``TlFile.write`` does not try to re-read ``target``
        # (which does not exist yet on the first run).
        tl.path = target
        # ``backup`` only matters when *target* already exists, which is exactly
        # the ``--force`` path: without it a re-run silently destroys whatever
        # had been translated in that file.  ``TlFile.write`` skips the copy when
        # there is nothing to preserve, so this is free on a first run.
        tl.write(backup=exists)

    if not result.planned:
        result.warnings.append(
            "nothing to write -- every template file was already present"
            if result.skipped_existing
            else "template contained no translatable text"
        )
    return result
