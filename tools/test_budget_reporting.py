import contextlib,io,json,os,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import translate_codex as m

class BudgetReporting(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  tr=self.root/'data/translations';(tr/'en').mkdir(parents=True)
  (tr/'defer.json').write_text('[99]');(tr/'needs-review.json').write_text('{"99":["existing"]}')
 def tearDown(self):self.temp.cleanup()
 def run_batch(self,ticks,results,ids=('1','2','3')):
  output=io.StringIO()
  def translated(root,rid):
   result=next(results)
   if isinstance(result,Exception):raise result
   if result=='ok':(root/'data/translations/en'/f'{rid}.json').write_text('{"fixture":"approved"}')
   return result
  with patch.object(m,'check_auth'),patch.object(m.time,'monotonic',side_effect=ticks),patch.object(m,'translate',side_effect=translated) as called,patch('sys.argv',['runner','--root',str(self.root),*ids]),contextlib.redirect_stdout(output):
   code=m.main()
  return code,output.getvalue(),called.call_count
 def test_budget_after_approved_output(self):
  code,out,calls=self.run_batch([0,0,14401],iter(['ok']))
  self.assertEqual((code,calls),(2,1));self.assertIn('ok=1 failed=0 deferred=0 kimi=0 total_en=1',out)
  self.assertIn('status=partial reason=time_budget remaining=2',out)
  self.assertTrue((self.root/'data/translations/en/1.json').exists())
  self.assertEqual((self.root/'data/translations/defer.json').read_text(),'[99]')
  self.assertFalse((self.root/'data/translations/en/2.json').exists())
 def test_budget_with_zero_outputs(self):
  code,out,calls=self.run_batch([0,14401],iter([]))
  self.assertEqual((code,calls),(2,0));self.assertIn('ok=0 failed=0 deferred=0',out)
  self.assertIn('status=partial reason=time_budget remaining=3',out)
 def test_budget_preserves_quality_deferral_counts(self):
  code,out,calls=self.run_batch([0,0,0,14401],iter(['failed','ok']))
  self.assertEqual((code,calls),(2,2));self.assertIn('ok=1 failed=1 deferred=1',out)
  self.assertIn('status=partial reason=time_budget remaining=1',out)
 def test_provider_failure_is_not_budget_stop(self):
  for approved in (False,True):
   with self.subTest(approved=approved):
    results=iter(['ok',m.ProviderError('fixture provider unavailable')]) if approved else iter([m.ProviderError('fixture provider unavailable')])
    code,out,calls=self.run_batch([0,0,0] if approved else [0,0],results)
    self.assertEqual(code,1);self.assertIn('FAIL: fixture provider unavailable',out)
    self.assertIn('status=failed reason=operational_error remaining='+('2' if approved else '3'),out)
    self.assertIn('ok='+str(int(approved))+' failed=0',out);self.assertNotIn('status=partial',out)
 def test_complete_run(self):
  code,out,calls=self.run_batch([0,0,0,0],iter(['ok','existing','ok']))
  self.assertEqual(code,0);self.assertIn('status=complete reason=queue_processed remaining=0',out)
 def test_all_processed_with_quality_deferrals(self):
  code,out,calls=self.run_batch([0,0,0,0],iter(['failed','ok','held']))
  self.assertEqual(code,1);self.assertIn('failed=1 deferred=2',out)
  self.assertIn('status=completed_with_deferrals reason=quality_review remaining=0',out)

class LauncherReporting(unittest.TestCase):
 def run_launcher(self,status,result,state):
  source=Path(__file__).with_name('translate_cron.sh').read_text()
  block=source.split('# Outcome handling:',1)[1].split('# Overrun detection.',1)[0]
  with tempfile.TemporaryDirectory() as d:
   heartbeat=Path(d)/'heartbeat'
   env={**os.environ,'RUN_STATUS':str(status),'RESULT':result,'OUT':result+'\n'+state,'HEARTBEAT':str(heartbeat),'LOG':'fixture-log'}
   # Execute the exact outcome block with inert notification/log fixtures.
   script='set -o pipefail\nlog(){ printf "LOG %s\\n" "$*"; }\nalert(){ printf "ALERT %s\\n" "$*"; }\n# Outcome handling:'+block+'\nexit 0\n'
   p=subprocess.run(['/bin/bash','-c',script],env=env,capture_output=True,text=True)
   return p.returncode,p.stdout,heartbeat.read_text() if heartbeat.exists() else None
 def test_partial_summary_is_persisted_even_with_zero_outputs(self):
  for approved in (0,8):
   result=f'BATCH-RESULT: ok={approved} failed=4 deferred=4 kimi=20 total_en={2358+approved}'
   state='BATCH-STATE: status=partial reason=time_budget remaining=8'
   code,out,hb=self.run_launcher(2,result,state)
   self.assertEqual(code,2);self.assertIn('PARTIAL:',out);self.assertIn(result,hb);self.assertIn(state,hb);self.assertNotIn('FAIL:',out)
 def test_provider_failure_cannot_be_disguised_as_partial(self):
  result='BATCH-RESULT: ok=1 failed=0 deferred=0 kimi=2 total_en=1'
  for state in ('BATCH-STATE: status=failed reason=operational_error remaining=1','BATCH-STATE: status=partial reason=time_budget remaining=1'):
   code,out,hb=self.run_launcher(1,result,state)
   self.assertEqual(code,1);self.assertIn('FAIL:',out);self.assertIn(result,out);self.assertIsNone(hb)
 def test_missing_summary_or_state_is_failure(self):
  for status,result,state in ((2,'','BATCH-STATE: status=partial reason=time_budget remaining=1'),(2,'BATCH-RESULT: ok=0 failed=0 deferred=0 kimi=0 total_en=0',''),(0,'BATCH-RESULT: ok=0 failed=0 deferred=0 kimi=0 total_en=0','')):
   code,out,hb=self.run_launcher(status,result,state)
   self.assertEqual(code,1);self.assertIn('FAIL:',out);self.assertIsNone(hb)
 def test_complete_summary_has_explicit_complete_state(self):
  result='BATCH-RESULT: ok=1 failed=0 deferred=0 kimi=1 total_en=1'
  state='BATCH-STATE: status=complete reason=queue_processed remaining=0'
  code,out,hb=self.run_launcher(0,result,state)
  self.assertEqual(code,0);self.assertIn(state,hb);self.assertNotIn('PARTIAL:',out)

if __name__=='__main__':unittest.main()
