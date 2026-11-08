# Ren'Py 制作侧入门参考

面向「从未写过 Ren'Py 脚本、但已有通用编程经验」的读者，用于配合工具包的制作侧功能
（`project` 检查、`scaffold` 建工程）。参考资料来自 Ren'Py 官方文档
（[www.renpy.org/doc/html](https://www.renpy.org/doc/html/)）与社区中文教程 `ZYKsslm/RenPy-Tutorial`。
官方文档里能直接查到的，本文只给结论 + 条目名；社区教程里有价值的是**踩坑顺序**，
这部分按「先学什么、后学什么」组织。

---

## 0. 心智模型：Ren'Py 是「Python + 一个视觉小说运行时」

一句话概括：`.rpy` 文件被编译成 `.rpyc`，运行时是一个 Python 进程（`renpy.main`），
你的脚本语言（say / menu / label / show）在导入期被翻译成 Python 语句树，
`label` 变成函数，`say` 变成 `renpy.say()` 调用。

因此有三条贯穿全流程的结论：

1. **Python 能力直接可用**。`init python:`、`python:`、`$`、内联表达式
   `[expr]` 全是真 Python。
2. **`.rpy` 不是 Python**，它是另一套 DSL，缩进敏感、`label` 是作用域。
   Python 里能做的循环/条件，在脚本层用 `while`/`if`（Ren'Py 自己的语句）写。
3. **有两种「时刻」**：`init` 阶段（启动，构建对象、注册图像、定义样式）和
   运行阶段（玩家点击时）。混淆这两者是新手最大错误来源——
   比如在 `label` 里 `init` 一些东西、或以为 `init python` 里的代码随存档一起走。

---

## 1. 起步顺序（不要按官方文档目录的顺序读）

官方文档是按「参考手册」组织的，不适合线性学习。推荐的顺序：

| 阶段 | 目标 | 看什么 |
| --- | --- | --- |
| 1 | 能跑起来、能改字 | **Quickstart** → 自带的 The Question 工程 |
| 2 | 会写一条线 | **Script / Label & Control Flow / Dialogue / Menu** |
| 3 | 会摆画面 | **Displaying Images / Transforms（ATL）/ Screens（Screen Language）** |
| 4 | 会存东西 | **Python Statements / Persistent Data / Save, Load, and Rollback** |
| 5 | 会做界面 | **Screens and Screen Language / Text / Styles** |
| 6 | 会处理多媒体 | **Audio / Movie / Image Gallery** |
| 7 | 会发布 | **Building Distributions / Android / Translating Ren'Py Games** |

社区教程里最有价值的一条经验：**第 3 步的 Screen Language 是最陡的坡**。
它和脚本层语法完全不同（`screen` / `frame` / `vbox` / `textbutton`），
而且 `screens.rpy` 有 2000+ 行默认内容。建议的策略是先**只在 `screens.rpy` 里
改东西**（换字体、换颜色、改按钮文字），等到需要加新界面时再回头系统学。

---

## 2. 脚本层最小可用集合

```
label start:                    # 每个游戏的入口点，名字必须是 start
    scene bg room               # 换背景（清空画面再显示）
    show eileen happy at right  # 显示立绘，happy 是 image attribute
    e "Hello!"                  # 台词；e 是 Character
    menu:                       # 分支
        "Go left":
            jump left_path
        "Go right":
            jump right_path
    return                      # 结束游戏（不是返回上一句！）
```

`return` 的语义容易搞错：在 `label` 里是「这个 label 结束」，主流程里就是回到主菜单。

`jump` 是**不返回**的跳转（goto），`call` 才是带返回栈的调用。
社区教程反复强调：把 `jump` 当函数调用是新手第一坑。

---

## 3. 命名与资源约定

- 图像自动发现：`game/images/eileen happy.png` 自动成为 `image eileen happy`。
  目录分隔和空格都参与命名，用 `images/` 子目录组织角色。
- 图像属性（attributes）是第二个词往后：`show eileen happy glasses`。
  属性可以叠加，`Scene` 会重置。
- 音频自动发现同理：`game/audio/` 下 `play music "theme.ogg"`。
- **中文文件名不要用**。Windows 打包 + Android 上都会出问题；
  资源名用 ASCII，文本走翻译。

---

## 4. Python 介入点（四种，各有用途）

| 写法 | 何时执行 | 典型用途 |
| --- | --- | --- |
| `init python:` | 启动期，一次 | 定义类、注册图像、改 config |
| `init -1 python:` | 比默认更早 | 需要覆盖 gui/options 的默认值 |
| `python:` | 运行期，进入该语句时 | 计算、调 API、写存档变量 |
| `$ x = 1` | 同上，单行简写 | 小赋值 |

`init python` 的优先级（那根 `init -1` / `init 5` / `init offset` 的链）是进阶话题，
初学只需记住：**默认 `init` 块之间按文件名字母序执行**，需要确定性就写明数字。

存档：只有**赋值给 `store` 的简单类型**会进存档。用 `persistent` 存跨存档数据
（解锁记录、CG 收集），用 `renpy.retain_after_load()` 保留带回调的复杂对象。

---

## 5. Screen Language 速览

```
screen main_menu():
    tag menu
    frame:
        vbox:
            textbutton _("Start")  action Start()
            textbutton _("Load")   action ShowMenu("load")
            textbutton _("Quit")   action Quit(confirm=True)
```

- `screen` 是**声明式**的：每次界面刷新都重新执行，别在里面做副作用。
- `action` 是回调对象（`Start()` / `Jump()` / `Return()`），不是函数调用。
- 默认 UI 高度依赖 `gui.rpy` 的变量（`gui.accent_color` 等），
  想改外观先改这些变量，不要直接改 `screens.rpy` 里的字面值。

---

## 6. 汉化视角的注意点（与 `plugin/dsh-renpy-toolkit/` 直接相关）

学完脚本层再回头看翻译机制，会清楚很多：

- 可翻译的东西有四类：**对话**、**menu 选项与 `_()` 字符串**、
  **图像文件**（`tl/<lang>/` 下同路径覆盖）、**样式**。
- `translate <lang> <identifier>:` 里的 identifier 由
  `label 名 + md5(台词 code)[:8]` 决定。**改一个标点就会换 identifier**，
  所以汉化必须对着已冻结的源文本做，源文本一改就得重新对齐。
- 界面文字（`screens.rpy` 里的 `_("Start")`）走 `translate <lang> strings:` 块，
  用 `old` / `new` 配对，`{#context}` 区分同串多处。
- **运行期**：`screen` / `say` / `menu caption` 里的文本字面量一律过
  `renpy.translation.translate_string()`，与源码里有没有 `_()` 无关。
  这一点让很多汉化者误判「这里没写 `_()` 所以不能翻」。

详见 [`official-cli-and-translation.md`](official-cli-and-translation.md)。

---

## 7. 常见误区清单（来自社区教程与本人实测）

1. **`.rpyc` 没删**：改了 `.rpy` 却看不到效果。Ren'Py 只比对 `.rpyc` 末尾 16 字节的
   源文件 md5，mtime 不管用。手动改名/删翻译文件时必须连 `.rpyc` 一起处理。
2. **`jump` 当 `call` 用**：流程回不来。
3. **`init` 里读存档变量**：那时还没读档。
4. **中文文件名**：Linux/Android 打包炸。
5. **在 screen 里写副作用**：界面每帧重跑，等于每帧执行一次。
6. **改 `gui.rpy` 的分辨率后图像没缩放**：`gui.init(w, h)` 只管布局，
   图像要自己按新分辨率出。
7. **翻译块手写 identifier**：一旦算错就永不命中，且不报错（静默显示原文）。

---

## 8. 与其他文档的关系

- 想了解引擎内部结构 → [`renpy-engine-notes.md`](renpy-engine-notes.md)
- 想做汉化 → [`official-cli-and-translation.md`](official-cli-and-translation.md)
  + [`renpy-translation-ecosystem.md`](renpy-translation-ecosystem.md)
- 想拆 `.rpa` → [`rpa-format-spec.md`](rpa-format-spec.md)
- 想用工具 → [`../plugin/dsh-renpy-toolkit/README.md`](../plugin/dsh-renpy-toolkit/README.md)
