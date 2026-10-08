"""Frame-exact, shot-aware motion-transfer preparation (CPU only)."""
import math
import re
import subprocess

FPS = 24


def plan(total, cuts=()):
    boundaries = sorted({0, total, *(int(v) for v in cuts if 0 < v < total)})
    jobs = []
    for shot, (begin, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
        sizes = [min(124, end - i) for i in range(begin, end, 124)]
        if len(sizes) > 1 and sizes[-1] <= FPS:
            tail = sizes.pop()
            sizes[-1] += tail
        start = begin
        for size in sizes:
            lead = min(12, start - begin)
            native = 5 + math.ceil((max(107, size + lead) - 5) / 17) * 17
            stretch = size + lead < 96
            jobs.append(dict(index=len(jobs)+1, shot=shot, shot_start=begin, shot_end=end,
                start_frame=start, frames=size, context_start=start-lead, leading_frames=lead,
                model_frames=native, padding_frames=0 if stretch else native-size-lead,
                time_mode='stretch_short_shot' if stretch else 'native'))
            start += size
    return jobs


def detect_cuts(path):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-i', str(path),
        '-vf', "select='gt(scene,0.15)',showinfo", '-an', '-f', 'null', '-'],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=True,
        timeout=600, creationflags=0x08000000)
    frames = sorted({round(float(t)*FPS) for t in re.findall(r'pts_time:([0-9.]+)', result.stderr)})
    kept = []
    for frame in frames:
        if not kept or frame-kept[-1] > 2:
            kept.append(frame)
    return kept


def prepare_reference(original, target, part, width, height):
    from .motion_core import ffmpeg, frame_count
    begin, end = part['context_start'], part['start_frame'] + part['frames']
    filt = (f'fps=24,trim=start_frame={begin}:end_frame={end},setpts=N/(24*TB),'
        f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
        f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,')
    if part['time_mode'] == 'stretch_short_shot':
        filt += f'setpts=PTS*{part["model_frames"]}/{end-begin},fps=24,tpad=stop_mode=clone:stop=2'
    else:
        filt += f'tpad=stop_mode=clone:stop={part["padding_frames"]}'
    ffmpeg(['-i', original, '-vf', filt+',setpts=N/(24*TB)', '-r', FPS,
        '-frames:v', part['model_frames'], '-an', '-c:v', 'libx264', '-crf', 18, target])
    if frame_count(target) != part['model_frames']:
        raise RuntimeError('参考片段帧数不匹配，已停止，避免错位生成')


def clean(raw, target, part):
    from .motion_core import ffmpeg, frame_count
    count = part['frames'] + part['leading_frames']
    if part['time_mode'] == 'stretch_short_shot':
        filt = f'setpts=(PTS-STARTPTS)*{count}/{part["model_frames"]},fps=24,'
    else:
        filt = 'fps=24,'
    filt += (f'trim=start_frame={part["leading_frames"]}:end_frame={count},'
             'setpts=N/(24*TB)')
    # Original audio is restored after concatenation; generated audio is discarded.
    ffmpeg(['-i', raw, '-vf', filt, '-an', '-r', FPS, '-frames:v', part['frames'],
            '-c:v', 'libx264', '-crf', 18, '-pix_fmt', 'yuv420p', target])
    if frame_count(target) != part['frames']:
        raise RuntimeError('分段还原帧数不匹配')


def join(parts, target):
    from .motion_core import ffmpeg
    args, graph, streams = [], [], ''
    for i, part in enumerate(parts):
        args += ['-i', part['output_path']]
        graph.append(f'[{i}:v]fps=24,trim=end_frame={part["frames"]},setpts=N/(24*TB)[v{i}]')
        streams += f'[v{i}]'
    graph.append(streams + f'concat=n={len(parts)}:v=1:a=0[v]')
    frames = sum(p['frames'] for p in parts)
    ffmpeg(args + ['-filter_complex', ';'.join(graph), '-map', '[v]', '-r', FPS,
        '-frames:v', frames, '-an', '-c:v', 'libx264', '-crf', 18,
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', target], timeout=max(120, int(frames/FPS*4)))
