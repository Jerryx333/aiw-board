#!/usr/bin/env bash
# 用途：幂等安装状态栏入口、ccstatusline 与配置软链。
# 运行：./install.sh [--apply-settings]；依赖：bash 3.2+、Python 3.11+，可选 bun。
set -euo pipefail
CCSTATUSLINE_VERSION=2.2.30
apply_settings=0
for arg in "$@"; do
  case "$arg" in
    --apply-settings) apply_settings=1 ;;
    *) echo "未知参数：$arg；用法：$0 [--apply-settings]" >&2; exit 2 ;;
  esac
done
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  case "$src" in /*) ;; *) src="${dir}/${src}" ;; esac
done
repo="$(cd -P "$(dirname "$src")" && pwd)"
python="${AIW_PYTHON:-python3}"
"$python" -c 'import sys; sys.exit("当前 Python %s；需要 Python 3.11 及以上；可用 AIW_PYTHON 指定解释器" % ".".join(map(str, sys.version_info[:3])) if sys.version_info < (3, 11) else 0)'
"$python" - "$repo" "${AIW_BOARD_BIN:-$HOME/.local/bin}" "$CCSTATUSLINE_VERSION" "$apply_settings" <<'PY'
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

repo, dest = map(Path, sys.argv[1:3])
version, apply_settings = sys.argv[3], sys.argv[4] == '1'
home = Path.home()
config = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config') / 'ccstatusline/settings.json'
settings = Path(os.environ.get('CLAUDE_CONFIG_DIR') or home / '.claude') / 'settings.json'
status = dict(type='command', command=str(repo / 'bin/aiw-statusline'), padding=0, refreshInterval=15)
links = [(dest / name, repo / 'bin' / name) for name in ('aiw-board', 'aiw-statusline')]
# 先检查全部冲突和 JSON，避免预检失败时只安装一半。
for link, target in links:
    if link.is_symlink() and link.resolve() == target.resolve():
        continue
    if link.exists() or link.is_symlink():
        sys.exit(f'拒绝覆盖已有目标：{link}')
data = {}
if apply_settings and (settings.exists() or settings.is_symlink()):
    try:
        data = json.loads(settings.read_text())
        if not isinstance(data, dict):
            raise ValueError('顶层必须是 JSON 对象')
    except (OSError, ValueError) as exc:
        sys.exit(f'无法读取 Claude settings JSON：{settings}：{exc}')


def backup_path(path, prefix):
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    candidate = path.with_name(path.name + prefix + stamp)
    # 同一秒再次替换不同配置时也不能覆盖旧备份。
    index = 0
    while candidate.exists() or candidate.is_symlink():
        index += 1
        candidate = path.with_name(path.name + prefix + stamp + f'-{index}')
    return candidate


def executable(name):
    found = shutil.which(name)
    if found:
        return Path(found)
    for directory in (home / '.bun/bin', home / '.local/bin'):
        path = directory / name
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def installed_version(path):
    if path is None:
        return None
    for directory in path.resolve().parents:
        package = directory / 'package.json'
        if package.is_file():
            try:
                metadata = json.loads(package.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(metadata, dict) and metadata.get('name') == 'ccstatusline':
                return metadata.get('version')
    return None


if os.environ.get('AIW_BOARD_SKIP_CCSTATUSLINE') == '1':
    print('跳过 ccstatusline 安装（AIW_BOARD_SKIP_CCSTATUSLINE=1）')
elif installed_version(executable('ccstatusline')) == version:
    print(f'ccstatusline {version} 已安装，跳过')
else:
    bun = executable('bun')
    if bun:
        subprocess.run([str(bun), 'add', '-g', f'ccstatusline@{version}'], check=True)
    else:
        print('未找到 bun，跳过 ccstatusline 安装。请参照 https://bun.sh 安装 bun；'
              '可用 bunx 时，运行状态栏会自动启用 bunx 兜底。')

dest.mkdir(parents=True, exist_ok=True)
for link, target in links:
    if link.is_symlink():
        print(f'已安装，跳过：{link}')
    else:
        link.symlink_to(target)
        print(f'已安装：{link} -> {target}')
target = repo / 'config/ccstatusline/settings.json'
config.parent.mkdir(parents=True, exist_ok=True)
if config.is_symlink() and config.resolve() == target.resolve():
    print(f'配置已链接，跳过：{config}')
else:
    if config.exists() or config.is_symlink():
        backup = backup_path(config, '.bak-')
        config.rename(backup)
        print(f'配置已备份：{backup}')
    config.symlink_to(target)
    print(f'配置已链接：{config} -> {target}')

if apply_settings:
    if data.get('statusLine') == status:
        print(f'statusLine 已相同，跳过：{settings}')
    else:
        data['statusLine'] = status
        settings.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.settings-aiw-board-', dir=settings.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            if settings.exists():
                backup = backup_path(settings, '.bak-aiw-board-')
                shutil.copy2(settings, backup)
                os.chmod(temporary, settings.stat().st_mode & 0o777)
                print(f'Claude settings 已备份：{backup}')
            os.replace(temporary, settings)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(f'statusLine 已写入：{settings}')
else:
    print('请将以下 statusLine 合并到 Claude Code settings.json（不会自动修改）：')
    print(json.dumps({'statusLine': status}, ensure_ascii=False, indent=2))
PY
