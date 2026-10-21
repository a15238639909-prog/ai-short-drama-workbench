"""Local, resumable media operations for story-video; no model calls or deletion."""
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
FPS = 24
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _run(command, timeout=600):
    result = subprocess.run([str(value) for value in command], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError('媒体处理失败：' + result.stderr.strip()[-1500:])
    return result


def probe(path):
    return json.loads(_run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', str(Path(path).resolve())], 90).stdout)


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _save_new(path, value):
    pending = path.with_name(path.name + '.pending_' + uuid.uuid4().hex)
    with pending.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        raise RuntimeError('已有记录保留，未覆盖：' + str(path))
    pending.rename(path)


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _source(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('媒体源必须是文件')
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': _hash(path)}


def _claim(folder, filename, identity, existing=()):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    if path.exists():
        if _read(path) != identity:
            raise ValueError('本目录已有不同输入，原文件保留；请使用新的输出目录')
    else:
        if any(Path(value).exists() for value in existing):
            raise ValueError('已有输出但缺少输入身份记录，原文件保留')
        _save_new(path, identity)


def _produce(target, command, validate):
    if target.exists():
        return validate(target)
    attempt = target.with_name(target.stem + '.attempt_' + uuid.uuid4().hex + target.suffix)
    try:
        _run(command(attempt))
        checked = validate(attempt)
        if target.exists():
            raise RuntimeError('输出被其他进程创建，本次尝试保留：' + str(attempt))
        attempt.rename(target)
        return checked
    except Exception as error:
        _save_new(target.parent / ('failure_' + uuid.uuid4().hex + '.json'),
                  {'target': str(target), 'attempt': str(attempt), 'error': str(error), 'at': time.time()})
        raise


def _video(info):
    videos = [stream for stream in info.get('streams', []) if stream.get('codec_type') == 'video']
    if len(videos) != 1:
        raise ValueError('需要且仅支持一条视频流')
    video = videos[0]
    try:
        rate = Fraction(video.get('avg_frame_rate') or video.get('r_frame_rate') or '0/1')
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError('视频帧率无效') from error
    if rate != FPS:
        raise ValueError('故事视频需要实际24fps，未重采样原片')
    return video


def _timeline(video):
    info = probe(video)
    stream = _video(info)
    rows = json.loads(_run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                           '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', video], 180).stdout).get('frames', [])
    if not rows or any(row.get('best_effort_timestamp_time') is None for row in rows):
        raise ValueError('无法取得实际视频帧时间')
    stamps = [float(row['best_effort_timestamp_time']) for row in rows]
    origin = stamps[0]
    times = [stamp - origin for stamp in stamps]
    if any(not math.isfinite(value) or abs(value - index / FPS) > 0.00005 for index, value in enumerate(times)):
        raise ValueError('视频不是连续24fps时间轴，未擅自重采样')
    return info, stream, times


def _audio(info):
    streams = [stream for stream in info.get('streams', []) if stream.get('codec_type') == 'audio']
    if len(streams) > 1:
        raise ValueError('暂不支持多条音轨，原片保留')
    if not streams:
        return None
    stream = streams[0]
    return {key: stream.get(key) for key in ('codec_name', 'sample_rate', 'channels', 'channel_layout')}


def _verify_video(path, frames=None):
    info = probe(path)
    stream = _video(info)
    count = stream.get('nb_frames')
    if count in (None, 'N/A'):
        counted = json.loads(_run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                                  '-show_entries', 'stream=nb_read_frames', '-of', 'json', path], 300).stdout)
        count = counted['streams'][0]['nb_read_frames']
    count = int(count)
    if frames is not None and count != frames:
        raise RuntimeError('视频帧数不一致：%d / %d' % (count, frames))
    decoded = _run(['ffmpeg', '-v', 'error', '-xerror', '-err_detect', 'explode', '-i', path,
                    '-map', '0:v:0', '-map', '0:a:0?', '-f', 'null', '-'])
    if decoded.stderr.strip():
        raise RuntimeError('完整音画解码有错误：' + decoded.stderr[-1000:])
    return {'probe': info, 'frames': count, 'seconds': count / FPS,
            'duration_seconds': float(info['format']['duration']), 'audio': _audio(info),
            'full_decode_passed': True, 'full_decode_exit_code': 0, 'full_decode_diagnostics': ''}


def _frame_hash(video, index):
    result = _run(['ffmpeg', '-v', 'error', '-i', video, '-an', '-vf', f'select=eq(n\\,{index})',
                   '-vsync', '0', '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'hash', '-hash', 'sha256', '-'])
    if not result.stdout.strip().startswith('SHA256='):
        raise RuntimeError('无法验证采用末帧')
    return result.stdout.strip()


def _frame_rounding(source, source_index, clip, clip_index, source_stream, clip_stream):
    """Allow only sparse one-level rounding in matching 8-bit YUV frames."""
    fields = ('width', 'height', 'pix_fmt', 'color_range', 'color_space',
              'color_transfer', 'color_primaries', 'chroma_location')
    if any(source_stream.get(key) != clip_stream.get(key) for key in fields):
        return {'accepted': False, 'reason': '视频尺寸、像素格式或色彩信息改变'}
    pixel_format = source_stream['pix_fmt']
    if pixel_format not in ('yuv420p', 'yuv422p', 'yuv444p'):
        return {'accepted': False, 'reason': '该像素格式仍要求逐像素完全一致'}

    def pixels(path, index):
        result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-an',
            '-vf', f'select=eq(n\\,{index})', '-vsync', '0', '-frames:v', '1',
            '-pix_fmt', pixel_format, '-f', 'rawvideo', '-'], capture_output=True,
            timeout=90, creationflags=NO_WINDOW)
        if result.returncode:
            raise RuntimeError('末帧像素检查解码失败：' + result.stderr.decode('utf-8', 'replace')[-500:])
        return result.stdout

    before, after = pixels(source, source_index), pixels(clip, clip_index)
    if not before or len(before) != len(after):
        return {'accepted': False, 'reason': '末帧像素数据长度不一致'}
    changed, maximum = 0, 0
    for a, b in zip(before, after):
        difference = abs(a - b)
        if difference:
            changed += 1
            maximum = max(maximum, difference)
    fraction = changed / len(before)
    # A different pose/frame cannot pass merely because its average error is low:
    # EVERY changed channel must differ by at most one, affecting at most 1%.
    return {'accepted': maximum <= 1 and fraction <= 0.01,
            'maximum_sample_difference': maximum, 'changed_samples': changed,
            'total_samples': len(before), 'changed_fraction': fraction,
            'maximum_allowed_difference': 1, 'maximum_allowed_fraction': 0.01}


def _image(path, expected=None):
    from PIL import Image
    with Image.open(path) as image:
        image.verify()
    digest = _frame_hash(path, 0)
    if expected is not None and digest != expected:
        raise RuntimeError('锚点图片与采用视频的末帧像素不一致')
    return digest


def _extract(video, index, target, expected=None):
    return _produce(target,
                    lambda attempt: ['ffmpeg', '-v', 'error', '-n', '-i', video, '-an', '-vf',
                                     f'select=eq(n\\,{index})', '-vsync', '0', '-frames:v', '1',
                                     '-pix_fmt', 'rgb24', attempt],
                    lambda path: _image(path, expected))


def sample_frames(video, outdir, seconds=8):
    source = _source(video)
    folder = Path(outdir).resolve()
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('抽帧秒数必须为正数')
    _claim(folder, 'samples_inputs.json', {'version': 1, 'source': source, 'seconds': seconds},
           list(folder.glob('sample_*.png')) + [folder / 'contact.jpg'])
    _, _, times = _timeline(source['path'])
    duration = min(seconds, len(times) / FPS)
    targets = [0, min(0.25, duration), duration / 4, duration / 2, duration * 3 / 4]
    indices = sorted(set(min(range(len(times)), key=lambda index: abs(times[index] - target)) for target in targets) | {len(times) - 1})
    result = []
    for index in indices:
        path = folder / ('sample_%06d.png' % index)
        _extract(source['path'], index, path)
        result.append({'path': str(path), 'time_seconds': times[index], 'frame_index': index})
    contact = folder / 'contact.jpg'
    if not contact.exists():
        from PIL import Image, ImageDraw
        columns, width, height, label = 3, 384, 216, 24
        sheet = Image.new('RGB', (columns * width, math.ceil(len(result) / columns) * (height + label)), '#15171b')
        draw = ImageDraw.Draw(sheet)
        for number, row in enumerate(result):
            with Image.open(row['path']) as image:
                thumbnail = image.convert('RGB')
                thumbnail.thumbnail((width, height))
                x, y = number % columns * width, number // columns * (height + label)
                sheet.paste(thumbnail, (x, y + label))
                draw.text((x + 7, y + 5), 'Frame %d / %.3fs' % (row['frame_index'], row['time_seconds']), fill='white')
        attempt = folder / ('contact.attempt_' + uuid.uuid4().hex + '.jpg')
        sheet.save(attempt, quality=92)
        _image(attempt)
        attempt.rename(contact)
    else:
        _image(contact)
    return result


def sample_at_times(video, outdir, targets):
    """One bounded evidence supplement, using real frame timestamps."""
    targets = list(targets)
    if not 1 <= len(targets) <= 3 or any(isinstance(t, bool) or not isinstance(t, (int, float))
            or not math.isfinite(t) or t < 0 for t in targets):
        raise ValueError('定点补查须为1至3个非负有限时间')
    source = _source(video)
    _, _, times = _timeline(source['path'])
    if any(t > times[-1] + .001 for t in targets):
        raise ValueError('补查时间不能越过实际采用片末帧')
    folder = Path(outdir).resolve()
    _claim(folder, 'evidence_inputs.json', {'version': 1, 'source': source, 'targets': targets},
           list(folder.glob('sample_*.png')))
    indices = sorted({min(range(len(times)), key=lambda index: abs(times[index] - target)) for target in targets})
    result = []
    for index in indices:
        path = folder / ('sample_%06d.png' % index)
        _extract(source['path'], index, path)
        result.append({'path': str(path), 'time_seconds': times[index], 'frame_index': index})
    return result


def adopt(video, outdir, end_seconds=None):
    source = _source(video)
    info, stream, times = _timeline(source['path'])
    duration = len(times) / FPS
    requested = duration if end_seconds is None else float(end_seconds)
    if not math.isfinite(requested) or not 0 < requested <= duration:
        raise ValueError('采用区间须为[0,end)，end不能超出实际视频时长')
    selected = [index for index, timestamp in enumerate(times) if timestamp < requested]
    if not selected:
        raise ValueError('采用区间中没有实际帧')
    last = selected[-1]
    count, kept = last + 1, (last + 1) / FPS
    folder = Path(outdir).resolve()
    clip, anchor = folder / 'clip.mp4', folder / 'anchor.png'
    _claim(folder, 'adopt_inputs.json', {'version': 1, 'source': source, 'requested_end_seconds': requested,
                                       'frame_index': last, 'kept_frames': count}, [clip, anchor])
    audio = _audio(info)

    def command(attempt):
        # Input timestamps were verified as exact 24fps. Lock that same rate in
        # the encoder/muxer; passthrough can otherwise give its last frame zero
        # duration. No fps conversion, interpolation or speed filter is used.
        args = ['ffmpeg', '-v', 'error', '-n', '-i', source['path'], '-map', '0:v:0', '-map', '0:a:0?',
                '-vf', 'trim=end_frame=%d,setpts=PTS-STARTPTS' % count, '-fps_mode', 'cfr', '-r', FPS,
                '-c:v', 'libx264', '-qp', '0', '-preset', 'fast', '-pix_fmt', stream['pix_fmt']]
        if audio:
            args += ['-af', 'atrim=duration=%.12f,asetpts=PTS-STARTPTS' % kept, '-c:a', 'aac', '-b:a', '192k']
        return args + ['-t', '%.12f' % kept, '-movflags', '+faststart', attempt]

    clip_checked = _produce(clip, command, lambda path: _verify_video(path, count))
    if bool(audio) != bool(clip_checked['audio']):
        raise RuntimeError('采用视频音轨丢失或被额外添加')
    source_last = _frame_hash(source['path'], last)
    clip_last = _frame_hash(clip, count - 1)
    pixel_check = {'accepted': True, 'kind': 'exact'}
    if source_last != clip_last:
        pixel_check = _frame_rounding(source['path'], last, clip, count - 1,
                                      stream, _video(clip_checked['probe']))
        pixel_check['kind'] = 'sparse_one_level_rounding'
        if not pixel_check['accepted']:
            _save_new(folder / ('pixel_mismatch_' + uuid.uuid4().hex + '.json'), pixel_check)
            raise RuntimeError('采用末帧存在超过编码舍入范围的差异，原文件保留，未继续续接')
    # The continuation anchor MUST still match the adopted clip exactly.
    _extract(clip, count - 1, anchor, clip_last)
    result = {'anchor_path': str(anchor), 'clip_path': str(clip), 'raw_path': source['path'],
              'frame_index': last, 'frame_time_seconds': times[last], 'kept_seconds': kept,
              'requested_end_seconds': requested, 'kept_frames': count, 'raw_frames': len(times),
              'fps': FPS, 'start_seconds': 0, 'playback_speed': 1.0,
              'anchor_matches_clip_last_frame': True, 'last_frame_rgb_sha256': clip_last,
              'source_last_frame_rgb_sha256': source_last, 'source_frame_pixel_check': pixel_check,
              'clip_sha256': _hash(clip), 'anchor_sha256': _hash(anchor), 'verification': clip_checked}
    record = folder / 'adopt_result.json'
    if record.exists():
        old = _read(record)
        if old.get('clip_sha256') != result['clip_sha256'] or old.get('anchor_sha256') != result['anchor_sha256']:
            raise RuntimeError('已有采用产物内容改变，原记录保留')
    else:
        _save_new(record, result)
    return result


def _url(path):
    from urllib.parse import quote
    try:
        relative = Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return ''
    return '/files/' + quote(relative.as_posix(), safe='/')


def join(videos, outdir):
    if not isinstance(videos, list) or not videos:
        raise ValueError('需要至少一段原片与采用片')
    folder = Path(outdir).resolve()
    inputs, originals, clips, raw_checks, clip_checks = [], [], [], [], []
    for row in videos:
        original, clip = _source(row['raw_path']), _source(row['clip_path'])
        kept = float(row['kept_seconds'])
        if not math.isfinite(kept) or kept <= 0 or abs(kept * FPS - round(kept * FPS)) > 0.00001:
            raise ValueError('采用时长必须对应完整的24fps帧')
        raw_check, clip_check = _verify_video(original['path']), _verify_video(clip['path'], round(kept * FPS))
        if raw_check['audio'] != clip_check['audio']:
            raise ValueError('同段原片与采用片的音轨参数不一致，未静默丢弃或补声音')
        inputs.append({'raw': original, 'clip': clip, 'kept_seconds': kept})
        originals.append(original['path']); clips.append(clip['path'])
        raw_checks.append(raw_check); clip_checks.append(clip_check)
    raw_path, film_path = folder / 'raw.mp4', folder / 'film.mp4'
    _claim(folder, 'join_inputs.json', {'version': 1, 'inputs': inputs, 'playback_speed': 1.0}, [raw_path, film_path])
    reference_video = _video(raw_checks[0]['probe'])
    reference_audio = raw_checks[0]['audio']
    for checked in raw_checks + clip_checks:
        video = _video(checked['probe'])
        if (video['width'], video['height'], video['pix_fmt']) != (reference_video['width'], reference_video['height'], reference_video['pix_fmt']):
            raise ValueError('分段视频尺寸或像素格式不一致，未缩放原片')
        if checked['audio'] != reference_audio:
            raise ValueError('分段音轨参数不一致，原片保留')

    def produce(paths, target, copy, expected_frames, segment_frames):
        # AAC duration is rounded to its audio/container clock. It must not set
        # the following video's offset (e.g. 191/24 is 7.958333, not 7.959).
        # A new list name preserves lists/attempts made by the previous strategy.
        concat = folder / (target.stem + '_concat_frames_v2.txt')
        contents = ''.join("file '" + path.replace('\\', '/').replace("'", "'\\''") +
                           "'\nduration %.12f\n" % (frames / FPS)
                           for path, frames in zip(paths, segment_frames))
        if concat.exists() and concat.read_text(encoding='utf-8') != contents:
            raise ValueError('拼接清单输入改变，原文件保留')
        if not concat.exists():
            with concat.open('x', encoding='utf-8') as stream:
                stream.write(contents)

        def command(attempt):
            args = ['ffmpeg', '-v', 'error', '-n', '-f', 'concat', '-safe', '0', '-i', concat,
                    '-map', '0:v:0', '-map', '0:a:0?']
            if copy:
                args += ['-c', 'copy']
            else:
                args += ['-fps_mode', 'cfr', '-r', FPS, '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
                         '-pix_fmt', reference_video['pix_fmt'], '-c:a', 'copy']
            return args + ['-movflags', '+faststart', attempt]
        return _produce(target, command, lambda path: _verify_video(path, expected_frames))

    raw_counts = [row['frames'] for row in raw_checks]
    clip_counts = [row['frames'] for row in clip_checks]
    raw = produce(originals, raw_path, True, sum(raw_counts), raw_counts)
    # Adopted clips have verified timing/pixel fidelity and exact endpoint
    # anchors. Copy their streams so joining cannot duplicate/drop a frame.
    film = produce(clips, film_path, True, sum(clip_counts), clip_counts)
    if raw['audio'] != film['audio'] or raw['audio'] != reference_audio:
        raise RuntimeError('原片与成片音轨参数不一致，未标记验收通过')
    report = {'inputs': inputs, 'raw': raw, 'film': film, 'playback_speed': 1.0,
              'concat_duration_source': 'actual_video_frames_divided_by_24',
              'audio_consistency_passed': True, 'audio_present': bool(reference_audio),
              'audio_listened': False, 'full_decode_passed': True,
              'raw_sha256': _hash(raw_path), 'film_sha256': _hash(film_path)}
    report_path = folder / 'report.json'
    if report_path.exists():
        old = _read(report_path)
        if old.get('raw_sha256') != report['raw_sha256'] or old.get('film_sha256') != report['film_sha256']:
            raise RuntimeError('已有合成视频内容改变，原验收记录保留')
    else:
        _save_new(report_path, report)
    return {'output_path': str(film_path), 'url': _url(film_path), 'raw_path': str(raw_path),
            'raw_url': _url(raw_path), 'manifest_path': str(folder / 'join_inputs.json'),
            'manifest_url': _url(folder / 'join_inputs.json'), 'report_path': str(report_path),
            'report_url': _url(report_path), 'seconds': film['seconds'], 'raw_seconds': raw['seconds'],
            'audio_present': bool(reference_audio), 'audio_consistency_passed': True,
            'full_decode_passed': True, 'playback_speed': 1.0}
