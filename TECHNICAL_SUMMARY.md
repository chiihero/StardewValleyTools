# 技术文档总结

## 项目目标

这是一个基于 Python 3.11 + tkinter 的 Stardew Valley Mod 管理器与 AI 汉化助手。核心用途是：

1. 扫描本地 Mod 库；
2. 记录 Mod 启用 / 停用状态；
3. 一键导入启用的 Mod 到游戏 `Mods` 目录；
4. 扫描 `manifest.json` 与 `i18n` 目录并判断汉化状态；
5. 检查 Nexus 更新、下载并安装可更新的 Mod；
6. 使用 OpenAI 配置生成安全的 `i18n/zh.json`。

## 当前实现结构

- `app.py`：程序入口。
- `src/ui.py`：tkinter 界面、管理页 / 设置页、日志与状态管理。
- `src/manager.py`：Mod 库扫描与导入执行。
- `src/storage.py`：本地状态文件读写。
- `src/scanner.py`：扫描 Mod 目录、解析 manifest、定位 locale 文件。
- `src/detector.py`：判定 Mod 类型、比较 JSON 结构、检测占位符。
- `src/translator.py`：组装提示词并调用 OpenAI `responses.create(...)`，并提供 AI 连通性测试。
- `src/writers.py`：JSON 校验与安全写入。
- `src/nexus.py`：Nexus 更新检查、下载链接解析、压缩包解压与更新安装。
- `src/models.py`：数据结构定义。
- `src/prompts.py`：AI 提示词模板。

## 核心流程

### 1. 扫描 Mod 库

用户设置 Mod 库目录后，程序递归查找 `manifest.json`，并为每个 Mod 构建记录。

### 2. 管理状态

启用 / 停用只保存为管理器记录，不直接修改游戏目录。

### 3. 导入

当用户点击导入时，程序只复制启用的 Mod 到游戏 `Mods` 目录；默认不删除其他内容。

### 4. AI 汉化

在后台线程调用 OpenAI，仅对已启用的 Mod 批量处理 JSON 文本资源，并要求返回合法 JSON。

### 4.1 汉化检查

在 Mod 管理页可单独触发汉化状态检查，复用 `scan_mod()` 更新 `translation_status`、`missing_keys_count` 和警告信息。

### 5. 写入

默认输出为 `i18n/zh.json`；完整时跳过，缺失时补写。

### 6. Nexus 更新

程序从 `manifest.json` 的 Nexus 更新键读取 mod id / file id，查询 Nexus API 后展示更新状态，并支持下载更新包、解压后替换本地 Mod 库中的目标目录。

## 安全策略

- 不直接覆盖原始 `zh.json`。
- AI 返回内容必须通过 JSON 校验。
- 占位符 token 需要保留，如 `{{token}}`、`{0}`、`[SMAPI]`。
- GUI 线程不阻塞，扫描 / 导入 / 翻译都放入后台线程。
- Nexus 更新检查与下载也放入后台线程，避免卡住界面。

## 本地状态文件

默认写入：`.stardewvalleytools/state.json`

## 运行方式

可通过 `start.bat` 或直接执行：

```bash
python app.py
```

## 已知约束 / 备注

- 当前只默认支持 OpenAI AI 配置，默认模型为 `gpt-5.4-nano`，`Base URL` 默认值为 `https://api.openai.com/v1`，并保留自定义代理地址兼容中转站；
- 设置页提供 AI 测试按钮，用于验证当前 API Key / 模型 / Base URL 是否可用；
- 设置页新增 Nexus API Key，用于 Nexus 更新检查与下载；
- Nexus API Key 旁边有“获取 API Key”按钮，点击会打开 Nexus SSO 页面并自动回填；
- 更新包支持 zip，7z 依赖 `py7zr`；
- 对混合 flat/tree locale 结构和非常规 i18n 布局，当前按保守策略处理为 `unknown` 或不自动支持。
