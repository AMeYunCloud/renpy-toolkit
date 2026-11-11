# 从 renpy 源码逐行核实的接口

本文只记录**在 `renpy/renpy` 源码里逐行确认过**的事实，每条都附文件路径与函数名，
方便以后需要时重新 grep 验证。与官方文档冲突时以本文为准（源码即事实）。

> 核实基线：Ren'Py 8.x 分支（`renpy/` 包结构，非 7.x 的 `renpy/` 同名但实现不同）。

---

## 1. 翻译标识符的生成

**文件**：`renpy/translation/__init__.py`

```python
def encode_say_string(s):
    s = s.replace("\\", "\\\\")
    s = s.replace("\"", "\\\"")
    s = s.replace("\n", "\\n")
    s = re.sub(r"(?<= ) ", "\\ ", s)
    return "\"" + s + "\""
```

`TranslateBlock` / `translate` 语句在生成 identifier 时：

```python
code = " ".join([who, encode_say_string(what), *arguments])
identifier = label.replace(".", "_") + "_" + hashlib.md5(code.encode("utf-8") + b"\r\n").hexdigest()[:8]
```

要点：

- **`code` 用空格连接**，`who` 为空时也会产生一个前导空格（`"" + " " + "\"...\""`），
  这一点必须照抄，否则 md5 不同。
- **行尾是 `\r\n`**，不是 `\n`。跨平台一致性靠这个常量。
- `arguments` 里的 `(multiple=N)` 这类会**计入 code**，所以它必须有对应的
  translate 块，否则运行时显示英文。
- `encode_say_string` 里 `re.sub(r"(?<= ) ", "\\ ", s)` 的作用是**把「空格后紧跟的
  空格」转义成 `\ `**，防止连续空格在 .rpy 里被吃掉。手工抄台词时必须复刻。
- 同名冲突时（同一 identifier 出现两次），生成的块里**追加序号**
  （`start_636ae3f5`、`start_636ae3f5_1`），这也是为什么解析时要支持
  「同一 identifier 多个块」并按下标消歧。

**同行 `with` 的归属**：`e "Hi" with vpunch` 是 Say 语句的 `with_` 参数，
**并入 code**；而独立一行的 `with vpunch` 是独立的 `With` 语句，
**不并入**。这是同一个视觉效果在翻译上完全不同的两件事。

---

## 2. 运行期的字符串查表链路

```
Text.set_text(text)
  → renpy.substitutions.substitute(text, translate=True)
    → renpy.translation.translate_string(s)
      → StringTranslator 查表
```

结论：**`screen`、`say`、`menu` 的 caption 里出现的文本字面量，一律会过表**，
与源码里有没有 `_()` 无关。`_()` 只是给**提取器**看的标记（决定这句是否被
`renpy translate` 收进 `strings` 块），不影响运行时是否翻译。

例外：`[var]` 插值里若写成 `[mood!t]`（带转换符 `!t`）才会查表，
`[mood]` 是原样输出。这个区别在汉化里经常被忽略。

---

## 3. `.rpyc` 容器格式

**文件**：`renpy/compat/` 之外的加载逻辑集中在 `renpy/script.py` 的
`RENPY RPC2` 处理段。

```
偏移 0:  b"RENPY RPC2"          # 10 字节魔数
偏移 10: slot 表，每项 12 字节
         struct.unpack("<III", item) → (slot, start, length)
         遇到 slot == 0 立即结束
其后:    各 slot 的数据块，每块 zlib.compress
末尾 16 字节: 源 .rpy 的 md5 digest
```

加载时对每个 slot 尝试 `zlib.decompress`，**解不开就原样保留**（兼容早期未压缩格式）。
多个 slot 用 `b"\n".join(...)` 拼回源码文本。

**新鲜度判定**：Ren'Py 比较 `.rpyc` 末尾 16 字节与当前 `.rpy` 的 md5，
**与 mtime、文件大小都无关**。这一点很重要：
- 判断「`.rpyc` 是不是过期」必须算 md5，不能比大小。
- 打包/分发时如果 `.rpy` 与 `.rpyc` 的 md5 不匹配，Ren'Py 会重新编译（若源码在）
  或直接报错（若源码不在）。

---

## 4. `.rpa` 归档

**文件**：`renpy/loader.py` 的 `RPAv2` / `RPAv3` 处理。

头行格式（含换行符在内）：

```
RPA-2.0 %016x\n                                  # 25 字节
RPA-3.0 %016x %08x\n                             # 34 字节
```

- `%016x` 是**索引区偏移**，`%08x` 是 **XOR key**。
- 索引 = `pickle.loads(zlib.decompress(tail))`，`pickle.HIGHEST_PROTOCOL`。
- 索引是 `{相对 game 路径: [(offset, length)]}` 或 `[(offset, length, prefix)]`。
- **混淆**：v3 里 offset 与 length 都与 key 异或，但 **prefix 不参与异或**。
- **读取**：`data = prefix + f.read(length - len(prefix))`。

注意默认 key 是 `0xDEADBEEF`，但**必须从头部读**，不能假设。

工具包实现见 [`../dsh-plugin/dsh-renpy-toolkit/renpy_toolkit/rpa.py`](../dsh-plugin/dsh-renpy-toolkit/renpy_toolkit/rpa.py)，
格式细节见 [`rpa-format-spec.md`](rpa-format-spec.md)。

---

## 5. 切换语言时发生什么

**文件**：`renpy/translation/__init__.py` 的 `change_language()`。

顺序：

1. 样式复位到 `init` 结束时的状态（`renpy.style.restore_initial()`）。
2. 按顺序执行 `translate <lang> python:` 块。
3. 执行 `translate <lang> style:` 块。
4. 重建样式（`renpy.style.rebuild()`）。

关键推论：**`translate <lang> python:` 块在每次切语言时都会重跑**，
所以块内不能有外部副作用（写文件、累加全局计数）。
官方文档明确写了这一条，源码行为一致。

---

## 6. `config.defer_tl_scripts`

设为 `True` 时，`game/tl/<lang>/` 下的脚本**只在切到该语言时加载**，
并且**只允许 `translate` 类块**（出现 `label`、`define` 等会报错）。

用途：多语言游戏里避免把所有语言的翻译全加载进内存。
副作用：`tl/<lang>/` 里如果有 `init python` 会**不执行**（因为整个文件延迟加载），
所以需要 init 逻辑时要放在 `game/` 根下，或用 `translate <lang> python:` 块。

---

## 7. 语言选择顺序

`renpy/translation/__init__.py`：

```
RENPY_LANGUAGE 环境变量
  → config.language
  → 记住的上次选择（persistent）
  → autodetect
  → config.default_language
  → None（原文）
```

`renpy.known_languages()` 返回所有已存在的 `tl/<lang>/` 目录对应的语言名。

---

## 8. 官方 CLI 命令的稳定性

`renpy/arguments.py` 的 `renpy.arguments.register_command(name, function, uses_display=False)`。

**官方文档明言 CLI 不是稳定接口**。这意味着：

- 不要依赖 CLI 的**输出文本格式**做解析（版本间会变）。
- 要依赖就依赖**退出码**和**产物文件**（`tl/<lang>/*.rpy` 是否被生成/修改）。
- 想加自己的命令：在 `init python` 里 `register_command`，比外部脚本更稳。

`sdk.py` 因此把 CLI 输出当「给人看的日志」处理，只解析
`--count` 这种有明确数值语义的输出。

---

## 9. Android 上的一个隐蔽陷阱

在 Android 上 `store['renpy']` **天生就是 `renpy.exports` 门面模块**。
如果代码里写 `import renpy.loader`，会把这个门面**顶掉**，导致后续
`renpy.say()` 等全部失效。

正确写法：

```python
import renpy.loader as _loader     # 起别名，不动 renpy 这个名字
# 或
_loader = sys.modules["renpy.loader"]
```

配套的另两个 Android 坑：

- `config.font_replacement_map` **只在启动时查一次**，运行期改它无效。
- 字体文件名必须是**纯 ASCII**（APK 内的资源名限制）。

---

## 10. 校验器的等价关系（实现 TL 校验时用）

| 检查项 | 判据 |
| --- | --- |
| 空译文 | `new == ""` 且 `old != ""` |
| 标签配平 | `{}` 与 `[]` 的**多重集**相等（不只是数量） |
| 字面换行 | 译文值里出现真实的 `\n` 字符（不是 `\\n` 两字符） |
| 引用未定义图像 | `show` 的 image name 不在 `image` 定义与自动发现集合里 |
| `.rpyc` 过期 | `.rpyc` 末尾 16 字节 ≠ md5(当前 `.rpy`) |
