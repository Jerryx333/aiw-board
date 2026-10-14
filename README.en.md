# aiw-board
English | [中文](README.md)

A standalone, read-only dispatch board and Claude Code status line for aiw. Uses only the Python 3.11+ standard library and bash 3.2+, with macOS support. It does not dispatch tasks, write aiw state, remove stale process files, or touch dispatch locks.

[aiw](https://github.com/Jerryx333/aiw) runs tasks and writes state; this project displays it. The two can be updated independently. On a new machine, install aiw first, normally at `~/.claude/aiw` (which can be a symlink).

## Installation

```bash
git clone https://github.com/Jerryx333/aiw-board.git
cd aiw-board
./install.sh --apply-settings
```

Clone the repository anywhere you like. The installed symlinks point to the clone, so do not delete or move that directory.

The installer symlinks both entry points into `~/.local/bin`, which must be on PATH. Running it again skips existing symlinks that point to the correct targets. Regular files or symlinks pointing elsewhere cause an error and are never overwritten. Use `AIW_BOARD_BIN=/other/dir ./install.sh` to choose another location.

A single `./install.sh --apply-settings` sets up the entire status line. If bun is available, it globally installs the pinned version `ccstatusline@2.2.30`; it skips installation if that version is already found on PATH, in `~/.bun/bin`, or in `~/.local/bin`. Without bun, it prints installation instructions and continues. If bunx is available, the status line automatically uses it as a fallback at runtime. Set `AIW_BOARD_SKIP_CCSTATUSLINE=1` to skip package installation for tests or offline setup.

Installation backs up any existing ccstatusline configuration and replaces it with this repository's configuration. It symlinks `config/ccstatusline/settings.json` to `${XDG_CONFIG_HOME:-$HOME/.config}/ccstatusline/settings.json`. Existing regular files or other symlinks are first backed up as `settings.json.bak-<YYYYmmdd-HHMMSS>`; correct symlinks are skipped. Edits to the repository's configuration take effect directly. To sync it across machines, fork the repository and pull your fork on each machine.

`--apply-settings` merges `statusLine` into `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json`, preserving other keys. Before replacing the file atomically, it creates a backup named `settings.json.bak-aiw-board-<stamp>`. If the configuration already matches, it skips the update and creates no new backup. Invalid JSON causes an error and leaves the original file untouched.

Without `--apply-settings`, the installer only prints suggested configuration and does not modify Claude settings. When merging it manually, replace the example's command with the absolute repository path printed by the script:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/aiw-board/bin/aiw-statusline",
    "padding": 0,
    "refreshInterval": 15
  }
}
```

## Commands

```bash
aiw-board [--all|--session id] [--once] [--interval 秒] [--recent 条]
aiw-board --once --all
aiw-board statusline
printf '%s\n' '{"session_id":"x"}' | AIW_STATUSLINE_BASE= aiw-statusline
```

By default, tasks are filtered by `CLAUDE_CODE_SESSION_ID`; if unset, all tasks on the local machine are shown. Use `--session` to select a session or `--all` to show all local tasks. The board refreshes every 2 seconds by default; press Ctrl-C to exit. `--once` displays one frame. `--recent` defaults to 15 entries and can be set to 0. The interval must be a finite positive number, and the recent-entry count cannot be negative.

In-flight entries show the seat → actual executor, elapsed time / timeout, working directory, model, and panel group. Files for dead processes are marked `残留` (stale) and are not deleted. Liveness is determined by whether the matching `.lock` is held; older records without a lock file, or lock-probe errors, fall back to checking the pid. Today's totals count execution attempts and exclude skipped tasks. Circuit breakers and substitution quotas are shared across the local machine. Older ledger rows without a session appear only in the all-sessions view.

## State Files

Reads `AIW_STATE` first, or `<安装目录>/state` if unset. The installation directory comes from `AIW_HOME`, defaulting to `~/.claude/aiw`. Paths support `~` expansion and are independent of where this project is installed.

| File | Fields used |
| --- | --- |
| `inflight/<pid>.json` | The matching `.lock` determines liveness, falling back to `pid` if the lock file is missing or probing fails; `host`, `session` for filtering; `seat`, `actual` for the seat and actual executor; `model`, `workdir`; `timeout`, `started` for timing; `panel` for panel grouping |
| `seat-ledger.jsonl` | `ts`, `host`, `session`, `seat`, `actual`, `kind`, `rc`, `secs`, `err_tail`, `substituted` for history, success/failure, today's totals, and substitution counts |
| `breaker.json` | Per-seat `until`, `reason` for displaying active circuit breakers |
| `daily-count.json` | `date`, `counts`; `seat`, `ts` in `reserved` for today's usage and reservations made within the last two hours |

All these fields are written by aiw. The board tolerates missing fields and retains the compatibility behavior of the imported version: a missing host defaults to the local machine; a missing session does not match a selected session; missing model/actual and similar fields use display defaults. Missing files are treated as empty state, and malformed ledger rows are skipped. If required fields (such as in-flight pid, started, or timeout) are invalid or missing, the full board reports an explicit error and exits with a nonzero status; the status line falls back to `派单 ?` (dispatch status unknown). Neither modifies the source data.

## Status Line

- `● 派单中 N 单` (N tasks running): the number of running tasks; two tasks on the same seat count as 2.
- `○ 派单空闲` (dispatch idle): no tasks are running in the current session.
- `会诊[...]` (panel [...]): in-flight tasks in the same panel, with elapsed time / timeout for each provider; substitutions appear as `座位→实际` (seat → actual executor).
- `刚完成 … ✓/✗` (just completed … ✓/✗): success/failure for the latest 4 tasks completed within 10 minutes. The time in parentheses is based on the most recent task.
- `今日 N 单 ✗N` (N attempts today, N failures): today's attempts and failures; the failure count is omitted when zero.

The session is taken first from `session_id` in stdin JSON, then from `CLAUDE_CODE_SESSION_ID`. Both `aiw-statusline` and `aiw-board statusline` fall back to all local tasks when no session ID is available. The wrapper's cache filenames are still derived from the session ID, retaining only `[A-Za-z0-9_-]`; a missing ID or an empty result after sanitization uses `default`. The summary reads only the last 200 ledger rows, so today's counts and the just-completed list are limited to those rows. Use the full board for complete statistics.

By default, Claude Code reruns the status line on events such as new messages. Setting `refreshInterval: 15` keeps timers updating continuously. Each refresh uses about 50ms of CPU time, adding up across sessions. For more frequent updates, use 2 seconds (minimum 1 second).

The wrapper first outputs the cached base status line, then appends the dispatch summary without waiting for the base command. By default, the base status line refreshes in the background every 5 minutes. A new message (the input `transcript_path` is newer than the cache) triggers a background refresh only once the cache reaches the minimum age (60 seconds by default). Each cache directory allows at most 2 concurrent base refreshes by default; when that limit is reached, the old cache stays in use. With no cache on the first run, only the dispatch summary is shown. If the base command fails or returns empty output, the old cache is retained.

### Long-running aiw track Tasks

Live tasks registered with `aiw track` appear after the dispatch segment: `▶ <target> <label> <已运行 m:ss>[/<timeout m:ss>] <progress>` (`已运行` means elapsed). Multiple tasks are joined with ` · `. The target is omitted when it matches the local hostname's short form; timeout and progress are omitted when null and empty, respectively. Progress is limited to 24 display columns (Chinese characters count as 2; the truncation marker `…` counts toward the limit). The full board also shows a `本地/远端任务` (local/remote tasks) subsection.

This display reads only `STATE/tracks/<pid>.json`, reusing inflight's matching `.lock` liveness rules and host/session filters. Stale tasks are neither displayed nor deleted. Reading tracks requires no network access, ssh calls, new subprocesses, or process-table scans; it adds only one `tracks/*.json` glob, small-file reads, and lock probes. A missing directory is treated as empty state, leaving the output unchanged. These constraints apply to task display; existing external-command behavior in the base status line stays the same.

## Environment Variables

| Variable | Default and purpose |
| --- | --- |
| `AIW_STATE` | Sets the state directory directly, taking precedence over HOME settings |
| `AIW_HOME` | `~/.claude/aiw`; reads state beneath it when STATE is not specified |
| `AIW_PYTHON` | `python3`, the Python 3.11+ executable used by the entry points and installer; the version is checked before installation |
| `AIW_STATUSLINE_BASE` | When unset, prefers local `ccstatusline` (search order below); set explicitly for a custom base command, or to an empty string to show only the dispatch summary and ignore old base caches |
| `AIW_STATUSLINE_CACHE` | `~/.cache/aiw-statusline`, storing base output, input payloads, and refresh locks per session |
| `AIW_STATUSLINE_BASE_TTL` | `300` seconds; must be a positive integer, otherwise falls back to 300 |
| `AIW_STATUSLINE_BASE_MIN_INTERVAL` | `60` seconds, the minimum cache age for a new-message refresh; must be a positive integer, otherwise falls back to 60; does not limit refreshes caused by a missing cache or expired TTL |
| `AIW_STATUSLINE_BASE_MAX_CONCURRENT` | `2`, the maximum concurrent base refreshes across all sessions in one cache directory; must be a positive integer, otherwise falls back to 2; ignores refresh locks older than 120 seconds |
| `AIW_BOARD_BIN` | `$HOME/.local/bin`, the destination directory for installed symlinks |

When no base command is set, the wrapper searches for executable `ccstatusline` on PATH, then `~/.bun/bin/ccstatusline`, then `~/.local/bin/ccstatusline`. Only if none is found does it fall back to `bunx -y ccstatusline@latest`, which requires bun/bunx and may access the network. Disabling the base command removes that dependency. Cache cleanup removes known session files older than 7 days, and refresh locks older than 120 seconds can be reclaimed. The status-line cache is the only routine write destination; aiw state always remains read-only.

Measured on 2026-10-09: the wrapper took about 50 ms; the bunx base command took 2.7–5.7 seconds per run, and a local installation still took 2.5–6 seconds. About 2.3 seconds went to Keychain credential queries, git, and other subprocesses, also triggering macOS signature checks. Active sessions continually write to the transcript, which previously bypassed the 300-second TTL. The minimum interval and cross-session concurrency limit reduce repeated refreshes and CPU usage.

## Self-tests

```bash
python3 tests/test_board.py
python3 tests/test_board.py test_install
```

Requires neither pytest nor aiw source code. Tests build state, a fake HOME, base commands, and installation locations in temporary directories. Each test prints PASS; failures exit with a nonzero status. Real settings and installation directories are not modified.

## Changelog

- 2026-10-14 Initial public release: a read-only dispatch board and status line extracted from aiw.

## License

Apache-2.0; see [LICENSE](LICENSE).
