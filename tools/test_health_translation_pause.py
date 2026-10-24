import os,subprocess,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).parent
SCRIPT=HERE/'health_check.sh'
BLOCK=SCRIPT.read_text().split('# 3b. Translation pipeline alive?',1)[1].split('# 4. Latest deploy failed?',1)[0]
class PauseTests(unittest.TestCase):
 def run_check(self,state,heartbeat='old',queued=50,error=False):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'.venv/bin').mkdir(parents=True)
   driver=p/'.venv/bin/python';driver.write_text('#!/bin/bash\ntouch queue-read\necho "queue: remaining: '+str(queued)+'"\n');driver.chmod(0o700)
   hb=p/'Library/Application Support/qdaily/translate_last_run';hb.parent.mkdir(parents=True)
   if heartbeat!='missing':hb.write_text(str(1000000-(263*3600 if heartbeat=='old' else 100))+'\n')
   script='launchctl(){ printf "%s\\n" "$FIXTURE_STATE"; return "$FIXTURE_EXIT"; }\nid(){ echo 501; }\ndate(){ if [ "$1" = +%s ]; then echo 1000000; else echo fixture-time; fi; }\nadd(){ printf "ALERT-CANDIDATE: %s\\n" "$1"; }\n# 3b. Translation pipeline alive?'+BLOCK
   r=subprocess.run(['/bin/bash','-c',script],cwd=p,env={**os.environ,'HOME':d,'FIXTURE_STATE':state,'FIXTURE_EXIT':str(int(error))},capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stderr)
   return r.stdout,(p/'queue-read').exists()
 def test_disabled_skips_queue_and_stale_alert(self):
  for state in ('"org.qdaily.translate-cron" => disabled','"org.qdaily.translate-cron" => true'):
   out,read=self.run_check(state);self.assertIn('intentionally paused',out);self.assertNotIn('ALERT-CANDIDATE',out);self.assertFalse(read)
 def test_disabled_missing_heartbeat_is_also_quiet(self):
  out,read=self.run_check('"org.qdaily.translate-cron" => disabled','missing');self.assertNotIn('ALERT-CANDIDATE',out);self.assertFalse(read)
 def test_enabled_stale_job_still_alerts(self):
  for state in ('"org.qdaily.translate-cron" => enabled','"org.qdaily.translate-cron" => false','{}'):
   out,read=self.run_check(state);self.assertIn('263 h ago (50 articles still queued)',out);self.assertTrue(read)
 def test_unrelated_disabled_job_does_not_suppress(self):
  out,_=self.run_check('"org.qdaily.health-check" => disabled');self.assertIn('Translation cron stalled',out)
 def test_status_error_does_not_suppress(self):
  for state in ('','\"org.qdaily.translate-cron\" => disabled'):
   out,_=self.run_check(state,error=True);self.assertIn('Translation cron stalled',out)
 def test_enabled_missing_heartbeat_still_alerts(self):
  out,_=self.run_check('{}','missing');self.assertIn('never recorded',out)
 def test_enabled_recent_or_empty_queue_does_not_alert(self):
  for heartbeat,queued in (('recent',50),('old',0)):
   out,_=self.run_check('{}',heartbeat,queued);self.assertNotIn('ALERT-CANDIDATE',out)
if __name__=='__main__':unittest.main()
