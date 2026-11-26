from pathlib import Path
import os,subprocess,tempfile,unittest
block=Path(__file__).with_name("health_check.sh").read_text().split("# 5. Site up?",1)[1].split("# Dedup",1)[0]
class SiteProbe(unittest.TestCase):
 def probe(self,*cases):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d); (p/"answers").write_text("\n".join(cases)+"\n")
   result=subprocess.run(["/bin/bash","-c",'''
curl(){ n=$(cat "$STATE/count" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$STATE/count"; line=$(sed -n "${n}p" "$STATE/answers"); echo "${line%:*}"; return "${line##*:}"; }
sleep(){ :; }
add(){ printf '%s\\n' "$1"; }
# 5. Site up?'''+block],env={**os.environ,"STATE":d},capture_output=True,text=True,check=True)
   return int((p/"count").read_text()),result.stdout.strip()
 def test_success(self): self.assertEqual(self.probe("200:0"),(1,""))
 def test_transient_failure(self): self.assertEqual(self.probe("000:28","200:0"),(2,""))
 def test_persistent_transport_failure(self): self.assertEqual(self.probe("000:28","000:6"),(2,"Could not reach www.qdaily.org from this Mac after two attempts (curl exit 6; no usable HTTP response)."))
 def test_persistent_http_failure(self): self.assertEqual(self.probe("503:0","503:0"),(2,"www.qdaily.org returned HTTP 503 after two attempts."))
 def test_real_http_recovers(self): self.assertEqual(self.probe("503:0","204:0"),(2,""))
if __name__=="__main__": unittest.main()
