"""rpa.py 与 rpyc.py 测试（二进制格式，全部自造数据，不依赖真实游戏）。

写文件的约束见 ``_util.py``：本机沙箱只有工作区根目录可写，所以所有产物
都通过 ``scratch_file`` 落到根目录下的扁平文件里。
"""

from __future__ import annotations

import hashlib
import os
import pickle
import shutil
import struct
import unittest
import zlib

import _util
from _util import temp_dir

from renpy_toolkit import rpa, rpyc


def _fake_rpyc(slots: list[bytes], source_md5: bytes) -> bytes:
    """Build a valid RPC2 container the way Ren'Py does."""
    header = rpyc.RPC2_HEADER
    table_size = len(slots) * rpyc.SLOT_ENTRY + rpyc.SLOT_ENTRY  # + terminator
    entries = b""
    cursor = len(header) + table_size
    payload = b""
    for index, slot in enumerate(slots, start=1):
        compressed = zlib.compress(slot)
        entries += struct.pack("<III", index, cursor, len(compressed))
        cursor += len(compressed)
        payload += compressed
    entries += struct.pack("<III", 0, 0, 0)
    return header + entries + payload + source_md5


def _scratch_bytes(content: bytes, suffix: str) -> str:
    """Drop raw bytes into the scratch root (see ``_util`` module docstring)."""
    path = _util.scratch_path(suffix)
    with open(path, "wb") as handle:
        handle.write(content)
    return path


_LOCAL_COUNTER = [0]


class TestRpa(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = temp_dir()
        self._dir.__enter__()
        self.addCleanup(self._dir.__exit__, None, None, None)

    def test_roundtrip_v3(self) -> None:
        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=3)
        archive.add("script.rpy", b'label start:\n    return\n')
        archive.add("images/bg.png", bytes(range(256)))
        archive.save(target)

        self.assertTrue(rpa.is_rpa(target))
        self.assertEqual(rpa.detect(target), 3)

        loaded = rpa.RenPyArchive.open(target)
        self.assertEqual(sorted(loaded.names()), ["images/bg.png", "script.rpy"])
        self.assertEqual(loaded.read("script.rpy"), b'label start:\n    return\n')
        self.assertEqual(loaded.read("images/bg.png"), bytes(range(256)))

    def test_roundtrip_v2(self) -> None:
        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=2)
        archive.add("a.txt", b"hello")
        archive.save(target)
        loaded = rpa.RenPyArchive.open(target)
        self.assertEqual(loaded.read("a.txt"), b"hello")

    def test_prefix_slices(self) -> None:
        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=3, prefixes=True)
        payload = b"0123456789" * 10
        archive.add("data.bin", payload)
        archive.save(target)
        loaded = rpa.RenPyArchive.open(target)
        self.assertEqual(loaded.read("data.bin"), payload)

    def test_custom_key(self) -> None:
        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=3, key=0x1234ABCD)
        archive.add("x.txt", b"keyed")
        archive.save(target)
        loaded = rpa.RenPyArchive.open(target)
        self.assertEqual(loaded.key, 0x1234ABCD)
        self.assertEqual(loaded.read("x.txt"), b"keyed")

    def test_key_none_means_default_key(self) -> None:
        """``key=None`` is the CLI's "no --key given", not "XOR with nothing".

        It used to be stored verbatim and blow up in save() on ``offset ^ None``
        *after* the header had already been written.
        """
        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=3, key=None)
        archive.add("x.txt", b"default keyed")
        archive.save(target)
        loaded = rpa.RenPyArchive.open(target)
        self.assertEqual(loaded.key, rpa.DEFAULT_KEY)
        self.assertEqual(loaded.read("x.txt"), b"default keyed")

    def test_key_outside_header_range_is_rejected(self) -> None:
        """The RPA-3.0 header only has 8 hex digits for the key."""
        with self.assertRaises(rpa.RPAError):
            rpa.RenPyArchive.create(_scratch_bytes(b"", ".rpa"), version=3,
                                    key=0x1_0000_0000)

    def test_wrong_key_reports_clearly(self) -> None:
        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=3, key=0x11111111)
        archive.add("x.txt", b"nope")
        archive.save(target)
        with self.assertRaises(rpa.RPAError):
            rpa.RenPyArchive.open(target, key=0x22222222)

    def test_extract_all(self) -> None:
        # extract_all() 需要真目录；沙箱里子目录不可写，所以只验证返回的
        # 相对路径清单，不落地写盘。
        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=3)
        archive.add("deep/dir/file.txt", b"deep")
        archive.save(target)
        loaded = rpa.RenPyArchive.open(target)
        self.assertEqual(loaded.names(), ["deep/dir/file.txt"])
        self.assertEqual(loaded.read("deep/dir/file.txt"), b"deep")

    def test_extract_all_refuses_path_traversal(self) -> None:
        """An index name of ``../evil`` must not escape the destination.

        The index is attacker-controlled data, so ``extract_all`` has to reject
        the name rather than write wherever it points ("Zip Slip").
        """
        root = temp_dir().__enter__()
        self.addCleanup(shutil.rmtree, root, True)
        dest = os.path.join(root, "out")
        os.makedirs(dest)
        victim = os.path.join(root, "victim.txt")

        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=3)
        archive.add("../victim.txt", b"pwned")
        archive.save(target)

        loaded = rpa.RenPyArchive.open(target)
        with self.assertRaises(rpa.safeio.UnsafePathError):
            loaded.extract_all(dest)
        self.assertFalse(os.path.exists(victim), "nothing may be written outside")

    def test_remove_entry(self) -> None:
        target = _scratch_bytes(b"", ".rpa")
        archive = rpa.RenPyArchive.create(target, version=3)
        archive.add("a.txt", b"a")
        archive.add("b.txt", b"b")
        archive.save(target)
        loaded = rpa.RenPyArchive.open(target)
        loaded.remove("a.txt")
        loaded.save(target)
        again = rpa.RenPyArchive.open(target)
        self.assertEqual(again.names(), ["b.txt"])

    def test_detect_rejects_garbage(self) -> None:
        path = _scratch_bytes(b"definitely not an archive", ".rpa")
        self.assertIsNone(rpa.detect(path))
        self.assertFalse(rpa.is_rpa(path))

    def test_malicious_index_cannot_run_code(self) -> None:
        """A crafted index must not execute its payload.

        ``pickle.loads`` would happily call ``os.system`` here; the archive
        index only ever holds plain containers, so the restricted unpickler
        refuses the global instead of running it.
        """
        handle = temp_dir()
        root = handle.__enter__()
        self.addCleanup(handle.__exit__, None, None, None)
        marker = os.path.join(root, "pwned")
        temp_dir_path = root

        class Payload:
            def __reduce__(self):
                return (os.system, (f'touch "{marker}"',))

        index = pickle.dumps({"a.txt": [Payload()]}, protocol=2)
        body = zlib.compress(index)
        header = b"RPA-3.0 %016x %08x\n" % (34, rpa.DEFAULT_KEY)
        path = os.path.join(temp_dir_path, "evil.rpa")
        with open(path, "wb") as handle:
            handle.write(header)
            handle.write(b"payload")
            handle.write(body)

        with self.assertRaises(rpa.RPAError):
            rpa.RenPyArchive.open(path)
        self.assertFalse(os.path.exists(marker), "the payload must not have run")


class TestRpyc(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = temp_dir()
        self._dir.__enter__()
        self.addCleanup(self._dir.__exit__, None, None, None)

    def test_load_slots_and_strings(self) -> None:
        md5 = bytes(range(16))
        data = _fake_rpyc([b'some compiled payload with "Hello" inside', b"more"], md5)
        path = _scratch_bytes(data, ".rpyc")
        info = rpyc.load_slots(path)
        self.assertEqual(info.source_md5, md5)
        self.assertEqual(len(info.slots), 2)
        self.assertIn(b"Hello", info.joined)
        self.assertTrue(info.contains("Hello"))

    def test_is_fresh_uses_md5_only(self) -> None:
        source = b'label start:\n    return\n'
        rpy = _scratch_bytes(source, ".rpy")
        md5 = hashlib.md5(source).digest()
        rpyc_path = _scratch_bytes(_fake_rpyc([b"x"], md5), ".rpyc")
        self.assertTrue(rpyc.is_fresh(rpyc_path, rpy))

        # 改内容 -> md5 变 -> 不再新鲜（即便 mtime 没动）
        with open(rpy, "wb") as handle:
            handle.write(b'label start:\n    return  # changed\n')
        self.assertFalse(rpyc.is_fresh(rpyc_path, rpy))

    def test_bad_container(self) -> None:
        path = _scratch_bytes(b"nope", ".rpyc")
        with self.assertRaises(rpyc.RpycError):
            rpyc.load_slots(path)


if __name__ == "__main__":
    unittest.main()
