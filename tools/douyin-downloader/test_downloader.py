import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import uuid

import downloader as d

VALIDATION = Path(__file__).resolve().parent / 'validation' / ('cpu-' + uuid.uuid4().hex[:8])
VALIDATION.mkdir(parents=True)


def box(kind, data=b''):
    return struct.pack('>I4s', len(data) + 8, kind) + data


class CoreTests(unittest.TestCase):
    def test_share_text_batch_dedup(self):
        self.assertEqual(d.extract_links('复制 https://v.douyin.com/ABC/。复制此链接\nhttps://v.douyin.com/ABC/ https://www.douyin.com/video/7692645250620557233'), ['https://v.douyin.com/ABC/', 'https://www.douyin.com/video/7692645250620557233'])

    def test_reject_foreign_links(self):
        for text in ('', '文字', 'http://127.0.0.1/', 'https://v.douyin.com.evil.example/a', 'https://v.douyin.com@evil.example/a'):
            with self.subTest(text=text), self.assertRaises(d.DownloadError):
                d.extract_links(text)

    def test_link_limit(self):
        with self.assertRaises(d.DownloadError):
            d.extract_links(' '.join(f'https://v.douyin.com/{n}/' for n in range(21)))

    def test_ids(self):
        vid='7692645250620557233'
        for url in (f'https://www.iesdouyin.com/share/video/{vid}/', f'https://www.douyin.com/video/{vid}', f'https://www.douyin.com/jingxuan?modal_id={vid}'):
            self.assertEqual(d.video_id(url), vid)
        self.assertEqual(d.video_id('https://evil.example/video/'+vid), '')

    def test_filename(self):
        name=d.safe_filename('../CON:一条/视频?*\n', '123')
        self.assertNotRegex(name, r'[<>:"/\\|?*\x00-\x1f]')
        self.assertTrue(name.endswith('_123.mp4'))
        self.assertTrue(d.safe_filename('NUL', '1').startswith('_'))

    def test_watermark_urls(self):
        self.assertFalse(d.is_plain_play_url('https://api.amemv.com/aweme/v1/playwm/?id=1'))
        self.assertFalse(d.is_plain_play_url('https://v.douyinvod.com/a?watermark=1'))
        self.assertTrue(d.is_plain_play_url('https://v.douyinvod.com/a?watermark=0'))

    def test_quality_sort_and_no_watermark_download_addr(self):
        video={'width':720,'height':1280,'duration':10000,'play_addr':{'url_list':['https://v.douyinvod.com/720'],'data_size':20000},'download_addr':{'url_list':['https://v.douyinvod.com/watermarked']},'bit_rate':[{'bit_rate':10000,'play_addr':{'width':1080,'height':1920,'url_list':['https://v.douyinvod.com/1080']}}]}
        choices=d.quality_candidates(video)
        self.assertEqual(choices[0]['width'],1080)
        self.assertEqual(len(choices),2)

    def test_no_plain_stream(self):
        with self.assertRaises(d.DownloadError):d.quality_candidates({'play_addr':{'url_list':['https://v.douyin.com/playwm/']}})

    def test_target_id_not_recommended_clip(self):
        obj={'aweme_details':[{'aweme_id':'2','video':{}},{'aweme_id':'1','video':{}}]}
        self.assertEqual(d.pick_entry(obj,'1')['aweme_id'],'1')
        self.assertIsNone(d.pick_entry(obj,'3'))

    def test_mp4_valid_and_truncated(self):
        valid=VALIDATION/'valid.mp4'
        valid.write_bytes(box(b'ftyp',b'isom')+box(b'moov')+box(b'mdat',b'12345'))
        d.check_mp4(valid)
        for name,body in [('html.mp4',b'<html>not video</html>'),('truncated.mp4',valid.read_bytes()[:-3]),('empty.mp4',b'')]:
            path=VALIDATION/name;path.write_bytes(body)
            with self.assertRaises(d.DownloadError):d.check_mp4(path)

    def test_private_url(self):
        with self.assertRaises(d.DownloadError):d.public_host('http://127.0.0.1/a.mp4',True)
        with self.assertRaises(d.DownloadError):d.public_host('https://douyinvod.com.evil.example/a',True)

    def test_resolve_compatibility_endpoint(self):
        vid='7692645250620557233'
        data={'aweme_details':[{'aweme_id':vid,'desc':'测试','video':{'duration':1000,'play_addr':{'url_list':['https://v.douyinvod.com/a']}}}]}
        with patch.object(d,'get_page',return_value=(json.dumps(data),'')):
            self.assertEqual(d.resolve('https://www.douyin.com/video/'+vid)['video_id'],vid)

    def test_resolve_ssr_fallback(self):
        vid='7692645250620557233';data={'aweme_id':vid,'video':{'play_addr':{'url_list':['https://v.douyinvod.com/a']}}}
        with patch.object(d,'get_page',side_effect=[('{}',''),('window._ROUTER_DATA = '+json.dumps(data)+';</script>','')]):
            self.assertEqual(d.resolve('https://www.douyin.com/video/'+vid)['video_id'],vid)

    def test_existing_file_not_overwritten(self):
        vid='0000000001';title='不覆盖';target=VALIDATION/d.safe_filename(title,vid)
        original=box(b'ftyp',b'isom')+box(b'moov')+box(b'mdat',b'123');target.write_bytes(original)
        result=d.download({'title':title,'video_id':vid,'candidates':[]},VALIDATION,'test')
        self.assertTrue(result['already_exists']);self.assertEqual(target.read_bytes(),original)

    def test_cancel_before_download(self):
        with self.assertRaises(d.Cancelled):d.download({'title':'取消','video_id':'1','candidates':[{}]},VALIDATION,'cancelled',cancelled=lambda:True)


if __name__=='__main__':
    unittest.main(verbosity=2)
