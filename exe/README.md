# 图形界面（独立发行线）

这个目录只服务于 Windows exe，**不随 Python 包分发**。插件与 skill 都不 import 它，
`dist/plugin`、便携 zip 与 skill 包里也没有这里的任何文件。

```
exe/
├── gui.py         本地 HTTP 服务 + 打开系统浏览器 app 窗口；也含全部纯逻辑
├── ui/index.html  界面（深色卡片，无外部资源）
└── tests/         纯逻辑测试（23 例，不需要 tkinter 或浏览器）
```

## 它做什么

- 启动后扫「exe 所在目录 + 一级子目录」里的 Ren'Py 游戏，列出卡片让你选一个；
  0 个、1 个、多个都会弹出这个窗口，一次只处理选中的那一个。
- 每个按钮都翻译成一次 `cli.main(argv)` 调用，所以界面行为与命令行完全一致
  （`--write` 语义、备份模式、退出码）。
- 除「生成汉化骨架」写进 `game/tl/<语言>/`，其余产物统一落在 exe 旁边的
  `renpy-toolkit-数据/`（日志、导出、反编译、备份、临时）。
- 报错给一行结论 + 可操作提示 + 退出码含义（1 有检查未通过 / 2 用法有误 /
  3 环境不具备）。

## 为什么是「本地服务 + 系统浏览器」

冻结后的原生 WebView 绑定（pywebview + pythonnet）在 Windows 上要先解析 CLR，
PyInstaller 打包后实测失败（`Failed to resolve Python.Runtime.Loader.Initialize`）。
改用标准库 `http.server` 起一个只监听 `127.0.0.1` 的服务，再用系统 Edge/Chrome 的
`--app=` 窗口打开：没有原生依赖，也不需要 WebView2。

## 构建

必须在装了 PyInstaller 的解释器里跑（PyInstaller 只打包它自己 site-packages 里的东西），
`fontTools` 与 `openpyxl` 也要装在同一个解释器里：

```bash
python tools/build_exe.py --clean      # 打单文件 exe 并跑 --selftest 自检
python tools/build_exe.py --verify     # 只自检已有产物
```

已知限制：onefile 引导器要创建带自定义 ACL 的临时目录，在受限令牌环境里会被拒
（`Could not create temporary directory`）；此时用 `--runtime-tmpdir <可写目录>` 重建即可，
`build_exe.py --verify` 会把这种情况判为「环境受限」而不是失败。
