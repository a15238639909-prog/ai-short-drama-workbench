"""Loopback-only tests. Retain temp audit fixtures; never contact the Internet."""
import functools
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

from cores import story_video_offline as offline


class OfflineBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='story_offline_test_'))
        cls.bootstrap = str(Path(offline.__file__).resolve())
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0),
            functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(cls.root)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def run_worker(self, args, name, **kwargs):
        log = self.root / (name + '.jsonl')
        result = subprocess.run([sys.executable, '-B', self.bootstrap, '--', *args],
            env=offline.build_env(log), capture_output=True, text=True,
            timeout=25, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(row) for row in log.read_text(encoding='utf-8').splitlines()]
        return result, events

    def test_build_env_does_not_patch_parent_or_mutate_env(self):
        old_env = dict(os.environ)
        connect = socket.socket.connect
        env = offline.build_env(self.root / 'env.jsonl')
        self.assertEqual(env['HF_HUB_OFFLINE'], '1')
        self.assertEqual(env['TRANSFORMERS_OFFLINE'], '1')
        self.assertEqual(env['HF_DATASETS_OFFLINE'], '1')
        self.assertEqual(env['NO_PROXY'], '127.0.0.1,localhost,::1')
        self.assertEqual(env[offline.OFFLINE_FLAG], '1')
        self.assertNotIn('HTTP_PROXY', env)
        self.assertEqual(dict(os.environ), old_env)
        self.assertIs(socket.socket.connect, connect)

    def test_loopback_http_and_dns_are_allowed_external_attempts_blocked(self):
        port = self.server.server_address[1]
        code = '''import socket, urllib.request
print(urllib.request.urlopen('http://localhost:%d/', timeout=5).status)
assert all(row[4][0] in ('127.0.0.1', '::1') for row in socket.getaddrinfo('localhost', 80))
assert socket.getnameinfo(('localhost', 80), 0)[0] == '127.0.0.1'
checks = [lambda: socket.getaddrinfo('example.invalid', 443),
          lambda: socket.gethostbyname('example.invalid'),
          lambda: socket.gethostbyname_ex('example.invalid'),
          lambda: socket.gethostbyaddr('203.0.113.1'),
          lambda: socket.socket().connect(('203.0.113.1', 443)),
          lambda: socket.socket().connect_ex(('203.0.113.1', 443)),
          lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('203.0.113.1', 53)),
          lambda: socket.getaddrinfo('::ffff:203.0.113.1', 443)]
for check in checks:
    try: check()
    except PermissionError: pass
    else: raise AssertionError('external operation was not blocked')
print('blocked', len(checks))
''' % port
        result, events = self.run_worker(['-c', code], 'network')
        self.assertIn('200', result.stdout)
        self.assertIn('blocked 8', result.stdout)
        self.assertEqual(sum(row['event'] == 'blocked' for row in events), 8)
        self.assertEqual(sum(row['event'] == 'guard_installed' for row in events), 1)

    def test_ipv4_ipv6_mapped_classification(self):
        for host in ('127.0.0.1', '127.9.8.7', '::1', '::ffff:127.0.0.1',
                     '::ffff:7f00:1', 'localhost', 'LOCALHOST.'):
            self.assertTrue(offline._loopback(host), host)
        for host in ('192.168.1.1', '0.0.0.0', '::', '203.0.113.1',
                     '::ffff:192.168.1.1', 'localhost.example.invalid', 'example.invalid'):
            self.assertFalse(offline._loopback(host), host)

    def test_descendant_script_keeps_cwd_argv_env_and_guard(self):
        folder = self.root / 'script folder'
        folder.mkdir(exist_ok=True)
        (folder / 'sibling.py').write_text('VALUE = 23\n', encoding='utf-8')
        script = folder / 'child.py'
        script.write_text('''import json, os, socket, sys, sibling
try: socket.getaddrinfo('example.invalid', 443)
except PermissionError: pass
else: raise AssertionError('child escaped')
print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), 'custom': os.environ['CUSTOM_VALUE'], 'sibling': sibling.VALUE, 'no_user_site': sys.flags.no_user_site}))
''', encoding='utf-8')
        code = '''import json, os, subprocess, sys
child = subprocess.run([sys.executable, '-s', %r, 'argument with spaces'], cwd=%r,
    env=dict(os.environ, CUSTOM_VALUE='retained'), capture_output=True, text=True, check=True)
print(child.stdout)
''' % (str(script), str(folder))
        result, events = self.run_worker(['-c', code], 'descendant-script')
        data = json.loads(result.stdout)
        self.assertEqual(data['args'], ['argument with spaces'])
        self.assertEqual(data['cwd'], str(folder))
        self.assertEqual(data['custom'], 'retained')
        self.assertEqual(data['sibling'], 23)
        self.assertEqual(data['no_user_site'], 1)
        installed = [row for row in events if row['event'] == 'guard_installed']
        self.assertEqual(len({row['pid'] for row in installed}), 2)
        self.assertEqual(sum(row['event'] == 'blocked' for row in events), 1)

    def test_descendant_module_and_code_modes_are_bootstrapped(self):
        module = self.root / 'offline_fixture_module.py'
        module.write_text('''import socket, sys
try: socket.socket().connect_ex(('203.0.113.1', 80))
except PermissionError: print('module-blocked', sys.argv[1])
else: raise AssertionError('module escaped')
''', encoding='utf-8')
        child_code = "import socket; socket.getaddrinfo('example.invalid', 443)"
        code = '''import os, subprocess, sys
module = subprocess.run([sys.executable, '-m', 'offline_fixture_module', 'kept'], cwd=%r, capture_output=True, text=True, check=True)
print(module.stdout)
child = subprocess.run([sys.executable, '-c', %r], capture_output=True, text=True)
assert child.returncode != 0 and 'OfflineNetworkBlocked' in child.stderr
print('code-blocked')
''' % (str(self.root), child_code)
        result, events = self.run_worker(['-c', code], 'descendant-module')
        self.assertIn('module-blocked kept', result.stdout)
        self.assertIn('code-blocked', result.stdout)
        self.assertEqual(len({row['pid'] for row in events if row['event'] == 'guard_installed'}), 3)
        self.assertEqual(sum(row['event'] == 'blocked' for row in events), 2)

    def test_native_commands_are_not_rewritten(self):
        for command in (['ffmpeg.exe', '-version'], ['llama-server.exe', '-m', 'local.gguf']):
            wrapped, python = offline._wrap_command(command)
            self.assertEqual(wrapped, command)
            self.assertFalse(python)


if __name__ == '__main__':
    unittest.main()
