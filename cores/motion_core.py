"""Isolated source-video reference transfer. Never changes story generation."""
import json
import logging
import math
from pathlib import Path
import subprocess
import time
import uuid
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'outputs' / 'motion'
FPS = 24
# Accepted full-video trials: 124 frames lie on H3's native 17n+5 grid.
SEGMENT_FRAMES = 124
PIPELINE_VERSION = 'short124-source-audio-v1'


def frame_count(path):
    stream = next(s for s in probe(path)['streams'] if s['codec_type'] == 'video')
    return int(stream['nb_frames'])


def segment_plan(total_frames):
    """Exact, disjoint output intervals; short tails borrow preceding input context."""
    parts = []
    for start in range(0, total_frames, SEGMENT_FRAMES):
        count = min(SEGMENT_FRAMES, total_frames - start)
        model_frames = 5 + math.ceil((max(96, count) - 5) / 17) * 17
        lead = min(start, max(0, 107 - count)) if count < 96 else 0
        parts.append(dict(index=len(parts) + 1, start_frame=start, frames=count,
                          context_start=start - lead, leading_frames=lead,
                          model_frames=model_frames, padding_frames=model_frames - count - lead))
    return parts


def ffmpeg(args, timeout=600):
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y'] + list(map(str, args)),
                   check=True, timeout=timeout, creationflags=0x08000000)


def media_path(value, video=False, optional=False):
    value = str(value or '').strip().strip('"')
    if not value and optional:
        return None
    p = Path(value)
    allowed = {'.mp4', '.mov', '.mkv', '.webm'} if video else {'.png', '.jpg', '.jpeg', '.webp'}
    if not p.is_absolute() or str(p).startswith(('\\\\', '//')) or p.suffix.lower() not in allowed or not p.is_file():
        raise ValueError('请选择存在的本地' + ('视频' if video else '图片') + '文件（完整路径）')
    return p.resolve()


def probe(path):
    cmd = ['ffprobe', '-v', 'error', '-show_entries',
           'format=duration:stream=codec_type,width,height,r_frame_rate,nb_frames', '-of', 'json', str(path)]
    return json.loads(subprocess.check_output(cmd, timeout=30, encoding='utf-8', creationflags=0x08000000))


def validate(data):
    result = dict(data)
    for k, video, optional in (('video', True, False), ('character', False, False), ('scene', False, True)):
        p = media_path(data.get(k), video, optional)
        result[k] = str(p) if p else ''
    result['range_mode'] = str(data.get('range_mode') or 'clip')
    if result['range_mode'] not in ('full', 'clip'):
        raise ValueError('请选择处理整段或指定片段')
    info = probe(result['video'])
    duration = float(info['format']['duration'])
    for key, default in (('start', 0), ('seconds', 8)):
        if result['range_mode'] == 'full':
            result[key] = 0.0 if key == 'start' else duration
            continue
        result[key] = float(data.get(key, default))
        if not math.isfinite(result[key]):
            raise ValueError('起点和时长须为有效数字')
    if result['start'] < 0 or result['seconds'] < 1 / FPS:
        raise ValueError('起点须大于等于0，时长至少1/24秒')
    if not any(s.get('codec_type') == 'video' for s in info.get('streams', [])):
        raise ValueError('输入文件没有视频轨')
    if result['start'] + result['seconds'] > duration + .05:
        raise ValueError('选择的片段超出原视频时长')
    result['steps'] = int(data.get('steps', 10))
    if not 1 <= result['steps'] <= 50:
        raise ValueError('采样步数范围1～50')
    result['seed'] = int(data.get('seed') or (uuid.uuid4().int % (2**31)))
    if not 0 <= result['seed'] < 2**63:
        raise ValueError('种子超范围')
    result['size_tier'] = str(data.get('size_tier', '0.4'))
    if result['size_tier'] not in ('0.4', '0.7', '1.0'):
        raise ValueError('分辨率档不支持')
    result['prompt'] = str(data.get('prompt') or '').strip()
    result['character_layout'] = str(data.get('character_layout') or 'single')
    if result['character_layout'] not in ('single', 'four_panel'):
        raise ValueError('人物图类型须为单人全身图或四格设定图')
    for key in ('character_details', 'scene_details'):
        result[key] = str(data.get(key) or '').strip()
        if len(result[key]) > 2000:
            raise ValueError('外观说明请控制在2000字以内')
    if not result['prompt']:
        if not result['character_details']:
            raise ValueError('请写一句新人物特征（如发型、衣服款式和体型）。仅给图片的迁移效果未通过验证，暂不启动生成。')
        if result['scene'] and not result['scene_details']:
            raise ValueError('请写一句新场景特征（如石桥、峡谷和吊钟）。要换场景时，图片和说明需一起填写。')
    return result


def reference_prompt(seconds, replace_scene, character_details='', scene_details=''):
    """Edit the source timeline instead of inventing a new continuous performance."""
    appearance = character_details.strip() or 'the face, hairstyle, build and complete outfit shown in the image'
    setting = scene_details.strip() or 'the architecture, ground, vegetation, colors and lighting shown in the image'
    scene_def = (f'<Subject 2> is the environment in <Picture 2>: {setting}.' if replace_scene else
                 '<Subject 2> is the environment visible in <Video 1>.')
    scene_retain = ('the environment depicted in <Picture 2>, including its layout, materials and lighting' if replace_scene else
                    'the source environment and its geometry')
    return f'''subject_definitions:
<Subject 1> is the character in <Picture 1>: {appearance}.
{scene_def}
<Video 1> is the source video being edited, defining the complete visual timeline, performer positions, gestures, prop trajectories, camera movement and shot transitions.

summary:
[video editing + reference generation] The target video is an edited version of <Video 1>. Replace the source performer's face, hair, body appearance and complete outfit with <Subject 1> from <Picture 1>. The environment is <Subject 2>. Follow the source {seconds:g}-second timeline at corresponding moments.

retention_analysis:
<Subject 1> (throughout the source shots): attribute_transfer - face, hair, physique, outfit silhouette, garment lengths and footwear from <Picture 1> replace the original performer's appearance.
<Subject 2> (throughout the source shots): fully_preserved - {scene_retain}.
<Video 1> (visual timeline): fully_preserved - action phases, body and hand trajectories, prop positions, camera trajectory, framing, perspective, shot order, cut times and real-time speed.

detailed_description:
The replacement character has the visual finish of <Picture 1>, integrated with the lighting of <Subject 2>. The complete shot structure comes from <Video 1>.
[Shot 1] Begin at the first frame of <Video 1>, preserving its framing, perspective and performer position. <Subject 1> has {appearance}. Apply this appearance edit to each subsequent source shot at its original cut time. At each corresponding moment, the replacement follows the source head orientation, gaze, torso angle, hand positions, steps and prop handling. Preserve each source camera move, close-up, pullback and foreground occlusion in source order. The environment is {scene_retain}; its perspective follows the source camera. The character's clothing moves with the transferred performance. The final pose and action phase correspond to the last frame of <Video 1>.

overall_soundscape:
Generate environmental sound and physical movement sounds synchronized to the visible source actions. Original source audio is not supplied in this test.

non_diegetic_music:
N/A'''


def prepare_character(data, folder):
    """Explicit layout choice: deterministically crop the first of four panels.

    Never guess a layout from aspect ratio; keep the original asset unchanged.
    """
    if data.get('character_layout', 'single') == 'single':
        return data['character']
    target = folder / 'character_front.png'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', data['character'],
        '-vf', 'crop=iw/4:ih:0:0', '-frames:v', '1', str(target)],
        check=True, timeout=30, creationflags=0x08000000)
    return str(target)


def release_idle_cache(h3_client):
    """Bound cache growth between segments; never clear a busy/unknown queue."""
    try:
        queue = h3_client._http('GET', '/queue')
        if queue.get('queue_running') != [] or queue.get('queue_pending') != []:
            return
        request = urllib.request.Request(h3_client.H3_URL + '/free',
            data=json.dumps({'unload_models': True, 'free_memory': True}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()  # ComfyUI succeeds with an empty body here.
        time.sleep(2)
    except Exception:
        logging.exception('动作迁移：本段已保存，空闲缓存释放失败')


def restore_source_audio(generated, source, target, start, frames):
    """Use the selected source interval, never the model's invented soundtrack."""
    duration = frames / FPS
    has_audio = any(s['codec_type'] == 'audio' for s in probe(source)['streams'])
    args = ['-i', generated]
    if has_audio:
        args += ['-ss', start, '-i', source, '-map', '0:v:0', '-map', '1:a:0',
                 '-af', f'aresample=48000,apad,atrim=duration={duration}', '-c:a', 'aac']
    else:
        args += ['-map', '0:v:0', '-an']
    ffmpeg(args + ['-c:v', 'copy', '-t', duration, '-movflags', '+faststart', target])
    return 'source' if has_audio else 'silent'


def run(handle, task):
    from models import anima_client, h3_client, gpu_manager
    from cores import gen_history
    data = validate(task['inputs'])
    handle.check_pause()
    anima_client.local_guard()
    anima_client.idle_guard()
    saved_folder = (task.get('checkpoint') or {}).get('motion_folder')
    folder = Path(saved_folder).resolve() if saved_folder else OUTPUT / (time.strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:6])
    if folder.resolve().parent != OUTPUT.resolve():
        raise ValueError('动作迁移检查点目录不正确')
    folder.mkdir(parents=True, exist_ok=True)
    def save(name, obj):
        (folder / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    if saved_folder and json.loads((folder / 'request.json').read_text(encoding='utf-8')) != data:
        raise ValueError('素材参数已变化，请新建任务')
    pipeline_file = folder / 'pipeline.json'
    if saved_folder and (not pipeline_file.exists() or
            json.loads(pipeline_file.read_text(encoding='utf-8')).get('version') != PIPELINE_VERSION):
        raise ValueError('该检查点使用旧分段方式，请新建动作迁移任务；已有结果保留不变')
    save('pipeline.json', {'version': PIPELINE_VERSION, 'segment_frames': SEGMENT_FRAMES})
    save('request.json', data)
    handle.set(checkpoint={'motion_folder': str(folder)})
    handle.step('准备原视频：24帧/秒，约5秒自动分段', 0, 1)
    original = folder / 'original.mp4'
    ffmpeg(['-ss', data['start'], '-i', data['video'],
        '-t', data['seconds'], '-vf', "fps=24,scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
        '-an', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', str(original)],
        timeout=max(120, int(data['seconds'] * 4)))
    character = prepare_character(data, folder)
    images = [character] + ([data['scene']] if data['scene'] else [])
    source_probe = probe(original)
    source_video = next(s for s in source_probe['streams'] if s['codec_type'] == 'video')
    ratio = '9:16' if source_video['height'] > source_video['width'] else '16:9'
    width, height = h3_client._sized(ratio, data['size_tier'])
    assert source_probe['streams'][0]['r_frame_rate'] == '24/1'
    parts = segment_plan(frame_count(original))
    if not parts:
        raise ValueError('所选范围没有可处理的视频帧')
    save('segments.json', parts)
    results = []
    for part in parts:
        handle.check_pause()
        index = part['index']
        seg = folder / ('segment_%03d' % index)
        seg.mkdir(exist_ok=True)
        source = seg / 'reference.mp4'
        # Exact H3 grid: source and target have identical lengths. Short tails use
        # preceding source context, then only their new frames enter the final film.
        ffmpeg(['-i', original, '-vf',
            f"trim=start_frame={part['context_start']}:end_frame={part['start_frame'] + part['frames']},setpts=PTS-STARTPTS,scale={width}:{height},setsar=1,tpad=stop_mode=clone:stop={part['padding_frames']},setpts=N/(24*TB)",
            '-an', '-r', FPS, '-frames:v', part['model_frames'], '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', source])
        if frame_count(source) != part['model_frames']:
            raise RuntimeError('参考片段帧数不匹配，已停止，避免错位生成')
        prompt = data['prompt'] or reference_prompt(part['model_frames'] / FPS, bool(data['scene']),
            data['character_details'], data['scene_details'])
        (seg / 'prompt.txt').write_text(prompt, encoding='utf-8')
        generation_file = seg / 'generation.json'
        raw = json.loads(generation_file.read_text(encoding='utf-8')) if generation_file.exists() else None
        if not raw or not Path(raw['output_path']).is_file():
            handle.check_pause()
            anima_client.local_guard()
            anima_client.idle_guard()
            # Keep the label stable for motion_api.cancel's H3 interrupt check.
            handle.step('H3动作迁移生成中', index - 1, len(parts) + 1,
                        f"第{index}/{len(parts)}段 · {part['frames'] / FPS:g}秒")
            try:
                with gpu_manager.GPU_LOCK:
                    raw = h3_client.generate('ref2va', prompt, images=images,
                        reference_videos=[str(source)], seconds=part['model_frames'] / FPS,
                        exact_frames=part['model_frames'], ratio=ratio,
                        size_tier=data['size_tier'], steps=data['steps'], seed=data['seed'])
                    generation_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
                    release_idle_cache(h3_client)
            finally:
                gpu_manager.release('h3')
        handle.check_pause()
        if frame_count(raw['output_path']) < part['model_frames']:
            raise RuntimeError('模型输出缺帧，已保留分段结果，请重试')
        cleaned = seg / 'clip.mp4'
        trim_generation(raw['output_path'], cleaned, part)
        results.append(dict(part, output_path=str(cleaned), raw_output_path=raw['output_path']))
        handle.set(checkpoint={'motion_folder': str(folder), 'completed_segments': index})
    handle.check_pause()
    handle.step('自动拼合、保留原声与左右对比', len(parts), len(parts) + 1)
    joined = folder / 'joined_model_audio.mp4'
    concat_segments(results, joined)
    generated = folder / 'transferred.mp4'
    audio_mode = restore_source_audio(joined, data['video'], generated, data['start'], frame_count(original))
    if frame_count(generated) != frame_count(original):
        raise RuntimeError('拼合帧数与原片不一致，已保留所有分段')
    comparison = folder / 'comparison.mp4'
    box = '480:864' if ratio == '9:16' else '864:480'
    filt = (f'[0:v]fps=24,scale={box}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={box}:(ow-iw)/2:(oh-ih)/2,setsar=1[a];'
            f'[1:v]fps=24,scale={box}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={box}:(ow-iw)/2:(oh-ih)/2,setsar=1[b];'
            '[a][b]hstack=inputs=2:shortest=1[v]')
    ffmpeg(['-i', str(original), '-i', generated,
        '-filter_complex', filt, '-map', '[v]', '-map', '1:a:0?', '-c:a', 'copy', '-c:v', 'libx264', '-crf', '18',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(comparison)],
        timeout=max(120, int(data['seconds'] * 4)))
    if frame_count(comparison) != frame_count(original):
        raise RuntimeError('对比视频帧数与原片不一致，已保留迁移结果')
    result = dict(output_path=str(generated), output_file=generated.name, original=str(original), comparison=str(comparison),
                  url='/files/' + Path(generated).resolve().relative_to(ROOT).as_posix(),
                  comparison_url='/files/' + comparison.relative_to(ROOT).as_posix(),
                  prompt=prompt, probe=probe(generated), source_probe=source_probe, segments=results,
                  frames=frame_count(generated), seconds=frame_count(generated) / FPS,
                  audio_mode=audio_mode, pipeline_version=PIPELINE_VERSION,
                  review='约5秒短段自动拼合；有原声则保留原声，无原声则保持静音。动作近似跟随，分段接缝可能跳变，偶有背景替换不完整。')
    save('result.json', result)
    gen_history.record('video', str(generated), prompt, source='动作迁移', extra={'comparison': str(comparison)})
    handle.step('完成，等待用户看效果', len(parts) + 1, len(parts) + 1)
    return result


def trim_generation(raw, target, part):
    """Remove model-only context/padding, preserve exact frames and audio interval."""
    lead, count = part['leading_frames'], part['frames']
    duration = count / FPS
    has_audio = any(s['codec_type'] == 'audio' for s in probe(raw)['streams'])
    args = ['-i', raw]
    if not has_audio:
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    audio = '0:a' if has_audio else '1:a'
    audio_start = lead / FPS if has_audio else 0
    filt = (f'[0:v]fps=24,trim=start_frame={lead}:end_frame={lead + count},setpts=PTS-STARTPTS[v];'
            f'[{audio}]atrim=start={audio_start}:duration={duration},asetpts=PTS-STARTPTS,'
            f'aresample=48000,apad,atrim=duration={duration}[a]')
    ffmpeg(args + ['-filter_complex', filt, '-map', '[v]', '-map', '[a]', '-t', duration,
                  '-r', FPS, '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', '2', target])
    if frame_count(target) != count:
        raise RuntimeError('分段裁剪帧数不匹配')


def concat_segments(parts, target):
    args, filters, streams = [], [], ''
    for i, part in enumerate(parts):
        args += ['-i', part['output_path']]
        filters += [f"[{i}:v]fps=24,trim=end_frame={part['frames']},setpts=N/(24*TB)[v{i}]",
                    f"[{i}:a]atrim=duration={part['frames'] / FPS},asetpts=PTS-STARTPTS[a{i}]"]
        streams += f'[v{i}][a{i}]'
    filters.append(streams + f'concat=n={len(parts)}:v=1:a=1[v][a]')
    duration = sum(p['frames'] for p in parts) / FPS
    ffmpeg(args + ['-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]',
                  '-t', duration, '-r', FPS, '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
                  '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', target],
           timeout=max(120, int(duration * 4)))
