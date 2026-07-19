# PhyCode 斜杠命令补全设计

日期：2026-07-19
状态：已获用户批准，等待书面复核

## 1. 背景

当前 `phycode chat` 通过 `typer.prompt()` 一次性读取整行文本。斜杠命令只有在用户按下回车后才会进入 `_handle_slash()`，因此输入 `/` 时无法展示候选，继续输入时也没有过滤、参数提示或键盘选择。

本设计参考 Claude Code 的公开交互约定：输入 `/` 展示可用命令，继续输入字符实时过滤；命令用法使用 `<arg>` 表示必填参数、`[arg]` 表示可选参数。OpenCode 的斜杠命令和模糊匹配体验作为补充参考，但不照搬其完整 TUI。

参考资料：

- [Claude Code 交互模式](https://code.claude.com/docs/en/interactive-mode)
- [Claude Code 命令参考](https://code.claude.com/docs/en/commands)
- [OpenCode TUI](https://opencode.ai/docs/tui/)
- [prompt_toolkit 官方项目](https://github.com/prompt-toolkit/python-prompt-toolkit)

## 2. 目标

1. 在真实终端中输入 `/` 后，无需回车即可看到全部规范斜杠命令。
2. 继续输入时实时过滤候选，并支持方向键、Tab、Enter 和 Esc。
3. 候选同时展示命令用法和简短说明；选中项显示完整参数提示。
4. `/model` 提供来自真实供应商的动态模型候选，失败时仍允许手工输入。
5. `/url` 提供格式提示，`/key` 保持完全隐藏且不进入补全或历史。
6. 命令元数据只维护一份，同时驱动补全、帮助、别名解析和必填参数校验。
7. 保持 AgentLoop、审批、工具运行时和非 TTY 调用行为不变。

## 3. 非目标

- 不实现 `@文件` 引用、`!shell` 模式、自定义命令或插件命令。
- 不引入全屏 TUI、Textual 或新的应用级事件循环。
- 不实现跨会话持久输入历史。
- 不改变模型调用、工具审批、trace 或凭据存储机制。
- 不把真实 API 测试加入默认测试套件或 CI。

## 4. 交互设计

### 4.1 候选菜单

候选菜单采用已批准的 Claude Code 风格双层布局：

```text
phycode › /mo
┌────────────────────────────────────────────┐
│ /model <name>       切换当前模型            │
│ /models             列出可用模型            │
└────────────────────────────────────────────┘
用法：/model <name> · 设置模型并立即重载会话
```

规范命令为：`/model`、`/url`、`/key`、`/models`、`/config`、`/status`、`/help`、`/exit`。`/login`、`/quit`、`/?` 等现有别名继续可执行，但默认候选只展示规范命令。输入别名时可以匹配并规范化到对应命令。

匹配仅在整条输入以 `/` 开头时启用。裸 `/` 展示全部命令；后续字符使用不区分大小写的模糊子序列匹配，命令名前缀命中优先于别名和中间位置命中，候选始终显示规范命令。普通聊天文本不产生斜杠候选。

菜单使用纵向单列候选，最多保留八个可见行，超出时滚动。`display` 渲染命令用法，`display_meta` 渲染说明；窄终端由 `prompt_toolkit` 截断说明，但不得截断当前输入或参数占位。

### 4.2 键盘行为

- `↑` / `↓`：候选菜单打开时移动选中项；菜单关闭时使用当前会话历史。
- `Tab`：接受候选但不执行。
- `Enter`：接受选中的候选。无参数命令直接提交；有缺失必填参数的命令只补全到参数位置并继续编辑。
- `Esc`：关闭候选菜单，保留当前输入。
- `Ctrl+C`：取消本次输入并回到新的空白提示符，不退出聊天。
- `Ctrl+D`：仅在输入为空时退出聊天。

### 4.3 参数感知

- `/model <name>`：输入命令和空格后，在后台读取并缓存当前 token 可见的模型 ID；继续输入时过滤模型候选。获取失败或返回空列表时，保留手工输入和正常执行路径。
- `/url <base_url>`：不枚举值，只显示必填占位、OpenAI-compatible URL 说明和示例。
- `/key`：候选只描述“隐藏输入 API key”。执行后复用现有隐藏输入流程，不把 key 作为斜杠参数。
- 其余命令无参数。缺少必填参数时不执行，输入框保持可编辑并持续显示用法。

## 5. 架构

### 5.1 `src/phycode/interactive.py`

新增一个职责单一的交互模块，包含：

- 不可变的 `SlashCommandSpec` 和 `SlashArgumentSpec`。
- `SlashAction`：CLI 分发使用的规范动作标识。
- 唯一的 `SLASH_COMMANDS` 注册表。
- 命令规范化、别名解析和缺失参数判断。
- 基于 `prompt_toolkit` 的自定义 `Completer`。
- 会话级模型候选缓存。
- `InteractivePrompt`：真实 TTY 的 `PromptSession` 封装。
- `BasicPrompt`：非 TTY 和测试管道的整行输入回退。

该模块只接收一个返回模型 ID 的可注入 callable。它不持有 `CredentialStore`、项目配置写权限、LLM 实例或 AgentLoop。

### 5.2 `src/phycode/cli.py`

CLI 继续负责命令副作用：写配置、设置凭据、列出模型、显示状态和退出。它根据 `SlashAction` 分发，不再自行维护命令名称、别名、用法和帮助文本。

`_CHAT_HELP` 改为由注册表生成。现有 `_handle_slash()` 可以保留为兼容入口，但它先使用统一解析器生成规范动作，再执行对应逻辑。

`chat()` 在真实 TTY 中创建一个 `InteractivePrompt`；非 TTY 时使用 `BasicPrompt`。最终得到的文本仍按原路径进入 `_handle_slash()` 或 AgentLoop。

### 5.3 依赖

`pyproject.toml` 增加直接依赖 `prompt-toolkit>=3.0.52,<4`，并用 `uv` 更新锁文件。选用 3.x 稳定 API，不依赖未发布功能。

## 6. 数据流

### 6.1 斜杠命令

```text
按键
  → PromptSession 文档状态
  → SlashCompleter 查询只读注册表
  → 候选菜单 / 参数提示
  → 用户提交完整文本
  → 统一解析器生成 SlashAction 与参数
  → CLI 执行副作用
```

### 6.2 普通对话

```text
按键
  → PromptSession
  → 完整普通文本
  → 现有 AgentLoop
```

补全器不会直接触发配置写入、凭据访问或 AgentLoop 调用。

### 6.3 模型候选

`/model ` 首次请求候选时通过 `prompt_toolkit` 的后台补全能力调用注入的模型列表函数。会话缓存保证同一聊天会话不会因每个按键重复访问网络。成功结果只保存模型 ID；失败状态只保存经过脱敏的短消息。`/models` 显式命令仍走现有输出逻辑，并可刷新当前会话缓存。

## 7. 终端生命周期与兼容性

真实终端的补全发生在等待用户输入期间。用户提交后，`PromptSession.prompt()` 已经返回，因此 Rich spinner、agent 事件输出和风险审批不会与候选菜单同时控制终端。审批结束后进入下一次输入时再重新显示提示符。

TTY 判定同时检查标准输入和标准输出。重定向、`CliRunner`、管道输入或不支持完整终端能力的环境使用 `BasicPrompt`，保持现有逐行行为和脚本测试稳定。

`KeyboardInterrupt` 在聊天循环边界转换为“取消当前输入”，`EOFError` 转换为正常退出。AgentLoop 内部异常和审批异常不由交互模块吞掉。

## 8. 错误处理与安全

- 模型列表超时、HTTP 错误或供应商错误不能阻塞输入线程或终止会话。
- 补全错误状态必须经过现有脱敏函数处理，并限制长度。
- 模型候选缓存只存在于内存，不写入 trace、配置、日志或文件历史。
- 使用会话内存历史，不启用 `FileHistory`。
- `/key` 的隐藏输入独立于 `PromptSession` 的普通历史。
- 未知斜杠命令继续确定性拦截，绝不转发给模型。
- 非 ASCII API key 校验、钥匙串存储和状态输出沿用现有实现。

## 9. 测试策略

严格执行 TDD：先提交或记录失败测试的红色结果，再写最少实现使其变绿，最后重构。

### 9.1 确定性单元测试

- 注册表包含八个规范命令，名称、别名和参数无冲突。
- `/help` 输出从注册表生成，防止帮助、解析和补全漂移。
- `Document("/")` 返回全部规范命令。
- `Document("/mo")` 只返回 `/model` 和 `/models`。
- 模糊匹配按“规范命令前缀、别名前缀、其他子序列”的顺序稳定排序。
- 普通文本和非起始位置的 `/` 不产生候选。
- `/model ` 使用 fake supplier 返回动态模型候选并支持过滤。
- supplier 失败或返回空列表时不抛出到 UI，手工模型名仍可提交。
- `/url` 显示占位、格式说明和示例。
- `/key` 不产生参数候选，不包含任何 secret。
- 缺少必填参数时解析器返回“继续编辑”，不执行动作。

### 9.2 键盘集成测试

使用 `prompt_toolkit` 的 `create_pipe_input()` 与 `DummyOutput` 模拟真实按键，覆盖：

- 方向键选择。
- Tab 接受但不提交。
- Enter 对无参数命令提交、对缺失参数命令继续编辑。
- Esc 关闭菜单且保留文本。
- Ctrl+C 取消输入。
- Ctrl+D 在空输入退出。

### 9.3 回归与真实 smoke

- 现有 `CliRunner` 非 TTY chat 测试必须保持通过。
- Windows 与 WSL 分别运行全量 `uv run pytest -q`。
- 运行 `uvx pyright` 和 `uv build`。
- 使用本机安全存储的真实供应商配置进行一次真实终端 smoke：输入 `/` 检查菜单，输入 `/model ` 检查真实模型候选，再完成一次真实对话。
- 真实 URL、key 和响应内容不得写入仓库、构建物或测试日志。

## 10. 验收标准

1. 真实终端输入 `/` 时无需回车即可看到八个规范候选。
2. `/mo` 实时过滤为 `/model` 和 `/models`。
3. 候选显示参数占位和说明，选中项显示完整用法。
4. 方向键、Tab、Enter、Esc、Ctrl+C 和 Ctrl+D 符合第 4.2 节。
5. `/model ` 能展示真实供应商模型候选；网络失败时手工模型名仍可使用。
6. `/url` 有格式提示；`/key` 不泄漏敏感信息。
7. 未知斜杠命令不会进入 AgentLoop。
8. 非 TTY 行为和现有全部测试不回归。
9. Windows、WSL、Pyright 和构建门禁通过。

## 11. 风险与控制

- **Rich 与 prompt_toolkit 重绘冲突**：只在等待输入时运行 PromptSession；模型输出和审批期间输入会话已经结束。
- **模型枚举造成输入卡顿**：后台补全 + 会话缓存；失败不阻止手工输入。
- **命令元数据再次漂移**：注册表成为唯一来源，并增加漂移测试。
- **交互模块膨胀**：范围限制为输入、补全、解析和只读缓存；所有副作用留在 CLI。
- **Windows 终端差异**：使用 prompt_toolkit 的跨平台后端，并保留非 TTY 回退和 Windows CI。

## 12. 文档与过程记录

实现完成后同步更新：

- `README.md`：斜杠补全、键盘操作和参数提示。
- `PLAN.md`：任务、失败测试、验证命令和 commit hash。
- `AGENT_LOG.md`：brainstorming、TDD 红绿结果、真实终端 smoke 和人工确认。
- 必要时更新 `SPEC.md` 的 CLI 可用性验收项，但不扩大 harness 核心边界。
