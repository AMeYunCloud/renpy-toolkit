# Ren'Py 文本标签与插值：引擎侧实证（8.5.4）

本文件记录 `renpy_toolkit` 的 `protect.py` / `verify.py` 规则**依据的引擎事实**。
每条都附源码位置或实测命令，便于以后 Ren'Py 升级时复核。

源码路径写作 `<game>\renpy\...` —— 发行版自带完整 `renpy/` 源码树，
它不在 `game/` 里，与 `game/*.rpyc` 是两套东西。

## 1. 自闭合标签表（`{image}` 不接受 `{/image}`）

`<game>\renpy\text\extras.py`：

```python
text_tags = dict(
    alpha=True, alt=True, art=True, axis=True, done=False, instance=True,
    image=False, p=False, w=False, fast=False, b=True, i=True, u=True, a=True,
    plain=True, font=True, color=True, outlinecolor=True, size=True,
    noalt=True, nw=False, s=True, shader=True, rt=True, rb=True, k=True,
    cps=True, space=False, vspace=False
    )
```

值是「能否被闭合」：`False` 的标签写 `{/image}` 会直接抛异常
（`<game>\renpy\text\text.py`）：

```python
if not renpy.text.extras.text_tags.get(tag, True):
    raise Exception("{/%s} isn't valid, since the %s text tag doesn't take a close tag." % (tag, tag))
raise Exception("%r closes a text tag that isn't open." % text)
```

→ 插件里 `protect.SELF_CLOSING_TAGS = {done, image, p, w, fast, nw, space, vspace}`。

**踩过的坑**：把 `{image=...}` 当成未闭合的开标签，会让
`old '"Magic"?{image=bg/icons/item_text.png}'` 这种完全正确的行被报「泄漏格式」。
真实成品译文里就有这一行。

## 2. 未知标签只有渲染时才报错，lint 抓不到

`<game>\renpy\text\text.py`：

```python
raise Exception("Unknown text tag %r" % text)
```

这条在 `Text` 渲染过程中抛出，**`renpy <game> lint` 不会提前发现**。写错一个标签名
（例如 `{scc=red}` 或大写的 `{B}`）游戏会当场崩。`protect.check_unknown_tags()` 就是补这个缺口。

注意标签表是**开放集合**：`renpy.register_text_tag()` / `config.custom_text_tags` /
`config.self_closing_custom_text_tags` 都能加词。所以 `check_unknown_tags` 做成**对比**而非成员测试 ——
只报「译文新增、源码里没有、引擎也不认识」的标签。实测一份真实成品译文里 17 个标签名有 7 个
（`sc`、`outlinecolor`、`damage`、`gold`、`item_name`、`final_damage`、`tag`）不在内置表里，
纯成员测试会淹没真问题。

## 3. 插值 `[...]` 自 8.2.0 起是 Python 表达式

<https://www.renpy.org/doc/html/incompatible.html#incompatible-7-7-0>（8.2.0 / 7.7.0 条目）：

> **Interpolation Changes** Interpolations in strings are now treated as Python
> expressions, this results in mostly equivalent behaviour when interpreting fields
> except when item getters are in use. For example:
>
>     # Previously
>     e "[player[money]]" #=> player['money']
>     # But now
>     e "[player[money]]" #=> player[money]

→ **不能用 `\[[^\[\]]*\]` 匹配**。嵌套下标会被截断成两段，再被当成「译文多/少了插值」误报。
插件用括号配平 + 跳过字符串字面量的扫描器（`protect._match_interp`）。

## 4. `[[` 与 `{{` 是转义，不是插值/标签

`<game>\renpy\substitutions.py` 的 `parse()` 状态机：

```python
if state is LITERAL:
    if c == '[':
        lit += s[cut:pos]
        cut = pos + 1
        if c == s[pos+1:pos+2]:
            pos += 1
        else:
            state = EXPRESSION
```

即 `[[` 跳过第二个方括号、留在字面量里。

→ `old "type filenames(ex: variable, '<silence 2.>' or [[variable, variable])"`
这一行是**在描述语法**，`[[variable, variable]` 不是插值。把它当插值会误报。

## 5. 三引号值里的真换行是正文，不是被劈开的字面量

真实工程 `devlog_text.rpy` 的补丁说明长这样：

```
    old """{size=-6}⛩️ {b}Update 0.4.7{/b} ⛩️
第一行

第二行"""
```

真换行是段落分隔。`verify.check_literal_newlines()` 只对**单引号值**有意义 ——
单引号 Ren'Py 字符串不能跨行，真换行才意味着字面量被劈开。
`verify.is_multiline_literal(raw)` 判断 raw 是否以 `old """` / `new """` 开头。

## 6. 空译文 `new ""` 会擦掉界面文字

`<game>\renpy\translation\__init__.py` 的 `StringTranslator.translate()`：

```python
new = self.translations.get(old, None)
if new is not None:
    return new
```

空串不是 `None` → 返回 `''` → **玩家看到空白**。探针 `rtk-empty` 实测：

```
RTK before: {}
RTK table now: {'RTK_EMPTY_CASE': '', 'RTK_TEXT_CASE': '译文'}
RTK EMPTY  -> ''
RTK TEXT   -> '译文'
RTK MISSING-> 'RTK_NOT_THERE'
```

引擎自己的 `translate` 命令默认用 `null_filter`（恒等，保留原文），只有显式 `--empty`
才用 `empty_filter`；`<game>\renpy\translation\generation.py:509 translate_command`。

→ 插件生成骨架时用**原文占位**，不用空串。

## 7. 评测基准：成品汉化语料的误报计数

一份已上线的成品译料（56 文件 / 57,552 块 / 2,043 条 unique old，
已上线、可正常游玩）是判断规则好坏的最佳语料 —— 每条报错要么是它的真缺陷，要么是我的误报。

`check_chunk_parity` 规则演进（都是同一批 59,369 个真实单元上量的）：

| 规则 | 误报 |
| --- | --- |
| 原子序列严格相等 | 24 |
| 多重集合 + 豁免 `[mc]` + 豁免补闭合 | 1（但漏检颜色值被改） |
| 语义分层（内容标签严格 / 呈现标签忽略） | 2（但漏检标签名拼错） |
| 标签名集合 + 语义原子两步 | 17 |
| **不平衡度 + 内容标签多重集合** | **3 → 修完自闭合/转义后 2** |

最终 2 条经逐条核对**都是该工程的真缺陷**：

- 某事件文件 `:40` —— 源文本 `I already talked to her.` 不含图片，译文却加了
  `{image=bg/icons/item_text.png}`（这条 strings 条目本身也是冗余的，见下）
- 另一事件文件 `:2409` —— 源文本有两处 `{color=#FCDEB3}`，译文只保留一处，
  “afternoon.” 丢了颜色

## 8. `[mc]` 插值豁免

中文译文经常把英文的 “Human” / 隐含主语还原成玩家名字 `[mc]`，这在源文本里没有。
实测真实工程 20+ 条这样写，全部正确。故 `protect.FREE_INTERPS = {"[mc]"}`。

## 9. 真实工程的无效条目（不是插件缺陷）

同一个源行既写了 `translate Chinese strings:` 的 old/new，又写了
`translate Chinese <identifier>:` 块：

```
# game/events.rpy:7
translate Chinese strings:
    old "I already talked to her."
    new "\"魔法\"?{image=bg/icons/item_text.png}"

# game/events.rpy:7
translate Chinese events_variables_e7afb492:
    # t "I already talked to her."
    t "我已经和她谈过了。"
```

对话走 `TranslateSay` → `lookup_translate()` 查 `language_translates`，**不查 strings 表**，
所以那条 strings 条目永远不会命中。任务表里的 `old "C omplete"` / `old "C ompleted"` 同型。

## 10. f-string 永远命中不了翻译表

`<game>\renpy\substitutions.py` 的 `substitute()` **先**翻译**再**插值：

```python
def substitute(s, scope=None, force=False, translate=True):
    s = renpy.translation.translate_string(s)
```

所以 `f"You bought 1 {item_name}!"` 在查表时已经是 `"You bought 1 Sandwich!"`。
写成 `old "You bought 1 {item_name}!"` 的条目是**永久无效**的。

→ `tlgen` 只**报告**这类字符串（`tl untranslatable`），不自动转成 `[var]` ——
自动转会制造出实践中那种无效条目。

## 复核方法

```powershell
# 1. 跑插件自己的测试
cd dsh-plugin\dsh-renpy-toolkit
python -m unittest discover -s tests -t tests -q

# 2. 拿成品译料验误报（应为 2 条，均为真缺陷）
python -m renpy_toolkit tl verify <shipped-tl> --allow-empty --max-items 100

# 3. 验自己生成的骨架（应为 0 条）
python -m renpy_toolkit tl verify <outdir> --expect-dialogue 58249
```

引擎侧对账（数字必须逐个一致）：

```powershell
$g = "<game-dir>"
$launcher = (Get-ChildItem $g -Filter "*.py" | Select-Object -First 1).FullName
# 游戏自带的解释器可能写不了 %TEMP%（打包发行版的常见情况），指一个可写目录
$env:TMP = "<writable-dir>"; $env:TEMP = $env:TMP
& "$g\lib\py3-windows-x86_64\python.exe" $launcher $g lint
```

期望输出：

```
The game contains 58,249 dialogue blocks, containing 342,592 words and 2,103,107 characters
The chinese translation contains 58,249 dialogue blocks, containing 342,592 words and 2,103,107 characters
```

## 10. 松散文件 vs 归档：`config.force_archives` 会静默吞掉汉化

`<game>\renpy\loader.py:97` 把优先级写在注释里：

```python
# Files on disk should be checked before archives. Otherwise, among
# other things, using a new version of bytecode.rpyb will break.
archives = [ ]
```

实现处 `load_from_filesystem()`：

```python
if not renpy.config.force_archives:
    try:
        fn = transfn(name)
        return open_file(fn, "rb")
    except Exception:
        pass
return None
```

→ 磁盘上的松散文件**优先**于归档，但只在 `config.force_archives` 为假时。
把它设成 `True`，发行版里的 `game/archive.rpa` 就会盖住写进
`game/tl/<lang>/` 的补丁——**游戏照常启动、界面全英文，没有任何报错**。

这不是假设。一份发行版的 `game/archive.rpa` **2,301,570,939 字节 / 21,857 条目**，
实测条目里 `tl/` 前缀 **0 条**、`.rpyc` **0 条**（全是 audio/images/fonts 资源），
所以松散 TL 确实生效。但只要作者加一行 `config.force_archives = True`，
整份汉化就悄悄失效。

→ `audit.scan_project` 增加 `force-archives` 检查（**error** 级）：
在 `.rpy`/`.rpym` 里匹配去掉注释后的 `config.force_archives = True`。

## 11. 同一语言内重复 `old` 会在启动时抛异常

`<game>\renpy\translation\__init__.py` 的 `StringTranslator.add()`：

```python
def add(self, old, new, newloc):
    if old in self.translations:
        if old in self.translation_loc:
            fn, line = self.translation_loc[old]
            raise Exception("A translation for \"{}\" already exists at {}:{}.".format(
                quote_unicode(old), fn, line))
        else:
            raise Exception("A translation for \"{}\" already exists.".format(
                quote_unicode(old)))
    self.translations[old] = new
```

玩家侧后果：游戏在加载翻译文件时直接异常，**进不到主菜单**。

键是 `(语言, old)` 而不是 `old`：每个语言在 `translator.strings` 里有自己的
`StringTranslator`（`add_string_translation()` 里 `stl = tl.strings[language]`），
所以 `tl/chinese` 与 `tl/spanish` 出现同一个 `old` 完全合法。

→ `audit.scan_project` 增加 `duplicate-translation` 检查（**error** 级），
键为 `(block.language, pair.old)`。

**这个坑的实际来源是生成器不幂等**：向已存在的文件追加（而不是重建），
第二次运行就把每个 `strings` 块再堆一遍。插件侧已用「默认输出目录每次重建 +
文件内去重 + 跨调合并」修掉，并有三连跑幂等验证
（`reported=2679 / on-disk=2679 / duplicated=0`，三次一致）。

**误报基准**：三份真实语料（发行版 `tl/chinese`+`tl/None`+`tl/Spanish`、
真实工程 56 个 TL 文件、自己生成的骨架 53 个文件）跑新检查，
`duplicate-translation` 与 `force-archives` **均为 0**。
