# 技术文档总结

## 项目目标

这是一个基于 Python 3.11 + tkinter 的 Stardew Valley Mod 管理器与 AI 汉化助手。核心用途是：

1. 扫描本地 Mod 库；
2. 记录 Mod 启用 / 停用状态；
3. 一键导入启用的 Mod 到游戏 `Mods` 目录；
4. 扫描 `manifest.json` 与 `i18n` 目录并判断汉化状态；
5. 使用 OpenAI 配置生成安全的 `i18n/zh.generated.json`。

## 当前实现结构

- `app.py`：程序入口。
- `src/ui.py`：tkinter 界面、管理页 / 设置页、日志与状态管理。
- `src/manager.py`：Mod 库扫描与导入执行。
- `src/storage.py`：本地状态文件读写。
- `src/scanner.py`：扫描 Mod 目录、解析 manifest、定位 locale 文件。
- `src/detector.py`：判定 Mod 类型、比较 JSON 结构、检测占位符。
- `src/translator.py`：组装提示词并调用 OpenAI `responses.create(...)`。
- `src/writers.py`：JSON 校验与安全写入。
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

### 5. 写入

默认输出为 `i18n/zh.generated.json`；如果已存在，会自动生成带序号的备用文件名，避免覆盖。

## 安全策略

- 不直接覆盖原始 `zh.json`。
- AI 返回内容必须通过 JSON 校验。
- 占位符 token 需要保留，如 `{{token}}`、`{0}`、`[SMAPI]`。
- GUI 线程不阻塞，扫描 / 导入 / 翻译都放入后台线程。

## 本地状态文件

默认写入：`.stardewvalleytools/state.json`

## 运行方式

可通过 `start.bat` 或直接执行：

```bash
python app.py
```

## 已知约束 / 备注

- 当前只默认支持 OpenAI AI 配置；
- 对混合 flat/tree locale 结构和非常规 i18n 布局，当前按保守策略处理为 `unknown` 或不自动支持。
