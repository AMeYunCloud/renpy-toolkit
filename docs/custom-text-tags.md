# 官方文档第二轮：自定义文本标签

读的是 8.5.4 的 `custom_text_tags.html`，目的不是了解"怎么定义标签"，而是回答
一个插件里悬着的问题：**`tl verify` 怎么区分「译者打错标签」和「游戏自己的标签」**。

## 一、文档给出的事实

自定义标签登记在两张表里：

```python
define config.custom_text_tags = { }               # 可包裹内容，有 {/tag}
define config.self_closing_custom_text_tags = { }  # 自闭合，没有 {/tag}
```

登记方式：

```python
init python:
    config.custom_text_tags["big"] = big_tag
    config.self_closing_custom_text_tags["bang"] = bang_tag
```

标签函数签名 `f(tag, argument, contents)`（自闭合版少一个 `contents`），返回新的
`(kind, value)` 列表，`kind` 取 `renpy.TEXT_TEXT` / `TEXT_TAG` / `TEXT_DISPLAYABLE`
/ `TEXT_PARAGRAPH`。

文档里两条警告值得记：

1. **标签函数可能在预测（prediction）阶段被调用**，所以不能依赖游戏状态、不能有
   副作用。这解释了为什么自定义标签不能"顺手"用来做翻译替换。
2. **`{p}` `{w}` `{nw}` `{fast}` 在自定义标签之前处理**，不应被包进自定义标签里。
   这和 `protect.py` 把 `w/nw/fast/p` 归为自闭合是一致的。

## 二、据此改掉的真 bug

### 引擎权威表

`<game>\renpy\text\extras.py` 里有一张表，**值是"这个标签能否被闭合"**：

```python
text_tags = dict(
    alpha=True, alt=True, art=True, axis=True,
    done=False, instance=True, image=False, p=False, w=False, fast=False,
    b=True, i=True, u=True, a=True, plain=True, font=True, color=True,
    outlinecolor=True, size=True, noalt=True, nw=False, s=True,
    shader=True, rt=True, rb=True, k=True, cps=True, space=False, vspace=False,
    )
```

`False` 的那些，写 `{/image}` 会在渲染时抛
`{/%s} isn't valid, since the %s text tag doesn't take a close tag`。

拿我们原来的表逐项对账，发现三类错：

| 类型 | 具体 | 后果 |
| --- | --- | --- |
| 引擎有、我们没收录 | `axis`、`instance`、`k`、`outlinecolor`、`plain` | 译文里合法的 `{outlinecolor=#000}` 被判成"未知标签" |
| 我们当自闭合、引擎说可闭合 | `cps`、`art`、`rb`、`ruby`、`alpha`、`kern`、`vertical`、`rtl`、`clear` | 平衡检查把 `opens - closes` 算错，报假的"标签未闭合" |
| 我们当可闭合、引擎说自闭合 | 无 | — |

`outlinecolor` 这一条最要命：一份真实成品译料里它出现 **2,910 次**，全是合法的
`{outlinecolor=#000000}...{/outlinecolor}`。旧表把它算进 `SELF_CLOSING_TAGS`，
于是每次闭合都被当成"多余闭合"，平衡数算反。

修法：`SELF_CLOSING_TAGS` 与 `KNOWN_TAGS` 全部照抄引擎表；模块内原本还藏着一份
重复定义的 `SELF_CLOSING_TAGS`（在 `_atom_counts` 上方），已删除，只留模块级那一份。

### 从源码里把自定义标签读出来

`check_unknown_tags` 原本只能靠"源码里也出现过就放行"的启发式。现在新增：

```python
protect.custom_tags(*sources) -> (wrapping, self_closing)
```

用三条正则从游戏脚本里扫 `config.custom_text_tags["x"] = ...`、
`config.self_closing_custom_text_tags["x"] = ...`、`renpy.register_text_tag("x", ...)`，
允许单/双引号与任意空白。`verify_tl_dir(..., game_dir=DIR)` 会把 `game/` 下所有
`.rpy`/`.rpym` 读一遍，把结果喂给 `check_unknown_tags(..., known=...)`。

对 `.rpyc`-only 的游戏这一步读不到东西（源码不在盘上），那就退回原来的启发式 ——
**退化的行为与改之前一致**，不会变得更糟。

CLI：

```bash
python -m renpy_toolkit tl verify game/tl/chinese --game-dir game
```

## 三、验证

对三份真实语料跑回归，改表前后 verdict 不变（说明修的是误报、没有放松检查）：

```
mother         exit=1  56 file(s), 57552 block(s); coverage 59589/59597 (100.0%); 2 error(s), 71 warning(s)
ported         exit=1  53 file(s), 58249 block(s); coverage 60921/60927 (100.0%); 1 error(s), 878 warning(s)
ported+tags    exit=1  53 file(s), 58249 block(s); coverage 60921/60927 (100.0%); 1 error(s), 878 warning(s)
```

新增 12 个测试（`TestTagTables` 7 个 + `TestCustomTagDiscovery` 5 个），当时全套
**385 tests OK**（当前 422）。其中 `test_outlinecolor_and_plain_are_closeable` 与
`test_engine_self_closing_tags_are_not_closeable` 是直接照着引擎表写的回归，
表再被改错就会立刻失败。

### 第三轮：`{=style}` 曾被整类漏检

`text.html` 里还有一句容易被跳过的话：

> There are also text tags where the tag name is empty. In this case, the
> argument is taken to be the name of a style.

也就是 `{=mystyle}…{/=mystyle}`。`tag_name()` 对它的判定是"没有标签名"（来源
字符类 `[^{}=:/]*` 排除 `=`，捕获出空串），于是 `_atom_counts` 里那句
`if not name or name.startswith(("#", "=")): continue` 把它**整类跳过**：

| 译者犯的错 | 修复前 | 修复后 |
| --- | --- | --- |
| 丢掉整个 `{=style}` 对 | 不报 | `missing 1x content tag '{=mystyle}'` |
| 只丢 `{/=style}` | 不报 | `missing 1x content tag '{/=mystyle}'` |
| 凭空多一个 `{/=style}` | 不报 | `adds 1x content tag '{/=mystyle}'` |
| 忠实翻译 | 不报 | 不报 |

这里有个连带的坑值得记：判断必须看 **chunk 原文**而不是 `name`。我第一版写成
`if name.startswith("=")`，它永远不成立 —— 因为正则早就把 `=` 从名字里排掉了。
正确的判定是 `chunk.text.startswith("{=") or chunk.text.startswith("{/=")`。

`{#ctx}` 不受影响：它是翻译标记，没有闭合标签、不影响渲染，仍然跳过。
`{feature:liga=0}` / `{axis:width=125}` / `{shader=…:…}` 这些**有名字**、参数里
带冒号的标签也不会走这条分支（`_TAG_NAME_RE` 先按 `=` 切再按 `:` 切）。

`_count_style_tag()` 除配对计数外还把整对计入 `content`，因为样式承载字体、
颜色、字号 —— 整对丢掉后没有任何"未闭合"可言，但那一行的排版已经变了。

## 四、没做但记下来的

`custom_text_tags.html` 里提到 `Text(..., tokenized=True)` 可以直接吃
`(kind, value)` 列表。这条路对翻译工具没用 —— 我们要产出的是 `.rpy` 源文件，
不是运行时对象。

`cds.html`（自定义语句）没有细看：它影响的是"游戏作者能不能加新语法"，而翻译
工具只处理 `translate` / `strings` / `python` 三类块，新语句会以
`# TODO: unsupported node` 的形式出现在反编译结果里，不影响 TL 生成。
