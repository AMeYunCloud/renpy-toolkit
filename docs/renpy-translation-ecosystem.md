# Ren'Py 汉化生态参考

本文是随工具包发布的背景资料：说明第三方汉化生态的工具链、工程惯例、反编译做法与
中文本地化难点，以及它们与 [`docs/official-cli-and-translation.md`](./official-cli-and-translation.md)
记录的官方翻译机制之间的关系。

官方机制（四类可翻译对象、`tl/<lang>/` 目录、`translate` / `strings` 块、翻译标识符生成、
`config.defer_tl_scripts`、Unsanctioned 汉化路径、CLI 全部子命令）已在该文中逐条核实，
本文**不重复**，只在有交叉处引用。

> 可信度标记约定（每条结论后括注）：
> - **【官方】** = renpy.org 官方文档 / 官方 changelog 原文
> - **【官方文档·推测】** = 由官方文档事实直接推出的结论，文档未明说
> - **【仓库 README】** = 工具/教程仓库自述（仓库主页或文档站）
> - **【教程】** = 中文社区的教程、文档站、博客（一手实践，但未经独立验证）
> - **【论坛】** = Lemma Soft 论坛等讨论帖（个人经验，不可当作已验证事实）
> - **【未能核实】** = 未找到可靠来源，或来源本身不足以支撑结论
>
> 版本基准：Ren'Py 8.5.4 文档；社区文档站 `docs.dclef.com`。

---

## 1. 总览与结论摘要

1. **生态其实是"官方机制 + 一圈补丁"，不存在第三套翻译模型。** 所有主流汉化工作流——无论起点是源码、`.rpyc` 还是 JSON——最后都收敛到官方那套 `game/tl/<lang>/*.rpy` 的 `translate` / `translate strings` 块。第三方工具解决的从来不是"怎么翻"，而是**"怎么拿到被 `.rpa` 打包、被 `.rpyc` 编译掉的可翻译文本"**【官方+仓库 README】。

2. **源码级汉化是质量天花板，二进制反编译是可用性底线。** 拿到 `.rpy` 源码时，可直接用 launcher "Generate Translations" 或 CLI `translate <lang>` 生成全量骨架，标识符由引擎自己算，最安全；只有发布包时，才用 unren / unrpyc 之类反编译 `.rpyc` 并解包 `.rpa`，此路径**必然伴随**标识符漂移、文本遗漏、加密失败三类风险【教程+仓库 README】。

3. **`extract_strings` / `merge_strings` 的 JSON 中转是被严重低估的中庸方案。** 官方 CLI 自带 `dialogue <lang>`（导出 TSV/Markdown）、`extract_strings <lang> <dest.json>`、`merge_strings <lang> <src.json>`（含 `--reverse` / `--replace`，且隐含 `--compile`），可以做到"提取 → 外部表格/AI 翻译 → 回填"，全程不碰反编译、不动标识符。缺点只覆盖**对话与 strings**，不覆盖图像/样式/非标准写法【官方 CLI 文档】。

4. **`rpa` 解包层已经非常成熟且规格公开**，本工作区 [`plugin/dsh-renpy-toolkit/renpy_toolkit/rpa.py`](../plugin/dsh-renpy-toolkit/renpy_toolkit/rpa.py) 与 [`docs/rpa-format-spec.md`](./rpa-format-spec.md) 已覆盖 RPA-2.0/3.0 的读写；社区等价物是 `shizmob/rpatool`（已迁 Codeberg）与 PyPI 包 `unrpa`（`Lattyware/unrpa`）。**rpa 层不是汉化的瓶颈**——瓶颈在 `.rpyc`【仓库 README+本工作区】【官方文档·推测：rpa 只做索引/XOR 混淆，不是加密】。

5. **`.rpyc` 反编译是整条链上最脆弱的一环，且随版本断裂。** 官方从不承诺 `.rpyc` 格式稳定；社区观测到的断代点是 Ren'Py 7→8（Python 2.7 → Python 3），表现为"老 unren 对新引擎报错"。中文社区教程已把"按 SDK 大版本选 unren 版本"明确写进流程【教程】。

6. **"翻译文件导出的文本不全"是生态共识，也是最容易被低估的坑。** 官方生成器只扫描它认识的语句与 `_()` 包装的字符串；作者用变量拼接、自定义文本标签、自绘 draw 文本、`__()` 之外的裸字符串时，文本**根本不会进入 `tl/`**。此时任何"只改 `tl/`"的汉化方案都会漏翻，必须回源码改【教程+官方·推测】。

7. **Dclef/renpy-tl 不是"汉化工具"，而是一份中文汉化教程/知识库仓库（GPL-3.0）。** 它的主体是 `docs/` 下的 VuePress 文档站 `https://docs.dclef.com/`，内容 = 官方 sanctioned 流程的中文化 + 若干实战修补（字体、语言开关、unren 版本选择、vscode 逐条翻译）。它自己不做文本提取/回填；其"翻译工具"章节实际转述的是 `anonymousException/renpy-translator`【仓库 README+文档站原文】。

8. **中文本地化的真正难点不在"翻译"，而在"排版与字体"。** 缺字（口口字）、文本框溢出、半宽/全角标点、换行断句是四个高频失败点；社区"通杀"做法是**全局替换字体为含 CJK 的 ttf**（`SourceHanSansLite.ttf`，Ren'Py SDK 自带），而不是逐个修【教程】。竖排 Ren'Py 原生不支持，需要自定义 draw/多行拼接，社区少有成熟方案【未能核实：未找到官方竖排支持文档】。

---

## 2. 主流汉化工作流对比

| 维度 | A. 源码级汉化 | B. 二进制反编译汉化 | C. `extract_strings` / `merge_strings` JSON 中转 |
| --- | --- | --- | --- |
| **起点** | 作者提供 `.rpy` 源码工程（或开源游戏、jam 游戏） | 仅发布包：`game/` 下全是 `.rpyc`，资源在 `.rpa` 里 | 发布包或源码包均可；只要有能跑起来的引擎 + SDK |
| **流程** | launcher → Generate Translations / CLI `translate <lang>` → 得到全量 `tl/<lang>/*.rpy` 骨架 → 逐条填译文 → 引擎自动生成 `.rpyc` | ① unren/`unrpa` 解 `.rpa` ② unrpyc 反编译 `.rpyc` → `.rpy` ③ 再走 A 的流程 ④ 重新打包/直接放 `game/` 目录 ⑤ 处理字体 | ① `renpy ... dialogue <lang> --strings --notags` 导出表格 ② 外部翻译（人工/表格/AI） ③ `extract_strings <lang> dest.json` ④ 翻译 JSON ⑤ `merge_strings <lang> src.json` 回填（隐含 compile） |
| **标识符安全** | 最高：标识符由引擎在加载脚本时自己算，汉化者完全不碰 | 中：反编译产物若与原 `.rpyc` 的语句结构不完全一致，重编译后标识符会漂移，旧存档/旧译文可能错位 | 高：JSON 以原文/old 串为键，不涉及对话标识符 |
| **覆盖面** | 全量（对话、menu、`_()` 字符串、图片、样式） | 与 A 相同，但**减掉**反编译失败的部分 | **仅** 对话 + strings；不含图像/样式/文件替换；不覆盖"生成器扫不到的文本" |
| **主要风险** | 作者不开源时无法使用；非标准写法仍需手工接管 | 版本断代（7 vs 8）、自定义加密、反编译不完整、目标文件被 hash 校验 | JSON 结构被翻译引擎破坏（`[var]`/`{}`/`<>`）、覆盖已有译文（`--replace` 误用） |
| **适合场景** | 与作者合作、开源项目、委托汉化；**任何可获得的场景都应优先走这里** | 只有发布包、作者失联、需要快速出"可玩汉化" | 大批量机翻 + 人工校对的流水线；多语言并行；需要把文本交给外部译者/AI 又不想泄露源码结构 |
| **与官方关系** | 完全 sanctioned | unsanctioned（官方建议先征得作者同意，见官方 translation.html 的 Unsactioned 章节） | 完全 sanctioned |

补充说明：

- **C 列的"隐含 compile"很关键**：`merge_strings` 会顺带编译，所以回填后 `.rpyc` 已是最新，不会出现"改了 JSON 但不生效"的错觉【官方 CLI 文档】。
- **B 列的重新打包是可选项**：把 `.rpy`/`.rpyc` 直接散放在 `game/` 目录下即可生效，Ren'Py 会优先使用同目录下的散文件；打回 `.rpa` 只在需要"看起来干净"或规避简单校验时有意义【官方文档·推测】。
- **B 列与 A 列在字体问题上完全相同**：反编译本身不解决缺字，字体必须单独处理（见第 5 节）。

---

## 3. 工具清单

> 原则：只列**能核实到**仓库主页/搜索条目/文档页的工具；Star 数只在搜索结果卡片上读到时才写，并标注为"读取时点读数"；否则写"未能核实"。

### 3.1 解包（`.rpa` → 文件）

| 名称 | owner/repo | 状态 | 用途 | 可信度 |
| --- | --- | --- | --- | --- |
| `rpatool` | `shizmob/rpatool`（**已迁移**至 `codeberg.org/shiz/rpatool`） | 真实存在；Codeberg 页面显示 19 stars、22 commits、Python 100%、WTFPL | 单文件 `rpatool/rpatool.py`；`-l` 列表 / `-x` 解包 / `-c` 创建 / `-d` 删除 / `-a` 添加 / `-2` `-3` 指定版本 / `-k KEY`（默认 `0xDEADBEEF`）/ `-p COUNT` 对齐填充 / `-o OUTFILE`；支持 `ARCHIVE=REAL` 映射（从别的文件借数据）；API `RenPyArchive.load/save/list/has_file/add/change/remove/read`。README 明示**只支持写回 RPAv2/RPAv3**，并附免责声明（仅用于作者允许修改/提取的文件） | 【仓库 README】 |
| `unrpa` | `Lattyware/unrpa`（PyPI 包名 `unrpa`） | 真实存在；描述 "Extract files from the RPA archive format"；PyPI 上 18 issues（抓取时点） | `python -m unrpa archive.rpa` 解包；中文教程里最常被引用的命令行方案 | 【仓库 README+教程】 |
| `Chinese-UnRPA` | `Fppk/Chinese-UnRPA` | 搜索命中，描述"中文版 UnRPA，用于解包 ren'py" | 汉化版 UI 包装，功能应等价；细节**未能核实** | 【搜索条目】 |
| `rpaextract` | `Kaskadee/rpaextract` | 搜索命中，描述 "An application for extracting contents from Ren'py archives" | 图形化提取工具；依赖/版本**未能核实** | 【搜索条目】 |
| `UnRen*.bat` | `F95Sam/UnRen`（73 stars 抓取时点，Batchfile；Linux 移植 `Linux-gaming-wine/UnRen-Linux`） | 真实存在；上游讨论帖在 f95zone（`UnRen.bat v1.0.11d`） | **一体化批处理**：RPA 解包 + RPYC 反编译 + 控制台/开发者菜单开启 + 一些档案修补。中文教程把它当作"一次解决第 2、3 步"的入口 | 【仓库 README+论坛】 |
| 本工作区实现 | [`plugin/dsh-renpy-toolkit/renpy_toolkit/rpa.py`](../plugin/dsh-renpy-toolkit/renpy_toolkit/rpa.py) | 本仓库自研 | RPA-2.0/3.0 读写：`RPAError`、`Entry(name/offset/length/prefix)`、`normalise_name()`、`_read_header()`；不依赖第三方 | 【本工作区】 |

### 3.2 反编译（`.rpyc` → `.rpy`）

| 名称 | owner/repo | 状态 | 用途 | 可信度 |
| --- | --- | --- | --- | --- |
| `unrpyc`（Un.rpyc） | `CensoredUsername/unrpyc` | 真实存在；搜索卡片显示 1265 stars（抓取时点）、MIT；有 `v2.0.1`（2024-04-01）、`v2.0.4` 等 release；描述明确 "It will **not** extract files from `.rpa` archives. For that, use rpatool or …" | Ren'Py 脚本反编译器的事实标准。注意：它是**单文件**工具，按 Ren'Py 的惯例放在 `game/` 下随游戏启动一次即可产出 `.rpy` | 【仓库 README+教程】 |
| `UnRen`（含反编译环节） | `F95Sam/UnRen` | 同 3.1 | 调用/内置反编译器，一条龙；中文教程推荐"Ren'Py ≤7 用 `unren0.91.bat`，≥8 用 `UnRen-1.0.10.bat`" | 【教程+论坛】 |
| `rpycdec` | `cnfatal/rpycdec`（fork：`Portalboy07/rpycdec`） | 搜索命中，描述 "Tools to decompile renpy .rpyc .rpymc files"；活跃度/维护度**未能核实** | 备选反编译器；特性与兼容版本**未能核实** | 【搜索条目】 |
| `garc`（"Gar 的 RenPy 反编译器"） | **未能核实** | 多轮检索（`garc renpy decompiler`、`garc Ren'Py decompiler Gar github`）返回的全是 Global Alliance for Rabies Control、印度 Global Automotive Research Centre、美军无人艇 GARC 等无关实体；GitHub 平台搜索接口未能取得结果 | 无法确认存在 | **【未能核实】**——建议后续用 GitHub 站内搜索复核，或向提出该名字的一方索取链接 |

### 3.3 辅助/流水线工具

| 名称 | owner/repo | 状态 | 用途 | 可信度 |
| --- | --- | --- | --- | --- |
| `renpy-translator` | `anonymousException/renpy-translator` | 真实存在；详见第 6 节 | 读取已生成的 `tl/` 文件，把 `new`/`old→new` 交给翻译引擎（Google/百度/OpenAI 等）批量回填翻译，再由人校对润色 | 【仓库 README+教程转述】 |
| `renpy-translator`（作者封装版） | `Dclef/renpy-translator` | 真实存在；2 stars（抓取时点）、AGPL-3.0；About "renpy翻译工具 renpy tl to google trans" | 与上游同名、作者不同的实现/包装；与上游的关系**未能核实** | 【仓库 README】 |
| 重命名 X- 前缀工具 | 作者 `dclef` 自述"源码已放在 github 上"（具体 repo **未能核实**） | 教程级 | 安卓包的资源常带 `X-` 前缀，需批量重命名；作者声明"只能重命名当前目录下的文件，不能处理子目录" | 【教程】 |
| VSCode + Comment Translate | 第三方 VSCode 插件 | 教程级 | 悬停翻译字符串，逐句人肉翻译；可切 Google/Baidu/Bing，百度通用翻译需 APP ID + 密钥；**必须卸载 VSCode Python 扩展**否则悬停不显示；速度过快会超时 | 【教程】 |
| 本工作区插件 | `plugin/dsh-renpy-toolkit/renpy_toolkit/` | 本仓库自研 | 面向 `tl/<lang>` 的读写与校验（参见 `official-cli-and-translation.md` 描述的官方格式约束） | 【本工作区】 |

> 说明：`renpy-tools` 这个仓库名没有找到可靠对应物（官方 SDK 中的 `rpatool`/`unrpa` 与社区 `shizmob/rpatool` 是两个来源）。使用者口中的"renpy-tools 自带 rpatool"多半是把 **SDK 的 `launcher/game/tools/`** 与社区 rpatool 混称——**未能核实**。

---

## 4. 反编译与解包技术细节（工具无关）

### 4.1 `.rpa`：不是加密，只是索引混淆

- RPA-2.0/3.0 的结构：文件头 + zlib 压缩的 pickle 索引；RPAv3 的索引字段与一个 key（默认 `0xDEADBEEF`）做 XOR，**只有索引字段参与混淆，文件内容前缀（prefix）不混淆**【本工作区 `rpa-format-spec.md`】。
- 所以任何"解包失败"在 `.rpa` 层几乎只有三种原因：**magic 不是 RPA-2.0/3.0**（被套壳/自定义格式）、**key 不是默认值**（需 `-k`）、**索引被二次加密**（需要拿到作者的打包脚本）。前两种可解，第三种实际上不可解【官方文档·推测+教程】。
- `config.archives` 在启动时自动按**反 ASCII 序**填充（`patch02.rpa` > `patch01.rpa` > `data.rpa`），同名文件取**先找到的那个**。这意味着：**散放在 `game/` 的文件与 `.rpa` 内的同名文件，谁生效取决于加载顺序**，汉化时"改了文件却不生效"常见于此【官方 config 文档】。
- 官方对打包的态度值得引用：`build.html` 在 Archives 一节直说 "While not very secure, this protects files from casual copying"，并劝作者 "Please think twice about archiving your game"（照顾未来的平台移植）。这从官方口径印证了：`.rpa` 的定位是**防手滑**而非防破解【官方 build 文档】。

### 4.2 `.rpyc`：编译产物 + ID 仓库

- `.rpyc` 同时承担两件事：**编译后的脚本**，以及**每行对话/场景的唯一 ID 存储**。官方 "The Old-game Directory" 一节写得很清楚：`.rpyc` "also store important details, like unique IDs for each line of dialogue or scene, that save files rely on"【官方 build 文档】。
- 推论（对汉化重要）：**汉化时如果重建了 `.rpyc`，翻译标识符（如 `start_636ae3f5`）可能变化**，这会影响旧 `.rpa` 里的译文、旧存档、以及外部维护的对照表。这正是官方提供 `old-game/` 目录的原因——把上一版 `.rpyc` 放在 `old-game/` 下，Ren'Py 会在重编译时复用未变部分的 ID【官方 build 文档】。
- 反编译的本质是：从 `.rpyc` 的 AST 反序列化结果**重建近似源码**。它不保证字节级还原：注释、原始空白、字符串前缀（`u`/`r`）、部分执行顺序信息会丢；作者若用了非常规语句或自定义 CDS，重建可能失败或走样【仓库 README+论坛】。
- 版本断代：Ren'Py 8 = Python 3，Ren'Py 7.5 = Python 2.7 的续存分支；`.rpyc` 的序列化依赖 Python 的 marshal/pickle 语义，跨 major 不兼容。社区教程据此给出"unren 版本与 SDK 版本配对"的做法【官方 changelog+教程】。

### 4.3 厂商锁/反汉化手段（仅技术了解）

| 手段 | 真实强度 | 观察到的应对 | 可信度 |
| --- | --- | --- | --- |
| 打 `.rpa` 包 | 很低（只是打包） | `unrpa`/`rpatool` 直接解 | 【官方+仓库 README】 |
| 改 RPA key | 低 | `rpatool -k KEY`（若 key 能从启动脚本/日志反推） | 【仓库 README】 |
| 仅发布 `.rpyc`，不带 `.rpy` | 低—中（取决于反编译器对新版支持） | unrpyc / UnRen | 【仓库 README】 |
| 自定义 `.rpyc` 序列化 / 套壳 exe | 中—高 | 需要逆向壳；作者在教程里还把"自定义加密"当作**卖点**推荐给开发者/汉化者 | 【教程】 |
| 文件 hash / 完整性校验 | 中 | 通常散落在脚本里，反编译后可定位并绕过；但会牵连存档校验 | 【未能核实：未见公开的、可复核的实现样本】 |
| 平台侧（Steam 成就、DRM） | 与汉化无关 | — | — |

> 立场说明：本节仅用于**识别与评估风险**（例如判断某个汉化包为什么在某游戏上做不下去），不提供绕过步骤。官方对 unsanctioned 翻译的态度是"先征得作者同意"【官方 translation 文档】。

---

## 5. 中文本地化特有难点

按"被踩到的频率"排序。

### 5.1 缺字（口口字）—— 第一杀手

- 现象：中文渲染成方块/问号。
- 根因：游戏的 `gui.text_font` / `gui.name_text_font` 等指向不含 CJK 字形的字体（Ren'Py 默认 `DejaVuSans` 只覆盖西部语言，官方 `translating_renpy.html` 明说"other fonts are often needed"）【官方】。
- 通杀做法（教程级，社区广泛使用）：把 `SourceHanSansLite.ttf`（**Ren'Py SDK 自带**，路径 `launcher/game/fonts`）拷进 `game/`，然后全文替换 `gui.rpy` / `screens.rpy` 中所有 `.otf` / `.ttf` 引用为 `SourceHanSansLite.ttf`【教程】。
- **更干净的做法**（不碰作者文件，冲突最小）：在 `game/tl/chinese/style.rpy` 里写一个 `translate chinese python:` 块，把全套 `gui.*_font` 指过去：

```renpy
translate chinese python:
    gui.system_font = gui.main_font = gui.text_font = gui.name_text_font = \
        gui.interface_text_font = gui.button_text_font = gui.choice_button_text_font = \
        "SourceHanSansLite.ttf"
```

  【教程+官方 style 翻译机制】。注意官方约束：`config.defer_tl_scripts` 打开时，tl 文件**只能**包含 `translate` / `translate python` / `translate style` 三类块，上面的写法正好合规【官方 translation 文档】。

### 5.2 半宽/全角标点

- 中文文本请使用全角 `，。！？：「」` 而不是 `,.!?`；省略号用 `……`（U+2026 ×2）而非 `...`。Ren'Py 的换行算法按 UAX #14 做行断点，全角标点有正确的"禁则"行为，半角标点会出现在行首。
- 需要微调时，Ren'Py 提供了官方 API `renpy.language_tailor(chars, cls)`，可把某类字符的行断类改成 `ID`（视作表意文字，前后都允许断行）。这是**唯一**的官方断行微调口子【官方 other.html】。

### 5.3 换行与文本框溢出

- 中文信息密度高，同一句英文翻成中文通常**变短**，但在窄栏 NVL / 气泡（`bubble.html`）里反而容易因标点和引号换行难看。
- 手工换行用 `\n`；但**不要把 `\n` 写进 `old` 字符串**——`old` 必须与源码字符串**完全一致**，否则 `translate strings` 匹配不上【教程+官方】。
- 文本框高度在 `screens.rpy` 的 `say` screen 里由 `yalign`/`area` 决定，权威做法是在 `tl/<lang>/` 里用 `translate <lang> style` 覆盖 style 属性，而不是改作者文件【官方 style 翻译】。

### 5.4 竖排

- Ren'Py 原生文本渲染没有"竖排"模式；社区常见替代是把每行拆成单字并用 `vbox`/多次 `text` 拼装，或对图片式文本框做替换。**未找到官方竖排支持文档，也无成熟的公开竖排方案——判定为【未能核实】**。多数汉化项目选择放弃竖排或重做美术。

### 5.5 结巴、口吃、口语与拟声

- 原作用连字符表示结巴（`"I-I don't know..."`）时，中文用 `我、我不知道……` 或 `我……我不知道`；要注意 `-` 在 Ren'Py 文本标签里不是保留字符，但 `{` `}` `[` `]` 是。
- 这是文本级处理，与工程无冲突；但**整句翻译引擎会把结巴结构冲掉**，这是采用 `renpy-translator` 类工具后必须人工复检的固定成本（上游文档也承认"特殊符号结构被破坏"是最常见故障）【教程】。

### 5.6 `MISSING_TRANS` / orphan translation 相关 lint

- 官方 `lint` 对孤儿翻译的定义是"不引用主语言字符串的 translate 语句"，并提供了 `--no-orphan-tl` 关闭该检查、`--error-code` 让 lint 失败返回错误码【官方 CLI 文档】。
- 实际踩坑：
  - **改了原文但没删旧译文** → 旧 `translate` 块变成 orphan；
  - **原作者更新脚本后重新生成翻译** → `translate <lang>` 只补缺失、不动已有，导致新旧标识符共存；
  - 用反编译产物重编译后标识符变化 → 旧块整体变成 orphan。
- 实务处置：把 `lint` 作为汉化 CI 的一环；对 orphan 先确认是"原文变了"还是"标识符漂移了"，前者删块重修，后者应从 `old-game/` 复用 ID【官方+官方 build 文档·推测】。

### 5.7 其他高频坑（教程级）

- **`tooltip` / `notify` 里的字符串翻不动**：需要在 tl 文件加 `old "[tooltip]"` / `new "[tooltip!t]"` 的 `translate <lang> strings:` 块；`notify` 需要把源码里的 `_()` 改成 `__()`。
- **翻译文件导出不全**：生成器正则扫不到作者自定义文本 → 必须回源码改。
- **汉化后白屏**：多半是填了空字符串（`new ""`）或忘了换字体。
- **默认语言**：`define config.language = "chinese"` 放在 `screens.rpy` 前面即可开机即中文（注意这里的 `"chinese"` 必须与你生成翻译时的语言名一致）。
- **语言名会被当 Python 标识符**：官方明说备用语言名"必须能当 Python 标识符"，所以 `chinese`/`schinese` 都合法，`zh-CN`/`简体中文` **不合法**【官方】。

---

## 6. 与 Dclef/renpy-tl 的关系分析

### 6.1 它是什么（已抓到，结论为一手）

- **仓库**：`Dclef/renpy-tl`，GitHub 首页显示 **91 stars / 14 forks / 1 watcher**（抓取时点）、**GPL-3.0**、topics: `chinese-translation, ren-py, renpy, tutorial`、About "ren'py游戏汉化包和汉化教程"。
- **形态**：**不是软件，是教程站**。仓库主体是 `docs/` 下的 VuePress 站点，线上地址 `https://docs.dclef.com/`；每页底部有 `github.com/dclef/renpy-tl/edit/master/docs/…` 的编辑入口，说明"文档即仓库内容"。首页标语："让人人都会汉化，以杜绝机翻汉化组"，流程口号："机翻 → 校对 → 润色"。
- **站点导航**：Guide（介绍 / 开始 / 进阶 / 8.0新特性 / 制作安卓壳子）、翻译工具、FAQ（介绍 / 问题汇总 / renpy-translator问题汇总）、协同翻译、支持。

### 6.2 它教的工作流（逐页核对）

1. **环境准备**：`unrpyc` 与 `unrpa` 两个步骤在页面上被**划线标注"已过时"**，改为推荐 **unren 单文件 bat**（Ren'Py ≤7 用 `unren0.91.bat`，≥8 用 `UnRen-1.0.10.bat`），下载走蓝奏云；SDK 作者自用 `renpy-7.4.11`。
2. **基础汉化 = 官方 sanctioned 流程**：`renpy.exe` → 教程 → Generate Translations → 语言名填 `chinese` → 勾"为翻译生成空字符串" → 得到 `game/tl/chinese/*.rpy` → 在 `new` 的引号内填中文。
3. **界面汉化**：把 `SourceHanSansLite.ttf` 放入 `game/`，全文替换 `gui.rpy`/`screens.rpy` 中的字体后缀；或在 `tl/chinese/style.rpy` 里写 `translate chinese python:` 统一切字体（与 5.1 完全一致）；并在 `screens.rpy` 的语言 `vbox` 里加 `textbutton _("Chinese") text_font "SourceHanSansLite.ttf" action Language("chinese")`。
4. **语言名**：全站统一用 `chinese`。**没有**看到 `schinese` / `zh-Hans` 之类的用法；`schinese` 只出现在它转述的 `renpy-translator` 文档示例里。→ "`schinese` 是主流惯例"这一说法**未能核实**；就本仓库而言，**`chinese`** 是它推荐并演示的名字。

### 6.3 它与本工具包的定位差异（重要）

| 维度 | Dclef/renpy-tl | 本工具包（`plugin/dsh-renpy-toolkit/renpy_toolkit`） |
| --- | --- | --- |
| 产出物 | 面向人类的教程文档 | 面向程序的工具库（`.rpa` / `.rpyc` / `tl` 读写与校验） |
| 是否有代码 | 仅文档 + 若干下载工具的分发链接 | 有，`rpa.py` 等 |
| 能否处理 `translate` 与 `strings` 块 | **不涉及**：它只教人手工在 `new` 里粘贴中文 | 目标流程需要程序化生成/校验两类块 |
| 许可证 | GPL-3.0 | 见仓库根 `README.md` |
| 引用价值 | 提供**中文本地化的实战失败清单**（字体、默认语言、导出不全、unren 版本配对、安卓 X- 前缀） | — |

**结论**：`Dclef/renpy-tl` 的价值是**经验库**而非依赖项。它没有可复用的代码接口，但它验证了几件事：(a) 中文社区的事实标准语言名是 `chinese`；(b) 字体问题必须单独处理且"全局替换字体"是可接受的工程折衷；(c) 反编译链的版本配对是汉化流程的固定章节；(d) 官方生成器覆盖不全这件事，社区已经当作前提接受。这四点已经落成工具包的默认假设与默认参数（见 §9）。

### 6.4 它的"翻译工具"章节 = 转述 `anonymousException/renpy-translator`

`docs.dclef.com/faq/renpy-translator-qa.html` 开头明写"来源于[官方的文档]"，链接指向 `anonymousException/renpy-translator` 的 `README_zh.md`。该页提供了迄今最具体的**工具行为证据**，直接可用：

- **生效格式判定**：它只认两种形态——

```renpy
# game/script.rpy:553
translate schinese naming_0f7b6e71:
    # r "Do name yourself like that and I'll break your face..."
    r "Do name yourself like that and I'll break your face..."
```

```renpy
    # game/script.rpy:30886
    old "Win or Lose?"
    new "Win or Lose?"
```

  且要求：`#`/`old` 之后的原文与 `new`/译文行的**非注释文本完全一致**，否则判定为未翻译。→ 这正是官方 `translate` 块的形状，说明社区工具是**贴着官方格式做正则**的。
- **不要勾"生成空字符串"**：否则工具会认为"全部已翻译"而跳过。→ 与本工具包骨架生成的默认行为相反，`tl gen` 因此用原文占位而非空串。
- **占位符保护是硬伤**：工具把 `[s_name]`、`{}`、`<>` 替换成序号 `[0]`、`[1]` 再还原，翻译引擎一旦破坏结构（如 `É [0] . E 1] .`）就还原失败，该行**直接不翻**。这是机翻流水线固有的、必须人工兜底的损耗。
- **抽取过滤**：工具**不会抽取**"有效长度 < 8"的句子（空格与特殊符号计 0）。→ `"I know [special symbol]"` 只有 5 个有效字符，会被丢弃。这类"短句被静默吞掉"是最难发现的问题，`tl verify` 因此显式报告"未被任何 tl 块覆盖的原文串"。
- **奇怪的 `old`**：`old "old:1709016761.706834_0.8853..."` 是工具写入的时间戳随机标记（`时间戳_随机数`），用于标记抽取批次，不是引擎机制。

### 6.5 抓取情况说明

- **已抓到**：仓库首页元信息（stars/forks/许可证/topics/About）、文档站首页、`/guide/`、`/guide/quick_start.html`、`/guide/new_version.html`、`/faq/qa.html`、`/faq/renpy-translator-qa.html`。
- **未抓到**：`/guide/vscode.html`（两次 30s 超时）、`/guide/android_make.html`（页内标注"建设中"）、`/cooperation/`（超时）、`sitemap.xml`（404）。→ 该仓库的**仓库内原始 markdown 文件内容未能直接读取**（raw 通道在本环境不可靠），以上结论均来自渲染后的文档站页面。

---

## 7. 参考来源清单（含可信度）

### 7.1 官方（最高可信度）

| URL | 内容 | 可信度 |
| --- | --- | --- |
| https://www.renpy.org/doc/html/translation.html | 翻译框架主文档（四类对象、tl 目录、translate/strings 块、标识符、defer_tl_scripts、unsanctioned 路径） | 【官方】 |
| https://www.renpy.org/doc/html/cli.html | CLI：`translate` / `dialogue` / `extract_strings` / `merge_strings` / `lint` 全部选项；声明 CLI 非稳定接口 | 【官方】 |
| https://www.renpy.org/doc/html/build.html | `build.archive/classify`、Archives 一节（"not very secure…protects files from casual copying"）、**The Old-game Directory**（`.rpyc` 存 ID、`old-game/` 复用之） | 【官方】 |
| https://www.renpy.org/doc/html/config.html | `config.archives` 自动按反 ASCII 序填充、取先找到者；`config.language`/`config.defer_tl_scripts` 等 | 【官方】 |
| https://www.renpy.org/doc/html/translating_renpy.html | **Ren'Py 自身的翻译**：`launcher/game/tl/<lang>/`、`common.rpy`/`script.rpym`/`style.rpy` 等文件清单、`translate_font()`/`translate_define()`、`{#language name and font}` 约定、"DejaVuSans 只覆盖西部语言" | 【官方】 |
| https://www.renpy.org/doc/html/other.html | `renpy.language_tailor(chars, cls)`（UAX #14 行断类覆盖，唯一官方断行微调口）；`renpy.count_dialogue_blocks()` 等 | 【官方】 |
| https://www.renpy.org/doc/html/changelog.html | 8.2.0/7.7.0 Translation Improvements（翻译对象数降为 1/3、`config.defer_tl_scripts`）；8.0/7.5 的 Py3/Py2 分叉 | 【官方】（整页极大，建议按锚点取，勿整页抓） |
| https://www.renpy.org/doc/html/bubble.html / style.html / text.html | 气泡与文本框排版、style 覆盖、文本标签（`[]`/`{}` 保留） | 【官方】 |
| https://www.renpy.org/latest.html | 版本页（8.5.x 时点） | 【官方】 |
| https://doc.renpy.cn/zh-CN/index.html | 官方文档中文镜像（社区维护） | 【官方·镜像】 |

### 7.2 工具仓库

| URL | 内容 | 可信度 |
| --- | --- | --- |
| https://codeberg.org/shiz/rpatool （原 https://github.com/shizmob/rpatool） | rpatool 单文件实现、命令行与 API、只支持写回 v2/v3、免责声明 | 【仓库 README】 |
| https://github.com/Lattyware/unrpa | `unrpa` 解包，PyPI 同名包 | 【仓库 README】 |
| https://github.com/CensoredUsername/unrpyc | `.rpyc` 反编译；明示**不解 `.rpa`**；MIT；1265 stars（抓取时点） | 【仓库 README】 |
| https://github.com/CensoredUsername/unrpyc/releases | `v2.0.1`（2024-04-01）、`v2.0.4` 等 release | 【仓库 README】 |
| https://github.com/F95Sam/UnRen | UnRen.bat 一体化：RPA 解包 + RPYC 反编译 + 开发者菜单 | 【仓库 README】 |
| https://f95zone.to/threads/unren-bat-v1-0-11d-rpa-extractor-rpyc-decompiler-console-developer-menu-enabler.3083/ | UnRen 上游发布帖 | 【论坛】 |
| https://github.com/Linux-gaming-wine/UnRen-Linux | UnRen 的 Linux 移植（含 UnRen-Ultrahack 的更新） | 【搜索条目】 |
| https://github.com/cnfatal/rpycdec （fork: https://github.com/Portalboy07/rpycdec） | 备选 `.rpyc`/`.rpymc` 反编译器 | 【搜索条目】 |
| https://github.com/Kaskadee/rpaextract | 图形化 RPA 提取 | 【搜索条目】 |
| https://github.com/Fppk/Chinese-UnRPA | UnRPA 中文化 | 【搜索条目】 |

### 7.3 中文社区教程与经验

| URL | 内容 | 可信度 |
| --- | --- | --- |
| https://github.com/Dclef/renpy-tl | 91★/14 fork、GPL-3.0、topics `chinese-translation,ren-py,renpy,tutorial`；教程仓库 | 【仓库 README】 |
| https://docs.dclef.com/ | 教程站首页 "让人人都会汉化，以杜绝机翻汉化组"；"机翻→校对→润色" | 【教程】 |
| https://docs.dclef.com/guide/ | 创建初衷（汉化组解散背景）、issue 政策 | 【教程】 |
| https://docs.dclef.com/guide/quick_start.html | 环境准备（unrpyc/unrpa 标注过时→unren）、基础汉化、界面汉化、`language chinese` 按钮、`translate chinese python:` 统一切字体 | 【教程】 |
| https://docs.dclef.com/guide/new_version.html | Ren'Py 8 新特性对汉化者的影响：Py2→Py3、unren0.95 对 8.0+ 失效需 1.10 以上、不再有 `.pyo`、可加密/套壳 | 【教程】 |
| https://docs.dclef.com/faq/qa.html | 实战坑：工程目录放二级目录、`python -m unrpa archive.rpa`、口口字通杀、白屏、`define config.language`、`ImportError: No module named unittest`、无 `screen.rpy` 时搜 `preferences()`、**导出文本不全须回改源码**、安卓两套资源 + `X-` 前缀重命名 | 【教程】 |
| https://docs.dclef.com/faq/renpy-translator-qa.html | `renpy-translator` 行为细节：生效格式、禁用"生成空串"、占位符序号化并还原、抽取过滤"有效长度<8"、`old:时间戳_随机数` 标记 | 【教程】（转述上游 README） |
| https://github.com/anonymousException/renpy-translator | 上游翻译自动化工具（上游 README 未能读取，见 6.5） | 【仓库 README·部分】 |
| https://github.com/Dclef/renpy-translator | 2★、AGPL-3.0、"renpy翻译工具 renpy tl to google trans" | 【仓库 README】 |
| https://www.renpy.cn/thread-16-1-1.html | 《最新Ren'Py引擎游戏解包教程》：7.8+ 的 rpyc 反编译问题、unrpyc-master 停更说法 | 【论坛/博客·低】 |
| https://blog.sunmkt.uk/post/renpy-unpack/ | unrpa 提取 + unrpyc 反编译步骤 | 【博客·低】 |
| https://zhuanlan.zhihu.com/p/378644389 | "unrpa 不是内部或外部命令" 等排错 | 【博客·低】 |

### 7.4 论坛（仅作现象参考，不作为事实依据）

| URL | 内容 | 可信度 |
| --- | --- | --- |
| https://lemmasoft.renai.us/forums/viewtopic.php?t=23283 | "Ren'Py Unsanctioned Translations"（2013），unsanctioned 概念的社区讨论 | 【论坛】 |
| https://lemmasoft.renai.us/forums/viewtopic.php?t=60634 | "Decompile Error .RPYC Files"（2020），UnRen.bat v0.9-dev 反编译失败求助 | 【论坛】 |
| https://lemmasoft.renai.us/forums/viewtopic.php?t=67001 | "Recovering my script from .rpyc file"（2023） | 【论坛】 |
| https://lemmasoft.renai.us/forums/viewtopic.php?t=67620 | 用 `tl/proofread` 语言做"校对工作流"的变通法（"renpy launcher → generate translations → 命名 proofread → 发给校对 → 把校对后的文件贴回"） | 【论坛】（页面本体读取超时，仅据搜索摘要） |
| https://lemmasoft.renai.us/forums/viewtopic.php?t=61095 | xavimat 回答"对话可导出不可导入，用 None 翻译发给校对"（2021） | 【论坛】（页面本体读取超时，仅据搜索摘要） |
| https://lemmasoft.renai.us/forums/viewtopic.php?t=43333 | "添加语言选项"教程，明确反对机翻 | 【论坛】 |

### 7.5 明确未能核实的事项

1. **`garc`（"Gar 的 RenPy 反编译器"）** —— 多轮检索无任何相关命中，不作存在性假设。
2. **"renpy-tools 自带 rpatool"** —— 未找到以此名存在且含 rpatool 的仓库；疑为 SDK `launcher/game/tools/` 与社区 rpatool 的混称。
3. **`schinese` 是"主流目录名惯例"** —— 仅在 `renpy-translator` 的示例代码里见到；`docs.dclef.com` 全站用 `chinese`。两者都满足官方"必须能作 Python 标识符"的约束，但**没有任何来源表明其中一个比另一个"更主流"**。
4. **`common.rpym` vs `common.rpy` 的"命名差异"** —— 官方 `translating_renpy.html` 中 `launcher/game/tl/<lang>/` 下确实有 `common.rpy` 与 `script.rpym`（后者是"新工程时逐字拷进 `script.rpy` 的模板"），而游戏工程内 `tl/None/common.rpym` 是引擎内部字符串。两者是**不同层级的同名文件**，不是"汉化项目里该用哪个"的选择题；社区层面的其它叫法**未能核实**。
5. **pot/po 与 Ren'Py 的现成桥接** —— 未找到被广泛引用的 gettext 桥接项目。Ren'Py 用自己的 `translate`/`strings` 格式而非 po；`extract_strings`/`merge_strings` 的 JSON 才是官方提供的中转格式。若要做 po 桥接只能自写转换器——**判定为"生态空白"**而非"有工具但没搜到"。
6. **竖排文本** —— 无官方支持、无成熟公开方案。
7. **文件 hash 校验类反汉化** —— 未见可复核的公开实现样本。

---

## 9. 本工具包对应的实现

上面几节列出的生态缺口，在本工具包里各有对应的命令。这张表是本文与工具本身的接口：

| 生态缺口 | 工具包里的入口 |
|---|---|
| 只有 `.rpyc`、拿不到源码，官方生成器产不出骨架 | `tl gen` —— 跑游戏本身，从内存读 `translator.file_translates` 与 SL2 屏幕树，实测 58,249 块、19.6 s；引擎 `lint` 数字逐项吻合 |
| 反编译风险（标识符漂移、文本遗漏） | `decompile`（白名单 pickle 虚拟机，不执行游戏字节码）+ `verify_decompile` 三级校验 |
| 生成器静默漏句（变量拼接、`show text`、裸字面量） | `tl untranslatable <dump>` 列出「源码里有、但拿不到稳定翻译身份」的串；`tl verify` 报覆盖度 |
| 界面文本没有 `_()` 包裹 | 硬编码文本走 TL：`tl hardcoded scan/apply`，靠运行时 `translate_string` 命中，不改源码 |
| 同一字符串在不同界面要译成不同结果 | `tl context` 生成跨屏消歧补丁（`{#ctx}` + 运行时钩子） |
| 术语不统一 | `tl glossary check`（内置通用 UI 术语表 + 工程术语 `--scope project`） |
| orphan / 标识符漂移 | `tl verify --expect-dialogue N` 对账引擎 `lint`；`port` 按标识符搬运旧译文 |
| `.rpa` 解包/打包 | `rpa list/extract/pack`，RPA-2.0/3.0 读写，非默认 key 可 `--key` 指定 |
| 字体与缺字 | `font map`（生成 `translate <lang> python:` 替换块）+ `font check`（cmap 覆盖检测） |
| 改文件怕改坏 | `--backup-mode bak|git`：每次写盘前自动留副本或快照，`tl backup list/restore/prune` |

