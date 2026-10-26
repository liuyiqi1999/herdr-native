# 远端 pi 原版界面，本地原位编辑

## Claude Code

已为本机和 `liuyiqi.1@10.251.230.15` 的 zsh/bash 安装自动入口。在 **Herdr Native 的新终端面板**中直接运行 `claude`，普通输入框就会自动使用本地编辑，不需要菜单启动，也不需要按 Ctrl+G。已有 shell 可新开一个面板，或执行一次：

```sh
source ~/.cache/herdr-native/claude/entry.sh
```

- 中文、emoji、多行、删除、光标移动在本地处理，远端 Claude 的聊天画面仍保留。
- **Enter 一次提交**；`/model` 等命令交给原版 Claude 打开菜单，退出菜单后恢复自动本地输入。
- **Tab / Shift+Tab**、空草稿的上下箭头会交回原版 Claude。**Esc / Ctrl+G** 可暂时回到原版输入，使用其他原版交互；回到下一轮普通输入时重新自动接管。
- 窗口缩放先保存草稿、重新确定输入框位置，再恢复本地编辑。没有确认的提交不会自动重发。
- `⌘⇧A → new remote claude` 也使用自动模式，在远端创建或复用工作区。旧版手动编辑的工作区继续保留，不强制转换正在运行的 Claude。
- Herdr 外的 `claude`、`claude --version`、`claude -p` 和管理命令沿用原来的命令。用 `HERDR_NATIVE_CLAUDE=0 claude` 可临时关闭自动接管。

本机和远端分别验证了 Claude 2.1.305、2.1.299；没有升级 Claude 或更改权限模式。自动入口通过用户私有目录里的 Python PTY 代理运行原版 Claude，检测光标、输入框边界和状态栏组合后调用官方外部编辑器接口。Herdr 只根据代理明确输出的输入区标记合成本地画面。未识别的界面不会自动提交，登录、信任提示和菜单保留原版处理。

这仍是兼容适配层，不是 Claude 官方开放的可替换输入组件。修改了外部编辑器快捷键、未知版本、特殊主题可能退回原版输入；图片、拖放和全部自定义快捷键尚未逐一验证。本地编辑组件沿用现有 pi 1.3.0；远端随包携带 pyte 0.8.1、wcwidth 0.2.13 及许可证，兼容 Python 3.7，无需安装远端依赖。

为其他机器安装：

```sh
python3 scripts/install_claude_native.py --host <ssh-target>
# 本机：
python3 scripts/install_claude_native.py --local
```

安装只追加 shell 配置的加载入口，旧配置备份在 `~/.cache/herdr-native/claude/backups/`，运行文件按内容哈希保存在同目录。已有 Claude 会话不重启。

验证命令（不发送模型请求）：

```sh
python3 remote/tests/test_claude_auto.py
python3 remote/tests/test_claude_editor.py
python3 scripts/smoke_claude_editor.py --automatic --plain
python3 scripts/smoke_claude_editor.py --automatic --plain --host <ssh-target>
python3 scripts/smoke_claude_native.py --automatic
```

以下为既有 pi 集成说明。


0.4 版的 **new remote pi** 保留远端完整 InteractiveMode 和原有插件。面板就在远端机器的真实工作区中；客户端只在原输入框的位置运行同版 pi 的 `CustomEditor`。不再为 pi 创建 Local 工作区或重写聊天界面。远端官方 Herdr server 不需要修改或重启。

## 使用

打开 `/Applications/Herdr Native.app` 的新版窗口，选择远端机器和项目。在普通终端直接运行 **`pi`** 即可自动启用本地输入；**⌘⇧A → new remote pi** 仍可用于自动创建或聚焦该项目的远端 pi 工作区。

已为保存的机器 `10.251.230.15` 安装默认扩展 `~/.pi/agent/extensions/herdr-native-input.ts`。它只在 Herdr 面板内启用；显式 native 启动会跳过默认入口，避免重复加载；RPC/print 模式不创建编辑器。此前已运行的普通 pi 可以执行 `/reload`，或在下次启动时加载，不需要重启 Herdr server。

为其他保存的机器配置同样行为：

```sh
python3 scripts/install_native_editor.py --machine <saved-profile-id>
```

- 普通键入、中文、多行、删除和光标移动在本地完成。
- Enter 提交完整内容；切换原版菜单等控制操作会同步当前草稿，再交给远端 pi 处理。
- 标题、会话历史、工具展示、模型菜单、扩展自定义界面和状态栏均来自远端原版 pi。
- 同一机器和项目重复打开会复用已有工作区。Local 中的旧版 RPC 面板和原有远端会话保留，不会被删除或强制转换。
- 普通远端 `pi` 通过默认扩展自动加载桥接。显式使用 `--no-extensions` 会关闭该能力；尚未安装默认扩展的机器仍可用新版菜单入口。

Codex 继续使用官方本地 `--remote` TUI 和远端 app-server，仍显示在 Local 下；本次远端工作区与原版界面的调整针对 pi。

## 版本与插件核对

本地和远端都是 `@earendil-works/pi-coding-agent` **1.3.0**，主题都是 dark。

远端额外配置了 `pi-subagents`、`pi-mcp-adapter`、`pi-web-access`、`pi-cursor-sdk@0.5.3`，以及 `footer-layout.ts`、`auto-title.ts` 等扩展。新版直接使用远端原安装，包括其模型、凭据、MCP、工具和配置，因此这些插件继续在远端运行，无需在本机重复执行。`footer-layout.ts` 的标题、最近输入、路径、分支、统计和模型行不再被简化界面替换。

本地编辑器从连接读取远端的有效快捷键、padding、历史和补全命令。远端文件补全使用异步文件名缓存，最多 10,000 个文件。图片粘贴、拖放、物理 GUI 输入法候选窗，以及所有第三方扩展快捷键的组合尚未逐一验证，不能把当前测试结果理解为这些操作全部通过。

## 实现与持久性

- `remote/native-editor.ts`：由默认扩展或菜单显式加载到远端原版 pi。只给获得焦点的编辑器添加结构化 OSC 8 标记，并在用户私有 Unix socket 上接受完整提交或控制操作。进入原版菜单后不再标记输入框。
- `remote/native-input.mjs`：本地运行原版 `CustomEditor`，向客户端输出编辑器帧；不运行模型或项目工具。
- `herdr/src/client/shell/native_editor.rs`：根据明确的输入框标记，在对应远端面板原位置合成编辑器画面；不识别或猜测屏幕文字，不修改冻结的 endpoint codec。
- 远端缓存和扩展：`~/.cache/herdr-native/`；socket：权限 0700 的 `/tmp/hn-editor-*/`。
- 本地草稿：`~/Library/Application Support/Herdr Native/drafts/`，文件权限 0600，按远端连接标识隔离。切换面板保留编辑器实例，重开连接读取草稿。
- 创建前记录启动状态；启动确认丢失不自动重复创建。提交确认丢失保留内容、标记不确定，不自动重发。
- 关闭客户端不结束远端 pi；关闭其远端工作区才会结束它。

原始 RPC 连接器保留用于兼容旧面板及诊断，使用 `herdr-remote auto --agent pi` 或 `herdr-remote pi` 仍进入旧模式；新菜单调用 `open --agent pi` 使用完整原版界面。

## 验证

- Herdr 客户端测试：349 项通过，2 项原有测试忽略。
- `scripts/smoke_native_editor.py`：原版 pi TUI + 原版本地编辑器；整个 pi 进程暂停时，中文、emoji、多行仍在本地显示。
- `scripts/smoke_native_editor_live.py`：真实 SSH、独立临时远端 Herdr session、未修改的官方 server、完整远端 pi 及原有插件。确认面板在远端机器下；暂停真实远端 pi 后本地输入仍显示；确认打开的是原版模型选择菜单。测试不提交模型任务，不操作用户的默认远端 session。
- 旧 RPC / Codex 的既有测试和连接器仍保留。

```sh
python3 scripts/smoke_native_editor.py
# 真实 SSH 测试；当前脚本的测试目标是已有 devbox，使用独立临时 session：
python3 scripts/smoke_native_editor_live.py
# 验证普通 pi 的自动加载，不注入 -e 或桥接环境变量：
python3 scripts/smoke_native_editor_live.py --plain
python3 remote/tests/test_auto.py
node --test remote/tests/remote.test.mjs
```

PTY 测试使用 `.tools/python` 中的 pyte。测试截图文本保存在 `.local/native-editor-live-screen.txt` 与 `.local/native-editor-model-screen.txt`。
