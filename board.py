#!/usr/bin/env python3
# 用途：aiw-board 独立展示 aiw 状态的只读派单看板与 Claude Code 状态栏。
# 运行：aiw-board [--once]；aiw-board statusline；依赖：Python 3.11+ 标准库。
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import select
import shlex
import shutil
import socket
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

def state_directory():
    state = os.environ.get('AIW_STATE')
    if state is not None:
        return Path(state).expanduser()
    home = os.environ.get('AIW_HOME')
    if home is not None:
        return Path(home).expanduser() / 'state'
    return Path('~/.claude/aiw').expanduser() / 'state'


STATE = state_directory()
HOST = socket.gethostname()


def clean(value):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def local(row, session):
    return row.get('host', HOST) == HOST and (session is None or row.get('session') == session)


def live_records(directory, session):
    active, stale = [], []
    for path in sorted((STATE / directory).glob('*.json')):
        row = read_json(path, None)
        if row is None or not local(row, session):
            continue
        pid = int(row['pid'])
        if pid <= 0:
            raise ValueError('invalid pid')
        try:
            fd = os.open(path.with_suffix('.lock'), os.O_RDONLY)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    alive = True
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    alive = False
            finally:
                os.close(fd)
        except OSError:
            pass  # 旧 worker 没有锁文件，或无法探测锁时退回 PID。
        else:
            (active if alive else stale).append(row)
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            stale.append(row)
        except PermissionError:
            active.append(row)
        else:
            active.append(row)
    return active, stale


def inflights(session):
    return live_records('inflight', session)


def tracks(session):
    return live_records('tracks', session)[0]


def short_progress(value):
    text = clean(value)
    widths = [0 if unicodedata.combining(c) else
              2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text]
    if sum(widths) <= 24:
        return text
    used = 0
    for i, width in enumerate(widths):
        if used + width > 23:
            return text[:i] + '…'
        used += width
    return text


def track_text(row):
    target = clean(row.get('target', ''))
    parts = ['▶']
    if target and target != HOST.split('.')[0]:
        parts.append(target)
    parts.extend([clean(row.get('label', '')), clean(elapsed(row))])
    if row.get('timeout') is not None:
        parts[-1] += '/' + clean(duration(row['timeout']))
    progress = short_progress(row.get('progress', ''))
    if progress:
        parts.append(progress)
    return ' '.join(parts)


def ledger(limit=None):
    try:
        with (STATE / 'seat-ledger.jsonl').open('rb') as f:
            if limit is None:
                data = f.read()
            else:
                f.seek(0, 2)
                pos, chunks, lines = f.tell(), [], 0
                while pos and lines <= limit:
                    size = min(pos, 8192)
                    pos -= size
                    f.seek(pos)
                    chunk = f.read(size)
                    chunks.append(chunk)
                    lines += chunk.count(b'\n')
                data = b''.join(reversed(chunks))
                if pos:
                    data = data.partition(b'\n')[2]
        lines = data.splitlines()
        if limit is not None:
            lines = lines[-limit:]
        result = []
        for line in lines:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    result.append(row)
            except (ValueError, UnicodeError):
                continue  # 包括正在追加的末行；不修改账本。
        return result
    except FileNotFoundError:
        return []


def timestamp(value):
    return dt.datetime.fromisoformat(value).astimezone()


def today_rows(rows):
    result = []
    for row in rows:
        try:
            if row.get('rc') is not None and timestamp(row['ts']).date() == dt.date.today():
                result.append(row)
        except (ValueError, KeyError, TypeError):
            continue
    return result


def duration(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}:{seconds % 60:02d}'


def elapsed(row):
    return duration(time.time() - timestamp(row['started']).timestamp())


def seat_name(row):
    seat, actual = clean(row.get('seat', '?')), clean(row.get('actual', '?'))
    return seat if seat == actual else f'{seat}→{actual}'


def panel_label(panel):
    return '会诊#' + hashlib.sha1(str(panel).encode()).hexdigest()[:4]


def failed(row):
    return row.get('kind') != 'ok'


def read_payload():
    # 管道未关闭也最多等 50ms。
    chunks = []
    if not sys.stdin.isatty():
        deadline = time.monotonic() + 0.05
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
                break
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 1_048_576:
                break
    raw = b''.join(chunks)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        payload = {}
    return raw, payload if isinstance(payload, dict) else {}


def session_id(payload, default=None):
    value = payload.get('session_id')
    return (value if isinstance(value, str) and value else None) or os.environ.get('CLAUDE_CODE_SESSION_ID') or default


def line(session) -> str:
    try:
        active, _ = inflights(session)
        rows = [r for r in ledger(200) if local(r, session)]
        today = today_rows(rows)
        groups = {}
        for i, row in enumerate(sorted(active, key=lambda r: timestamp(r['started']))):
            key = ('panel', row['panel']) if row.get('panel') else ('single', i)
            groups.setdefault(key, []).append(row)
        parts = []
        for group in groups.values():
            text = ' '.join(f"{seat_name(r)} {elapsed(r)}/{duration(r['timeout'])}" for r in group)
            parts.append(f'会诊[{text}]' if group[0].get('panel') else text)
        segments = [f'● 派单中 {len(active)} 单：' + ' · '.join(parts) if active else '○ 派单空闲']
        running = tracks(session)
        if running:
            segments.append(' · '.join(track_text(row) for row in running))
        completed = []
        now = time.time()
        for row in rows:
            try:
                ts = timestamp(row['ts']).timestamp()
                if row.get('rc') is not None and 0 <= now - ts <= 600:
                    completed.append((ts, row))
            except (ValueError, KeyError, TypeError):
                continue
        completed.sort(key=lambda pair: pair[0])
        completed = completed[-4:]
        if completed:
            age = int(now - completed[-1][0])
            ago = '刚刚' if age < 60 else f'{age // 60} 分钟前'
            items = ' '.join(f"{seat_name(r)} {'✗' if failed(r) else '✓'}" for _, r in completed)
            segments.append(f'刚完成 {items}（{ago}）')
        failures = sum(map(failed, today))
        segments.append(f'今日 {len(today)} 单' + (f' ✗{failures}' if failures else ''))
        return ' │ '.join(segments).rstrip()
    except Exception:
        return '派单 ?'


def statusline():
    try:
        _, payload = read_payload()
        print(line(session_id(payload)))
    except Exception:
        print('派单 ?')
    return 0


def remove_cache_entry(path):
    try:
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    except OSError:
        pass


def base_refresh(cache, key, base):
    cache = Path(cache)
    temporary = None
    try:
        with (cache / f'{key}.payload').open('rb') as payload, tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
            temporary = Path(output.name)
            result = subprocess.run(base, shell=True, stdin=payload, stdout=output,
                                    stderr=subprocess.DEVNULL, timeout=60)
        if result.returncode == 0 and temporary.stat().st_size:
            os.replace(temporary, cache / f'{key}.out')
    except Exception:
        pass  # 刷新失败保留旧缓存。
    finally:
        if temporary is not None:
            remove_cache_entry(temporary)
        remove_cache_entry(cache / f'{key}.lock')
    return 0


def positive_env(name, default):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def base_command():
    if 'AIW_STATUSLINE_BASE' in os.environ:
        return os.environ['AIW_STATUSLINE_BASE']
    installed = shutil.which('ccstatusline')
    if installed:
        return shlex.quote(installed)
    for location in ('~/.bun/bin/ccstatusline', '~/.local/bin/ccstatusline'):
        path = Path(location).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return shlex.quote(str(path))
    return 'bunx -y ccstatusline@latest'


def acquire_refresh_lock(cache, key, now, maximum):
    # 锁住目录以原子执行计数和 mkdir；非阻塞，不等待其他状态栏。
    fd = os.open(cache, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        live = 0
        for path in cache.glob('*.lock'):
            try:
                if path.is_dir() and now - path.stat().st_mtime <= 120:
                    live += 1
            except FileNotFoundError:
                continue  # 后台刷新可能刚释放锁。
        if live >= maximum:
            return False
        lock = cache / f'{key}.lock'
        for attempt in range(2):
            try:
                lock.mkdir()
                return True
            except FileExistsError:
                try:
                    stale = now - lock.stat().st_mtime > 120
                except FileNotFoundError:
                    continue
                if attempt == 0 and stale:
                    remove_cache_entry(lock)
                else:
                    break
        return False
    finally:
        os.close(fd)


def statusline_wrap():
    try:
        raw, payload = read_payload()
        session = session_id(payload)
        key = re.sub(r'[^A-Za-z0-9_-]', '', session or 'default') or 'default'
        base = base_command()
        if not base:
            print(line(session))
            return 0
        cache = Path(os.environ.get('AIW_STATUSLINE_CACHE', '~/.cache/aiw-statusline')).expanduser()
        out = cache / f'{key}.out'
        try:
            data = out.read_bytes()
        except FileNotFoundError:
            data = None
        if data is not None:
            sys.stdout.buffer.write(data + (b'' if data.endswith(b'\n') else b'\n'))
            sys.stdout.buffer.flush()
        cache.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for path in cache.iterdir():
            try:
                if re.fullmatch(r'[A-Za-z0-9_-]+\.(out|payload|lock)', path.name) and now - path.stat().st_mtime > 7 * 86400:
                    remove_cache_entry(path)
            except OSError:
                pass
        modified = out.stat().st_mtime if out.exists() else 0
        ttl = positive_env('AIW_STATUSLINE_BASE_TTL', 300)
        minimum = positive_env('AIW_STATUSLINE_BASE_MIN_INTERVAL', 60)
        maximum = positive_env('AIW_STATUSLINE_BASE_MAX_CONCURRENT', 2)
        age = now - modified
        refresh = not out.exists() or age > ttl
        transcript = payload.get('transcript_path')
        if isinstance(transcript, str) and transcript:
            try:
                refresh = refresh or (age >= minimum and Path(transcript).stat().st_mtime > modified)
            except OSError:
                pass
        if refresh:
            lock = cache / f'{key}.lock'
            if acquire_refresh_lock(cache, key, now, maximum):
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as f:
                        temporary = Path(f.name)
                        f.write(raw)
                    os.replace(temporary, cache / f'{key}.payload')
                    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_base-refresh', str(cache), key, base],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True, close_fds=True)
                except Exception:
                    if temporary is not None:
                        remove_cache_entry(temporary)
                    remove_cache_entry(lock)
                    raise
        print(line(session))
    except Exception:
        print('派单 ?')
    return 0


def render(session, recent):
    now = dt.datetime.now().astimezone()
    print(f"派单看板 {HOST} · {clean(session[:8]) if session else '全部'} · {now:%Y-%m-%d %H:%M:%S %z}")
    active, stale = inflights(session)
    print(f'状态：派单中 {len(active)} 单（' + '、'.join(clean(r['seat']) for r in active) + '）' if active else '状态：空闲')
    print(f'\n在途 {len(active)} 单：座位→实际  已跑/超时  工作目录  会诊  模型')
    for row in sorted(active, key=lambda r: (r.get('panel') or '', r['started'], r['pid'])):
        print(f"  {seat_name(row)}  {elapsed(row)}/{duration(row['timeout'])}  "
              f"{clean(Path(row['workdir']).name)}  {panel_label(row['panel']) if row.get('panel') else '-'}  {clean(row.get('model', '-'))}")
    if not active:
        print('无在途派单')
    for row in stale:
        print(f"残留：pid={row['pid']} {seat_name(row)}（进程已退出）")
    running = tracks(session)
    if running:
        print('\n本地/远端任务：')
        for row in running:
            print('  ' + track_text(row))
    rows = [r for r in ledger() if local(r, session)]
    today = today_rows(rows)
    print(f'\n今日汇总：{len(today)} 单；顶替成功 {sum(bool(r.get("substituted")) and not failed(r) for r in today)} 次')
    seats = sorted({r['seat'] for r in today})
    counts = Counter((r['seat'], failed(r)) for r in today)
    for seat in seats:
        print(f'  {clean(seat)} 成功 {counts[seat, False]} / 失败 {counts[seat, True]}')
    breakers = read_json(STATE / 'breaker.json', {})
    opened = [f"{clean(s)}: {clean(b.get('reason', '?'))}" for s, b in breakers.items() if b.get('until', 0) > time.time()]
    print('本机熔断：' + ('；'.join(opened) or '无'))
    daily = read_json(STATE / 'daily-count.json', {})
    used = daily.get('counts', {}) if daily.get('date') == now.date().isoformat() else {}
    reserved = Counter(r['seat'] for r in daily.get('reserved', {}).values() if time.time() - r['ts'] < 7200)
    print('本机顶替额度使用：' + ('；'.join(f'{clean(s)} 已用 {used.get(s, 0)} / 预约 {reserved[s]}' for s in sorted(set(used) | set(reserved))) or '0'))
    print(f'\n最近流水（{"会话" if session else "本机"}，最近 {recent} 条）：时间  seat→actual  kind  rc  secs  错误末行')
    for row in (rows[-recent:][::-1] if recent else []):
        try:
            clock = timestamp(row['ts']).strftime('%H:%M:%S')
        except (ValueError, KeyError, TypeError):
            clock = '?'
        tail = str(row.get('err_tail') or '').strip().splitlines()
        mark = '· ' if row.get('rc') is None else ('✗ ' if failed(row) else '✓ ')
        print(f"{mark}{clock} {seat_name(row)} {clean(row.get('kind'))} rc={row.get('rc')} {row.get('secs', 0)}s {clean(tail[-1]) if tail else ''}".rstrip())


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('必须为有限正数')
    return value


def main():
    if sys.argv[1:2] == ['statusline']:
        return statusline()
    if sys.argv[1:2] == ['statusline-wrap']:
        return statusline_wrap()
    if sys.argv[1:2] == ['_base-refresh']:
        return base_refresh(*sys.argv[2:])
    parser = argparse.ArgumentParser(prog='aiw-board', description='只读实时派单看板')
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--all', action='store_true')
    scope.add_argument('--session')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=positive, default=2)
    parser.add_argument('--recent', type=int, default=15)
    args = parser.parse_args()
    if args.recent < 0:
        parser.error('--recent 不能为负数')
    session = None if args.all else args.session or os.environ.get('CLAUDE_CODE_SESSION_ID') or None
    try:
        while True:
            if not args.once:
                print('\x1b[2J\x1b[H', end='')
            render(session, args.recent)
            if args.once:
                return 0
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, TypeError, KeyError) as e:
        print(f'看板读取失败：{e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
