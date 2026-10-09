"""Public Douyin share-page downloader. Standard library only; no account cookies."""
from __future__ import annotations

import hashlib
import http.cookiejar
import ipaddress
import json
from pathlib import Path
import re
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'
SHARE_HOSTS = {'v.douyin.com', 'www.douyin.com', 'douyin.com', 'www.iesdouyin.com', 'iesdouyin.com'}
MEDIA_DOMAINS = ('douyinvod.com', '365yg.com', 'amemv.com', 'snssdk.com', 'douyin.com', 'bytecdn.cn', 'bytecdn.com', 'ibytedtos.com', 'bytedance.com', 'ixigua.com', 'bytednsdoc.com', 'byteimg.com', 'pstatp.com')
MAX_FILE = 2 * 1024**3
MAX_PAGE = 12 * 1024**2


class DownloadError(Exception):
    pass


class Cancelled(DownloadError):
    pass


def share_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or p.hostname not in SHARE_HOSTS or p.username or p.password or p.port not in (None, 80, 443):
        raise DownloadError('只支持抖音官方分享链接或视频网页链接。')
    return urllib.parse.urlunsplit(('https', p.netloc, p.path, p.query, ''))


def extract_links(text):
    if not isinstance(text, str) or len(text) > 30000:
        raise DownloadError('分享文字过长，一次最多粘贴20条链接。')
    links = []
    for url in re.findall(r'https?://[^\s<>"\u3000，。！？；、（）【】《》]+', text):
        url = url.rstrip('，。！!？?；;、）)]}》>\'')
        try:
            url = share_url(url)
        except (DownloadError, ValueError):
            continue
        if url not in links:
            links.append(url)
    if not links:
        raise DownloadError('没有找到抖音链接。请粘贴完整分享文字，或 https://v.douyin.com/… 链接。')
    if len(links) > 20:
        raise DownloadError('一次最多20条，请分批下载。')
    return links


def video_id(url):
    p = urllib.parse.urlsplit(url)
    if p.hostname not in SHARE_HOSTS:
        return ''
    match = re.search(r'/(?:share/)?(?:video|note)/(\d{10,24})(?:/|$)', p.path)
    if match:
        return match.group(1)
    for key in ('modal_id', 'vid'):
        value = urllib.parse.parse_qs(p.query).get(key, [''])[0]
        if re.fullmatch(r'\d{10,24}', value):
            return value
    return ''


def public_host(url, media=False):
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ''
    valid = host in SHARE_HOSTS or (media and any(host == d or host.endswith('.' + d) for d in MEDIA_DOMAINS))
    if p.scheme not in ('http', 'https') or not valid or p.username or p.password or p.port not in (None, 80, 443):
        raise DownloadError('平台返回了不支持的地址，已停止下载。')
    try:
        addresses = socket.getaddrinfo(host, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except OSError as error:
        raise DownloadError('网络域名解析失败，请检查网络后重试。') from error
    if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise DownloadError('拒绝访问本地或内网下载地址。')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, media=False):
        self.media = media

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_host(newurl, self.media)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener(media=False):
    return urllib.request.build_opener(SafeRedirect(media), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def get_page(client, url):
    public_host(url)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://www.iesdouyin.com/'})
    with client.open(req, timeout=25) as response:
        content = response.read(MAX_PAGE + 1)
        if len(content) > MAX_PAGE:
            raise DownloadError('平台返回的数据过大，请稍后再试。')
        return content.decode('utf-8', 'replace'), response.url


def objects(data):
    stack = [data]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            yield obj
            stack.extend(obj.values())
        elif isinstance(obj, list):
            stack.extend(obj)


def pick_entry(data, vid):
    return next((o for o in objects(data) if str(o.get('aweme_id')) == vid and isinstance(o.get('video'), dict)), None)


def is_plain_play_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(p.query)
        return p.scheme in ('http', 'https') and 'playwm' not in p.path.lower() and not any(q.get(k, ['0'])[0] in ('1', 'true') for k in ('watermark', 'logo', 'watermark_enabled'))
    except ValueError:
        return False


def quality_candidates(video):
    result, seen = [], set()
    duration = float(video.get('duration') or 0) / 1000

    def add(address, rate=0, codec=''):
        if not isinstance(address, dict):
            return
        width = int(address.get('width') or video.get('width') or 0)
        height = int(address.get('height') or video.get('height') or 0)
        size = int(address.get('data_size') or 0)
        rate = int(rate or (size * 8 / duration if duration else 0))
        codec = codec or ('h264' if 'h264' in address.get('url_key', '') else '')
        for url in address.get('url_list', []):
            if isinstance(url, str) and url not in seen and is_plain_play_url(url):
                seen.add(url)
                result.append({'url': url, 'width': width, 'height': height, 'bitrate': rate, 'size': size, 'md5': address.get('file_hash', ''), 'codec': codec})

    for key in ('play_addr', 'play_addr_h264', 'play_addr_265'):
        add(video.get(key), codec='h264' if key == 'play_addr_h264' else '')
    for variant in video.get('bit_rate') or []:
        add(variant.get('play_addr'), variant.get('bit_rate'), 'hevc' if variant.get('is_h265') or variant.get('is_bytevc1') else 'h264')
    result.sort(key=lambda c: (c['width'] * c['height'], c['bitrate'], c['codec'] == 'h264'), reverse=True)
    if not result:
        raise DownloadError('这条视频未提供可用的无平台水印播放地址；不会下载带水印版本冒充。')
    return result


def resolve(link, cancelled=lambda: False):
    client = opener()
    link = share_url(link)
    vid = video_id(link)
    if not vid:
        _, final = get_page(client, link)
        vid = video_id(final)
    if not vid:
        raise DownloadError('没有识别到单条视频。请从具体视频复制分享链接，主页、直播和合集暂不支持。')
    if cancelled():
        raise Cancelled('已停止下载。')
    # Public compatibility endpoint also used by the official share-page client.
    endpoint = 'https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/?' + urllib.parse.urlencode({'aweme_ids': '[' + vid + ']'})
    entry = None
    try:
        raw, _ = get_page(client, endpoint)
        data = json.loads(raw)
        entry = pick_entry(data, vid)
    except (urllib.error.URLError, json.JSONDecodeError):
        pass
    if not entry:
        # The original, previously verified workflow remains a bounded fallback.
        raw, _ = get_page(client, f'https://www.iesdouyin.com/share/video/{vid}/')
        match = re.search(r'window\._ROUTER_DATA\s*=\s*', raw)
        if match:
            try:
                data, _ = json.JSONDecoder().raw_decode(raw[match.end():].lstrip())
                entry = pick_entry(data, vid)
            except json.JSONDecodeError:
                pass
    if not entry:
        raise DownloadError('抖音没有返回这条视频的公开下载数据，可能已删除、限制访问或临时风控。可打开原链接确认后再试；工具不绕过登录、验证码或私密权限。')
    if entry.get('images'):
        raise DownloadError('这是图集/实况照片，不是普通视频，本版暂不支持。')
    if entry.get('is_private') or entry.get('status', {}).get('is_private'):
        raise DownloadError('该内容不是公开视频，已停止。')
    return {'video_id': vid, 'title': entry.get('desc', '').strip() or '抖音视频', 'author': entry.get('author', {}).get('nickname', ''), 'duration': float(entry['video'].get('duration') or 0) / 1000, 'source': link, 'candidates': quality_candidates(entry['video'])}


def safe_filename(title, vid):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')[:48].rstrip(' .') or '抖音视频'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', title, re.I):
        title = '_' + title
    return f'{title}_{vid}.mp4'


def check_mp4(path):
    """Verify complete ISO-BMFF boxes; never mark an HTML response/truncated MP4 complete."""
    size = path.stat().st_size
    boxes, offset = set(), 0
    with path.open('rb') as file:
        while offset < size:
            file.seek(offset)
            head = file.read(8)
            if len(head) != 8:
                raise DownloadError('视频文件不完整，未标记为下载成功。')
            length, kind = struct.unpack('>I4s', head)
            header = 8
            if length == 1:
                extra = file.read(8)
                if len(extra) != 8:
                    raise DownloadError('视频文件头不完整。')
                length = struct.unpack('>Q', extra)[0]
                header = 16
            if length == 0:
                length = size - offset
            if length < header or offset + length > size:
                raise DownloadError('视频下载不完整，残片已保留，重试不会覆盖它。')
            boxes.add(kind)
            offset += length
    if not {b'ftyp', b'moov', b'mdat'}.issubset(boxes):
        raise DownloadError('下载内容不是完整MP4视频，未标记为成功。')


def download(info, folder, job_id, progress=lambda **k: None, cancelled=lambda: False):
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe_filename(info['title'], info['video_id'])
    if target.exists():
        try:
            check_mp4(target)
            return {'path': str(target), 'size': target.stat().st_size, 'already_exists': True}
        except DownloadError:
            target = target.with_name(target.stem + '_' + job_id[:8] + '.mp4')
    last_error = None
    attempts = []
    # At most six offered CDN addresses, in descending resolution/bitrate order.
    for number, candidate in enumerate(info['candidates'][:6], 1):
        if cancelled():
            raise Cancelled('已停止，未完成的下载文件已保留。')
        partial = folder / (target.name + '.' + job_id + f'.{number}.part')
        try:
            public_host(candidate['url'], media=True)
            req = urllib.request.Request(candidate['url'], headers={'User-Agent': UA, 'Referer': 'https://www.iesdouyin.com/'})
            with opener(media=True).open(req, timeout=30) as response:
                expected = int(response.headers.get('Content-Length') or 0)
                if response.status != 200 or 'text/' in response.headers.get('Content-Type', '') or expected > MAX_FILE:
                    raise DownloadError('平台未提供可直接下载的视频，或文件超过2GB限制。')
                total = 0
                digest = hashlib.md5()
                with partial.open('xb') as file:
                    while chunk := response.read(256 * 1024):
                        if cancelled():
                            raise Cancelled('已停止，未完成的下载文件已保留。')
                        total += len(chunk)
                        if total > MAX_FILE:
                            raise DownloadError('视频超过本工具单文件2GB限制。')
                        file.write(chunk)
                        digest.update(chunk)
                        progress(received=total, total=expected, width=candidate['width'], height=candidate['height'], codec=candidate['codec'])
            if expected and total != expected:
                raise DownloadError('下载长度与服务器不一致，请重试。')
            expected_hash = candidate['md5']
            if re.fullmatch(r'[a-fA-F0-9]{32}', expected_hash) and digest.hexdigest().lower() != expected_hash.lower():
                raise DownloadError('文件校验不一致，残片已保留，未标记为成功。')
            check_mp4(partial)
            if target.exists():
                target = target.with_name(target.stem + '_' + job_id[:8] + '.mp4')
            partial.rename(target)
            return {'path': str(target), 'size': total, 'width': candidate['width'], 'height': candidate['height'], 'codec': candidate['codec'], 'already_exists': False, 'md5': digest.hexdigest(), 'quality_note': '已选择接口提供的最高可用清晰度；未转码、未放大。' if number <= 4 else '优先地址不可用，已使用后备地址；以实际分辨率为准。', 'fallback_attempts': len(attempts)}
        except Cancelled:
            raise
        except (OSError, DownloadError) as error:
            last_error = error
            attempts.append(type(error).__name__)
            progress(note=f'下载地址 {number} 不可用，正在尝试平台备用地址…')
    raise DownloadError('平台播放地址下载失败：' + safe_error(last_error))


def safe_error(error):
    if isinstance(error, DownloadError):
        return str(error)[:400]
    if isinstance(error, urllib.error.HTTPError):
        return f'平台返回 HTTP {error.code}。请确认原视频可访问，稍后重试。'
    if isinstance(error, (TimeoutError, urllib.error.URLError)):
        return '连接超时或网络不可用，请检查网络后重试。'
    if isinstance(error, PermissionError):
        return '保存目录没有写入权限，请选择其他文件夹。'
    if isinstance(error, OSError):
        return '文件或网络操作失败：' + (error.strerror or str(error))[:160]
    return '处理失败，请重新复制单条视频的分享链接后重试。'
