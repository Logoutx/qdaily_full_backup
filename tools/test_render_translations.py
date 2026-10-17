import hashlib,json,tempfile,unittest
from pathlib import Path
import render_translations as m
class PublicationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.tr=self.root/'data/translations'
  for d in ('en','in'): (self.tr/d).mkdir(parents=True)
  record={'id':1,'title':'Sample article','excerpt':'Sample excerpt','body':'A complete sample sentence. '*50+'\n![](https://example.invalid/image)','src_n_images':1}
  (self.tr/'en/1.json').write_text(json.dumps(record));(self.tr/'in/1.json').write_text(json.dumps({'body':'示例。'*100+'\n![](https://example.invalid/image)'}))
  self.manifest=self.tr/'published.json';self.manifest.write_text(json.dumps({'files':{'1':hashlib.sha256((self.tr/'en/1.json').read_bytes()).hexdigest()}}))
 def tearDown(self):self.tmp.cleanup()
 def test_only_manifest_files_are_published(self):
  (self.tr/'en/2.json').write_text('invalid unapproved file')
  self.assertEqual([r['id'] for r in m.approved(self.root,self.manifest)],[1])
 def test_changed_translation_fails_closed(self):
  with (self.tr/'en/1.json').open('a') as f:f.write(' ')
  with self.assertRaises(ValueError):m.approved(self.root,self.manifest)
 def test_held_deferred_and_needs_review_fail_closed(self):
  for name in ('defer.json','needs-review.json'):
   p=self.tr/name;p.write_text('["1"]' if name=='defer.json' else '{"1":["held"]}')
   with self.assertRaises(ValueError):m.approved(self.root,self.manifest)
   p.unlink()
  (self.tr/'held/1').mkdir(parents=True)
  with self.assertRaises(ValueError):m.approved(self.root,self.manifest)
 def test_active_html_removed(self):
  html=m.safe_html('<script>alert(1)</script><img src="javascript:alert(1)" onerror="alert(1)"><p>Kept</p>')
  self.assertNotIn('script',html);self.assertNotIn('onerror',html);self.assertIn('Kept',html)
if __name__=='__main__':unittest.main()
