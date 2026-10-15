"""Isolated Python import/start-command checks; no model or process is started."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from cores import story_video_offline as offline


PROBE = r'''
import json, os
from unittest.mock import patch, mock_open, MagicMock
from models import qwen_client as qwen, gpu_manager as gpu
process = MagicMock()
process.poll.return_value = None
with patch.dict(os.environ, {'V41_NO_MODEL': ''}), \
     patch.object(qwen, '_game_mode_guard'), \
     patch.object(qwen, 'up', side_effect=[False]*9+[True]), \
     patch.object(gpu, 'guard'), patch.object(gpu, 'claim'), \
     patch.object(qwen.os.path, 'exists', return_value=True), \
     patch('builtins.open', mock_open()), patch.object(qwen.time, 'sleep'), \
     patch.object(qwen.subprocess, 'Popen', return_value=process) as launch:
    qwen.start()
    args = launch.call_args.args[0]
    start_port = int(args[args.index('--port')+1])
with patch.object(gpu, 'stop_process_on_port', return_value=None) as stop, \
     patch.object(gpu.subprocess, 'run') as terminate, patch.object(gpu.time, 'sleep'):
    gpu.stop_qwen()
    stop_port = stop.call_args.args[0]
    assert all('xwan' not in str(call).lower() for call in terminate.call_args_list)
with patch.object(gpu, 'pid_on_port', return_value=777) as lookup, \
     patch.object(gpu, 'stray_llama_pids', return_value=[777]), \
     patch.object(gpu.subprocess, 'run') as terminate:
    assert gpu.kill_stray_llama() == []
    keep_port = lookup.call_args.args[0]
    terminate.assert_not_called()
print(json.dumps({'url':qwen.QWEN_URL, 'start':start_port, 'gpu':gpu._PORTS['qwen'],
                  'stop':stop_port, 'keep':keep_port}))
'''


class StoryQwenPortTests(unittest.TestCase):
    def probe(self, env, expected):
        result = subprocess.run([sys.executable, '-B', '-c', PROBE],
            cwd=str(Path(__file__).resolve().parent.parent), env=env,
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['url'], 'http://127.0.0.1:%d' % expected)
        self.assertEqual([data[key] for key in ('start', 'gpu', 'stop', 'keep')], [expected]*4)

    def test_default_and_explicit_ports_match_start_stop_and_cleanup(self):
        env = dict(os.environ, V41_NO_MODEL='1')
        env.pop('V41_QWEN_PORT', None)
        env.pop('V41_QWEN_URL', None)
        self.probe(env, 8080)
        self.probe(dict(env, V41_QWEN_PORT='18080'), 18080)

    def test_worker_uses_18080_without_changing_parent(self):
        with patch.dict(os.environ, {'V41_QWEN_PORT': '8080', 'V41_QWEN_URL': 'http://127.0.0.1:8080'}):
            before = dict(os.environ)
            env = offline.build_env(Path(__file__).resolve().parent/'unused_port_audit.jsonl')
            self.assertEqual(env['V41_QWEN_PORT'], '18080')
            self.assertEqual(env['V41_QWEN_URL'], 'http://127.0.0.1:18080')
            self.assertEqual(dict(os.environ), before)
            self.probe(env, 18080)


if __name__ == '__main__':
    unittest.main()
