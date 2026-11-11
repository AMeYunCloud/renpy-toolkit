"""decompile.py 的测试：pickle VM、source 还原、安全边界。

这些用例覆盖的都是实机上踩过的坑：

* ``.rpyc`` 头是 ``RENPY RPC2`` **紧跟**槽表，没有换行/没有头行
* ``strings`` 块编译后藏在 ``init`` 里，必须还原成 ``translate <lang> strings:``
* ``Say`` 的 ``(multiple=N)`` 进标识符，丢了就整块失配
* 空串 ``""`` 是合法的可翻译 say，不能被当成「非 say」跳过
* 标识符可以以数字开头（``6c0c3176``），旧正则把它误判成 unknown
* ``pickle.loads`` 绝不能出现在这条路径上：恶意 GLOBAL 必须被拒
"""

from __future__ import annotations

import io
import os
import pickle
import unittest
import zlib

from _util import temp_dir

from renpy_toolkit import decompile as d
from renpy_toolkit import tl as tlmod


# --------------------------------------------------------------------------
# Building synthetic .rpyc containers
# --------------------------------------------------------------------------


def build_rpyc(payload: bytes, *, slot: int = 1) -> bytes:
    """Wrap *payload* in a two-slot RPC2 container like Ren'Py writes."""
    blob = zlib.compress(payload)
    table_size = 12 * 2
    start = len(b"RENPY RPC2") + table_size
    header = b"RENPY RPC2"
    header += (slot).to_bytes(4, "little") + start.to_bytes(4, "little")
    header += len(blob).to_bytes(4, "little")
    header += b"\x00" * 12
    return header + blob + b"\x00" * 16


# --------------------------------------------------------------------------
# Hand-built pickles
#
# ``pickle.dumps`` cannot be used for the fake AST classes: it would need
# ``renpy.ast`` to be importable so it could look the class up again.  These
# helpers emit the same opcode shapes Ren'Py's own protocol-2 picks use, which
# is also what lets us forge a hostile pickle without writing one to disk.
# --------------------------------------------------------------------------


def _unicode(text: str) -> bytes:
    raw = text.encode("utf-8")
    return b"X" + len(raw).to_bytes(4, "little") + raw


def _none() -> bytes:
    return b"N"


def _empty_dict() -> bytes:
    return b"}"


def _empty_list() -> bytes:
    return b"]"


def _global(module: str, name: str) -> bytes:
    return b"c" + module.encode() + b"\n" + name.encode() + b"\n"


def node_pickle(kind: str, fields: dict[str, bytes]) -> bytes:
    """Build ``Node(kind, fields)`` the way Ren'Py stores an AST node.

    Shape: ``GLOBAL renpy.ast <kind>`` then ``NEWOBJ`` with ``(None, {fields})``,
    matching ``__reduce__`` returning ``(cls, (None, state))``.
    """
    out = [b"\x80\x02"]
    out.append(_global("renpy.ast", kind))
    out.append(b"((" + _none() + b"}" + b"(")
    for key, value in fields.items():
        out.append(_unicode(key))
        out.append(value)
    out.append(b"u")  # SETITEMS  -> {key: value, ...}
    out.append(b"t")  # TUPLE    -> (None, {fields})
    out.append(b"\x85")  # TUPLE1 -> ((None, {fields}),)
    out.append(b"\x81")  # NEWOBJ  (\x89 is NEWFALSE, which silently yields False)
    out.append(b".")
    return b"".join(out)


class TestContainer(unittest.TestCase):
    def setUp(self) -> None:
        self._ctx = temp_dir()
        self.dir = self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)

    def test_slot_table_has_no_header_line(self) -> None:
        payload = pickle.dumps(({"version": 1}, []), 2)
        path = os.path.join(self.dir, "x.rpyc")
        with open(path, "wb") as fh:
            fh.write(build_rpyc(payload))
        slots = d._slot_payloads(path)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0], payload)

    def test_rejects_bad_magic(self) -> None:
        path = os.path.join(self.dir, "bad.rpyc")
        with open(path, "wb") as fh:
            fh.write(b"NOTRENPY" + b"\x00" * 64)
        with self.assertRaises(Exception):
            d._slot_payloads(path)


class TestSafety(unittest.TestCase):
    """A malicious pickle must be refused, not executed."""

    def test_os_system_is_refused(self) -> None:
        marker = os.path.join(temp_dir().__enter__(), "pwned")
        # Exactly what a hostile .rpyc would carry: a GLOBAL naming a shell
        # call.  Nothing may run.
        evil = (
            b"\x80\x02"
            + _global("os", "system")
            + b"("
            + _unicode(f"echo pwned > {marker}")
            + b"t"
            + b"R."  # REDUCE -> os.system(...)
        )
        with self.assertRaises(d.RpycError):
            d._Builder().run(evil)
        self.assertFalse(os.path.exists(marker))

    def test_unknown_statement_class_is_still_a_node(self) -> None:
        value = d._Builder().run(node_pickle("Parallel", {"block": _empty_list()}))
        self.assertIsInstance(value, d.Node)
        self.assertEqual(value.kind, "Parallel")

    def test_str_subclass_collapses_to_str(self) -> None:
        # PyExpr(text, filename, line) is a str subclass; only the text matters.
        payload = (
            b"\x80\x02"
            + _global("renpy.ast", "PyExpr")
            + b"("
            + _unicode("hello")
            + _unicode("f.rpy")
            + b"K\x03"  # BININT1 3
            + b"t"
            + b"R."
        )
        self.assertEqual(d._Builder().run(payload), "hello")


class TestEmit(unittest.TestCase):
    def test_say_with_arguments_keeps_them(self) -> None:
        # ``(multiple=2)`` is part of the identifier hash; dropping it would
        # silently renumber every block in the file.
        arg = d.Node("ArgumentInfo", {"arguments": [], "keywords": [("multiple", 2)]})
        node = d.Node("Say", {"who": "e", "what": "hi", "arguments": arg})
        out = d.emit_node(node)
        self.assertEqual(out, ['e "hi" (multiple=2)'])

    def test_with_is_appended(self) -> None:
        node = d.Node("Say", {"who": "e", "what": "hi", "with_": "vpunch"})
        self.assertEqual(d.emit_node(node), ['e "hi" with vpunch'])

    def test_empty_say_is_emitted(self) -> None:
        node = d.Node("Say", {"who": "None", "what": ""})
        self.assertEqual(d.emit_node(node), ['""'])

    def test_strings_block_is_rebuilt(self) -> None:
        pair = d.Node("TranslateString", {"old": "a", "new": "b"})
        init = d.Node("Init", {"priority": 0, "block": [pair]})
        out = d.emit_node(init)
        self.assertEqual(out, ["translate None strings:", '    old "a"', '    new "b"'])

    def test_escaping(self) -> None:
        self.assertEqual(d.renpy_escape('a"b'), 'a\\"b')
        self.assertEqual(d.renpy_escape("a\nb"), "a\\nb")
        self.assertEqual(d.renpy_escape("a\\b"), "a\\\\b")

    def test_unknown_node_becomes_todo(self) -> None:
        out = d.emit_node(d.Node("Parallel", {}))
        self.assertIn("TODO", out[0])
        self.assertIn("Parallel", out[0])


class TestRoundTripText(unittest.TestCase):
    """Emitted text must parse back through tl.py with the same identifiers."""

    def test_identifier_summary(self) -> None:
        blocks = [
            d.Node("Translate", {"language": "Spanish", "identifier": "a_1",
                                 "block": [d.Node("Say", {"who": "e", "what": "hi"})]}),
            d.Node("Translate", {"language": "Spanish", "identifier": "6c0c3176",
                                 "block": [d.Node("Say", {"who": "None", "what": ""})]}),
        ]
        lines: list[str] = []
        for node in blocks:
            d.emit_node(node, 0, lines)
        text = "\n".join(lines) + "\n"
        parsed = tlmod.parse_tl_text(text)
        self.assertEqual({b.identifier for b in parsed.blocks}, {"a_1", "6c0c3176"})
        for block in parsed.blocks:
            self.assertEqual(block.kind, "translate", block.identifier)


if __name__ == "__main__":
    unittest.main()
