# 安全审查报告

四轮审查，覆盖 `renpy_toolkit` 全部 27 个模块。分级的依据是**后果**：能不能
被一份恶意游戏或一份被篡改的 `.rpa` 触发，触发后能写到哪里、能不能执行代码。

威胁模型明确一下：本工具链的输入是**别人做的游戏**（`.rpyc`、`.rpa`、字体、
译文表），输出是**翻译工位上的文件**。所以真正要防的是三件事 ——

1. 解析不可信二进制时执行代码（pickle / zlib 炸弹）；
2. 用游戏里带来的字符串拼路径，写到工作目录之外（Zip Slip）；
3. 把游戏文本原样交给 Excel，变成公式（表格注入）。

游戏本身是可信的情况不在威胁模型里：那是用户自己的工程。

---

## 一、已修复

### Critical

| # | 位置 | 问题 | 修法 |
| --- | --- | --- | --- |
| 1 | `tlgen.py` `generate_tl` | `--outdir` 指向已有目录时 `shutil.rmtree(out)` **静默清空用户目录**（实测：放 3 个手写译文的目录跑一次生成，全没了） | 只清自己建的 scratch；`--outdir` 一律 `makedirs(exist_ok=True)`，不删 |
| 2 | `tlgen.py` `write_string_blocks` | strings 块用 `"a"` 追加模式 + 默认 scratch 跨运行复用 → 第二次生成把每个块再堆一遍，引擎启动即 `A translation for "X" already exists` | 生成前重建自建 scratch；跨调用合并为一次写入；文件内按 `old` 去重；追加前读回已有 `old` 只补缺 |

### High

| # | 位置 | 问题 | 修法 |
| --- | --- | --- | --- |
| 3 | `rpa.py` 索引加载 | `pickle.loads` 对归档索引无限制 —— 一份恶意 `.rpa` 可执行任意代码 | 白名单 `Unpickler`：`find_class` 只放行 `builtins` 的 `dict/list/tuple/set/frozenset/bytearray/bytes/str`，其余抛 `UnpicklingError` |
| 4 | `rpa.py` `extract_all` | 条目名来自归档索引（攻击者可控），`os.path.join(destdir, name)` 不消解 `..` → Zip Slip | `safeio.safe_join`：拒绝绝对路径/盘符/空名，`realpath` 后 `commonpath` 必须等于 base |
| 5 | `cli.py` rpa extract | 同一漏洞在 CLI 又拼一遍，且 `--no-overwrite` 默认关（默认覆盖） | 同上，改走 `safe_join` |
| 6 | `cli.py` `tl gen --write` | `shutil.copy2(outdir/rel, target/rel)` 的 `rel` 来自游戏自己的脚本文件名 —— 恶意游戏可命名 `../../x.rpy` 写到游戏外 | 改走 `safe_join(target, rel)` |
| 7 | `tables.py` CSV / XLSX 导出 | 译文以 `=` `+` `-` `@` 开头时被 Excel 当公式执行（游戏文本是攻击者可控的） | `_defuse` 加前导 `'`；导入时 `_rearm` 还原，往返无损 |

### Medium

| # | 位置 | 问题 | 修法 |
| --- | --- | --- | --- |
| 8 | `safeio.py`（新增） | 各处 `zlib.decompress` 无上限 —— 几十 KB 的压缩流可解出几 GB（zlib 炸弹） | `decompress_limited` 增量解压、超 64 MiB 抛 `DecompressError`、`eof` 判完整、`unused_data` 判尾随；接入 `rpyc.py` / `decompile.py` / `rpa.py` 三处 |
| 9 | `hardcoded.py` `ledger_path` | `--language` 未校验，`..` 可把 `hardcoded.json` 写出 tl 目录 | `LANGUAGE_RE = ^[A-Za-z][A-Za-z0-9_.-]*$` |
| 10 | `fresh.py` | `--force` 覆盖已翻译文件时 `write(backup=False)`，旧译文无 `.bak` | `write(backup=exists)` |
| 11 | `tlgen.py` `_restore_sources` | `except Exception: continue` 吞掉反编译失败，用户只看到"已反编译 N 个" | 收集 `(path, error)` 到 `GenerateResult.decompile_failures`，CLI 打印 |
| 12 | `sorttl.py` / `stringsplit.py` | 非 UTF-8 文件抛裸 `UnicodeDecodeError`；读不了的文件静默跳过 | 转 `TLParseError`；记入 `SourceIndex.unreadable` |

### Low

`tlgen.py` 越界校验改用 `safe_join`（原 `abspath`+`commonpath` 不解析符号链接、跨盘符抛 `ValueError`）。

---

## 二、第三轮：逐条实读写入点（新增结论）

前两轮是从调用链反推风险，第三轮改为把**每一处真正落盘/删目录的代码**逐个打开读，
确认保护条件确实成立。结论：未发现新的 Critical/High。

| 位置 | 保护条件 | 判定 |
| --- | --- | --- |
| `tlgen.py` `generate_tl` 收尾 | `own_out = not out`，只有调用方没给 `--outdir` 时才清自己建的目录；用户给的 outdir 永不清理 | 安全 |
| `tlgen.py` `_clean_scratch` | `rmtree` 前先 `scratch_is_ours(tree)`，只认 basename 是 `renpy-toolkit-gen` / `.renpy-toolkit-gen` | 安全 |
| `tlgen.py` `_WRITE_PROBE` | 一行程序只 `makedirs` + 写再删自己建的固定名 `.rtk-write-test`，参数走 `sys.argv[1]`，无 shell | 安全 |
| `tlgen.py` strings 追加 | `"a"` 模式只在「文件已存在且其中缺 strings 块」时走，其余直接 `continue` | 安全 |
| `scaffold.py` 建工程 | `os.path.exists(target)` 则抛 `ScaffoldError`；`chmod 0o755` 只在模板声明 `executable` 时执行（当前无） | 安全 |
| `decompile.py` 写源码 | `backup=True` 且目标已存在时先写 `.bak`；默认不覆盖 | 安全 |
| `contextmap.py` `write_patch` | 同名文件已存在且不含 `PATCH_MARKER` 则拒绝覆盖 | 安全 |
| `sdk.py` 调引擎 | `argv = [self.executable, basedir, *args]`，列表 argv，无 `shell=True` | 安全 |

顺带核实：硬编码的 `"Chinese"` / `"chinese"` 全部是 CLI 的 `--language` 默认值
（`cli.py`、`fontmap.py`、`scaffold.py`、`tables.py`），属中性配置，不构成标识。

---

## 三、已核实为安全（附证据）

| 关注点 | 结论 | 证据 |
| --- | --- | --- |
| 子进程注入 | 三处 `subprocess.run` 全是**列表 argv**，无 `shell=True`；用户输入只经环境变量传递 | `sdk.py:281`、`tlgen.py:1095`、`tlgen.py:1531` |
| 反编译器执行代码 | **从不调用 `pickle.loads`**，用 `pickletools.genops` 走白名单虚拟机 | `decompile.py:18-27` 文件头说明；`GLOBAL`/`STACK_GLOBAL` 只解析 `renpy.parameter` 等已知模块 |
| 润色脚本执行代码 | **从不 `exec`**，用 `ast.parse` + `ast.literal_eval` 只读字面量；有测试证明含 `open(...).write()` 的脚本不会执行 | `polish.py:89`；`test_polish.py::test_does_not_execute_code` |
| 语言名拼路径 | `contextmap.patch_filename` 用 `re.sub(r"[^A-Za-z0-9_]", "_", language)`；`fstringfix` / `hardcoded` 用正则白名单拒绝 | `contextmap.py:76`、`fstringfix.py:75`、`hardcoded.py:35` |
| 字体句柄泄漏 | 自己持有句柄（不让 fontTools 开路径），`finally` 里关闭 —— 损坏字体不会在 Windows 上锁住文件 | `fonts.py:134-170` |
| 覆盖他人文件 | 生成物只覆盖带自己 MARKER 的文件，否则抛 `ToolkitError` | `contextmap.py:296-305`、`fstringfix.py:478-492` |
| BOM / 行尾 | 读保留 BOM，写回原样；行数变化或出现 `\r\r` 时拒绝写盘 | `tl.py:356-364`、`tl.py:392-398` |
| 归档索引越界 | offset/length 有界校验，`length` 超过文件剩余长度直接拒 | `rpa.py:336-355` |
| 表格导入 | `row.file` 只作字典键查表，不做路径拼接 | `tables.py:827` |
| 异常吞噬 | 全库无裸 `except: pass`；唯一的 `except Exception: pass` 在 `rpa.py` 的 `__del__` 里 | 静态扫描 |

---

## 四、未处理（有意为之）

**`.rpa` 索引格式本身要求 pickle。** 白名单只放行内建容器类型，能挡掉「借
`__reduce__` 执行代码」这一类，但 pickle 反序列化本身仍不是安全边界 —— 如果
攻击者能找到只靠 `dict`/`list`/`bytes` 构造就能触发的问题（例如用超大嵌套
结构耗尽内存），那不在本工具的防护范围内。如实记录，不声称"已验证安全"。

**`--outdir` / `--output` 等由用户指定的路径不做越界检查。** 用户指名写哪里就
写哪里，这是命令行工具的正常语义；要防的是**游戏内容**影响路径，而不是用户
自己。

**译文内容不做转义净化。** 译文是译者写的，不是攻击者写的。`protect.py` 只做
标签/占位符的**结构**校验（闭合顺序、数量、未知标签），不改变内容。

---

## 五、第四轮：官方文档带出的新检查与两处性能/正确性缺陷

这一轮的触发点是回头读官方 `config.html` 与 `text.html`。两处都不是"安全漏洞"，
但都会让工具给出**错误结论**，按后果排在同等位置。

### 5.1 `{=style}` 整类漏检（正确性）

`text.html` 说 `{=mystyle}…{/=mystyle}` 是"标签名为空的标签，参数就是样式名"。
`tag_name()` 对它的判定是"没有名字"，于是 `_atom_counts` 里
`if not name or name.startswith(("#", "=")): continue` 把它整类跳过——译文丢掉
或凭空添加都不会被发现。修法见 `docs/custom-text-tags.md` 第三节。

关键坑：判定必须看 chunk 原文。`_TAG_NAME_RE` 的字符类 `[^{}=:/]*` 排除了 `=`，
所以 `name.startswith("=")` 永远不成立；正确写法是
`chunk.text.startswith("{=") or chunk.text.startswith("{/=")`。

### 5.2 `runtime-strings` 新检查（覆盖率的诚实性）

`config.translate_additional_strings_callbacks` 让游戏在**运行时**注册字符串。
`tl gen` 读编译后的语句图，看不到它们。新增 audit 检查（warning 级）报出来，
README 里也写明"覆盖率是上界，不是承诺"。

### 5.3 `_game_dirs` 把工作副本旁边的目录当成游戏内容（两条问题的共同根因）

`audit.py` 的 `_game_dirs()` 原本返回 `[project_dir] + [project_dir/game]`
（两者都有时）。它们其实是**替代关系**，不是并集：

| | 修复前 | 修复后 |
| --- | --- | --- |
| 扫描文件数（参考工程） | 8,285 | 228 |
| 扫描耗时 | 41.9 s | 6.19 s |
| `missing-image` 条数 | 20,591 | 1（合并说明） |

工作副本把笔记、截图、探针输出放在 `game/` 旁边是正当的。把根目录也扫进去，
这些截图的名字会灌进图片索引，让真正的图看起来缺失——**这是误报的成因，不是
扫描慢的副作用**。

### 5.4 `_exists_anywhere` / `_image_file_exists` 的 O(文件数 × 语句数)

两个函数对每个 image 定义、每条 `show` 都线性遍历整个文件列表。发行版 2,546 个
文件、58,249 条语句，审计跑了几分钟不结束（实测两次被 `job_kill`）。
改为 `_SourceIndex`：一次构建"所有组件边界的路径后缀"集合与"图片 stem"集合，
查询 O(1)。发行版审计 **5.34 s → 3.34 s**（`_game_dirs` 修完后的数字）。

`has_suffix` 保留了原 `str.endswith` 的语义（Ren'Py 的引用可以写
`images/bg/room.png` / `bg/room.png` / `room.png`），但只记录组件边界起的后缀；
旧代码"`room.png` 命中 `bigroom.png`"是巧合而非真实引用。

### 5.5 只放脚本的工作副本不再逐条报 2 万条

`game/` 下一个图片文件都没有、且未解析的图片名超过 25 个时，只报一条说明
"这里无法核验图片名，请改用完整构建"。25 这个阈值保证：真的漏了一张图的完整
构建仍然逐条报出来。

---

## 六、复现方式

```powershell
cd plugin\dsh-renpy-toolkit
python -m unittest discover -s tests -t tests -q      # 450 tests
```

`test_safeio.py`（14 例）、`test_binary.py::TestRpa`（10 例，含
`test_malicious_index_cannot_run_code`、`test_extract_all_refuses_path_traversal`）、
`test_tables_verify.py`（新增 2 例公式注入、3 例 `runtime-strings`）、
`test_tl.py::TestStyleReferenceTags`（7 例）覆盖了修复点，回归可测。

### 第四轮的安全重点：没有新增写盘点

本轮新增/改动的代码（`_SourceIndex`、`_audit_runtime_strings`、
`_count_style_tag`）全部是**纯分析**：不写文件、不跑子进程、不解析不可信二进制，
输入只有已经读进内存的 `.rpy` 文本与文件路径列表。三个既有防护点复测通过：

```
ok refused '../evil.txt' / '/abs.txt' / 'C:\\win.txt' / '' / '.' / 'a/../../b'
ok allowed <tmp>/sub/ok.txt
8MiB passes: True
ok limit enforced            # safeio.decompress_limited(limit=1000)
ok pickle refused: UnpicklingError
```
