# RPA 归档（.rpa / .rpi）格式规范 — 已核实

## 0. 文件头文本行（人类可读，按行读）

| 版本 | 首行文本 | 头部长度 |
| --- | --- | --- |
| RPA-2.0 | `RPA-2.0 %016x\n`（offset） | 25 字节 |
| RPA-3.0 | `RPA-3.0 %016x %08x\n`（offset, key） | 34 字节 |
| RPA-1.0 | `.rpi` 扩展名，无 magic | — |

- RPA-2/3 的 magic 是**行首固定 ASCII**：`RPA-2.0 ` / `RPA-3.0 `（含尾空格）
- 首行以 `\n` 结束，`readline()` 即可取到

## 1. 头部字段解析（Python 写法）

```python
self.handle.seek(0)
metadata = self.handle.readline()          # bytes
offset = int(metadata[8:24], 16)           # ← 索引区在文件中的绝对偏移
if version == 3:
    key = int(metadata[25:33], 16)         # ← 混淆 key，默认 0xDEADBEEF
```

字段切片（RPA-3.0）：`[0:8]="RPA-3.0 "`、`[8:24]=16位hex offset`、`[24]=" "`、`[25:33]=8位hex key`、`[33]="\n"`

## 2. 索引区（index table）

```python
self.handle.seek(offset)
indexes = pickle.loads(codecs.decode(self.handle.read(), "zlib"))
```

- 位于头部给出的 `offset` 处，一直到文件末尾
- 先 zlib 解压，再 pickle 反序列化
- pickle 协议：`pickle.HIGHEST_PROTOCOL`（写入时）

## 3. RPAv3 的索引混淆（deobfuscation）★关键

```python
obfuscated_indexes = indexes
indexes = {}
for i in obfuscated_indexes.keys():
    if len(obfuscated_indexes[i][0]) == 2:
        indexes[i] = [(offset ^ key, length ^ key) for offset, length in obfuscated_indexes[i]]
    else:
        indexes[i] = [(offset ^ key, length ^ key, prefix) for offset, length, prefix in obfuscated_indexes[i]]
```

- 索引 dict：`{文件名: [(offset, length)]}` 或 `{文件名: [(offset, length, prefix)]}`
- RPAv3 下 `offset` 和 `length` 都与 `key` 做 **XOR**；`prefix`（若有）**不混淆**
- RPAv2 下完全不混淆
- **混淆只用于索引字段，不用于文件数据本身**

## 4. prefix 机制（third element）

```python
if len(self.indexes[filename][0]) == 3:
    (offset, length, prefix) = self.indexes[filename][0]
else:
    (offset, length) = self.indexes[filename][0]
    prefix = ''
self.handle.seek(offset)
prefix = str.encode(prefix)
return prefix + self.handle.read(length - len(prefix))
```

- `prefix` 是**前缀字节串**：文件前几个字节不占归档空间，读取时拼接
- 常见于 `detrar` 加密头处理或文件共用头部（如多个文件共享同一声明）
- 读取长度要**减去 prefix 长度**

## 5. 文件名规范化

```python
def convert_filename(self, filename):
    (drive, filename) = os.path.splitdrive(os.path.normpath(filename).replace(os.sep, '/'))
    return filename
```

- 统一为 `/` 分隔、去盘符、去 `.`/`..`
- 归档内键名是**相对 game/ 的路径**（如 `script.rpy`、`images/bg/room.jpg`）

## 6. 写归档（rebuild）

```python
offset = 34 if version == 3 else 25      # 头部占位
archive.seek(offset)
for file, content in files.items():
    if padlength > 0:                    # 固定随机填充，under 0 = 不填充
        padding = generate_padding(); archive.write(padding); offset += len(padding)
    archive.write(content)
    if version == 3:
        indexes[file] = [(offset ^ key, len(content) ^ key)]
    else:
        indexes[file] = [(offset, len(content))]
    offset += len(content)
archive.write(pickle.dumps(indexes, pickle.HIGHEST_PROTOCOL).encode('zlib'))
archive.seek(0)
archive.write('RPA-3.0 %016x %08x\n' % (offset, key))     # offset = 索引区起始
```

- 头部 offset 指向**索引区起点**（=所有文件数据写完后的位置）
- 索引必须**整体重写**（zlib + pickle），不能增量
- `padlength` 生成随机 padding（`chr(random.randint(1,255))`），官方工具默认 0

## 7. rpatool CLI 参数（v0.8，参考设计）

```
-l/--list          列出归档内文件
-x/--extract       抽取（支持 ARCHIVE=REAL 重映射输出路径）
-c/--create        从 FILEs 创建
-d/--delete        删除
-a/--append        追加
-2/--two           RPAv2 格式
-3/--three         RPAv3（默认）
-k/--key           十六进制混淆 key（默认 0xDEADBEEF）
-p/--padding       文件间最大随机填充字节数
-o/--outfile       out 归档/目录
-v/--verbose
```
`FILE` 参数可写成 `archive内路径=真实路径`（例：`test.rpa script.rpyc=/home/foo/test.rpyc`）
⚠️ rpatool 只支持**写 RPAv2/RPAv3**；RPAv1(.rpi) 读取都不支持。

## 8. ⚠️ 已知限制与坑（对我们插件的约束）

1. **rpatool 原版是 Python 2 代码**（用 `unicode()`、`str.encode`），上面 gist 版是 py3 移植；
   移植版仍有 py2 残留，照抄会崩 —— 需重写。
2. **Ren'Py 官方 loader 支持更多变体**（`.rpa`/`.rpi`、多归档、`config.archives`），
   官方 launcher 的 `launcher/game/archiver.rpy` 自己也有实现。
   「These files are really easy to reverse-engineer, but are probably better than nothing」
   —— 官方原话，说明格式无强加密。
3. **Ren'Py 8.x 的 RAPT/移动端打包**会生成变体归档；PC 单文件 `archive.rpa` 最常见。
4. 索引 dict 顺序 = 写入顺序，**Python dict 保序**（3.7+），保存时按原顺序读回再写可保持二进制稳定。
5. **不要相信 `.rpa` 的 mtime**：内容与 mtime 无关。
6. RPAv3 的 XOR key 会**逐归档不同**（Ren'Py 默认 0xDEADBEEF，但作者可自定义），
   解析必须从头部读 key，不能硬编码。

## 9. 对我们插件的实现要求

- 支持 **读** RPAv1(尽力)/2/3，**写** RPAv2/3
- 支持 prefix 三元组与二元组两种索引形态
- `list` / `extract` / `add`(替换) / `delete` / `repack`
- 提取时支持按 glob 筛选（汉化场景常只需 `game/**/*.rpy`、`tl/**`、`fonts/**`）
- 大归档要流式读写（2.5GB 归档常见，见 renpy-tl-polish skill 实测）
- 保存前可选备份原文件（汉化组「绝不能覆盖原始包」铁律）
