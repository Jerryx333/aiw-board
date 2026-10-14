#!/usr/bin/env python3
# 用途：aiw-board 自包含测试，全部状态和 HOME 使用临时目录。
# 运行：python3 tests/test_board.py [用例名]；依赖：Python 3.11+ 标准库、bash。
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def sh(cmd, env=None, cwd=None, input=None):
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, input=input, timeout=120)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def board_fixture(t):
    import datetime as dt
    import socket
    state = t / 'state'; (state / 'inflight').mkdir(parents=True)
    row = dict(pid=os.getpid(), host=socket.gethostname(), session='board-session', claude_pid=None,
               seat='alpha', actual='bravo-cli', model='fake-model', workdir='/tmp/board-work',
               prompt='abc', ro=True, timeout=600, started=dt.datetime.now().astimezone().isoformat(),
               panel='/tmp/panel-one', nested=False)
    (state / 'inflight' / f'{os.getpid()}.json').write_text(json.dumps(row))
    (state / 'inflight' / '999999.json').write_text(json.dumps(dict(row, pid=999999, session='other-session', seat='dead-seat')))
    led = dict(ts=row['started'], host=row['host'], seat='alpha', actual='alpha', rc=0, kind='ok', secs=1)
    (state / 'seat-ledger.jsonl').write_text('\n'.join(json.dumps(x) for x in
        [dict(led, seat='legacy-seat'), dict(led, session='other-session', seat='other-seat'),
         dict(led, session='board-session'), dict(led, session='board-session', rc=1, kind='auth', err_tail='first\nlast')]) + '\n')
    home = t / 'home'; home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith('AIW_')}
    env.update(HOME=str(home), AIW_STATE=str(state), CLAUDE_CODE_SESSION_ID='board-session', AIW_PYTHON=sys.executable)
    return state, env


def test_board_once():
    with tempfile.TemporaryDirectory() as td:
        state, env = board_fixture(Path(td))
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in state.rglob('*') if p.is_file()}
        cmd = [str(REPO / 'bin/aiw-board'), '--once']
        r = sh(cmd + ['--all'], env=env)
        check(r.returncode == 0 and all(x in r.stdout for x in ['alpha→bravo-cli', '残留', '会诊#', '状态：派单中 1 单（alpha）', '在途 1 单：', 'board-work', 'legacy-seat']), r.stdout + r.stderr)
        r = sh(cmd + ['--session', 'board-session'], env=env)
        check(r.returncode == 0 and 'alpha→bravo-cli' in r.stdout and 'other-seat' not in r.stdout and 'legacy-seat' not in r.stdout and '残留' not in r.stdout, r.stdout)
        check('成功 1 / 失败 1' in r.stdout and '✗ ' in r.stdout and '✓ ' in r.stdout and 'last' in r.stdout, r.stdout)
        after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in state.rglob('*') if p.is_file()}
        check(before == after, '展示端改了 state')


def test_inflight_lock_liveness():
    import select
    with tempfile.TemporaryDirectory() as td:
        state, env = board_fixture(Path(td))
        inflight = state / 'inflight'
        row = json.loads((inflight / f'{os.getpid()}.json').read_text())
        for path in inflight.iterdir():
            path.unlink()
        cmd = [sys.executable, str(REPO / 'board.py')]

        def verify(pid, active):
            record = inflight / f'{pid}.json'
            record.write_text(json.dumps(dict(row, pid=pid)))
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in inflight.iterdir()}
            r = sh(cmd + ['--once', '--session', 'board-session'], env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            check(f'在途 {int(active)} 单：' in r.stdout, r.stdout)
            check(('残留：' in r.stdout) == (not active), r.stdout)
            check('alpha→bravo-cli' in r.stdout, r.stdout)
            r = sh(cmd + ['statusline'], env=env, input='{"session_id":"board-session"}')
            check(r.returncode == 0, r.stdout + r.stderr)
            check(('派单中 1 单' if active else '派单空闲') in r.stdout, r.stdout)
            after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in inflight.iterdir()}
            check(before == after, '锁判活修改了 inflight 文件名、内容或修改时间')
            record.unlink()

        holder = subprocess.Popen([sys.executable, '-c', '''
import fcntl, os, pathlib, sys
with (pathlib.Path(sys.argv[1]) / f'{os.getpid()}.lock').open('w') as lock:
    lock.write('held lock'); lock.flush()
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('ready', flush=True)
    sys.stdin.read()
''', str(inflight)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            check(select.select([holder.stdout], [], [], 10)[0], '持锁子进程未就绪')
            check(holder.stdout.readline().strip() == 'ready', '持锁子进程启动失败')
            verify(holder.pid, True)
        finally:
            holder.communicate(timeout=10)
        (inflight / f'{holder.pid}.lock').unlink()

        lock = inflight / f'{os.getpid()}.lock'
        lock.write_text('unheld lock')
        verify(os.getpid(), False)  # PID 仍存活，但锁已释放（PID 重用）。
        lock.unlink()
        verify(os.getpid(), True)  # 旧 worker：无锁文件且 PID 存活。
        verify(holder.pid, False)  # 已 wait 的子进程：无锁文件且 PID 已退出。


def test_statusline():
    with tempfile.TemporaryDirectory() as td:
        state, env = board_fixture(Path(td))
        cmd = [str(REPO / 'bin/aiw-board'), 'statusline']
        r = sh(cmd, env=env, input='{"session_id":"board-session"}')
        check(r.returncode == 0 and len(r.stdout.splitlines()) == 1 and '派单' in r.stdout and 'alpha→bravo-cli' in r.stdout and '今日 2 单 ✗1' in r.stdout, r.stdout)
        check(r.stdout.rstrip('\n') == r.stdout.strip() and '\x1b' not in r.stdout, '空格或 ANSI')
        r = sh(cmd, env=env, input='{"session_id":"no-match"}')
        check(not (state / 'tracks').exists(), 'fixture 不应有 tracks 目录')
        check(r.stdout == '○ 派单空闲 │ 今日 0 单\n', r.stdout)
        r = sh(cmd, env=env, input='bad json')
        check(r.returncode == 0 and 'alpha' in r.stdout, '非法 JSON 未回退')
        (state / 'inflight' / f'{os.getpid()}.json').unlink()
        check('空闲' in sh(cmd, env=env, input='').stdout, '无在途未显示空闲')
        (state / 'inflight' / 'bad.json').write_text('{bad')
        r = sh(cmd, env=env, input='')
        check(r.returncode == 0 and r.stdout == '派单 ?\n' and not r.stderr, '异常未吞掉')
        (state / 'inflight' / 'bad.json').unlink()
        # 只读最后 200 行，不读取早期记录；兼容坏末行。
        ledger = state / 'seat-ledger.jsonl'
        base = json.loads(ledger.read_text().splitlines()[-2])
        ledger.write_text(''.join(json.dumps(base) + '\n' for _ in range(250)))
        check('今日 200 单' in sh(cmd, env=env, input='').stdout, '账本未限尾部')


def test_tracks():
    import datetime as dt
    import fcntl
    import socket
    from contextlib import ExitStack
    with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
        state, env = board_fixture(Path(td))
        directory = state / 'tracks'
        directory.mkdir()
        now = dt.datetime.now().astimezone().isoformat()
        base = dict(pid=os.getpid(), host=socket.gethostname(), session='board-session',
                    target='build-box', label='v2 渲染', timeout=600, started=now,
                    progress='生成中', updated=now)

        def record(name, held=True, **changes):
            path = directory / f'{name}.json'
            path.write_text(json.dumps(dict(base, **changes)))
            lock = stack.enter_context(path.with_suffix('.lock').open('w'))
            if held:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        record('1')
        record('2', target=socket.gethostname().split('.')[0], label='本地任务', timeout=None,
               progress='')
        record('3', held=False, label='残留任务')  # 活 PID、释放锁：仍须隐藏。
        record('4', label='其他会话', session='other-session')
        record('5', label='其他主机', host=socket.gethostname() + '-other')
        record('6', label='长进度', progress='中' * 13)
        record('7', label='清理\n标签', target='远端\t主机', progress='进度\n正常')
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
        cmd = [sys.executable, str(REPO / 'board.py')]
        r = sh(cmd + ['statusline'], env=env, input='')
        check(r.returncode == 0, r.stderr)
        check(' │ ▶ build-box v2 渲染 ' in r.stdout and '/10:00 生成中 · ▶ 本地任务 ' in r.stdout, r.stdout)
        local_text = r.stdout.split('▶ 本地任务 ')[1].split(' · ')[0]
        check('/' not in local_text and len(local_text.split()) == 1, local_text)
        check(all(x not in r.stdout for x in ['残留任务', '其他会话', '其他主机']), r.stdout)
        check('中' * 11 + '…' in r.stdout and '中' * 12 not in r.stdout, r.stdout)
        check('▶ 远端 主机 清理 标签 ' in r.stdout and '进度 正常' in r.stdout, r.stdout)
        check(len(r.stdout.splitlines()) == 1, r.stdout)
        check(r.stdout.index('▶') < r.stdout.index('刚完成'), r.stdout)
        r = sh(cmd + ['--once'], env=env)
        check(r.returncode == 0 and '本地/远端任务：' in r.stdout and '▶ build-box v2 渲染 ' in r.stdout, r.stdout + r.stderr)
        check(all(x not in r.stdout for x in ['残留任务', '其他会话', '其他主机']), r.stdout)
        after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
        check(before == after, '读取 tracks 修改了文件内容、时间或删除了残留')


def test_tracks_performance_no_network():
    import datetime as dt
    import fcntl
    import socket
    import time
    from contextlib import ExitStack
    with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
        t = Path(td)
        state, env = board_fixture(t)
        directory = state / 'tracks'
        directory.mkdir()
        fake_bin = t / 'bin'
        fake_bin.mkdir()
        marker = t / 'ssh-called'
        ssh = fake_bin / 'ssh'
        ssh.write_text('#!/bin/sh\nprintf called > "$SSH_MARKER"\nexit 1\n')
        ssh.chmod(0o755)
        env.update(PATH=str(fake_bin) + os.pathsep + env.get('PATH', ''), SSH_MARKER=str(marker))
        now = dt.datetime.now().astimezone().isoformat()
        for i in range(50):
            record = directory / f'{i}.json'
            record.write_text(json.dumps(dict(pid=os.getpid(), host=socket.gethostname(),
                session='board-session', target='build-box', label=f'track-{i}',
                timeout=None, started=now, progress='生成中', updated=now)))
            lock = stack.enter_context(record.with_suffix('.lock').open('w'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        start = time.perf_counter()
        r = sh([sys.executable, str(REPO / 'board.py'), 'statusline'], env=env, input='')
        elapsed = time.perf_counter() - start
        check(r.returncode == 0 and r.stdout.count('▶') == 50, r.stdout + r.stderr)
        check(elapsed < 0.3, f'50 tracks 状态栏耗时 {elapsed:.3f}s >= 300ms')
        check(not marker.exists(), '状态栏调用了 ssh')


def test_track_progress_width():
    import runpy
    short = runpy.run_path(str(REPO / 'board.py'))['short_progress']
    for text, expected in [('a' * 24, 'a' * 24), ('中' * 12, '中' * 12),
                           ('a' * 25, 'a' * 23 + '…'),
                           ('中' * 11 + 'abc', '中' * 11 + 'a…'),
                           ('e\u0301' * 24, 'e\u0301' * 24)]:
        check(short(text) == expected, repr(short(text)))


def test_statusline_states():
    import datetime as dt
    with tempfile.TemporaryDirectory() as td:
        state, env = board_fixture(Path(td))
        for path in (state / 'inflight').glob('*.json'):
            path.unlink()
        now = dt.datetime.now().astimezone()
        def ts(age):
            return (now - dt.timedelta(seconds=age)).isoformat()
        for i, seat in enumerate(['charly', 'alpha', 'foxtrot-cli']):
            row = dict(pid=os.getpid(), session='S', seat=seat, actual=seat,
                       started=ts(6), timeout=180, panel='same-panel' if i else None)
            (state / 'inflight' / f'{i}.json').write_text(json.dumps(row))
        rows = [dict(session='S', ts=ts(age), seat=seat, actual=seat, rc=rc, kind=kind)
                for age, seat, rc, kind in [(1200, 'ancient-unique-seat', 0, 'ok'),
                                           (120, 'bravo-cli', 0, 'ok'), (90, 'failed-seat', 1, 'auth')]]
        ledger = state / 'seat-ledger.jsonl'
        ledger.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        cmd = [sys.executable, str(REPO / 'board.py'), 'statusline']
        def read():
            return sh(cmd, env=env, input='{"session_id":"S"}').stdout
        output = read()
        check(output.startswith('● 派单中 3 单：'), output)
        check(all(x in output for x in ['会诊[alpha', 'foxtrot-cli', '刚完成', '✓', '✗']), output)
        check('ancient-unique-seat' not in output and len(output.splitlines()) == 1, output)
        for path in (state / 'inflight').glob('*.json'):
            path.unlink()
        check(read().startswith('○ 派单空闲'), read())
        ledger.write_text(json.dumps(rows[0]) + '\n')
        check('刚完成' not in read(), read())


def test_wrap_without_session():
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state, env = board_fixture(t)
        env.pop('CLAUDE_CODE_SESSION_ID', None)
        cache = t / 'cache'
        env.update(AIW_STATUSLINE_CACHE=str(cache), AIW_STATUSLINE_BASE='')
        cmd = ['bash', str(REPO / 'bin/aiw-statusline')]
        r = sh(cmd, env=env, input='')
        check(r.returncode == 0 and r.stdout.startswith('● 派单中 1 单'), r.stdout + r.stderr)
        env['AIW_STATUSLINE_BASE'] = 'echo BASE-LINE'
        r = sh(cmd, env=env, input='')
        check(r.returncode == 0 and r.stdout.startswith('● 派单中 1 单'), r.stdout + r.stderr)
        lock = cache / 'default.lock'
        deadline = time.monotonic() + 5
        while lock.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        check(not lock.exists(), '残留刷新锁')
        check({p.name for p in cache.iterdir()} == {'default.out', 'default.payload'},
              '无会话时缓存文件名未使用 default')
        check((cache / 'default.out').read_text() == 'BASE-LINE\n', '基础输出未缓存')
        check((cache / 'default.payload').read_bytes() == b'', '空 stdin 未原样缓存')
        r = sh(cmd, env=env, input='')
        check(r.returncode == 0 and r.stdout.startswith('BASE-LINE\n● 派单中 1 单'), r.stdout + r.stderr)


def test_statusline_wrap_nonblocking():
    import shlex
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state, env = board_fixture(t)
        cache, count = t / 'cache', t / 'count'
        env.update(AIW_STATUSLINE_CACHE=str(cache),
                   AIW_STATUSLINE_BASE=f'sleep 2; echo BASE-LINE; echo x >> {shlex.quote(str(count))}')
        cmd = ['bash', str(REPO / 'bin/aiw-statusline')]
        for _ in range(2):
            start = time.monotonic()
            r = sh(cmd, env=env, input='{"session_id":"board-session"}')
            check(time.monotonic() - start < 1, '包装脚本等待基础命令')
            check(r.returncode == 0 and len(r.stdout.splitlines()) == 1 and r.stdout.startswith('● 派单中'), r.stdout + r.stderr)
        time.sleep(3.5)
        check(count.read_text().splitlines() == ['x'], '锁未防止重复刷新')
        r = sh(cmd, env=env, input='{}')
        check(r.stdout.splitlines()[0] == 'BASE-LINE' and r.stdout.splitlines()[-1].startswith('● 派单中'), r.stdout)
        r = sh(cmd, env=dict(env, AIW_STATUSLINE_BASE=''), input='{}')
        check(len(r.stdout.splitlines()) == 1 and r.stdout.startswith('● 派单中'), r.stdout)


def test_statusline_wrap_base_failure():
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state, env = board_fixture(t)
        cache = t / 'cache'
        env.update(AIW_STATUSLINE_CACHE=str(cache), AIW_STATUSLINE_BASE='exit 3')
        cmd = ['bash', str(REPO / 'bin/aiw-statusline')]
        r = sh(cmd, env=env, input='{}')
        check(r.returncode == 0 and r.stdout.startswith('● 派单中'), r.stdout + r.stderr)
        deadline = time.monotonic() + 5
        while list(cache.glob('*.lock')) and time.monotonic() < deadline:
            time.sleep(0.05)
        check(not list(cache.glob('*.lock')) and not list(cache.glob('*.out')), '失败产生缓存或残留锁')
        # 非空旧缓存（含 ANSI、无换行）在失败后仍原样保留。
        out = cache / 'board-session.out'
        out.write_bytes(b'\x1b[32mOLD\x1b[0m')
        os.utime(out, (time.time() - 301, time.time() - 301))
        r = sh(cmd, env=env, input='{}')
        check(r.stdout.startswith('\x1b[32mOLD\x1b[0m\n● 派单中'), r.stdout)
        deadline = time.monotonic() + 5
        while list(cache.glob('*.lock')) and time.monotonic() < deadline:
            time.sleep(0.05)
        check(out.read_bytes() == b'\x1b[32mOLD\x1b[0m', '失败覆盖旧缓存')



def test_statusline_wrap_base_ttl():
    import shlex
    import time
    for ttl, transcript_newer, expected in (
            (None, False, False), ('60', False, True),
            ('abc', False, False), ('0', False, False),
            ('-1', False, False), ('', False, False),
            ('1.5', False, False), (None, True, True)):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            state, env = board_fixture(t)
            cache = t / 'cache'; cache.mkdir()
            count = t / 'count'; count.write_text('')
            out = cache / 'S.out'; out.write_text('OLD')
            modified = time.time() - 100
            os.utime(out, (modified, modified))
            lock = cache / 'S.lock'
            env.update(AIW_STATUSLINE_CACHE=str(cache),
                       AIW_STATUSLINE_BASE=f'echo x >> {shlex.quote(str(count))}; echo NEW')
            env.pop('AIW_STATUSLINE_BASE_TTL', None)
            if ttl is not None:
                env['AIW_STATUSLINE_BASE_TTL'] = ttl
            payload = dict(session_id='S')
            if transcript_newer:
                transcript = t / 'transcript'; transcript.write_text('new message')
                payload['transcript_path'] = str(transcript)
            r = sh(['bash', str(REPO / 'bin/aiw-statusline')], env=env,
                   input=json.dumps(payload))
            check(r.returncode == 0 and r.stdout.startswith('OLD\n'), r.stdout + r.stderr)
            deadline = time.monotonic() + 5
            while lock.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            label = f'TTL={ttl!r}, transcript_newer={transcript_newer}'
            check(not lock.exists(), f'{label}: 残留刷新锁')
            check(count.read_text().splitlines() == (['x'] if expected else []),
                  f'{label}: 刷新次数错误')
            check(out.read_text() == ('NEW\n' if expected else 'OLD'),
                  f'{label}: 缓存内容错误')
            check((cache / 'S.payload').exists() == expected, f'{label}: 意外启动刷新')


def test_statusline_wrap_min_interval():
    import time
    for minimum, age, ttl, newer, expected in (
            (None, 30, '300', True, False), (None, 65, '300', True, True),
            ('120', 65, '300', True, False), ('20', 30, '300', True, True),
            ('600', 310, '300', False, True), ('20', 30, '300', False, False)):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            _, env = board_fixture(t)
            cache = t / 'cache'; cache.mkdir()
            out = cache / 'S.out'; out.write_text('OLD')
            modified = time.time() - age
            os.utime(out, (modified, modified))
            transcript = t / 'transcript'; transcript.write_text('message')
            if not newer:
                os.utime(transcript, (modified - 1, modified - 1))
            env.update(AIW_STATUSLINE_CACHE=str(cache), AIW_STATUSLINE_BASE='echo NEW',
                       AIW_STATUSLINE_BASE_TTL=ttl)
            if minimum is not None:
                env['AIW_STATUSLINE_BASE_MIN_INTERVAL'] = minimum
            r = sh([sys.executable, str(REPO / 'board.py'), 'statusline-wrap'], env=env,
                   input=json.dumps(dict(session_id='S', transcript_path=str(transcript))))
            check(r.returncode == 0 and r.stdout.startswith('OLD\n'), r.stdout + r.stderr)
            deadline = time.monotonic() + 5
            while (cache / 'S.lock').exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            check(not (cache / 'S.lock').exists(), '刷新未完成')
            check((cache / 'S.payload').exists() == expected, f'{minimum}, {age}: 刷新次数错误')
            check(out.read_text() == ('NEW\n' if expected else 'OLD'), '缓存内容错误')


def test_statusline_base_env_parsing():
    import runpy
    from unittest.mock import patch
    board = runpy.run_path(str(REPO / 'board.py'))
    for name, default in (('AIW_STATUSLINE_BASE_MIN_INTERVAL', 60),
                          ('AIW_STATUSLINE_BASE_MAX_CONCURRENT', 2)):
        for value in (None, '', 'abc', '0', '-1', '1.5', '7'):
            with patch.dict(os.environ):
                os.environ.pop(name, None)
                if value is not None:
                    os.environ[name] = value
                check(board['positive_env'](name, default) == (7 if value == '7' else default),
                      f'{name}={value!r}: 解析错误')


def test_statusline_wrap_concurrency_cap():
    import time
    for stale, maximum, missing, expected in (
            (False, None, False, False), (False, None, True, False),
            (True, None, False, True), (False, '3', False, True),
            (False, 'invalid', False, False)):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            _, env = board_fixture(t)
            cache = t / 'cache'; cache.mkdir()
            out = cache / 'S.out'
            if not missing:
                out.write_text('OLD')
                os.utime(out, (time.time() - 301, time.time() - 301))
            for key in ('foreign-one', 'foreign-two'):
                lock = cache / f'{key}.lock'; lock.mkdir()
                if stale:
                    os.utime(lock, (time.time() - 121, time.time() - 121))
            # 普通文件不是刷新锁。
            (cache / 'unrelated.lock').write_text('keep')
            env.update(AIW_STATUSLINE_CACHE=str(cache), AIW_STATUSLINE_BASE='echo NEW')
            if maximum is not None:
                env['AIW_STATUSLINE_BASE_MAX_CONCURRENT'] = maximum
            r = sh([sys.executable, str(REPO / 'board.py'), 'statusline-wrap'], env=env,
                   input='{"session_id":"S"}')
            check(r.returncode == 0 and '派单 ?' not in r.stdout, r.stdout + r.stderr)
            if not missing:
                check(r.stdout.startswith('OLD\n'), '未服务旧缓存')
            deadline = time.monotonic() + 5
            while (cache / 'S.lock').exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            check(not (cache / 'S.lock').exists(), '残留刷新锁')
            check((cache / 'S.payload').exists() == expected, '并发上限错误')
            check(out.read_text() == ('NEW\n' if expected else 'OLD') if out.exists() else missing,
                  '缓存内容错误')


def test_statusline_refresh_lock_race():
    import fcntl
    import runpy
    import time
    board = runpy.run_path(str(REPO / 'board.py'))
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td)
        fd = os.open(cache, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            check(not board['acquire_refresh_lock'](cache, 'blocked', time.time(), 2),
                  '目录锁竞争未跳过刷新')
        finally:
            os.close(fd)
        script = """
import pathlib, runpy, sys, time
board = runpy.run_path(sys.argv[1])
sys.stdin.read()
print(int(board['acquire_refresh_lock'](pathlib.Path(sys.argv[2]), sys.argv[3], time.time(), 2)))
"""
        workers = [subprocess.Popen([sys.executable, '-c', script, str(REPO / 'board.py'),
                                    str(cache), str(i)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                   for i in range(12)]
        for worker in workers:
            worker.stdin.close()
            worker.stdin = None
        acquired = 0
        for worker in workers:
            output, error = worker.communicate(timeout=10)
            check(worker.returncode == 0, error)
            acquired += int(output)
        check(1 <= acquired <= 2 and len(list(cache.glob('*.lock'))) == acquired,
              '同时抢锁突破并发上限或遗留多余锁')


def test_statusline_base_resolution():
    import runpy
    import shlex
    from unittest.mock import patch
    board = runpy.run_path(str(REPO / 'board.py'))
    with tempfile.TemporaryDirectory(prefix="board ' quoted ") as td:
        t = Path(td)
        _, env = board_fixture(t)
        path_bin = t / 'bin'; path_bin.mkdir()
        env['PATH'] = str(path_bin)
        with patch.dict(os.environ, env, clear=True):
            resolve = board['base_command']
            check(resolve() == 'bunx -y ccstatusline@latest', '未回退 bunx')
            local = Path(env['HOME']) / '.local/bin/ccstatusline'
            bun = Path(env['HOME']) / '.bun/bin/ccstatusline'
            executable = path_bin / 'ccstatusline'
            for path in (local, bun, executable):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/bin/sh\nprintf "FAKE-BASE\\n"\n')
                check(resolve() != shlex.quote(str(path)), '选中了不可执行文件')
                path.chmod(0o755)
                check(resolve() == shlex.quote(str(path)), '本地解析顺序错误')
                r = subprocess.run(resolve(), shell=True, capture_output=True, text=True, env=env)
                check(r.returncode == 0 and r.stdout == 'FAKE-BASE\n', '路径未安全引用')
            os.environ['AIW_STATUSLINE_BASE'] = 'echo EXPLICIT'
            check(resolve() == 'echo EXPLICIT', '显式命令未优先')
            os.environ['AIW_STATUSLINE_BASE'] = ''
            check(resolve() == '', '空字符串未禁用')


def test_statusline_wrap_cache_policy():
    import time
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state, env = board_fixture(t)
        cache = t / 'cache'; cache.mkdir()
        transcript = t / 'transcript'; transcript.write_text('new message')
        out = cache / 'S.out'; out.write_text('OLD')
        now = time.time()
        os.utime(out, (now - 65, now - 65))
        lock = cache / 'S.lock'; lock.mkdir()
        os.utime(lock, (now - 121, now - 121))
        old = cache / 'old.payload'; old.write_text('expired')
        old_lock = cache / 'old.lock'; old_lock.mkdir()
        unrelated = cache / 'unrelated.txt'; unrelated.write_text('keep')
        for path in (old, old_lock, unrelated):
            os.utime(path, (now - 8 * 86400, now - 8 * 86400))
        payload = json.dumps(dict(session_id='../S', transcript_path=str(transcript)))
        env.update(AIW_STATUSLINE_CACHE=str(cache), AIW_STATUSLINE_BASE='cat')
        cmd = ['bash', str(REPO / 'bin/aiw-statusline')]
        r = sh(cmd, env=env, input=payload)
        check(r.returncode == 0 and r.stdout.startswith('OLD\n○ 派单空闲'), r.stdout)
        deadline = time.monotonic() + 5
        while lock.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        check(not lock.exists() and out.read_text() == payload, '新消息未触发刷新或 payload 未原样传入')
        check(not old.exists() and not old_lock.exists() and unrelated.exists(), '清理范围错误')
        check((cache / 'S.payload').read_text() == payload, '会话文件名未清理')
        # 缓存新鲜且无新消息时不能再启动基础命令。
        env['AIW_STATUSLINE_BASE'] = 'echo UNEXPECTED'
        r = sh(cmd, env=env, input=payload)
        check(not lock.exists() and out.read_text() == payload, '新鲜缓存仍刷新')
        # 状态读取出错也保留先前缓存，摘要降级为问号。
        (state / 'inflight' / 'bad.json').write_text('{bad')
        r = sh(cmd, env=env, input=payload)
        check(r.returncode == 0 and r.stdout == payload + '\n派单 ?\n', r.stdout)


def test_state_environment():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state, env = board_fixture(t)
        cmd = [str(REPO / 'bin/aiw-board'), '--once', '--all']

        def verify():
            r = sh(cmd, env=env)
            check(r.returncode == 0 and 'alpha→bravo-cli' in r.stdout and not r.stderr,
                  r.stdout + r.stderr)

        # STATE 优先于 HOME。
        env.update(AIW_HOME=str(t / 'unused-home'))
        verify()
        env.pop('AIW_STATE')
        env['AIW_HOME'] = str(t)
        verify()
        # 显式空值也是已设置，不可用 HOME 覆盖。
        env['AIW_STATE'] = ''
        r = sh(cmd, env=env, cwd=t / 'home')
        check(r.returncode == 0 and 'alpha→bravo-cli' not in r.stdout, r.stdout + r.stderr)


def test_state_default_path():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state, env = board_fixture(t)
        env.pop('AIW_STATE')
        cmd = [str(REPO / 'bin/aiw-board'), '--once', '--all']
        preferred = Path(env['HOME']) / '.claude/aiw/state'
        # 安装目录不存在时默认使用新路径。
        import runpy
        from unittest.mock import patch
        with patch.dict(os.environ, env, clear=True):
            check(runpy.run_path(str(REPO / 'board.py'))['STATE'] == preferred,
                  '无安装目录时未使用新路径')
        preferred.parent.mkdir(parents=True)
        r = sh(cmd, env=env)
        check(r.returncode == 0 and 'alpha→bravo-cli' not in r.stdout, r.stdout + r.stderr)
        shutil.move(str(state), str(preferred))
        r = sh(cmd, env=env)
        check(r.returncode == 0 and 'alpha→bravo-cli' in r.stdout, r.stdout + r.stderr)
        env['AIW_HOME'] = '~/.claude/aiw'
        r = sh(cmd, env=env)
        check(r.returncode == 0 and 'alpha→bravo-cli' in r.stdout, r.stdout + r.stderr)
        env.update(AIW_STATE='~/.claude/aiw/state', AIW_HOME=str(t / 'unused'))
        r = sh(cmd, env=env)
        check(r.returncode == 0 and 'alpha→bravo-cli' in r.stdout, r.stdout + r.stderr)


def test_python_environment():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        _, env = board_fixture(t)
        tools = t / 'tools'; tools.mkdir()
        (tools / 'python3').symlink_to(sys.executable)
        env['PATH'] = str(tools) + os.pathsep + env.get('PATH', os.defpath)
        env.update(AIW_STATUSLINE_BASE='', AIW_STATUSLINE_CACHE=str(t / 'cache'),
                   AIW_BOARD_BIN=str(t / 'bin'), AIW_BOARD_SKIP_CCSTATUSLINE='1',
                   XDG_CONFIG_HOME=str(t / 'config'), CLAUDE_CONFIG_DIR=str(t / 'claude'))
        commands = ([str(REPO / 'bin/aiw-board'), '--once'],
                    [str(REPO / 'bin/aiw-statusline')], [str(REPO / 'install.sh')])
        for cmd in commands:
            r = sh(cmd, env=env, input='{}')
            check(r.returncode == 0 and not r.stderr, r.stdout + r.stderr)
            unset = dict(env)
            unset.pop('AIW_PYTHON')
            r = sh(cmd, env=unset, input='{}')
            check(r.returncode == 0 and not r.stderr, r.stdout + r.stderr)
            # 变量存在但为空时用 python3。
            empty = dict(env, AIW_PYTHON='')
            r = sh(cmd, env=empty, input='{}')
            check(r.returncode == 0 and not r.stderr, r.stdout + r.stderr)
        # 状态栏读取 STATE/HOME 时只输出摘要，stderr 保持为空。
        env.update(AIW_HOME=str(t))
        for cmd in ([str(REPO / 'bin/aiw-board'), 'statusline'],
                    [str(REPO / 'bin/aiw-statusline')]):
            r = sh(cmd, env=env, input='{"session_id":"board-session"}')
            check(r.returncode == 0 and '派单中 1 单' in r.stdout and not r.stderr,
                  r.stdout + r.stderr)


def test_bin_entry():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state, env = board_fixture(t)
        link = t / 'entry'; link.symlink_to(REPO / 'bin/aiw-board')
        chained = t / 'relative-entry'; chained.symlink_to('entry')
        r = sh([str(chained), '--once', '--all'], env=env)
        check(r.returncode == 0 and '派单看板' in r.stdout, r.stdout + r.stderr)
        r = sh([str(chained), '--help'], env=env)
        check(r.returncode == 0 and 'usage: aiw-board ' in r.stdout, r.stdout)


def test_install_python_version_gate():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        _, env = board_fixture(t)
        interpreter = t / 'old python'
        interpreter.write_text(f'''#!{sys.executable}
import sys
sys.version_info = (3, 10, 16)
if sys.argv[1] == '-c':
    exec(sys.argv[2])
else:
    exec(sys.stdin.read())
''')
        interpreter.chmod(0o755)
        dest, config, settings = t / 'installed', t / 'config', t / 'claude'
        env.update(AIW_PYTHON=str(interpreter), AIW_BOARD_BIN=str(dest),
                   AIW_BOARD_SKIP_CCSTATUSLINE='1', XDG_CONFIG_HOME=str(config),
                   CLAUDE_CONFIG_DIR=str(settings))

        def snapshot():
            return {str(p.relative_to(t)): (p.read_bytes(), p.stat().st_mtime_ns)
                    if p.is_file() else None for p in t.rglob('*')}

        for args in ([], ['--apply-settings']):
            before = snapshot()
            r = sh([str(REPO / 'install.sh')] + args, env=env)
            check(r.returncode != 0 and not r.stdout, r.stdout + r.stderr)
            check(r.stderr.splitlines() == [
                '当前 Python 3.10.16；需要 Python 3.11 及以上；可用 AIW_PYTHON 指定解释器'],
                r.stderr)
            check(not any(p.exists() or p.is_symlink() for p in (dest, config, settings)),
                  '低版本安装创建了入口或配置目录')
            check(snapshot() == before, '低版本安装修改了文件或创建了软链/配置')


def test_install():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        state, env = board_fixture(t)
        dest = t / 'bin with spaces'
        env.update(AIW_BOARD_BIN=str(dest), AIW_BOARD_SKIP_CCSTATUSLINE='1',
                   XDG_CONFIG_HOME=str(t / 'config'), CLAUDE_CONFIG_DIR=str(t / 'claude'))
        cmd = [str(REPO / 'install.sh')]
        for _ in range(2):
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            for name in ('aiw-board', 'aiw-statusline'):
                link = dest / name
                check(link.is_symlink() and link.resolve() == REPO / 'bin' / name, str(link))
            config = json.loads(r.stdout[r.stdout.index('{'):])['statusLine']
            check(config == dict(type='command', command=str(REPO / 'bin/aiw-statusline'),
                                 padding=0, refreshInterval=15), config)
        # 普通文件与其他软链都不覆盖；预检失败不安装另一入口。
        for other_link in (False, True):
            conflict = t / ('conflict-link' if other_link else 'conflict-file'); conflict.mkdir()
            target = conflict / 'aiw-statusline'
            if other_link:
                target.symlink_to('missing-target')
            else:
                target.write_text('KEEP')
            r = sh(cmd, env=dict(env, AIW_BOARD_BIN=str(conflict)))
            check(r.returncode != 0 and '拒绝覆盖' in r.stderr, r.stdout + r.stderr)
            check(not (conflict / 'aiw-board').exists(), '冲突后发生部分安装')
            check(os.readlink(target) == 'missing-target' if other_link else target.read_text() == 'KEEP', '覆盖了目标')
        check(not (Path(env['HOME']) / '.claude/settings.json').exists(), '安装修改了 settings')



def install_fixture(t):
    _, env = board_fixture(t)
    tools = t / 'tools'; tools.mkdir()
    for name in ('bash', 'dirname', 'readlink'):
        (tools / name).symlink_to(shutil.which(name))
    env.update(PATH=str(tools), XDG_CONFIG_HOME=str(t / 'xdg config'),
               CLAUDE_CONFIG_DIR=str(t / 'claude config'), AIW_BOARD_BIN=str(t / 'installed'))
    return env, tools


def fake_bun(t, tools, version='2.2.30'):
    # 模拟全局安装后的可执行文件及 npm 元数据，让第二次运行验证真实版本。
    bun = tools / 'bun'
    bun.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
root = Path({str(t)!r})
with (root / 'bun-args').open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
package = root / 'package'
package.mkdir(exist_ok=True)
(package / 'package.json').write_text(json.dumps(dict(name='ccstatusline', version={version!r})))
entry = package / 'ccstatusline'
entry.write_text('#!/bin/sh\\nexit 0\\n')
entry.chmod(0o755)
link = Path({str(tools)!r}) / 'ccstatusline'
if not link.is_symlink():
    link.symlink_to(entry)
''')
    bun.chmod(0o755)


def test_install_ccstatusline():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env, tools = install_fixture(t)
        fake_bun(t, tools)
        cmd = [str(REPO / 'install.sh')]
        config = Path(env['XDG_CONFIG_HOME']) / 'ccstatusline/settings.json'
        for _ in range(2):
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            check(config.is_symlink() and config.resolve() == REPO / 'config/ccstatusline/settings.json', str(config))
        calls = (t / 'bun-args').read_text().splitlines()
        check(calls == [json.dumps(['add', '-g', 'ccstatusline@2.2.30'])], calls)
        check(not list(config.parent.glob('settings.json.bak-*')), '幂等运行生成了备份')
        # 旧版本与无法识别版本均需要安装。
        for metadata in ('{"name":"ccstatusline","version":"0.0.1"}', 'invalid'):
            (t / 'package/package.json').write_text(metadata)
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
        check(len((t / 'bun-args').read_text().splitlines()) == 3, '没有更新旧版本或未知版本')
        # PATH 外两个约定目录也必须识别，且正确版本不触发 bun。
        for directory in ('.bun/bin', '.local/bin'):
            local = Path(env['HOME']) / directory
            local.mkdir(parents=True, exist_ok=True)
            link = local / 'ccstatusline'
            (tools / 'ccstatusline').rename(link)
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            check(len((t / 'bun-args').read_text().splitlines()) == 3, '未识别 HOME 内版本')
            link.rename(tools / 'ccstatusline')


def test_install_config_backup():
    for old_link in (False, True):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            env, tools = install_fixture(t)
            env['AIW_BOARD_SKIP_CCSTATUSLINE'] = '1'
            config = Path(env['XDG_CONFIG_HOME']) / 'ccstatusline/settings.json'
            config.parent.mkdir(parents=True)
            if old_link:
                config.symlink_to('missing-original')
            else:
                config.write_text('ORIGINAL')
            cmd = [str(REPO / 'install.sh')]
            r = sh(cmd, env=env)
            check(r.returncode == 0, r.stdout + r.stderr)
            backups = list(config.parent.glob('settings.json.bak-*'))
            check(len(backups) == 1 and str(backups[0]) in r.stdout, r.stdout)
            check(os.readlink(backups[0]) == 'missing-original' if old_link else backups[0].read_text() == 'ORIGINAL', '备份内容变了')
            check(config.is_symlink() and config.resolve() == REPO / 'config/ccstatusline/settings.json', str(config))
            r = sh(cmd, env=env)
            check(r.returncode == 0 and list(config.parent.glob('settings.json.bak-*')) == backups, r.stdout + r.stderr)


def test_install_without_bun_and_skip():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env, tools = install_fixture(t)
        cmd = [str(REPO / 'install.sh')]
        r = sh(cmd, env=env)
        check(r.returncode == 0 and '未找到 bun' in r.stdout and 'bunx' in r.stdout and 'https://bun.sh' in r.stdout, r.stdout + r.stderr)
        fake_bun(t, tools)
        r = sh(cmd, env=dict(env, AIW_BOARD_SKIP_CCSTATUSLINE='1'))
        check(r.returncode == 0 and not (t / 'bun-args').exists(), r.stdout + r.stderr)


def test_install_apply_settings():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        env, tools = install_fixture(t)
        env['AIW_BOARD_SKIP_CCSTATUSLINE'] = '1'
        settings = Path(env['CLAUDE_CONFIG_DIR']) / 'settings.json'
        settings.parent.mkdir(parents=True)
        original = '{ "permissions": {"allow": ["Read"]}, "other": [1, true], "statusLine": {} }\n'
        settings.write_text(original)
        cmd = [str(REPO / 'install.sh'), '--apply-settings']
        r = sh(cmd, env=env)
        check(r.returncode == 0, r.stdout + r.stderr)
        expected = json.loads(original)
        expected['statusLine'] = dict(type='command', command=str(REPO / 'bin/aiw-statusline'), padding=0, refreshInterval=15)
        check(json.loads(settings.read_text()) == expected, settings.read_text())
        backups = list(settings.parent.glob('settings.json.bak-aiw-board-*'))
        check(len(backups) == 1 and backups[0].read_text() == original and str(backups[0]) in r.stdout, r.stdout)
        before = (settings.read_bytes(), settings.stat().st_mtime_ns)
        r = sh(cmd, env=env)
        check(r.returncode == 0 and 'statusLine 已相同' in r.stdout, r.stdout + r.stderr)
        check(list(settings.parent.glob('settings.json.bak-aiw-board-*')) == backups, '重复备份')
        check((settings.read_bytes(), settings.stat().st_mtime_ns) == before, '重复写入')
        for invalid in ('{"broken":', '[]'):
            settings.write_text(invalid)
            r = sh(cmd, env=env)
            check(r.returncode != 0 and 'JSON' in r.stderr and settings.read_text() == invalid, r.stdout + r.stderr)
            check(list(settings.parent.glob('settings.json.bak-aiw-board-*')) == backups, '坏 JSON 生成了备份')
        settings.unlink()
        r = sh(cmd, env=env)
        check(r.returncode == 0 and json.loads(settings.read_text()) == {'statusLine': expected['statusLine']}, r.stdout + r.stderr)
        check(not list(settings.parent.glob('.settings-aiw-board-*')), '遗留临时文件')


CASES = {name: value for name, value in globals().copy().items() if name.startswith('test_') and callable(value)}

if __name__ == '__main__':
    failures = 0
    for name in sys.argv[1:] or CASES:
        try:
            CASES[name]()
            print(f'PASS {name}', flush=True)
        except Exception as exc:
            failures += 1
            print(f'FAIL {name}: {exc}', file=sys.stderr, flush=True)
    sys.exit(1 if failures else 0)
