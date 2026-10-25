"""统一异常层级 for :mod:`renpy_toolkit`.

依赖：仅标准库。

为什么这么设计
--------------
包里原本各模块自带独立异常（``rpa.RPAError``、``rpyc.RpycError``），
CLI 与跨模块脚本就没有一个统一的 ``except`` 目标：想「出错就以非零退出码
退出并把可读消息写到 stderr」必须逐个模块罗列异常类型，漏一个就会打印
traceback。本模块给出 ``ToolkitError`` 作为唯一根，再按「用户输入问题 /
文件内容问题 / 环境问题 / 拒绝写入」四类细分。

**向后兼容**：``rpa.RPAError`` 与 ``rpyc.RpycError`` 仍然存在且仍从各自
模块 import；它们现在同时继承 ``ToolkitError``，所以 ``except
ToolkitError`` 能一并捕获（多重继承，不改变原有 except 子句的语义）。
为避免循环 import，``errors.py`` 不 import 任何同级模块。

预期行为示例
------------
- ``issubclass(tl.TLError, ToolkitError)`` 为 True；``raise rpa.RPAError("x")``
  能被 ``except ToolkitError`` 捕获。
- ``MissingDependencyError`` 的 ``str()`` 形如
  ``"X requires the optional package 'openpyxl'; install it with: pip install openpyxl"``，
  CLI 直接把它打到 stderr。
- ``UnsafeWriteError`` 表示「解析能完成但回写会破坏文件」，调用方必须放弃写入。
"""

from __future__ import annotations

__all__ = [
    "DependencyError",
    "FontError",
    "InvalidRequestError",
    "MissingDependencyError",
    "ParseError",
    "ProjectError",
    "ProtectError",
    "SDKError",
    "SDKNotFoundError",
    "SDKTimeoutError",
    "ScaffoldError",
    "SerializationError",
    "TableError",
    "TLError",
    "TLParseError",
    "ToolkitError",
    "UnsafeWriteError",
    "VerifyError",
]


class ToolkitError(Exception):
    """Base class for every error raised by :mod:`renpy_toolkit`."""


class InvalidRequestError(ToolkitError, ValueError):
    """The caller asked for something impossible (bad language, empty dir…).

    Also a :class:`ValueError` so that ordinary argument-validation code
    which expects ``ValueError`` keeps working.
    """


class ParseError(ToolkitError):
    """File content could not be understood."""

    def __init__(self, path: str, message: str, lineno: int | None = None) -> None:
        self.path = path
        self.message = message
        self.lineno = lineno
        where = path if lineno is None else f"{path}:{lineno}"
        super().__init__(f"{where}: {message}")


class UnsafeWriteError(ToolkitError):
    """Writing would corrupt the target file, so the write was refused.

    Raised instead of producing a half-valid ``.rpy``. Callers should report
    it and leave the original file untouched.
    """


class DependencyError(ToolkitError, ImportError):
    """An optional third-party package is required but unavailable.

    Also an :class:`ImportError` so ``except ImportError`` keeps working, and
    the message always contains the exact ``pip install`` command.
    """

    def __init__(self, feature: str, package: str, extra_hint: str = "") -> None:
        self.feature = feature
        self.package = package
        hint = f" ({extra_hint})" if extra_hint else ""
        super().__init__(
            f"{feature} requires the optional package {package!r}; "
            f"install it with: pip install {package}{hint}"
        )


#: Alias kept for readability at call sites: ``raise MissingDependencyError(...)``.
MissingDependencyError = DependencyError


class TLError(ToolkitError):
    """Base class for translation (.rpy / tl) problems."""


class TLParseError(TLError, ParseError):
    """A translation file is not structurally valid."""


class VerifyError(ToolkitError):
    """Verification could not run (e.g. the directory is not a tl/<lang> dir)."""


class ProtectError(ToolkitError):
    """A protected span (tag or placeholder) was lost or duplicated.

    Raised by :mod:`protect` when a translated string cannot be restored
    without corrupting Ren'Py markup, or when parity between source and
    target chunks fails in a way that must stop the pipeline.
    """


class ProjectError(ToolkitError):
    """A game project (or a path inside one) is not usable."""


#: Raised by :mod:`scaffold` when a project cannot be generated at the
#: requested location. Named separately so ``except ScaffoldError`` reads well
#: at call sites that only build projects.
ScaffoldError = ProjectError


class TableError(ToolkitError):
    """Translation table import/export failed."""


class SerializationError(TableError):
    """A table row could not be encoded/decoded in the requested format."""


class FontError(ToolkitError):
    """Font file unreadable or not a supported sfnt."""


class SDKError(ToolkitError):
    """The Ren'Py SDK driver failed."""


class SDKNotFoundError(SDKError, FileNotFoundError):
    """No ``renpy.exe`` / ``renpy.sh`` at the expected location."""


class SDKTimeoutError(SDKError, TimeoutError):
    """The SDK command exceeded its time budget."""


# ``rpa.RPAError`` and ``rpyc.RpycError`` are *not* defined here on purpose.
# They stay in their owning modules (so ``renpy_toolkit.rpa.RPAError`` keeps
# working exactly as before) and there they derive from ToolkitError, which is
# enough for ``except ToolkitError`` to catch both.
