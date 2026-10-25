# .rpyc 格式与反编译

面向「只有 `.rpyc` 的发行版游戏要汉化」这一场景。所有结论都在
`test/the reference release`（107 个 `.rpyc`）上实测过。

## 1. 容器格式

`.rpyc` 不是「头行 + 数据」，而是一个极简的偏移量表：

```
offset 0    b"RENPY RPC2"                    10 字节，无换行、无版本号文本
offset 10   槽表：每项 12 字节 struct.pack("<III", slot, start, length)
            slot == 0 表示表结束（哨兵，不是数据）
offset ...  各槽数据，zlib 压缩，start/length 是**绝对文件偏移**
末尾 16 字节  源 .rpy 的 md5
```

两个细节值得记下来，因为猜错会得到「读了半截数据但没报错」：

- 槽表项里的 `start`/`length` 不是相对偏移，从 0 算。早期笔记里写的
  `read(size - offset)` 是把 `offset` 当起始位置用了，遇多槽文件会读错。
- 末尾 16 字节是**源文件 md5**，Ren'Py 判断 `.rpyc` 能否复用只看它，不看
  mtime。所以「改了 `.rpy` 忘了删 `.rpyc`」时引擎会继续用旧编译结果——
  这是汉化工程里最常见的「明明改了没生效」。

实测每个脚本有 **2 个槽，内容不相等**。选择哪个槽有讲究：本工具用
`_SCORE_KINDS = {Translate, Label, Init, TranslateString, Say}` 给槽打分，
取分高的那个。对 TL 文件通常 slot 0 是完整程序。

## 2. 载荷是 pickle，但绝不能 `pickle.loads`

槽里 `zlib.decompress` 之后是 Ren'Py 自己 `pickle` 出来的 AST：

```python
{"version": 5000000, "key": ..., "statements": [Node, Node, ...]}
```

`pickle.loads` 遇到 `GLOBAL os.system` 会**真的调用 `os.system`**。把一份恶意
游戏丢进汉化工具链，等于把机器交出去，而这个工具链的输入天然不可信（别人的
游戏）。所以 `decompile.py` 用 `pickletools.genops` 把字节流拆成 opcode，
喂给自建的栈式虚拟机：

- `GLOBAL` / `STACK_GLOBAL` 只允许白名单模块：`renpy.ast`、`renpy.atl`、
  `renpy.parameter`、`renpy.pyanalysis`、`renpy.lexer`、`renpy.object`、
  `renpy.sl2.slast`、`renpy.sl2.slparser`、`renpy.styledata.stylesets`，
  外加 `__builtin__` 的 `list`/`set` 与 `collections.defaultdict`。
  其它模块名一律抛 `RpycError`。
- 白名单内但本插件没有发射器的类，实例化成 `Node(kind, fields)`，发射时写成
  `# TODO: unsupported node <kind>` 并计数上报——**不中断**，因为翻译工作
  不需要这些节点。
- `renpy.ast.PyExpr` 是带 `origin` 的 `str` 子类，折叠成普通 `str`。
- memo 用 `dict` 而不是 `list`：`BINPUT` 的下标可以任意大，用 list 会
  `IndexError`。

实测真实文件里出现的 `GLOBAL` 只有这么几种，白名单足够：

```
__builtin__.list / set          collections.defaultdict
renpy.ast.EndTranslate / Init / PyExpr / Return / Say
renpy.ast.Translate / TranslateString
renpy.parameter.ArgumentInfo
```

## 3. 发射源码时踩过的坑

| 坑 | 现象 | 处理 |
| --- | --- | --- |
| 空 `say` | `what == ""` 被当「无内容」跳过 | 空串是**合法可翻译 say**（译者在删台词），必须发射 |
| 旁白 | `who` 是字符串 `"None"` | 不排除会生成 `None "text"`，语法错误 |
| `(multiple=2)` | 存在 `arguments` 里 | 还原成 `_format_arguments()`，丢了会让这句话不翻译 |
| strings 块 | `translate <lang> strings:` 编译后变成 `Init(priority=0, block=[TranslateString...])` | 直发 `init:` + `old`/`new` 会被解析成 `Say(who='old')`，报 `Sayer 'old' is not defined.`；须检测「子节点全是 TranslateString」并重建 `translate <lang> strings:` |
| `with` 追加 | AST 里 `with` 挂在 say 上 | 追加在行尾，语义等价且不影响标识符（标识符只由 `who` + `what` 派生） |

## 4. 验证方式

反编译这种「生成代码」的操作不能靠肉眼。做了三级验证，每级都能独立地失败：

1. **标识符比对**（纯内存往返）：解 AST → 发射文本 → 用 `tl.parse_tl_text`
   反解 → 比对标识符集合、strings 数、say 数。
   53 文件 / 67,453 标识符 / 2,025 strings / 67,467 say，0 差异。
2. **真实编译**：把反编译出的 `.rpy` 拷进探针工程（删光所有 `.rpyc`），用
   游戏自带的 Ren'Py 运行时跑 `compile`。exit 0，产出 54 个 `.rpyc`。
3. **语义比对**：把重编译出的 `.rpyc` 与原始 `.rpyc` 各自解 AST 比事实。
   0 个文件不同。

第 2、3 级需要一个能跑的 Ren'Py。发行版游戏自带，位置是
`<game>/lib/py3-windows-x86_64/python.exe`，用法：

```powershell
& "<game>\lib\py3-windows-x86_64\python.exe" "<game>\<启动器>.py" "<game>" compile
```

启动器文件名不能靠 `basename(basedir) + '.py'` 拼——作品名以撇号结尾时会拼错，
用 `[n for n in os.listdir(game) if n.endswith('.py')][0]`。

## 5. 能力边界

- **不是完整的反编译器**。屏幕语言（`renpy.sl2`）与较冷门的语句只是部分支持，
  缺的部分写成 `# TODO`。用来做汉化足够（译文只挂在 `translate` 块上），
  别指望还原一份可发行的完整源码。
- **RPAv1（`.rpi`）不支持**，只有 v2/v3。
- **不改游戏本体**：`decompile dir` 默认不覆盖已存在的 `.rpy`，因为存在源码
  就意味着它比 `.rpyc` 新或者是手改过的，覆盖会毁掉工作。
