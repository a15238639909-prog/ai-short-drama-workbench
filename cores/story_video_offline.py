"""Process-local offline bootstrap for a worker and its Python descendants.

This guards Python socket APIs, not Windows networking or native executables.
Importing this module has no side effects; install() belongs in the child worker.
"""
import errno
import ipaddress
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time


OFFLINE_FLAG = 'STORY_VIDEO_OFFLINE'
AUDIT_PATH = 'STORY_VIDEO_OFFLINE_LOG'
_GUARD_ATTRIBUTE = '_story_video_offline_guard'
_ROOT = Path(__file__).resolve().parent.parent
_BOOTSTRAP = str(Path(__file__).resolve())
_PROXIES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy')


class OfflineNetworkBlocked(PermissionError):
    """A Python operation tried to resolve or reach a non-loopback target."""


def _environment(log_path, source):
    result = dict(source)
    result.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  HF_DATASETS_OFFLINE='1', NO_PROXY='127.0.0.1,localhost,::1',
                  no_proxy='127.0.0.1,localhost,::1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    result[OFFLINE_FLAG] = '1'
    result[AUDIT_PATH] = str(Path(log_path).expanduser().resolve())
    for name in _PROXIES:
        result.pop(name, None)
    return result


def build_env(log_path):
    """Return an offline child environment without changing the caller's env."""
    if not log_path:
        raise ValueError('离线审计日志路径不能为空')
    result = _environment(log_path, os.environ)
    result.update(V41_QWEN_PORT='18080', V41_QWEN_URL='http://127.0.0.1:18080')
    return result


def _loopback(host):
    if isinstance(host, bytes):
        try:
            host = host.decode('ascii')
        except UnicodeError:
            return False
    if not isinstance(host, str):
        return False
    host = host.strip()
    if host.lower().rstrip('.') == 'localhost':
        return True
    try:
        address = ipaddress.ip_address(host.split('%', 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def _hostname(host):
    return host.decode('ascii') if isinstance(host, bytes) else str(host)


def _literal(host, ipv6=False):
    text = _hostname(host).strip()
    if text.lower().rstrip('.') == 'localhost':
        return '::1' if ipv6 else '127.0.0.1'
    address = ipaddress.ip_address(text.split('%', 1)[0])
    if not ipv6 and isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def _split_command(command):
    if isinstance(command, os.PathLike):
        return [command], False
    if not isinstance(command, (str, bytes)):
        return list(command), False
    if isinstance(command, bytes):
        command = os.fsdecode(command)
    if os.name != 'nt':
        return shlex.split(command), True
    # CommandLineToArgvW preserves Windows quoted paths and backslash semantics.
    import ctypes
    from ctypes import wintypes
    argc = ctypes.c_int()
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    argv = parse(command, ctypes.byref(argc))
    if not argv:
        raise ValueError('无法解析Python子进程命令')
    try:
        return [argv[index] for index in range(argc.value)], True
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(argv)


def _python_executable(value):
    name = Path(os.fsdecode(value)).name.lower()
    return bool(re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?w?|py)(?:\.exe)?', name))


def _python_entry(args):
    """Separate interpreter flags from the script/-m/-c entry point."""
    flags, index = [], 0
    while index < len(args):
        item = os.fsdecode(args[index])
        if item in ('-h', '--help', '-V', '--version', '--help-env', '--help-xoptions', '--help-all'):
            return None
        if item == '--':
            return flags, args[index + 1:]
        if item in ('-m', '-c', '-') or not item.startswith('-'):
            return flags, args[index:]
        if item.startswith('-c') and len(item) > 2:
            return flags, ['-c', item[2:], *args[index + 1:]]
        if item.startswith('-m') and len(item) > 2:
            return flags, ['-m', item[2:], *args[index + 1:]]
        flags.append(item)
        if item in ('-W', '-X', '--check-hash-based-pycs'):
            index += 1
            if index >= len(args):
                return None  # Python itself will report the missing option arg.
            flags.append(args[index])
        index += 1
    return flags, []


def _wrap_command(command, executable=None):
    argv, string_command = _split_command(command)
    if not argv or not _python_executable(executable or argv[0]):
        return command, False
    entry = _python_entry(argv[1:])
    if entry is None:
        return command, True
    flags, target = entry
    if target and os.fsdecode(target[0]) not in ('-m', '-c', '-'):
        try:
            if Path(target[0]).resolve() == Path(_BOOTSTRAP):
                return command, True
        except (OSError, ValueError):
            pass
    wrapped = [argv[0], *flags, '-B', _BOOTSTRAP, '--', *target]
    if string_command:
        return (subprocess.list2cmdline(wrapped) if os.name == 'nt' else shlex.join(wrapped)), True
    return wrapped, True


def install(log_path=None):
    """Install an idempotent socket/Popen guard in this process only."""
    existing = getattr(socket, _GUARD_ATTRIBUTE, None)
    if existing:
        return dict(existing)
    log_path = log_path or os.environ.get(AUDIT_PATH)
    if not log_path:
        log_path = Path(tempfile.gettempdir()) / 'story_video_offline' / ('%d.jsonl' % os.getpid())
    log_path = Path(log_path).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def audit(event, **details):
        record = dict(event=event, pid=os.getpid(), ppid=os.getppid(), time=time.time(), **details)
        with lock, log_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')

    def deny(operation, target):
        try:
            audit('blocked', operation=operation, target=target,
                  reason='non-loopback network target')
        finally:
            raise OfflineNetworkBlocked(errno.EACCES,
                                        'Offline worker blocked %s: %s' % (operation, target))

    def check_host(operation, host):
        if not _loopback(host):
            deny(operation, host)

    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_getnameinfo = socket.getnameinfo
    original_popen = subprocess.Popen

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        check_host('getaddrinfo', host)
        named_localhost = _hostname(host).lower().rstrip('.') == 'localhost'
        hosts = (['::1'] if family == socket.AF_INET6 else ['127.0.0.1']
                 if family == socket.AF_INET else ['127.0.0.1', '::1']) if named_localhost else [_literal(host, family == socket.AF_INET6)]
        results = []
        last_error = None
        for literal in hosts:
            try:
                # No OS DNS request, even for "localhost" or reverse lookups.
                results.extend(original_getaddrinfo(literal, port, family, type, proto,
                                                    flags | socket.AI_NUMERICHOST))
            except socket.gaierror as error:
                last_error = error
        if not results and last_error:
            raise last_error
        if any(not _loopback(row[4][0]) for row in results):
            deny('getaddrinfo_result', host)
        return results

    def gethostbyname(host):
        check_host('gethostbyname', host)
        if _hostname(host).lower().rstrip('.') == 'localhost':
            return '127.0.0.1'
        address = ipaddress.ip_address(_hostname(host).split('%', 1)[0])
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            raise socket.gaierror('IPv6 literal requires getaddrinfo')
        return str(address)

    def gethostbyname_ex(host):
        return _hostname(host), [], [gethostbyname(host)]

    def gethostbyaddr(host):
        check_host('gethostbyaddr', host)
        return 'localhost', [], [_hostname(host)]

    def getnameinfo(address, flags):
        check_host('getnameinfo', address[0])
        address = (_literal(address[0], len(address) == 4), *address[1:])
        return original_getnameinfo(address, flags | socket.NI_NUMERICHOST)

    def checked_address(sock, operation, address):
        if sock.family == getattr(socket, 'AF_UNIX', object()):
            return address  # Local filesystem IPC has no remote destination.
        if not isinstance(address, tuple) or not address:
            deny(operation, address)
        check_host(operation, address[0])
        # Pass numeric literals only; C-level connect must not resolve even an
        # unusual spelling of an otherwise allowed loopback address.
        return (_literal(address[0], sock.family == socket.AF_INET6), *address[1:])

    def connect(sock, address):
        return original_connect(sock, checked_address(sock, 'connect', address))

    def connect_ex(sock, address):
        return original_connect_ex(sock, checked_address(sock, 'connect_ex', address))

    def sendto(sock, *args):
        if len(args) not in (2, 3):
            return original_sendto(sock, *args)
        address = checked_address(sock, 'sendto', args[-1])
        return original_sendto(sock, *args[:-1], address)

    class OfflinePopen(original_popen):
        def __init__(self, args, *positional, **kwargs):
            executable = kwargs.get('executable', positional[1] if len(positional) > 1 else None)
            wrapped, python_child = _wrap_command(args, executable)
            if python_child:
                # env is the 10th optional positional Popen argument.  Preserve
                # all other caller options, including cwd, pipes and stdout.
                if len(positional) > 9:
                    mutable = list(positional)
                    source = mutable[9] if mutable[9] is not None else os.environ
                    mutable[9] = _environment(log_path, source)
                    positional = tuple(mutable)
                else:
                    source = kwargs.get('env')
                    kwargs['env'] = _environment(log_path, os.environ if source is None else source)
            super().__init__(wrapped, *positional, **kwargs)

    # Fail before claiming the guard is installed if the audit cannot be saved.
    audit('guard_installed', log_path=str(log_path), scope='python-process-and-python-descendants')
    os.environ.update(_environment(log_path, os.environ))
    for proxy in _PROXIES:
        os.environ.pop(proxy, None)
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    socket.gethostbyaddr = gethostbyaddr
    socket.getnameinfo = getnameinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    subprocess.Popen = OfflinePopen
    state = {'pid': os.getpid(), 'log_path': str(log_path), 'installed': True}
    setattr(socket, _GUARD_ATTRIBUTE, state)
    return dict(state)


def main(argv=None):
    target = list(sys.argv[1:] if argv is None else argv)
    if target and target[0] == '--':
        target.pop(0)
    install()
    root = str(_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    if not target or target[0] == '-':
        sys.argv = ['-', *target[1:]]
        sys.path.insert(0, os.getcwd())
        if sys.stdin.isatty():
            import code
            code.interact(local={'__name__': '__main__'})
        else:
            exec(compile(sys.stdin.read(), '<stdin>', 'exec'), {'__name__': '__main__'})
        return
    mode = target.pop(0)
    if mode in ('-m', '-c'):
        if not target:
            raise SystemExit('offline bootstrap: %s requires an argument' % mode)
        argument = target.pop(0)
        sys.argv = [argument if mode == '-m' else '-c', *target]
        if mode == '-m':
            sys.path.insert(0, os.getcwd())
            runpy.run_module(argument, run_name='__main__', alter_sys=True)
        else:
            sys.path.insert(0, os.getcwd())
            exec(compile(argument, '<string>', 'exec'), {'__name__': '__main__'})
    else:
        script = Path(mode)
        sys.path.insert(0, str(script.resolve().parent))
        sys.argv = [mode, *target]
        runpy.run_path(mode, run_name='__main__')


if __name__ == '__main__':
    main()
