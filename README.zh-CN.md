# aiw-board
[English](README.md) | 中文

aiw 的独立只读派单看板，提供 Claude Code 状态栏和 Codex 底栏（经 `aiw tui` 使用）。只用 Python 3.11+ 标准库和 bash 3.2+，兼容 macOS；不派单、不写 aiw 状态、不清理残留进程文件、不碰派单锁。

[aiw](https://github.com/Jerryx333/aiw) 负责执行任务和写状态，本项目负责显示。两者可以独立更新；新机器仍需先安装 aiw，默认位置为 `~/.claude/aiw`（可以是软链）。

## 安装

```bash
git clone https://github.com/Jerryx333/aiw-board.git
cd aiw-board
./install.sh --apply-settings
```

仓库放在哪都行，安装后的软链指向克隆位置，所以克隆目录不要删或挪。

安装脚本将两个入口软链到 `~/.local/bin`，该目录需在 PATH 中。重复运行跳过已有的正确软链；普通文件或指向别处的软链会报错退出，不覆盖。可用 `AIW_BOARD_BIN=/其他目录 ./install.sh` 指定位置。

一条 `./install.sh --apply-settings` 完成整套状态栏安装：有 bun 时全局安装固定版本 `ccstatusline@2.2.30`；PATH、`~/.bun/bin` 或 `~/.local/bin` 中已找到相同版本则跳过。没有 bun 时仅提示安装方法，不中断安装；可用 bunx 时运行状态栏会自动启用兜底。测试或离线安装可设置 `AIW_BOARD_SKIP_CCSTATUSLINE=1` 跳过包安装。

安装会把已有的 ccstatusline 配置备份后替换成本仓库这份。ccstatusline 配置会软链到仓库的 `config/ccstatusline/settings.json`，目标为 `${XDG_CONFIG_HOME:-$HOME/.config}/ccstatusline/settings.json`。已有普通文件或其它软链先备份为 `settings.json.bak-<YYYYmmdd-HHMMSS>`，正确软链直接跳过。改仓库里这份文件即生效；想在多台机器同步，可以 fork 后各机器 pull 自己的 fork。

`--apply-settings` 将 `statusLine` 合并到 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json`，保留其它键，修改前备份为 `settings.json.bak-aiw-board-<stamp>`，再原子替换；配置已相同则跳过，不产生新备份。JSON 损坏时报错退出且保持原文件不变。

不带 `--apply-settings` 时仅打印建议配置，不修改 Claude settings。手动合并时，示例中的 command 应替换为脚本打印的本仓库绝对路径：

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/aiw-board/bin/aiw-statusline",
    "padding": 0,
    "refreshInterval": 60
  }
}
```

## 命令

```bash
aiw-board [--all|--session id] [--once] [--interval 秒] [--recent 条]
aiw-board --once --all
aiw-board statusline
printf '%s\n' '{"session_id":"x"}' | AIW_STATUSLINE_BASE= aiw-statusline
```

默认按 `CLAUDE_CODE_SESSION_ID` 过滤；未设置时显示本机全部，`--session` 指定会话，`--all` 显示本机全部。默认每 2 秒刷新，Ctrl-C 退出；`--once` 只显示一帧，`--recent` 默认 15 条，可设为 0。间隔必须是有限正数，最近条数不能为负数。

在途展示座位→实际执行方、已跑/超时、工作目录、模型与会诊分组。死进程文件只提示「残留」，不删除。按同名 `.lock` 的锁是否被持有判活，旧记录无锁文件或锁探测出错时退回按 pid 判断。今日汇总按执行尝试计数，不含跳过；熔断和顶替额度为本机共享状态。旧账本缺少 session 的行仅在全部视图显示。

## 状态文件

优先读取 `AIW_STATE`，未设置则读取 `<安装目录>/state`。安装目录使用 `AIW_HOME`，未设置则使用 `~/.claude/aiw`。路径支持展开 `~`，与本项目安装位置无关。

| 文件 | 使用的字段 |
| --- | --- |
| `inflight/<pid>.json` | 同名 `.lock` 判活，无锁文件或锁探测出错时退回 `pid`；`host`、`session` 过滤；`seat`、`actual` 展示座位及实际执行方；`model`、`workdir`；`timeout`、`started` 计时；`panel` 会诊分组 |
| `seat-ledger.jsonl` | `ts`、`host`、`session`、`seat`、`actual`、`kind`、`rc`、`secs`、`err_tail`、`substituted`，用于流水、成败、今日统计及顶替次数 |
| `breaker.json` | 按座位保存的 `until`、`reason`，展示未到期熔断 |
| `daily-count.json` | `date`、`counts`；`reserved` 中的 `seat`、`ts`，展示今日使用量及两小时内预约 |

这些字段全部由 aiw 写入。看板应容错处理缺字段，沿用导入版本的兼容行为：缺 host 默认本机，缺 session 不匹配指定会话，缺 model/actual 等展示默认值；文件不存在按空状态处理，账本坏行跳过。必要字段（如在途 pid、started、timeout）损坏或缺失时，完整看板明确报错并非零退出，状态栏降级为 `派单 ?`，不修改原始数据。

## 状态栏

- `● 派单中 N 单`：正在跑的单数，同一座位两单也算 2。
- `○ 派单空闲`：当前会话没有在跑的单。
- `会诊[...]`：同一会诊的在途任务，每家显示已跑/超时；顶替显示 `座位→实际`。
- `刚完成 … ✓/✗`：10 分钟内最新 4 单的成功/失败，括号时间按最新一单计算。
- `今日 N 单 ✗N`：今日尝试数与失败数，失败为零时省略失败数。

会话优先取 stdin JSON 的 `session_id`，再取 `CLAUDE_CODE_SESSION_ID`；`aiw-statusline` 和 `aiw-board statusline` 在没有会话编号时都回退本机全部。包装入口的缓存文件名仍按会话编号生成，只保留 `[A-Za-z0-9_-]`，没有会话编号或清理后为空时用 `default`。摘要只读账本尾部 200 行，今日计数及刚完成列表限于这些行；完整统计看看板。

Claude Code 默认在新消息等事件发生时重跑状态栏。配置 `refreshInterval: 60` 后可持续更新计时。每次刷新约消耗 50ms CPU，多会话叠加，所以默认 60 秒；需要更实时可改小（最小 1 秒）。

包装脚本先输出基础缓存，再追加派单摘要，不等待基础命令。基础状态栏默认每 5 分钟后台刷新；有新消息时（输入的 `transcript_path` 比缓存新），缓存年龄达到最小间隔（默认 60 秒）才触发后台刷新。同一缓存目录默认最多并发 2 个基础刷新，满额时继续使用旧缓存。首次无缓存只显示派单摘要；基础命令失败或输出为空时保留旧缓存。

### Codex 底栏

先安装 aiw、aiw-board 和 tmux（`brew install tmux`）。运行 `aiw tui` 在 tmux 中启动 Codex，也可用 `aiw tui [codex 参数...]` 透传参数。
底栏每 15 秒刷新，派单行与 Claude Code 状态栏使用同一份会话摘要，沿用 `● 派单中 N 单` / `○ 派单空闲` / `刚完成 … ✓/✗` / `今日 N 单 ✗N` 格式。首单前尚无线程映射时显示空闲。
底栏其它行和 `AIW_TUI_STATUS_EXTRA` 的用法见 [aiw 参考文档](https://github.com/Jerryx333/aiw/blob/main/docs/reference.zh-CN.md)。

### aiw track 长任务

`aiw track` 登记的活任务显示在派单段之后：`▶ <target> <label> <已运行 m:ss>[/<timeout m:ss>] <progress>`，多个任务用 ` · ` 连接。target 等于本机 hostname 短名时省略；timeout 为 null、progress 为空时分别省略。进度最多 24 个显示宽度（中文按 2 宽，截断后的 `…` 计入上限）。完整看板同时显示“本地/远端任务”小节。

只读 `STATE/tracks/<pid>.json`，复用 inflight 的同名 `.lock` 判活规则和 host/session 过滤；残留任务不显示、不删除。读取 tracks 零网络、不调用 ssh、不新增子进程或扫描进程表，仅增加一次 `tracks/*.json` glob、小文件读取和锁探测；目录不存在时按空状态处理，输出保持不变。此约束针对任务展示，既有基础状态栏外部命令行为不变。

## 环境变量

| 变量 | 默认值与用途 |
| --- | --- |
| `AIW_STATE` | 直接指定状态目录，优先于 HOME 设置 |
| `AIW_HOME` | `~/.claude/aiw`；未指定 STATE 时在其下读取 state |
| `AIW_PYTHON` | `python3`，入口和安装脚本使用的 Python 3.11+ 可执行文件；安装前检查版本 |
| `AIW_STATUSLINE_BASE` | 未设置时优先本地 `ccstatusline`（顺序见下）；显式设置自定义基础命令，空字符串表示只显示派单摘要且忽略旧基础缓存 |
| `AIW_STATUSLINE_CACHE` | `~/.cache/aiw-statusline`，按会话存基础输出、输入 payload 和刷新锁 |
| `AIW_STATUSLINE_BASE_TTL` | `300` 秒，须为正整数；非法或非正数回退 300 |
| `AIW_STATUSLINE_BASE_MIN_INTERVAL` | `60` 秒，新消息触发刷新的最小缓存年龄；须为正整数，非法或非正数回退 60；不限制缓存缺失或 TTL 到期刷新 |
| `AIW_STATUSLINE_BASE_MAX_CONCURRENT` | `2`，同一缓存目录所有会话的基础刷新并发上限；须为正整数，非法或非正数回退 2；忽略超过 120 秒的刷新锁 |
| `AIW_BOARD_BIN` | `$HOME/.local/bin`，安装软链的目标目录 |

基础命令未设置时依次查找 PATH 中的 `ccstatusline`、`~/.bun/bin/ccstatusline`、`~/.local/bin/ccstatusline`（须可执行），均未找到才回退 `bunx -y ccstatusline@latest`，此时需要 bun/bunx 且可能联网；禁用基础命令后无需此依赖。缓存会清理超过 7 天的已知会话文件，超过 120 秒的刷新锁可回收。唯一常规写入为状态栏缓存，aiw 的 state 始终只读。

2026-10-09 实测：包装入口约 50 ms，bunx 基础命令每次 2.7–5.7 秒，本地安装仍需 2.5–6 秒，其中约 2.3 秒花在钥匙串凭据查询、git 等子进程，并触发 macOS 签名检查。活跃会话持续写入 transcript，原先会绕过 300 秒 TTL；最小间隔与跨会话并发上限用于减少重复刷新及 CPU 开销。

## 自测

```bash
python3 tests/test_board.py
python3 tests/test_board.py test_install
```

不依赖 pytest 或 aiw 源码；临时目录构造 state、假 HOME、基础命令与安装位置。逐项打印 PASS，失败非零退出，不修改真实 settings 或安装目录。

## 变更记录

- 2026-10-14 首次公开：从 aiw 拆出的只读派单看板与状态栏。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
