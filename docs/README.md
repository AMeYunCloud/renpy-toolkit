# 参考文档

这些文档是工具包规则的**依据**：每一条校验、每一个默认值，都能在这里找到对应的引擎事实或格式规范。
写代码前读它们能省掉重新踩坑；Ren'Py 升级后也按同一顺序复核。

| 文档 | 内容 |
| --- | --- |
| [`renpy-engine-notes.md`](./renpy-engine-notes.md) | 引擎架构与核心机制：启动流程、`.rpy`→`.rpyc` 编译、运行时对象、翻译表的挂载点 |
| [`official-cli-and-translation.md`](./official-cli-and-translation.md) | 翻译机制与官方 CLI：四类可翻译对象、`translate`/`strings` 块、标识符生成、全部子命令 |
| [`renpy-source-verification.md`](./renpy-source-verification.md) | 从 `renpy/` 源码逐行核实的接口，附文件路径与函数名 |
| [`rpyc-decompile.md`](./rpyc-decompile.md) | `.rpyc` 容器格式（RPC2 槽表）与反编译实现 |
| [`rpa-format-spec.md`](./rpa-format-spec.md) | RPA-2.0 / RPA-3.0 归档格式规范与读写约定 |
| [`text-tags-and-interpolation.md`](./text-tags-and-interpolation.md) | 文本标签、插值、空译文的引擎实测结论 |
| [`custom-text-tags.md`](./custom-text-tags.md) | 自定义文本标签的官方语义，以及标签表如何对齐引擎 |
| [`renpy-translation-ecosystem.md`](./renpy-translation-ecosystem.md) | 第三方汉化生态参考：工具链、工程惯例、中文本地化难点，以及本工具包的对应实现 |
| [`renpy-tutorial-notes.md`](./renpy-tutorial-notes.md) | 制作侧入门参考：学习 Ren'Py 脚本的推荐顺序与常见误区 |
| [`release-notes-1.0.0.md`](./release-notes-1.0.0.md) | 1.0.0 发布说明（中文 + English，GitHub 发布页正文） |
