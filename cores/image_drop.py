"""Import the image explicitly dragged by the user, without an AI service."""
import base64
import io
import ipaddress
import socket
from urllib.parse import urlsplit, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from pathlib import Path

MAX_BYTES = 20*1024*1024
ROOT = Path(__file__).resolve().parent.parent


def public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只支持公开的HTTP/HTTPS图片地址')
    if parsed.port not in (None, 80, 443):
        raise ValueError('图片地址端口不支持')
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('不支持访问内网或本机服务地址，请直接拖入图片文件')
    return url


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, public_url(newurl))


def read_image(url, local_origin):
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError('图片地址无效')
    parsed, origin = urlsplit(url), urlsplit(local_origin)
    if (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc) and parsed.path.startswith('/files/'):
        name = unquote(parsed.path[len('/files/'):])
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):
            raise ValueError('本机图片路径不支持')
        if path.stat().st_size > MAX_BYTES: raise ValueError('图片最大20MB')
        data = path.read_bytes()
    else:
        request = Request(public_url(url), headers={'User-Agent':'Local-Workbench-Image-Import/1.0'})
        with build_opener(ProxyHandler({}), Redirect()).open(request, timeout=20) as response:
            data = response.read(MAX_BYTES+1)
    if not data or len(data) > MAX_BYTES:
        raise ValueError('图片为空或超过20MB')
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        formats = {'PNG':('png','image/png'),'JPEG':('jpg','image/jpeg'),'WEBP':('webp','image/webp')}
        if im.format not in formats or im.width*im.height > 40_000_000:
            raise ValueError('仅支持4000万像素以内的PNG、JPG、WebP图片')
        ext, mime = formats[im.format]
        im.verify()
    return {'name':'网页拖入图片.'+ext,'mime':mime,'base64':base64.b64encode(data).decode('ascii')}
