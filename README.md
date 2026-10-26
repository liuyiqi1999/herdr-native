# Herdr Native — macOS prototype

新增：[远程 agent、本地原位编辑](REMOTE.md)。`dist/Herdr Native Auto.app` 可直接通过 **⌘⇧A** 或侧边栏 **menu → new remote pi / codex** 打开远端 agent；自动沿用已有机器、session 和项目，准备或复用后端，无需手动运行连接命令。

独立的 macOS `.app`，使用 Ghostty 的原生窗口和 Metal 渲染运行 Herdr TUI（仅本地客户端增加自动连接入口）。Swift 启动器直接 `execv` Ghostty，再通过 `-e` 运行随应用打包的 Herdr，不读取 shell rc 文件来启动应用。面板中的 shell 和 coding agent 仍然使用 PTY，依然需要各自的可执行文件与运行环境。

## 运行

```sh
open -na "dist/Herdr Native Auto.app"
```

应用可复制到 Applications。它包含 Ghostty 和 Herdr，不依赖原来的安装路径。当前构建使用本机 Ghostty 1.3.1，以及基于 Herdr 0.9.5 源码构建的定制本地客户端；远端官方 server 不需要改动或重启。精确版本记录在应用 `Contents/Resources/provenance.json`。

- 独立 bundle ID：`dev.herdr.native.local`
- 专用 Herdr 会话：`native`（再次打开会重连）
- 可编辑配置：`~/Library/Application Support/Herdr Native/herdr.toml`（首次复制；升级只补充缺失的远端 agent 快捷键，保留其他设置）
- 会话数据：`~/.config/herdr/sessions/native/`
- ⌘Q 关闭客户端，后台 Herdr 会话继续运行。
- 启动器补充常见 Homebrew、Cargo、`~/.local/bin` 路径；nvm 等仅在 shell rc 里提供的工具仍需在面板的 shell 中初始化。

## 快捷键

| 快捷键 | 动作 |
| --- | --- |
| ⌘⇧A | 打开远端 agent 菜单，默认 pi，向下选择 Codex |
| ⌘T | 新建 Herdr 标签：命名后选择 pi / claude / shell |
| ⌘D / ⌘⇧D | 向右 / 向下分屏 |
| ⌘W / ⌘⇧W | 关闭面板 / 标签，沿用 Herdr 原有确认逻辑 |
| ⌘1–9 | 切换标签 |
| ⌘⇧[ / ⌘⇧] | 上一个 / 下一个标签 |
| ⌘⌥方向键 | 聚焦相邻面板 |
| ⌘Enter | 放大 / 还原面板 |
| ⌘P | Go To 搜索跳转 |
| ⌘B | 切换侧边栏 |
| ⌘⇧N | 新建工作区 |
| ⌘⇧R | 重命名标签 |
| ⌘, | Herdr 设置 |
| ⌘⇧/ | Herdr 快捷键帮助 |
| ⌘F | 进入 Herdr copy mode；再按 `/` 搜索 |
| ⌘C / ⌘V | Ghostty 复制 / 粘贴 |
| ⌘+ / ⌘- / ⌘0 | 字号放大 / 缩小 / 重置 |
| ⌃⌘F | 全屏 |
| ⌘Q | 退出桌面客户端，保留会话 |

Herdr 的 Ctrl+B 及未覆盖的 prefix 快捷键作为兼容入口保留。新映射覆盖日常操作，并未删除所有旧绑定。

## 新标签启动命令与自动标题

本地版新建标签时，命名后会显示启动命令选择框。按 ↑ / ↓ / Tab 或点击 `choose` 在 `pi`、`claude`、空命令（shell）之间切换，也可直接输入 `pi --continue` 等命令。按 Enter / `start` 创建并启动，Esc 取消且不创建标签。⌘T、标签栏 `+` 和右键新建共用此流程。

pi 和 Claude Code 输出的终端标题会同步显示在 tab 上，后续自动归纳或手动修改会话标题也会更新。保留标签序号、手动标签名和缩放标记；同一 tab 有多个 agent 时优先显示聚焦面板的标题。此功能使用已有终端标题数据，不额外调用模型。

编辑 `~/Library/Application Support/Herdr Native/herdr.toml`，在已有的 `[ui]` 节中添加（没有则新建该节）：

```toml
[ui]
new_tab_command = "pi"
prompt_new_tab_command = true
# 可选：跳过新标签命名弹窗
# prompt_new_tab_name = false
```

配置支持热更新。`new_tab_command` 是选择框的初始命令（空配置默认选 pi），`prompt_new_tab_command = false` 可关闭选择框，直接执行配置的命令；同时设 `new_tab_command = ""` 可只打开 shell。新版原生启动器会为旧配置补上选择框开关，已有显式设置会保留。命令使用新标签的工作目录及 shell 环境，退出后仍可继续使用 shell。

连接远端工作区时，指令在远端的新标签中执行，因此该机器也需要安装对应工具，且后台服务需要包含此补丁以支持客户端调用 `pane.send_input`；旧版服务会显示 Action unavailable。此设置只作用于客户端主动新建的 Herdr 标签，不会在重连、恢复会话、切换旧标签、分屏或远端 agent 专用入口时重复执行。首次使用需让客户端及对应后台服务都运行新版；仅退出桌面窗口不会升级已运行的后台服务，请在保存工作并结束任务后重启对应会话。

## 按键传输与冲突边界

Ghostty 清除默认快捷键，再安装应用专属映射。⌘T 被转换为 Kitty 键盘序列 `CSI 116;9u`，Herdr 按 `super+t` 匹配。方向键使用 `CSI 1;11D/C/A/B`。没有模拟 prefix；新 agent 的启动命令只通过官方 API 发给新建的本地面板，不发送到已有远端会话。

系统先处理快捷键；其次是 Ghostty；之后是 Herdr；最后才是面板内程序。统一配置两层解决了 Ghostty 与 Herdr 的默认绑定冲突，不能消除系统保留键或让被 Herdr 消费的键同时交给内层应用。普通 Ctrl+C、Ctrl+R 等继续传入面板。Copy/paste 属于外层终端；复杂鼠标选择行为仍沿用上游 TUI/终端约定。

## 构建与验证

需要 macOS、Xcode Command Line Tools、Python 3、Rust、Zig 0.16，以及构建好的 Ghostty.app。本地 Herdr 的补丁保存在 `patches/herdr-native-agent-menu.patch`，基于提交 `143bf5fd4781d164260c63fe28e7dc08054ad32f`。

```sh
# 全新源码检出时应用一次；当前工作区已应用：
git -C herdr apply ../patches/herdr-native-agent-menu.patch
git -C herdr apply ../patches/herdr-native-new-tab-command.patch
(cd herdr && ZIG=/path/to/zig CARGO_PROFILE_RELEASE_OPT_LEVEL=1 cargo build --release --locked)
python3 scripts/build.py --output "dist/Herdr Native Auto.app"
# 或指定其他构建产物和新输出路径：
python3 scripts/build.py --ghostty-app /path/Ghostty.app --herdr /path/patched-herdr --output /path/HerdrNative.app
python3 scripts/smoke_native_agents.py
# 本地启动选择 / 标题同步版（完整补丁基于同一上游提交）：
# 全新检出使用 patches/herdr-native-local-tabs-complete.patch，勿与上面的补丁叠加。
python3 scripts/build.py --source-patch patches/herdr-native-local-tabs-complete.patch --output "dist/Herdr Native Local Tabs.app"
HERDR_TEST_BIN="$PWD/herdr/target/release/herdr" python3 scripts/smoke_local_tabs.py
```

构建器拒绝覆盖已有输出目录。它校验两份配置、编译 Swift 启动器、打包资源及上游许可证，并执行本地 ad-hoc 签名与签名验证。源码参考分别在 `herdr/`、`ghostty/`，构建器不修改它们。

Smoke 测试创建唯一的临时 Herdr 会话，在真实 PTY 中注入与 Ghostty 映射相同的键盘序列，通过 API 检查新增标签、新增分屏、方向聚焦、关闭面板的结果，最后停止并删除测试会话。它验证终端输入到 Herdr 动作这一段；不代替物理键盘到 macOS 菜单分发的完整 GUI 验证。

## 当前限制与下一步

这是可运行的原生桌面封装原型，并非重新编写的 libghostty 嵌入式宿主：沿用 Ghostty 原生 AppKit/SwiftUI 应用，部分菜单、图标、About、窗口操作仍保留 Ghostty 品牌和语义。原生菜单点击创建的是外层 Ghostty 标签/分屏，而快捷键创建的是 Herdr 内部标签/分屏；目前建议用上述快捷键或 TUI 操作。后续正式产品应定制 Ghostty 菜单或编写专属 libghostty 宿主，将菜单与快捷键统一路由到 Herdr。

目前只提供 macOS；Linux/Windows 未实现。应用为本机签名，未做 Developer ID 签名、公证、自动更新或跨机器发布验证。Ghostty 自动更新已禁用，避免覆盖定制应用。上游 Apache-2.0 / MIT 许可证随应用保留；分发前还应核对打包资源与所有依赖的通知要求。
