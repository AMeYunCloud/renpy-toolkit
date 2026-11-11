"""游戏工程健康检查（「制作」一侧的静态体检）。

`renpy <dir> lint` 只覆盖脚本层面且要跑引擎。本模块做**不需要引擎**的部分：

- 图像引用 vs 磁盘文件（找不到的 `show mystery`）
- 孤儿归档/孤儿 `.rpyc`/失效缓存
- 中文项目特有的可移植性问题（非 ASCII 文件名/路径、CRLF 混用）
- 定义在多个文件里的 `image` 冲突

刻意不做：语法检查、类型检查、字符标签拼写。那些交给 `sdk lint`
（见 `toolkit.sdk`），因为官方 lint 是唯一权威。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .errors import ToolkitError
from .rpa import detect, is_rpa

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".bmp", ".gif"}
AUDIO_EXTENSIONS = {".opus", ".ogg", ".mp3", ".wav", ".m4a", ".flac"}
MOVIE_EXTENSIONS = {".mp4", ".webm", ".ogv", ".mkv"}

#: `image x = "path.png"` / `image x y = ConditionSwitch(...)`
IMAGE_STMT_RE = re.compile(r"^\s*image\s+([A-Za-z0-9_ ]+?)\s*=\s*(.+?)\s*$")
#: `show <name> [at ...] [as ...] [with ...] [behind ...]`
SHOW_RE = re.compile(
    r"^\s*show\s+(?:expression\s+)?"
    r"[\"']?([A-Za-z0-9_ /]+?)[\"']?(?:\s+at\s+\S+|\s+as\s+\S+|\s+with\s+\S+|\s+behind\s+\S+|\s+zorder\s+\S+)*\s*$"
)
#: `scene <name>` / `hide <name>`
SIMPLE_NAME_RE = re.compile(r"^\s*(scene|hide)\s+([A-Za-z0-9_ ]+?)\s*$")
ARCHIVE_EXTENSIONS = (".rpa", ".rpi")

#: Trailing clauses of a show/scene/hide statement. They are attributes, not
#: part of the tag: `scene black with fade` shows the tag ``black``.
_SHOW_CLAUSE_RE = re.compile(
    r"\s+(?:at|as|with|behind|zorder|onlayer)\s+\S+"
    r"(?:\s*,\s*\S+)*\s*$"
)
#: Tags the engine itself provides (``scene black`` needs no image file) *and*
#: every tag defined in Ren'Py's own ``renpy/common`` -- those definitions are
#: not part of the project's source tree, so without this they look missing.
IMAGE_TAG_STOPWORDS = frozenset({"black", "text", "bg", "white", "solid"})

#: Above this many unresolved image names with zero image files on disk, the
#: project is assumed to be a scripts-only working copy and the findings are
#: collapsed into one. Below it, each name is reported: a real build that
#: dropped a handful of images should hear about every one of them.
_ARTLESS_THRESHOLD = 25



def image_tag(rest: str) -> str:
    """Return the image tag a show/scene/hide statement actually addresses.

    Ren'Py splits an image name on whitespace into *tag* + *attributes*, and
    the tag is what has to be defined; ``scene black zorder 0`` addresses the
    tag ``black`` with attribute ``zorder``, it is not an image called
    ``black zorder 0``.
    """
    name = rest.strip()
    while True:
        trimmed = _SHOW_CLAUSE_RE.sub("", name)
        if trimmed == name:
            break
        name = trimmed
    return " ".join(name.split())


@dataclass
class Issue:
    project: str
    category: str
    path: str
    detail: str

    @property
    def severity(self) -> str:
        return _SEVERITY.get(self.category, "warning")


_SEVERITY = {
    "missing-image": "error",
    "missing-file": "error",
    "non-ascii-path": "warning",
    "orphan-rpa": "warning",
    "orphan-rpyc": "warning",
    "duplicate-image": "warning",
    "crlf": "info",
    "empty-string": "warning",
    # Archives winning over loose files silently disables a translation patch.
    "force-archives": "error",
    # Ren'Py raises at import time on a duplicate `old`.
    "duplicate-translation": "error",
    # Strings registered from code are invisible to tl gen, so the coverage
    # number is an upper bound rather than an error.
    "runtime-strings": "warning",
}


@dataclass
class ProjectReport:
    project: str
    issues: list[Issue] = field(default_factory=list)
    counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.ok:
            return f"{self.project}: OK ({len(self.warnings)} warning(s))"
        return (
            f"{self.project}: {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s)"
        )

    def text(self) -> str:
        lines = [self.summary()]
        by_cat: dict[str, list[Issue]] = {}
        for item in self.issues:
            by_cat.setdefault(item.category, []).append(item)
        for category in sorted(by_cat):
            lines.append(f"\n[{category}] ({_SEVERITY.get(category, 'warning')})")
            for item in by_cat[category]:
                lines.append(f"  {item.path}: {item.detail}")
        return "\n".join(lines)


def _strip_comment(line: str) -> str:
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and i + 1 < len(line):
                out.append(ch)
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            out.append(ch)
        else:
            if ch == "#":
                break
            if ch in "\"'":
                quote = ch
            out.append(ch)
        i += 1
    return "".join(out)


def _split_paths(value: str) -> list[str]:
    """Split a Ren'Py expression into probable literal file paths."""
    found = []
    for match in re.findall(r"[\"']([^\"']+)[\"']", value):
        if "/" in match or "." in match:
            found.append(match)
    return found


def scan_project(
    project_dir: str,
    *,
    include_archives: bool = True,
    check_lf: bool = False,
) -> ProjectReport:
    """Static health check of a Ren'Py project rooted at *project_dir*.

    *project_dir* is the directory that holds ``options.rpy``/``script.rpy``
    (the SDK calls it ``<project>`` when you run ``renpy <project>``).
    """
    if not os.path.isdir(project_dir):
        raise ToolkitError(f"{project_dir} is not a directory")
    report = ProjectReport(project=os.path.abspath(project_dir))

    game_dirs = _game_dirs(project_dir)
    sources: list[str] = []
    manifest_files: list[str] = []
    for base in game_dirs:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for filename in filenames:
                if filename.lower().endswith((".rpy", ".rpym")):
                    manifest_files.append(os.path.join(dirpath, filename))
                sources.append(os.path.join(dirpath, filename))

    engine_tags = _engine_image_tags(manifest_files)

    # Built once: the image checks below ask "does this path exist" and "is
    # there an image with this stem" for every definition and every show, and
    # scanning the file list per question is O(files x statements).
    source_index = _SourceIndex(sources)

    image_defs: dict[str, list[str]] = {}
    image_uses: dict[str, list[str]] = {}

    for rel in _rpy_files(project_dir, game_dirs):
        text = _read_text(rel)
        if not text:
            continue
        for lineno, raw in enumerate(text.split("\n"), start=1):
            body = _strip_comment(raw)
            if not body.strip():
                continue
            match = IMAGE_STMT_RE.match(body)
            if match:
                name = match.group(1).strip()
                image_defs.setdefault(name, []).append(f"{rel}:{lineno}")
                store = match.group(2)
                for path in _split_paths(store):
                    if not _exists_anywhere(path, project_dir, source_index):
                        report.issues.append(Issue(
                            report.project, "missing-file", f"{rel}:{lineno}",
                            f"image {name!r} points at missing file {path!r}",
                        ))
                continue
            match = SHOW_RE.match(body)
            if match:
                image_uses.setdefault(image_tag(match.group(1)), []).append(
                    f"{rel}:{lineno}"
                )
                continue
            match = SIMPLE_NAME_RE.match(body)
            if match:
                image_uses.setdefault(image_tag(match.group(2)), []).append(
                    f"{rel}:{lineno}"
                )

    # Resolve every referenced image name first, so the "is this even checkable"
    # decision below can be made on the unresolved set rather than per name.
    unresolved: dict[str, str] = {}
    for name, uses in sorted(image_uses.items()):
        if name in IMAGE_TAG_STOPWORDS or name in engine_tags:
            # engine-provided names, no file expected
            continue
        if name in image_defs:
            continue
        if _image_file_exists(name, source_index):
            continue
        unresolved[name] = uses[0]

    # A scripts-only working copy (the usual layout for a translation project
    # built beside the released game) keeps the ``.rpy`` files but none of the
    # art, so every ``show`` looks broken. Reporting 20,000 findings there is
    # noise: nothing is wrong with the scripts, the assets were simply never
    # copied. Say it once -- but only when the count says so, so a real build
    # that forgot one image still gets a per-name finding.
    if not source_index.image_stems and len(unresolved) > _ARTLESS_THRESHOLD:
        report.issues.append(Issue(
            report.project, "missing-image", next(iter(unresolved.values())),
            f"no image files exist under game/ (a scripts-only working copy?), "
            f"so the {len(unresolved)} image name(s) these scripts use cannot "
            "be verified here; run this check against the full build instead",
        ))
    else:
        for name, where in unresolved.items():
            report.issues.append(Issue(
                report.project, "missing-image", where,
                f"show/scene/hide references {name!r} but no image definition or "
                "matching file exists",
            ))

    for name, sites in sorted(image_defs.items()):
        if len(sites) > 1:
            report.issues.append(Issue(
                report.project, "duplicate-image", sites[1],
                f"image {name!r} defined {len(sites)} times "
                f"(also {', '.join(sites[:-1])})",
            ))

    if include_archives:
        _audit_archives(project_dir, game_dirs, sources, report)

    if check_lf:
        _audit_line_endings(_rpy_files(project_dir, game_dirs), report)

    _audit_force_archives(project_dir, manifest_files, report)
    _audit_translation_duplicates(project_dir, report)
    _audit_runtime_strings(project_dir, manifest_files, report)

    counts = {
        "rpy_files": sum(1 for _ in _rpy_files(project_dir, game_dirs)),
        "archives": sum(1 for p in sources if p.lower().endswith(ARCHIVE_EXTENSIONS)),
        "images": sum(1 for p in sources if os.path.splitext(p)[1].lower()
                      in IMAGE_EXTENSIONS),
        "issues": len(report.issues),
    }
    report.counts = counts
    return report


def _engine_image_tags(rpy_files: Iterable[str]) -> frozenset[str]:
    """Image tags defined in Ren'Py's own ``renpy/common`` sources.

    ``renpy/common/00gltest.rpy`` ships with the engine and defines images the
    project never declares, so a project-only scan reports them as missing.
    """
    tags: set[str] = set()
    for path in rpy_files:
        if f"{os.sep}renpy{os.sep}common{os.sep}" not in path:
            continue
        for raw in _read_text(path).split("\n"):
            match = IMAGE_STMT_RE.match(_strip_comment(raw))
            if match:
                tags.add(image_tag(match.group(1)))
    return frozenset(tags)


def _audit_archives(
    project_dir: str,
    game_dirs: list[str],
    sources: list[str],
    report: ProjectReport,
) -> None:
    """Cross-check loose files against what the archives already contain."""
    from .rpa import RenPyArchive  # local import: only needed when enabled

    archive_paths = [p for p in sources if is_rpa(p)]
    loose_rel = {
        os.path.relpath(p, os.path.dirname(a)).replace("\\", "/")
        for a in archive_paths
        for p in sources
        if not is_rpa(p) and os.path.dirname(p) == os.path.dirname(a)
    }

    for path in archive_paths:
        if detect(path) == 1:
            report.issues.append(Issue(
                report.project, "orphan-rpa", path,
                "RPAv1 (.rpi) is recognised but not readable by this toolkit",
            ))
            continue
        try:
            with RenPyArchive(path) as archive:
                names = archive.names()
                overlapping = [n for n in names if n in loose_rel]
                if overlapping:
                    sample = overlapping[0]
                    report.issues.append(Issue(
                        report.project, "orphan-rpa", f"{path}: {sample}",
                        f"{len(overlapping)} file(s) exist both loose and in "
                        "the archive; the loose copy wins, so the archived "
                        "copy is dead weight",
                    ))
        except Exception as exc:  # noqa: BLE001 - report, never abort the scan
            report.issues.append(Issue(
                report.project, "orphan-rpa", path, f"unreadable archive: {exc}"
            ))


def _audit_line_endings(rpy_files: Iterable[str], report: ProjectReport) -> None:
    """Flag CRLF files: mixing endings inside Ren'Py source hurts diffs.

    ``new``/``old`` strings and translate blocks are line oriented, so a file
    that switches endings mid-way will produce confusing blame in a TL repo.
    """
    for path in rpy_files:
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        crlf = raw.count(b"\r\n")
        lf = raw.count(b"\n") - crlf
        if crlf and lf:
            report.issues.append(Issue(
                report.project, "crlf", path,
                f"mixed line endings ({crlf} CRLF, {lf} LF)",
            ))


def _game_dirs(project_dir: str) -> list[str]:
    """Directories that can hold game content.

    Ren'Py supports a project root holding the files directly (what our
    scaffold generates) and the classic ``<project>/game`` layout.

    The two are alternatives, not a union. Walking the project root *as well*
    as ``<project>/game`` swept in everything else that lives beside the game
    directory -- and a translation working copy legitimately keeps notes,
    screenshots and probe output there. On the reference project that pulled in
    8,285 files instead of 228, made the scan take 40 s instead of 5 s, and
    filled the image-name index with screenshot stems so real game images
    looked absent.
    """
    nested = os.path.join(project_dir, "game")
    if os.path.isdir(nested):
        return [nested]
    return [project_dir]


def _rpy_files(project_dir: str, game_dirs: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for base in game_dirs:
        for dirpath, dirnames, _filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d != "__pycache__"]
            for filename in sorted(os.listdir(dirpath)):
                if not filename.endswith(".rpy"):
                    continue
                full = os.path.join(dirpath, filename)
                if full not in seen:
                    seen.add(full)
                    out.append(full)
    return out


def _read_text(path: str) -> str:
    with open(path, "rb") as handle:
        raw = handle.read()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _exists_anywhere(rel: str, project_dir: str, sources: list[str]) -> bool:
    """Is *rel* present on disk (in any game dir) or inside an archive?

    *sources* may be a :class:`_SourceIndex`; passing the plain list rebuilds
    the index on every call, which is O(n) and was the reason a 2,500-file game
    took minutes. See :class:`_SourceIndex`.
    """
    index = sources if isinstance(sources, _SourceIndex) else _SourceIndex(sources)
    key = rel.strip("./").replace("\\", "/")
    if index.has_suffix(key):
        return True
    # archives are not expanded here; callers that care pass include_archives
    return os.path.isfile(os.path.join(project_dir, rel))


def _image_file_exists(name: str, sources: list[str]) -> bool:
    index = sources if isinstance(sources, _SourceIndex) else _SourceIndex(sources)
    return name.strip() in index.image_stems


class _SourceIndex:
    """Constant-time lookups over the project's file list.

    ``scan_project`` walks every file in ``game/`` (2,546 of them, 4.6 GB, in the
    release this was measured on) and then asks two questions about the
    resulting list for *every* image definition and *every* ``show`` statement.
    Answering them by scanning the list each time is O(files x statements):
    the 58,249-statement game took minutes and had to be killed. This builds
    the two lookup tables once instead.

    ``has_suffix`` keeps the original ``str.endswith`` semantics by recording
    every path suffix that starts at a component boundary. That is what a
    Ren'Py file reference can be (``images/bg/room.png`` or ``bg/room.png`` or
    ``room.png``); a suffix starting mid-component (``room.png`` inside
    ``bigroom.png``) was matched by the old code by accident and is not a real
    reference.
    """

    __slots__ = ("_suffixes", "image_stems")

    def __init__(self, sources: Iterable[str]) -> None:
        suffixes: set[str] = set()
        stems: set[str] = set()
        for src in sources:
            norm = src.replace("\\", "/")
            parts = norm.split("/")
            for i in range(len(parts)):
                suffixes.add("/".join(parts[i:]))
            base = parts[-1]
            stem, ext = os.path.splitext(base)
            if ext.lower() in IMAGE_EXTENSIONS:
                stems.add(stem)
        self._suffixes = suffixes
        self.image_stems = stems

    def has_suffix(self, key: str) -> bool:
        return key in self._suffixes


#: ``config.force_archives = True`` in any spelling Ren'Py accepts.
_FORCE_ARCHIVES_RE = re.compile(
    r"\bconfig\s*\.\s*force_archives\s*=\s*True\b"
)

#: Any mention of the runtime-string hook: assigning it, appending a callback
#: to it, or reading it. Appending is the documented way to use it, so matching
#: only ``=`` would miss the common case.
_RUNTIME_STRINGS_RE = re.compile(
    r"\bconfig\s*\.\s*translate_additional_strings_callbacks\b"
)


def _audit_force_archives(
    project_dir: str, rpy_files: Iterable[str], report: ProjectReport
) -> None:
    """Warn when loose files would be ignored because archives win.

    ``renpy/loader.py:97`` comments the precedence out loud::

        # Files on disk should be checked before archives. Otherwise, among
        # other things, using a new version of bytecode.rpyb will break.

    and ``load_from_filesystem()`` honours it via
    ``if not renpy.config.force_archives:``. Setting that flag inverts the
    order, so a loose ``game/tl/<lang>/*.rpy`` translation -- exactly what this
    toolkit writes -- is silently ignored in favour of whatever the shipped
    ``archive.rpa`` contains.

    This is not hypothetical: a 2.3 GB ``archive.rpa`` beside the scripts is
    normal for a distributed build, and the flag has no other visible effect,
    so it is easy to add and impossible to notice.
    """
    archives = [
        os.path.join(dirpath, name)
        for dirpath, _dirnames, filenames in os.walk(project_dir)
        for name in filenames
        if name.lower().endswith(ARCHIVE_EXTENSIONS)
    ]
    hits: list[str] = []
    for path in rpy_files:
        text = _read_text(path)
        if not text:
            continue
        for lineno, raw in enumerate(text.split("\n"), start=1):
            if _FORCE_ARCHIVES_RE.search(_strip_comment(raw)):
                hits.append(f"{path}:{lineno}")
    if not hits:
        return
    where = f"{len(archives)} archive(s) present" if archives else "no archive present"
    report.issues.append(Issue(
        report.project, "force-archives", hits[0],
        "config.force_archives = True makes archives win over loose files "
        f"({where}); translation files written next to the game would be "
        "ignored -- set it back to False to ship a loose-file patch",
    ))


def _audit_translation_duplicates(
    project_dir: str, report: ProjectReport
) -> None:
    """Reject the duplicate ``old`` values that make Ren'Py raise on startup.

    ``renpy/translation/__init__.py:528`` in ``StringTranslator.add()``::

        if old in self.translations:
            raise Exception("A translation for \\"{}\\" already exists at {}:{}.")

    and ``add_string_translation`` is reached from ``translate <lang> strings:``
    blocks *and* from wherever the engine merges them, so the same English key
    declared twice within one language aborts the game before the main menu
    appears. Which duplicate wins is not defined either, so this cannot be
    dismissed as a warning.

    The key is ``(language, old)``, not ``old``: each language owns a separate
    ``StringTranslator`` in ``translator.strings``, so repeating a key across
    ``tl/chinese`` and ``tl/spanish`` is legal and must not be reported.

    Appending to an existing file is the usual way to hit it: a generator that
    is not idempotent writes the same block twice and the build only breaks on
    the player's machine.
    """
    from .tl import TLParseError, parse_tl_file  # local import: avoid a cycle

    # Ren'Py accepts both `<project>/tl` and the classic `<project>/game/tl`,
    # and the shipped games use the latter, so both must be searched.
    tl_roots = [os.path.join(base, "tl") for base in _game_dirs(project_dir)]
    tl_roots = [p for p in tl_roots if os.path.isdir(p)]
    if not tl_roots:
        return
    seen: dict[tuple[str, str], str] = {}
    for path in sorted(_rpy_files(project_dir, tl_roots)):
        try:
            doc = parse_tl_file(path)
        except (TLParseError, OSError, UnicodeDecodeError):
            # Not a translation file (or unreadable): tl verify reports those.
            continue
        for block in doc.blocks:
            # `translate <lang> strings:` -- the language is part of the key.
            language = block.language or ""
            for pair in block.strings:
                where = f"{path}:{pair.old_lineno or pair.lineno}"
                key = (language, pair.old)
                first = seen.get(key)
                if first is None:
                    seen[key] = where
                    continue
                if first == where:
                    continue
                report.issues.append(Issue(
                    report.project, "duplicate-translation", where,
                    f"old {pair.old!r} is already translated at {first} "
                    f"(same language {language!r}); "
                    "Ren'Py raises \"A translation for ... already exists\" "
                    "unless the two are collapsed into one",
                ))


def _audit_runtime_strings(
    project_dir: str, rpy_files: Iterable[str], report: ProjectReport
) -> None:
    """Flag string sources no static scan can see.

    ``config.translate_additional_strings_callbacks`` is documented as "a list
    of callbacks that are called when the translation system is searching for
    strings ... each callback is expected to return an iterable of (filename,
    linenumber, string) tuples". A game using it registers strings from code,
    so ``tl gen`` -- which reads the compiled statement graph -- never sees
    them, and the finished translation silently falls back to English for those
    keys. Nothing in the generated files hints at the gap, which is why this is
    an audit finding rather than a note in the docs.

    Severity is ``warning``: the game still runs and everything the scan *can*
    see is translated; the point is that the coverage number is an upper bound.
    """
    hits: list[str] = []
    for path in rpy_files:
        text = _read_text(path)
        if not text:
            continue
        for lineno, raw in enumerate(text.split("\n"), start=1):
            line = _strip_comment(raw)
            if _RUNTIME_STRINGS_RE.search(line):
                hits.append(f"{path}:{lineno}")
    if not hits:
        return
    report.issues.append(Issue(
        report.project, "runtime-strings", hits[0],
        "config.translate_additional_strings_callbacks registers strings at "
        "run time; a static scan cannot see them, so those keys stay "
        "untranslated unless they are added to the translation by hand "
        f"({len(hits)} call site(s))",
    ))


__all__ = [
    "ARCHIVE_EXTENSIONS",
    "AUDIO_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "MOVIE_EXTENSIONS",
    "Issue",
    "ProjectReport",
    "scan_project",
]
