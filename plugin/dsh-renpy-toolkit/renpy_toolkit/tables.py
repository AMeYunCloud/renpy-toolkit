"""翻译表格的导入/导出：JSON / CSV / TSV / PO / XLSX。

为什么要自己写
--------------
官方给的中转格式只有 ``extract_strings`` / ``merge_strings`` 的 JSON，而且
它**只覆盖 dialogue 与 strings**，不覆盖图像/样式/非标准写法（见
docs/renpy-translation-ecosystem.md §2）。汉化组实际用的是 Excel 或
Google Sheets 排表、按行列校对，所以一条可用的流水线必须有：

    tl/<lang>/*.rpy  ──export──▶  .csv/.xlsx/.po/.json  ──人/机翻──▶  ──import──▶  回写 .rpy

其中 **PO 是生态空白**（调研结论：没有任何被广泛引用的 gettext 桥接），
所以 :func:`export_po` / :func:`import_po` 从零实现，严格按 gettext 语义把
Ren'Py 的两套东西映射过去：

============================  ==========================  =====================
Ren'Py                        gettext                    说明
============================  ==========================  =====================
``translate <l> <ident>:``    一条 entry                  ``msgctxt`` = ident
镜像注释 ``# e "原文"``        ``msgid``                  带文本标签的原文，原样
``e "译 文"``                 ``msgstr``                 含 ``\\n`` 等转义
``translate <l> strings:``    一条 entry                  ``msgctxt`` = ``strings``
``old "..."``                 ``msgid``                  ``{#ctx}`` 剥到 ctx 字段
``new "..."``                 ``msgstr``                 —
源文件:行号                   ``#: game/script.rpy:95``   参考注释
============================  ==========================  =====================

**关键约束（踩过的坑，写在这里防止回退）**：

* ``msgid`` 必须与 TL 文件里 ``# e "..."`` 的**字面量完全一致**，包括
  ``{size=+4}{w}`` 这类标签和 ``\\"`` 转义 —— 多一个标签、少一个标签都永不命中。
* 含 ``\\n`` 的译文必须以字面 ``\\n`` 写回，不能是真换行（否则劈行，
  触发 ``MISSING_TRANS_LINE`` / ``UNCLOSED_QUOTE``）。
* 含 ``\\"`` 的值回写时必须用 raw string 形态，否则二次转义。
* 文件编码/BOM/行尾由 :mod:`renpy_toolkit.tl` 负责保留，本模块只搬文本。

依赖：仅标准库；``.xlsx`` 需要可选的 ``openpyxl``（缺失时抛
:class:`renpy_toolkit.errors.DependencyError`，消息带 pip 命令）。
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from .errors import DependencyError, SerializationError, TableError
from .tl import (
    StringPair,
    TlBlock,
    TlFile,
    _set_string_new,
    apply_new_text,
    parse_tl_dir,
)

__all__ = [
    "COLUMNS",
    "Row",
    "TableFormat",
    "apply_rows",
    "collect_rows",
    "detect_format",
    "export_table",
    "export_po",
    "import_po",
    "import_table",
    "read_table",
    "write_table",
]

#: The fixed column order every table format uses. ``id`` is the stable key.
COLUMNS = (
    "id",          # translate 标识符，或 strings 的 "#N" 序号
    "kind",        # translate | strings
    "file",        # tl 文件相对路径
    "line",        # 行号
    "context",     # strings 的 {#context}，或 translate 的说话人
    "source",      # 原文（含标签，逐字节与 TL 文件一致）
    "target",      # 译文（空 = 未翻）
)

TableFormat = str  # "json" | "csv" | "tsv" | "po" | "xlsx"

_EXPORT_FORMATS = ("json", "csv", "tsv", "po", "xlsx")
_IMPORT_FORMATS = ("json", "csv", "tsv", "po", "xlsx")


@dataclass
class Row:
    """One translatable unit, as it travels through a spreadsheet."""

    id: str
    kind: str
    file: str
    line: int
    context: str
    source: str
    target: str = ""
    #: Index among same-id blocks (translate 标识符重复时用序号消歧)
    index: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "context": self.context,
            "source": self.source,
            "target": self.target,
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Row":
        try:
            return cls(
                id=str(data["id"]),
                kind=str(data.get("kind") or "translate"),
                file=str(data.get("file") or ""),
                line=int(data.get("line") or 0),
                context=str(data.get("context") or ""),
                source=str(data["source"]),
                target=str(data.get("target") or ""),
                index=int(data.get("index") or 0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SerializationError(f"bad row {data!r}: {exc}") from exc

    @property
    def empty(self) -> bool:
        return self.target.strip() == ""


# ---------------------------------------------------------------------------
# 提取：tl 目录 -> Row 列表
# ---------------------------------------------------------------------------
def _rel(path: str, base: str) -> str:
    try:
        return os.path.relpath(path, base).replace("\\", "/")
    except ValueError:  # 不同盘符
        return path.replace("\\", "/")


def _string_id(pair: StringPair, ordinal: int) -> str:
    return f"strings#{ordinal}"


def collect_rows(tl_dir: str, *, language: str | None = None) -> list[Row]:
    """Walk ``tl_dir`` and return every translatable unit as a :class:`Row`.

    * ``translate`` blocks contribute one row per live say line; the row id is
      the block identifier (``start_636ae3f5``), disambiguated by ``index``
      when an identifier repeats (Ren'Py appends a sequence number itself, but
      hand-edited files may not).
    * ``strings`` blocks contribute one row per ``old``/``new`` pair, id
      ``strings#N``. The ``{#context}`` prefix is split into ``context`` and
      kept out of ``source`` — that is what a translator needs to see.
    * Blocks deleted with ``pass`` are skipped: they are a deliberate
      "do not translate this" marker.
    """
    rows: list[Row] = []
    for tl in parse_tl_dir(tl_dir):
        file_rel = _rel(tl.path, tl_dir)
        seen: dict[str, int] = {}
        for block in tl.blocks:
            if language is not None and block.language not in (None, language):
                continue
            if block.kind == "translate":
                if block.deleted:
                    continue
                if not block.identifier:
                    continue
                ident = block.identifier
                idx = seen.get(ident, 0)
                seen[ident] = idx + 1
                aligned = block.aligned()
                src_for: dict[int, str] = {id(dst): src for src, dst in aligned}
                for line in block.say_lines:
                    if not line.is_say:
                        continue
                    source = src_for.get(id(line), "")
                    if not source:
                        # 没有镜像注释就没有原文，无法翻译，跳过而不是瞎猜
                        continue
                    rows.append(
                        Row(
                            id=ident,
                            kind="translate",
                            file=file_rel,
                            line=line.lineno,
                            context=line.who,
                            source=source,
                            target=line.text,
                            index=idx,
                        )
                    )
            elif block.kind == "strings":
                for n, pair in enumerate(block.strings):
                    rows.append(
                        Row(
                            id=_string_id(pair, n),
                            kind="strings",
                            file=file_rel,
                            line=pair.old_lineno,
                            context=pair.context or "",
                            source=_strip_context_prefix(pair.old),
                            target=pair.new or "",
                            index=n,
                        )
                    )
    return rows


def _strip_context_prefix(old: str) -> str:
    """``"{#gui}Select"`` -> ``"Select"``; the context travels separately."""
    if old.startswith("{#"):
        end = old.find("}")
        if end > 2:
            return old[end + 1 :]
    return old


def _with_context_prefix(context: str, text: str) -> str:
    if context:
        return f"{{#{context}}}{text}"
    return text


# ---------------------------------------------------------------------------
# 格式探测
# ---------------------------------------------------------------------------
_EXT_MAP = {
    ".json": "json",
    ".csv": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".po": "po",
    ".pot": "po",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
}


def detect_format(path: str, *, override: str | None = None) -> str:
    """Infer the table format from a path (or trust *override*)."""
    if override:
        fmt = override.lower().lstrip(".")
        if fmt not in _EXPORT_FORMATS:
            raise TableError(
                f"unsupported table format {override!r}; "
                f"choose one of {', '.join(_EXPORT_FORMATS)}"
            )
        return fmt
    ext = os.path.splitext(path)[1].lower()
    fmt = _EXT_MAP.get(ext)
    if fmt is None:
        raise TableError(
            f"cannot infer table format from {path!r}; "
            f"pass --format one of {', '.join(_EXPORT_FORMATS)}"
        )
    return fmt


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def _export_json(rows: Sequence[Row]) -> bytes:
    payload = {
        "columns": list(COLUMNS),
        "count": len(rows),
        "rows": [r.as_dict() for r in rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _import_json(data: bytes) -> list[Row]:
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SerializationError(f"invalid JSON table: {exc}") from exc
    if isinstance(payload, list):
        raw_rows = payload
    elif isinstance(payload, dict) and "rows" in payload:
        raw_rows = payload["rows"]
    elif isinstance(payload, dict) and "strings" in payload:
        # 兼容官方 extract_strings 的形态：{"strings": {ident: text}} 或
        # {"strings": [...]}
        raw_rows = _rows_from_official_json(payload["strings"])
    else:
        raise SerializationError(
            'JSON table must be a list, or an object with "rows"/"strings"'
        )
    if not isinstance(raw_rows, list):
        raise SerializationError("JSON table 'rows' must be a list")
    return [Row.from_dict(r) for r in raw_rows]


def _rows_from_official_json(strings: object) -> list[dict]:
    """Best-effort bridge for the official ``extract_strings`` shape.

    The official JSON is a mapping (or list) of identifier to translated text
    with no source text, so the row id is enough to write back; ``source`` is
    left empty and matched by id only.
    """
    out: list[dict] = []
    if isinstance(strings, dict):
        items = strings.items()
    elif isinstance(strings, list):
        items = []
        for entry in strings:
            if not isinstance(entry, dict):
                continue
            ident = entry.get("identifier") or entry.get("id")
            text = entry.get("new") or entry.get("text") or entry.get("target") or ""
            if ident is not None:
                items.append((ident, text))
    else:
        raise SerializationError('JSON "strings" must be an object or a list')
    for ident, text in items:
        out.append(
            {
                "id": str(ident),
                "kind": "translate",
                "source": "",
                "target": str(text or ""),
            }
        )
    return out


# ---------------------------------------------------------------------------
# CSV / TSV
# ---------------------------------------------------------------------------
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _defuse(value: object) -> object:
    """Stop a spreadsheet from evaluating a translated string as a formula.

    Game text is attacker-controlled: a line starting with ``=`` or ``@`` is a
    formula to Excel, and CSV/XLSX are exactly the formats people open there.
    A leading apostrophe is the conventional neutraliser -- the cell then shows
    the literal text.  Only string cells are touched, and only when they would
    otherwise be interpreted.
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _rearm(value: object) -> object:
    """Undo :func:`_defuse` when reading a table back.

    The inverse is exact for the values ``_defuse`` produced (``'`` followed by
    a formula-start character).  Text that genuinely begins with ``'=`` would
    lose its apostrophe -- vanishingly rare in game dialogue, and the
    alternative is an export Excel executes.
    """
    if isinstance(value, str) and len(value) > 1:
        if value[0] == "'" and value[1] in _FORMULA_START:
            return value[1:]
    return value


def _export_csv(rows: Sequence[Row], *, delimiter: str) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow(
            [
                _defuse(cell)
                for cell in (
                    row.id,
                    row.kind,
                    row.file,
                    row.line,
                    row.context,
                    row.source,
                    row.target,
                )
            ]
        )
    # BOM 让 Excel 正确识别 UTF-8（中文表交给 Excel 是主流工作流）
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _import_csv(data: bytes, *, delimiter: str) -> list[Row]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SerializationError(f"table is not UTF-8: {exc}") from exc
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if reader.fieldnames is None:
        raise SerializationError("table has no header row")
    missing = [c for c in ("id", "source", "target") if c not in reader.fieldnames]
    if missing:
        raise SerializationError(
            f"table is missing required column(s): {', '.join(missing)}; "
            f"expected header {','.join(COLUMNS)}"
        )
    rows: list[Row] = []
    for n, record in enumerate(reader, start=2):
        if not any((record.get(c) or "").strip() for c in reader.fieldnames):
            continue  # 空行
        record = {k: (v or "") for k, v in record.items() if k is not None}
        record.setdefault("kind", "translate")
        record.setdefault("index", "0")
        for field in ("source", "target"):
            if field in record:
                record[field] = str(_rearm(record[field]))
        try:
            rows.append(Row.from_dict(record))
        except SerializationError as exc:
            raise SerializationError(f"line {n}: {exc}") from exc
    return rows


# ---------------------------------------------------------------------------
# PO (gettext) —— 生态空白，自己实现
# ---------------------------------------------------------------------------
_PO_HEADER = (
    "msgid \"\"\n"
    "msgstr \"\"\n"
    "\"Project-Id-Version: renpy-toolkit\\n\"\n"
    "\"MIME-Version: 1.0\\n\"\n"
    "\"Content-Type: text/plain; charset=UTF-8\\n\"\n"
    "\"Content-Transfer-Encoding: 8bit\\n\"\n"
)


def _po_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )


def _po_unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\" or i + 1 >= len(text):
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "0": "\0"}
        out.append(mapping.get(nxt, "\\" + nxt))
        i += 2
    return "".join(out)


def _po_lines(field_name: str, value: str) -> list[str]:
    """Render one PO field, splitting on real newlines PO-style."""
    escaped = _po_escape(value)
    parts = escaped.split("\\n")
    if len(parts) == 1:
        return [f'{field_name} "{escaped}"']
    # 保留行尾的 \n：把它拼回每一段
    lines = [f'{field_name} ""']
    for n, part in enumerate(parts):
        suffix = "\\n" if n < len(parts) - 1 else ""
        lines.append(f'"{part}{suffix}"')
    return lines


def export_po(rows: Sequence[Row], *, language: str = "chinese") -> bytes:
    """Serialize *rows* as a gettext ``.po`` file.

    ``msgid`` is the exact source string (tags included), so a translation
    round-trips byte-for-byte through the TL file. ``msgctxt`` carries the
    Ren'Py identifier so that two identical English lines with different
    translations stay separate entries — the same problem ``{#context}``
    solves inside ``strings`` blocks.
    """
    chunks: list[str] = [
        f'# Ren\'Py translation export for language "{language}"\n',
        "# generated by renpy_toolkit.tables — regenerating overwrites this file\n",
        "#\n",
        "# msgctxt holds the Ren'Py identifier; keep it, it is the write-back key.\n",
        _PO_HEADER,
    ]
    for row in rows:
        ref = f"#: {row.file}:{row.line}"
        meta = [ref]
        if row.kind == "strings":
            meta.append(f"#. renpy-strings id={row.id}")
        if row.index:
            meta.append(f"#. index={row.index}")
        if row.context:
            meta.append(f"#. renpy-context={row.context}")
        body: list[str] = []
        # msgctxt 只放标识符本身：它是回写主键，上下文/序号放注释里，免得
        # 任何一方多写一个字符就永远配不上。
        body.extend(_po_lines("msgctxt", row.id))
        body.extend(_po_lines("msgid", row.source))
        body.extend(_po_lines("msgstr", row.target))
        chunks.append("\n".join(meta) + "\n" + "\n".join(body) + "\n\n")
    return "".join(chunks).encode("utf-8")


def import_po(data: bytes) -> list[Row]:
    """Parse a ``.po`` file back into rows.

    Tolerates the usual PO noise: ``#`` comments (including ``#~`` obsolete
    entries, which are skipped), fuzzy flags are *kept* (we do not want to
    silently drop a translator's work), and multi-line string continuations.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SerializationError(f"PO file is not UTF-8: {exc}") from exc

    entries: list[dict[str, object]] = []
    current: dict[str, object] = {}
    field: str | None = None
    obsolete = False

    def flush() -> None:
        nonlocal current, field, obsolete
        if current and not obsolete:
            if "msgid" in current and "msgstr" in current:
                entries.append(current)
        current = {}
        field = None
        obsolete = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line.startswith("#~"):
            obsolete = True
            continue
        if line.startswith("#"):
            if line.startswith("#:"):
                current["ref"] = line[2:].strip()
            elif line.startswith("#."):
                current["comment"] = line[2:].strip()
            continue
        if line.startswith('"'):
            # continuation of the previous field
            if field is None:
                raise SerializationError(f"string continuation without a field: {line!r}")
            current[field] = str(current.get(field, "")) + _po_extract(line)
            continue
        for name in ("msgctxt", "msgid_plural", "msgid", "msgstr"):
            if line.startswith(name):
                rest = line[len(name) :].strip()
                field = name
                current[name] = _po_extract(rest) if rest else ""
                break
        else:
            raise SerializationError(f"unrecognised PO line: {raw_line!r}")
    flush()

    rows: list[Row] = []
    for entry in entries:
        msgid = str(entry.get("msgid", ""))
        msgstr = str(entry.get("msgstr", ""))
        if msgid == "":
            continue  # 文件头
        ctxt = str(entry.get("msgctxt", ""))
        comment = str(entry.get("comment", ""))
        kind = "strings" if "renpy-strings" in comment or ctxt.startswith("strings#") else "translate"
        idx = 0
        if "index=" in comment:
            try:
                idx = int(comment.split("index=")[1].split()[0])
            except (IndexError, ValueError):
                idx = 0
        context = ""
        if "renpy-context=" in comment:
            context = comment.split("renpy-context=")[1].split("\n")[0].strip()
        ref = str(entry.get("ref", ""))
        file_part, _, line_part = ref.partition(":")
        try:
            lineno = int(line_part.split()[0]) if line_part else 0
        except ValueError:
            lineno = 0
        rows.append(
            Row(
                id=ctxt,
                kind=kind,
                file=file_part,
                line=lineno,
                context=context,
                source=msgid,
                target=msgstr,
                index=idx,
            )
        )
    return rows


def _po_extract(rest: str) -> str:
    """Pull the string out of ``"..."`` and unescape it."""
    rest = rest.strip()
    if not rest.startswith('"'):
        raise SerializationError(f"expected a quoted string, got {rest!r}")
    # 找收尾引号，跳过被转义的
    i = 1
    out: list[str] = []
    while i < len(rest):
        ch = rest[i]
        if ch == "\\" and i + 1 < len(rest):
            out.append(rest[i : i + 2])
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return _po_unescape("".join(out))


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------
def _export_xlsx(rows: Sequence[Row]) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("xlsx export", "openpyxl") from exc
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "translation"
    sheet.append(list(COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(
            [
                _defuse(cell)
                for cell in (
                    row.id,
                    row.kind,
                    row.file,
                    row.line,
                    row.context,
                    row.source,
                    row.target,
                )
            ]
        )
    widths = {"A": 22, "B": 10, "C": 26, "D": 6, "E": 14, "F": 52, "G": 52}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _import_xlsx(data: bytes) -> list[Row]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("xlsx import", "openpyxl") from exc
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise SerializationError(f"cannot read xlsx: {exc}") from exc
    sheet = workbook[workbook.sheetnames[0]]
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header = [str(c or "").strip() for c in next(rows_iter)]
    except StopIteration:
        raise SerializationError("xlsx has no rows") from None
    missing = [c for c in ("id", "source", "target") if c not in header]
    if missing:
        raise SerializationError(
            f"xlsx is missing required column(s): {', '.join(missing)}"
        )
    position = {name: n for n, name in enumerate(header)}
    rows: list[Row] = []
    for values in rows_iter:
        def get(name: str) -> str:
            idx = position.get(name)
            if idx is None or idx >= len(values):
                return ""
            value = values[idx]
            return "" if value is None else str(value)

        if not get("id").strip():
            continue
        rows.append(
            Row(
                id=get("id"),
                kind=get("kind") or "translate",
                file=get("file"),
                line=int(get("line") or 0) if get("line").isdigit() else 0,
                context=get("context"),
                source=str(_rearm(get("source"))),
                target=str(_rearm(get("target"))),
                index=int(get("index") or 0) if get("index").isdigit() else 0,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def export_table(
    rows: Sequence[Row],
    path: str,
    *,
    format: str | None = None,
    language: str = "chinese",
) -> str:
    """Write *rows* to *path*; returns the path written."""
    fmt = detect_format(path, override=format)
    if fmt == "json":
        data = _export_json(rows)
    elif fmt == "csv":
        data = _export_csv(rows, delimiter=",")
    elif fmt == "tsv":
        data = _export_csv(rows, delimiter="\t")
    elif fmt == "po":
        data = export_po(rows, language=language)
    elif fmt == "xlsx":
        data = _export_xlsx(rows)
    else:  # pragma: no cover - detect_format already validates
        raise TableError(f"unsupported export format {fmt}")
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def read_table(path: str, *, format: str | None = None) -> list[Row]:
    """Read a table file into rows."""
    fmt = detect_format(path, override=format)
    with open(path, "rb") as handle:
        data = handle.read()
    if fmt == "json":
        return _import_json(data)
    if fmt == "csv":
        return _import_csv(data, delimiter=",")
    if fmt == "tsv":
        return _import_csv(data, delimiter="\t")
    if fmt == "po":
        return import_po(data)
    if fmt == "xlsx":
        return _import_xlsx(data)
    raise TableError(f"unsupported import format {fmt}")  # pragma: no cover


#: Backwards-friendly alias.
import_table = read_table


def write_table(rows: Sequence[Row], path: str, **kwargs: object) -> str:
    """Alias of :func:`export_table`."""
    return export_table(rows, path, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 回填
# ---------------------------------------------------------------------------
@dataclass
class ApplyResult:
    """What :func:`apply_rows` did."""

    written: list[str] = field(default_factory=list)
    applied: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.applied} applied", f"{self.skipped} skipped"]
        if self.written:
            parts.append(f"{len(self.written)} file(s) written")
        if self.problems:
            parts.append(f"{len(self.problems)} problem(s)")
        return ", ".join(parts)


def apply_rows(
    rows: Iterable[Row],
    tl_dir: str,
    *,
    dry_run: bool = True,
    only_filled: bool = True,
    backup: bool = True,
) -> ApplyResult:
    """Write *rows*' ``target`` text back into the ``tl_dir`` files.

    Matching rules:

    * ``kind == "translate"``: the row id is the block identifier. The row's
      ``source`` (when present) must equal the block's mirrored source text —
      a mismatch means the game's script changed under us, and blindly writing
      would put a translation on the wrong line. Such rows are skipped with a
      problem message.
    * ``kind == "strings"``: matched by ``{#context}`` + ``old`` text, not by
      ordinal, because the ordinal shifts whenever the game adds a string.

    ``only_filled`` (default) skips rows whose target is empty, so re-exporting
    a half-done table never blanks existing translations.
    """
    parsed = parse_tl_dir(tl_dir)
    lookup = {_rel(os.path.abspath(t.path), tl_dir): t for t in parsed}
    result = ApplyResult()

    for row in rows:
        if only_filled and row.empty:
            result.skipped += 1
            continue
        tl = lookup.get(row.file.replace("\\", "/"))
        if tl is None:
            result.problems.append(f"{row.id}: unknown file {row.file!r}")
            result.skipped += 1
            continue
        try:
            if row.kind == "strings":
                _apply_string_row(tl, row)
            else:
                _apply_translate_row(tl, row)
            result.applied += 1
        except KeyError:
            result.problems.append(f"{row.id}: identifier not found in {row.file}")
            result.skipped += 1
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            result.problems.append(f"{row.id}: {exc}")
            result.skipped += 1

    for tl in lookup.values():
        if not tl.is_dirty():
            continue
        result.written.append(tl.path)
        if not dry_run:
            tl.write(backup=backup)
    return result


def _apply_translate_row(tl: TlFile, row: Row) -> None:
    blocks = [b for b in tl.blocks if b.identifier == row.id]
    if not blocks:
        raise KeyError(row.id)
    block: TlBlock = blocks[row.index] if row.index < len(blocks) else blocks[0]
    if row.source:
        current = block.old_text
        if current and current != row.source:
            raise TableError(
                f"source mismatch in {row.file}:{block.lineno}\n"
                f"  table: {row.source!r}\n"
                f"  file : {current!r}\n"
                "  (the game script likely changed; re-run translate and re-export)"
            )
    apply_new_text(tl, row.id, row.target, index=row.index)


def _apply_string_row(tl: TlFile, row: Row) -> None:
    wanted = _with_context_prefix(row.context, row.source)
    for block in tl.blocks:
        if block.kind != "strings":
            continue
        for pair in block.strings:
            if pair.old == wanted or (
                row.context
                and _strip_context_prefix(pair.old) == row.source
                and (pair.context or "") == row.context
            ):
                _set_string_new(tl, pair, row.target)
                return
    raise KeyError(wanted)


__all__ = __all__ + ["ApplyResult"]
