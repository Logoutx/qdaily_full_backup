import importlib.util,json,tempfile,unittest,shutil
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('runner',Path(__file__).with_name('translate_codex.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.tr=self.root/'data/translations'
  (self.tr/'in').mkdir(parents=True);(self.root/'tools').mkdir()
  shutil.copyfile(Path(__file__).with_name('translate_qa.py'),self.root/'tools/translate_qa.py')
  self.src={'id':1,'title':'测试','body':'测试内容。'*100+'\n![](https://example.invalid/a)'}
  (self.tr/'in/1.json').write_text(json.dumps(self.src))
  for f in ['STYLE.md','glossary.json','grok-review.md']:(self.tr/f).write_text('fixture')
  self.good={'title':'A sample','excerpt':'Excerpt','body':'A complete sentence about a sample. '*30+'\n![](https://example.invalid/a)'}
 def tearDown(self):self.tmp.cleanup()
 def test_success_and_idempotency(self):
  with patch.object(m,'call',return_value=self.good) as call, patch.object(m,'review_grok',return_value={'findings':[]}):
   self.assertEqual(m.translate(self.root,'1'),'ok');self.assertEqual(m.translate(self.root,'1'),'existing');self.assertEqual(call.call_count,1)
 def test_image_url_rejection(self):
  bad={**self.good,'body':self.good['body'].replace('/a)','/b)')}
  with patch.object(m,'call',return_value=bad) as call:
   self.assertEqual(m.translate(self.root,'1'),'failed');self.assertEqual(call.call_count,2);self.assertFalse((self.tr/'en/1.json').exists())
 def test_fidelity_repair(self):
  finding={'severity':'HIGH','class':'polarity','zh':'x','en':'y','reason':'reversed'}
  with patch.object(m,'call',return_value=self.good), patch.object(m,'review_grok',side_effect=[{'findings':[finding]},{'findings':[]}]):self.assertEqual(m.translate(self.root,'1'),'ok')
 def test_provider_failure(self):
  with patch.object(m,'call',side_effect=m.ProviderError('quota')):
   with self.assertRaises(m.ProviderError):m.translate(self.root,'1')
  self.assertFalse((self.tr/'en/1.json').exists())
 def test_no_api_keys(self):
  with patch.dict(m.os.environ,{'CODEX_API_KEY':'fixture','OPENAI_API_KEY':'fixture'}):self.assertNotIn('CODEX_API_KEY',m.environment());self.assertNotIn('OPENAI_API_KEY',m.environment())
 def test_api_auth_rejected(self):
  import subprocess
  with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'Logged in using an API key','')):
   with self.assertRaises(m.ProviderError):m.check_auth()
 def test_invalid_review_rejected(self):
  with patch.object(m,'call',return_value=self.good),patch.object(m,'review_grok',return_value={'findings':[{'severity':'maybe'}]}):
   with self.assertRaises(m.ProviderError):m.translate(self.root,'1')
  self.assertFalse((self.tr/'en/1.json').exists())
 def test_grok_failure_blocks_promotion(self):
  with patch.object(m,'call',return_value=self.good),patch.object(m,'review_grok',side_effect=m.ProviderError('authentication')):
   with self.assertRaises(m.ProviderError):m.translate(self.root,'1')
  self.assertFalse((self.tr/'en/1.json').exists())
 def test_candidate_reused_after_grok_failure(self):
  with patch.object(m,'call',return_value=self.good) as call,patch.object(m,'review_grok',side_effect=[m.ProviderError('network'),{'findings':[]}]):
   with self.assertRaises(m.ProviderError):m.translate(self.root,'1')
   self.assertEqual(m.translate(self.root,'1'),'ok');self.assertEqual(call.call_count,1)
 def test_failed_quality_deferred_without_losing_existing_state(self):
  (self.tr/'defer.json').write_text('[99]');(self.tr/'needs-review.json').write_text('{"99":["prior"]}')
  bad={**self.good,'body':'short'}
  with patch.object(m,'call',return_value=bad):self.assertEqual(m.translate(self.root,'1'),'failed')
  self.assertEqual(json.loads((self.tr/'defer.json').read_text()),[99,'1'])
  self.assertEqual(json.loads((self.tr/'needs-review.json').read_text())['99'],['prior'])
  self.assertIn('1',json.loads((self.tr/'needs-review.json').read_text()))
 def test_batch_continues_after_quality_failure(self):
  with patch.object(m,'check_auth'),patch.object(m,'translate',side_effect=['failed','ok']) as translated,patch('sys.argv',['runner','--root',str(self.root),'1','2']):
   self.assertEqual(m.main(),1);self.assertEqual(translated.call_count,2)
 def test_explicit_hold_wins_over_existing_final(self):
  (self.tr/'held/1').mkdir(parents=True);(self.tr/'en').mkdir()
  (self.tr/'en/1.json').write_text(json.dumps(self.good));(self.tr/'en/2.json').write_text(json.dumps(self.good))
  with patch.object(m,'call') as call,patch.object(m,'review_grok') as review:
   self.assertEqual(m.translate(self.root,'1'),'held');call.assert_not_called();review.assert_not_called()
  self.assertEqual([p.stem for p in m.final_output_paths(self.tr)],['2'])
 def test_legacy_collection_cannot_repromote_held_article(self):
  spec=importlib.util.spec_from_file_location('collector',Path(__file__).with_name('translate_collect.py'));c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
  (self.tr/'held/1').mkdir(parents=True);(self.tr/'out').mkdir()
  draft=self.tr/'out/1.txt';draft.write_text('@@QD_TITLE@@\nTitle\n@@QD_BODY@@\n'+self.good['body'])
  c.TROOT=self.tr;c.OUT=self.tr/'out';c.IN=self.tr/'in';c.EN=self.tr/'en'
  self.assertEqual(c.main(),0);self.assertTrue(draft.exists());self.assertFalse((self.tr/'en/1.json').exists())
 def test_queue_excludes_hold_without_defer_entry(self):
  import types,sys
  (self.tr/'held/1').mkdir(parents=True)
  modules={'validate_corpus':types.SimpleNamespace(load_corpus=lambda _:([{'id':1},{'id':2}],None,None)), 'html_to_md':types.SimpleNamespace(html_to_md=lambda x:x,count_images=lambda x:0), 'render':types.SimpleNamespace()}
  with patch.dict(sys.modules,modules):
   spec=importlib.util.spec_from_file_location('todo',Path(__file__).with_name('translate_todo.py'));todo=importlib.util.module_from_spec(spec);spec.loader.exec_module(todo)
  todo.TROOT=self.tr;todo.IN=self.tr/'in';todo.EN=self.tr/'en'
  with patch.object(todo,'build_queue',return_value=[(1,'longform'),(2,'longform')]),patch.object(todo,'materialize',return_value=0) as materialize,patch('sys.argv',['todo','--limit','20','--emit']):
   self.assertEqual(todo.main(),0);self.assertEqual(materialize.call_count,1);self.assertEqual(materialize.call_args.args[0]['id'],2)
 def test_truncation(self):self.assertIn('truncated body',m.validate(self.src,{**self.good,'body':'short'}))
if __name__=='__main__':unittest.main()
