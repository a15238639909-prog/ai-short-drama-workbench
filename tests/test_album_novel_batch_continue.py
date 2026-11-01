"""Exercise real album/novel loops with in-memory models/media; no inference."""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import runtime_core as runtime, runtime_reset, store, narrative_core, state_core
from production import album_core as album, novel_core as novel
from models import krea_client


class Handle:
    def __init__(self):
        self.task=dict(id='task_fixture',checkpoint={},status='running')
    def _get(self):
        return copy.deepcopy(self.task)
    def set(self,**changes):
        self.task.update(copy.deepcopy(changes))
    def step(self,*args):
        pass
    def check_pause(self):
        if self.task['status'] in ('paused','cancelled'):
            raise runtime.TaskPaused()


class AlbumNovelContinueTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='album_novel_code_only_'))
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for owner,name,value in ((store,'DATA',self.root/'data'),
                (runtime_reset,'STATE',self.root/'reset.json'),
                (album,'ALBUM_ROOT',self.root/'albums'),(novel,'NOVEL_ROOT',self.root/'novels')):
            self.stack.enter_context(patch.object(owner,name,value))
        self.addCleanup(runtime_reset.bind_thread,None);runtime_reset.bind_thread(None)
        self.story=dict(story_id='STORY_FIXTURE',title='同名作品',character_ids=[],master_plan={})
        self.handle=Handle()
        self.plan_calls=0;self.generated=[];self.writer_calls=[];self.opened=[]
        self.stack.enter_context(patch.object(narrative_core,'load_episode',return_value=dict(adopted=True,events=[])))
        self.initial=self.stack.enter_context(patch.object(state_core,'latest_state',return_value=dict(
            character_states={'甲':dict(location='起点')},plot_state={'open_threads':[]})))

    def album(self,fail=None,pdf_fail=False,copy_fail=False,task=True):
        def plan(*args):
            self.plan_calls+=1
            return dict(album_name='测试画册',series_bible={},images=[dict(index=1,title=str(n)) for n in (1,2,3)])
        def generate(prompt,**options):
            self.generated.append(int(prompt))
            if fail and int(prompt)==fail[0]:raise fail[1]
            path=self.root/('raw_%s.bin'%prompt);path.write_bytes(prompt.encode())
            return dict(output_path=str(path))
        owner=self
        class ImageReceipt:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def convert(self,mode):return self
            def close(self):pass
            def save(self,path,**kwargs):
                if pdf_fail:raise RuntimeError('mock PDF export failed')
                Path(path).write_bytes(b'PDF mock receipt, no encoder')
        def opened(path):
            owner.opened.append(str(path));return ImageReceipt()
        import shutil
        copy_file=shutil.copy2
        def copy(source,target):
            if copy_fail and Path(source).stem=='raw_2':raise PermissionError('mock copy sharing violation')
            return copy_file(source,target)
        with ExitStack() as stack:
            stack.enter_context(patch.object(album,'plan_album',side_effect=plan))
            stack.enter_context(patch.object(album,'_compile_album_prompt',side_effect=lambda item,*args:item['title']))
            stack.enter_context(patch.object(krea_client,'generate',side_effect=generate))
            stack.enter_context(patch.object(album.Image,'open',side_effect=opened))
            stack.enter_context(patch.object(shutil,'copy2',side_effect=copy))
            return album.produce_album(self.story,'测试',3,task=self.handle if task else None)

    def novel(self,fail=None,task=True,bad_state=False):
        def plan(*args):
            self.plan_calls+=1
            return [dict(title='章节%d'%n,brief='原提纲%d'%n,event_ids=[]) for n in (1,2,3)]
        def write(story,ch,state_text):
            self.writer_calls.append(dict(index=ch['index'],brief=ch['brief'],state=state_text))
            if fail and ch['index']==fail[0]:raise fail[1]
            after=dict(location='地点%d'%ch['index'],held_objects=['钥匙'] if ch['index']==1 else [],events=[])
            return '原正文%d'%ch['index']*150, ['malformed_state'] if bad_state else after
        with patch.object(novel,'plan_chapters',side_effect=plan),patch.object(novel,'write_chapter',side_effect=write):
            return novel.produce_novel(self.story,chapter_count=3,task=self.handle if task else None)

    def test_album_failed_middle_item_continues_and_only_missing_image_resumes(self):
        first=self.album((2,ValueError('mock model result')))
        self.assertEqual(self.generated,[1,2,3]);self.assertFalse(first['generation_completed'])
        self.assertEqual(set(first['failed_items']),{'2'})
        self.assertEqual([Path(p).name for p in first['images']],['01.png','03.png'])
        second=self.album()
        self.assertEqual(self.generated,[1,2,3,2]);self.assertEqual(self.plan_calls,1)
        self.assertTrue(second['generation_completed']);self.assertEqual(second['failed_items'],{})
        self.assertEqual([Path(p).name for p in second['images']],['01.png','02.png','03.png'])

    def test_album_copy_failure_reuses_retained_model_response_without_spending_again(self):
        first=self.album(copy_fail=True)
        self.assertFalse(first['generation_completed']);self.assertEqual(self.generated,[1,2,3])
        second=self.album()
        self.assertTrue(second['generation_completed']);self.assertEqual(self.generated,[1,2,3])

    def test_album_pdf_failure_preserves_images_and_retries_only_export(self):
        first=self.album(pdf_fail=True)
        self.assertFalse(first['generation_completed']);self.assertIsNone(first['pdf'])
        second=self.album()
        self.assertTrue(second['generation_completed']);self.assertEqual(self.generated,[1,2,3])
        self.assertEqual(self.plan_calls,1)

    def test_novel_missing_chapter_keeps_outline_and_does_not_invent_missing_results(self):
        first=self.novel((2,ValueError('mock text format error')))
        self.assertFalse(first['generation_completed']);self.assertEqual(first['chapters'],2)
        self.assertEqual([c['index'] for c in self.writer_calls],[1,2,3])
        third=self.writer_calls[2]
        self.assertEqual(third['brief'],'原提纲3')
        self.assertIn('第2章未生成',third['state']);self.assertIn('不能当作已经发生',third['state'])
        self.assertIn('地点1',third['state']);self.assertNotIn('地点2',third['state'])
        self.assertIn('本章待补',Path(first['file']).read_text(encoding='utf-8'))

    def test_novel_resume_replays_saved_state_only_writes_missing_and_keeps_later_content(self):
        first=self.novel((2,ValueError('mock format error')))
        chapter_file=Path(first['file']).with_name('chapters.json')
        before=store.load_json(chapter_file,[])[2]['content']
        self.initial.return_value=dict(character_states={'甲':dict(location='外部未来地点')})
        second=self.novel()
        self.assertEqual([c['index'] for c in self.writer_calls],[1,2,3,2])
        self.assertIn('地点1',self.writer_calls[-1]['state'])
        self.assertNotIn('外部未来地点',self.writer_calls[-1]['state'])
        self.assertTrue(second['generation_completed']);self.assertEqual(self.plan_calls,1)
        self.assertEqual(store.load_json(chapter_file,[])[2]['content'],before)
        self.assertIn('3',second['continuity_warnings']);self.initial.assert_called_once()

    def test_same_title_different_story_never_adopts_the_other_partial_chapters(self):
        first=self.novel((2,ValueError('missing')),task=False)
        self.story['story_id']='STORY_OTHER'
        second=self.novel(task=False)
        self.assertNotEqual(Path(first['file']).parent,Path(second['file']).parent)
        self.assertEqual(self.plan_calls,2)
        self.assertIn('本章待补',Path(first['file']).read_text(encoding='utf-8'))

    def test_malformed_optional_state_preserves_text_and_continues_with_warning(self):
        result=self.novel(bad_state=True)
        self.assertTrue(result['generation_completed']);self.assertEqual(result['chapters'],3)
        self.assertEqual(len(result['continuity_warnings']),3)

    def test_novel_export_failure_retains_chapters_and_resume_retries_export_only(self):
        original=Path.write_text
        def write(path,*args,**kwargs):
            if path.name=='novel.md':raise PermissionError('mock export sharing violation')
            return original(path,*args,**kwargs)
        with patch.object(Path,'write_text',new=write):first=self.novel()
        self.assertFalse(first['generation_completed']);self.assertIsNone(first['file'])
        self.assertEqual(len(store.load_json(first['chapters_path'],[])),3)
        second=self.novel()
        self.assertTrue(second['generation_completed'])
        self.assertEqual([row['index'] for row in self.writer_calls],[1,2,3])

    def test_controls_game_input_and_saved_content_mutation_remain_protected(self):
        with self.assertRaisesRegex(RuntimeError,'PUBG'):
            self.album((1,RuntimeError('PUBG正在运行')))
        self.assertEqual(self.generated,[1])
        self.handle=Handle()
        first=self.novel((2,ValueError('mock format error')))
        path=Path(first['file']).with_name('chapters.json')
        chapters=store.load_json(path,[]);chapters[0]['content']='changed';store.save_json(path,chapters)
        with self.assertRaisesRegex(RuntimeError,'产物内容改变'):self.novel()
        self.story['title']='changed source'
        with self.assertRaisesRegex(RuntimeError,'输入已变化'):self.novel()


if __name__=='__main__':unittest.main()
