"""Local, short-lived CPU ASR evidence for story-video dialogue.

Importing and capability checks never load a model. Audio and recognition traces
are retained; a transcript is evidence from ASR, not a human listening verdict.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from difflib import SequenceMatcher


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = (Path.home() / '.cache/huggingface/hub/models--Systran--faster-whisper-medium'
                 / 'snapshots/08e178d48790749d25932bbc082711ddcfdfbc4f')
VERSION = 'local-asr-v1'
REQUIRED_MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')


class LocalASRBlocked(RuntimeError):
    """A local-model safety gate stopped execution; callers must retain the task."""


def capability():
    model = Path(os.environ.get('V41_ASR_MODEL_PATH') or DEFAULT_MODEL).expanduser().resolve()
    missing = [name for name in REQUIRED_MODEL_FILES if not (model / name).is_file()]
    if missing:
        return dict(available=False, model_path=str(model), reason='本地ASR模型文件缺失：' + '、'.join(missing))
    try:
        available = importlib.util.find_spec('faster_whisper') is not None
    except (ImportError, ValueError):
        available = False
    return dict(available=available, model_path=str(model),
                reason='' if available else '当前Python未安装faster-whisper；未下载或切换云端服务')


def _save(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def _guard():
    from models import gpu_manager
    try:
        gpu_manager.assert_local_run_allowed()
    except RuntimeError as error:
        raise LocalASRBlocked(str(error)) from error


def _normal(text):
    try:
        from zhconv import convert
        text = convert(str(text), 'zh-cn')
    except ImportError:
        text = str(text)
    return ''.join(char for char in text.casefold() if char.isalnum())


def _phonetic(text):
    if not re.search(r'[\u4e00-\u9fff]', text):
        return None
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return None
    return tuple(lazy_pinyin(text, errors=lambda value: list(value)))


def _language(dialogue):
    text = ''.join(line['text'] for line in dialogue)
    if re.search(r'[\u3040-\u30ff]', text):
        return 'ja'
    if re.search(r'[\uac00-\ud7af]', text):
        return 'ko'
    if re.search(r'[\u4e00-\u9fff]', text) and not re.search(r'[A-Za-z]', text):
        return 'zh'
    # Latin scripts and mixed languages use Whisper's own detection.
    return None


def _reliable(segment):
    probability = float(segment.get('no_speech_prob', 1))
    score = float(segment.get('avg_logprob', float('-inf')))
    return math.isfinite(probability) and math.isfinite(score) and probability < .6 and score > -1.2


def _assess(segments, dialogue):
    transcript, normalized, refs = '', '', []
    for number, segment in enumerate(segments):
        if transcript:
            transcript += ' '
        segment_start = len(refs)
        for char in str(segment.get('text') or ''):
            position = len(transcript)
            transcript += char
            token = _normal(char)
            normalized += token
            refs.extend((position, number, segment.get('start'), segment.get('end'), 'segment') for _ in token)
        word_cursor = segment_start
        for word in segment.get('words') or []:
            token = _normal(word.get('word') or '')
            start, end = word.get('start'), word.get('end')
            if (not token or any(isinstance(value, bool) or not isinstance(value, (int, float))
                                 or not math.isfinite(value) for value in (start, end)) or not 0 <= start <= end):
                continue
            offset = normalized.find(token, word_cursor, len(refs))
            if offset >= 0:
                for index in range(offset, offset+len(token)):
                    refs[index] = (*refs[index][:2], float(start), float(end), 'word')
                word_cursor = offset+len(token)
    lines, issues, cursor, consumed = [], [], 0, set()
    for expected in dialogue:
        words = _normal(expected['text'])
        length = len(words)
        best = None
        phonetic = _phonetic(words)
        # Equal-length candidates conservatively avoid accepting missing words.
        for start in range(cursor, max(cursor, len(normalized) - length + 1)) if length else ():
            candidate = normalized[start:start+length]
            score = SequenceMatcher(None, words, candidate, autojunk=False).ratio()
            same_sound = phonetic is not None and phonetic == _phonetic(candidate)
            if same_sound and candidate != words:
                score = max(score, .98)
            if best is None or score > best[0]:
                best = (score, start, same_sound)
            if score == 1:
                break
        score, start, same_sound = best if best else (0.0, cursor, False)
        matched = bool(length and best and (score == 1 or same_sound or (length >= 9 and score >= .94)))
        stop = min(len(normalized), start+length)
        recognized = transcript[refs[start][0]:refs[stop-1][0]+1] if start < stop else ''
        if matched and not all(_reliable(segment) for segment in segments[refs[start][1]:refs[stop-1][1]+1]):
            matched = False
        lines.append(dict(expected=expected['text'], recognized=recognized,
                          similarity=round(score, 4), matched=matched,
                          start=refs[start][2] if start < stop else None,
                          end=refs[stop-1][3] if start < stop else None,
                          timing_precision=('word' if all(ref[4] == 'word' for ref in refs[start:stop])
                                            else 'segment') if start < stop else 'unavailable'))
        if matched:
            consumed.update(range(start, stop))
            cursor = stop
        else:
            issues.append(dict(code='missing_dialogue', expected=expected['text'],
                               recognized=recognized, reason='未按顺序可靠识别到原话'))
    if not dialogue:
        if normalized:
            clear = any(_normal(segment.get('text') or '') and _reliable(segment) for segment in segments)
            issues.append(dict(code='unexpected_dialogue' if clear else 'uncertain_speech',
                               recognized=transcript,
                               reason='无计划对白的片段识别出语音文字' if clear else 'ASR输出疑似语音，不能按静音通过'))
    elif not any(not line['matched'] for line in lines):
        extra = ''.join(char for index, char in enumerate(normalized) if index not in consumed)
        if len(extra) >= 2:
            issues.append(dict(code='unexpected_dialogue', recognized=extra, reason='识别出计划原话以外的额外文字'))
    return dict(status='failed' if issues else 'passed', transcript=transcript,
                lines=lines, issues=issues, audio_listened=False, method='local_asr')


def _base(status, code, reason):
    return dict(status=status, transcript='', lines=[], issues=[dict(code=code, reason=reason)],
                audio_listened=False, method='local_asr')


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def review(video_path, dialogue, outdir):
    """Return cached or newly recognized dialogue evidence, never silently pass gaps."""
    video = Path(video_path).expanduser().resolve()
    outdir = Path(outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    if not video.is_file():
        result = _base('failed', 'missing_video', '需要核对的视频文件不存在')
        _save(outdir/'review.json', result)
        return result
    if not isinstance(dialogue, list) or any(not isinstance(line, dict) or
            not isinstance(line.get('text'), str) or not _normal(line['text']) for line in dialogue):
        raise ValueError('ASR期望对白须为包含非空text的数组')
    capability_info = capability()
    identity = dict(version=VERSION, video_sha256=_sha(video), dialogue=copy.deepcopy(dialogue),
                    model_path=capability_info['model_path'])
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    folder = outdir/key[:20]
    folder.mkdir(parents=True, exist_ok=True)
    cache = folder/'review.json'
    if cache.is_file():
        result = json.loads(cache.read_text(encoding='utf-8'))
        if (result.get('cache_key') == key and result.get('status') in ('passed', 'failed')
                and result.get('recognition_source')):
            _save(outdir/'review.json', result)
            return result
    if not capability_info['available']:
        result = _base('unavailable', 'asr_unavailable', capability_info['reason'])
    else:
        request = dict(identity, video_path=str(video), model_path=capability_info['model_path'],
                       output_dir=str(folder), language=_language(dialogue))
        _save(folder/'request.json', request)
        response = folder/'transcription.json'
        _guard()  # Never turn PUBG or an uncertain local-run gate into optional success.
        from . import story_video_offline
        command = [sys.executable, '-m', 'cores.story_video_asr', '--worker', str(folder/'request.json'), str(response)]
        timeout = max(1, min(600, float(os.environ.get('V41_ASR_TIMEOUT_SECONDS', '300'))))
        try:
            process = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True,
                                     encoding='utf-8', errors='replace', timeout=timeout,
                                     env=story_video_offline.build_env(folder/'network_audit.jsonl'),
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            (folder/'worker_stdout.txt').write_text(process.stdout or '', encoding='utf-8')
            (folder/'worker_stderr.txt').write_text(process.stderr or '', encoding='utf-8')
            data = json.loads(response.read_text(encoding='utf-8')) if response.is_file() else {}
            if process.returncode == 3 or data.get('blocked'):
                raise LocalASRBlocked(data.get('error') or '本地ASR被运行保护暂停')
            if process.returncode or data.get('error') or 'segments' not in data:
                result = _base('failed' if data.get('code') == 'no_audio' else 'unavailable',
                               data.get('code') or 'asr_process_failed',
                               data.get('error') or '本地ASR进程未产生有效转写，见worker日志')
            else:
                result = _assess(data['segments'], dialogue)
                result['language'] = data.get('language')
                result['recognition_source'] = str(response)
        except subprocess.TimeoutExpired:
            result = _base('unavailable', 'asr_timeout', '本地ASR超过%.0f秒，子进程已结束，音频与记录保留' % timeout)
        except (OSError, ValueError, KeyError, TypeError) as error:
            result = _base('unavailable', 'asr_error', str(error))
    result.update(cache_key=key, video_sha256=identity['video_sha256'], model_path=identity['model_path'],
                  report_path=str(cache))
    _save(cache, result)
    _save(outdir/'review.json', result)
    return result


def _transcribe(audio, model_path, language):
    _guard()
    from faster_whisper import WhisperModel
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=4,
                         num_workers=1, local_files_only=True)
    _guard()
    segments, info = model.transcribe(str(audio), language=language, beam_size=5, vad_filter=True,
                                     condition_on_previous_text=False, word_timestamps=True)
    result = [dict(start=float(part.start), end=float(part.end), text=part.text,
                   avg_logprob=float(part.avg_logprob), no_speech_prob=float(part.no_speech_prob),
                   words=[dict(word=word.word, start=float(word.start), end=float(word.end),
                               probability=float(word.probability)) for word in getattr(part, 'words', None) or []])
              for part in segments]
    return dict(segments=result, language=info.language)


def _worker(request_path, response_path):
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    folder = Path(request['output_dir'])
    from . import story_video_offline
    story_video_offline.install(folder/'network_audit.jsonl')
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries',
                                'stream=index', '-of', 'json', request['video_path']],
                               capture_output=True, text=True, timeout=30,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if probe.returncode:
            raise RuntimeError('音轨探测失败：' + probe.stderr[-500:])
        if not json.loads(probe.stdout).get('streams'):
            _save(response_path, dict(code='no_audio', error='视频没有音轨，不能完成台词核对'))
            return 1
        audio = folder/'audio.wav'
        extraction = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', request['video_path'],
                                      '-vn', '-ac', '1', '-ar', '16000', str(audio)],
                                     capture_output=True, text=True, timeout=60,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if extraction.returncode or not audio.is_file():
            raise RuntimeError('音轨提取失败：' + extraction.stderr[-500:])
        result = _transcribe(audio, request['model_path'], request.get('language'))
        _save(response_path, result)
        (folder/'transcript.txt').write_text(''.join(part['text'] for part in result['segments']), encoding='utf-8')
        return 0
    except LocalASRBlocked as error:
        _save(response_path, dict(blocked=True, error=str(error)))
        return 3
    except Exception as error:
        _save(response_path, dict(code='asr_worker_error', error=str(error)))
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', nargs=2, metavar=('REQUEST', 'RESPONSE'), required=True)
    args = parser.parse_args()
    return _worker(*args.worker)


if __name__ == '__main__':
    raise SystemExit(main())
