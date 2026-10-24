"""Deterministic freshness and shell-integration tests; no external actions."""

from datetime import date
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from check_todays_pick import pick_problem


TOOLS = Path(__file__).resolve().parent
TODAY = date(2026, 10, 24)


class PickFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.output = self.root / "data/daily_picks.json"
        self.legacy_log = self.root / "data/todays_pick.claude.log"
        self.legacy_log.write_text("Failed to authenticate. API Error: 401 OAuth access token has expired.\n")

    def edition(self, value):
        self.output.write_text(json.dumps(value))

    def test_current_codex_output_ignores_old_claude_failure(self):
        self.edition({"date": str(TODAY), "editor_id": "codex-v1"})
        self.assertIsNone(pick_problem(self.root, TODAY))

    def test_stale_codex_output_alerts_without_provider_diagnosis(self):
        self.edition({"date": "2026-10-18", "editor_id": "codex-v1"})
        original = self.legacy_log.read_bytes()
        for timestamp in (0, 2_000_000_000):
            with self.subTest(log_mtime=timestamp):
                os.utime(self.legacy_log, (timestamp, timestamp))
                message = pick_problem(self.root, TODAY)
                self.assertIn("local output is stale", message)
                self.assertIn("2026-10-18", message)
                self.assertIn("2026-10-24", message)
                self.assertIn("Codex task qdaily-today-s-pick", message)
                for unsupported in ("401", "OAuth", "Claude", "authenticate", "did not run", "published"):
                    self.assertNotIn(unsupported, message)
        self.assertEqual(original, self.legacy_log.read_bytes())

    def test_current_date_only_proves_freshness_not_provider_or_publication(self):
        for editor in (None, "legacy-editor", "codex-v1"):
            with self.subTest(editor=editor):
                self.edition({"date": str(TODAY), "editor_id": editor})
                self.assertIsNone(pick_problem(self.root, TODAY))

    def test_missing_output_is_reported(self):
        self.assertIn("local output is missing", pick_problem(self.root, TODAY))

    def test_unreadable_output_is_reported_without_traceback(self):
        for value in (b"not json", b"\xff"):
            with self.subTest(value=value):
                self.output.write_bytes(value)
                message = pick_problem(self.root, TODAY)
                self.assertIn("local output is unreadable", message)
                self.assertNotIn("Traceback", message)

    def test_invalid_schema_and_dates_are_reported(self):
        for value in ([], None, {}, {"date": None}, {"date": 20261024},
                      {"date": ""}, {"date": "2026-02-30"}, {"date": "20261024"},
                      {"date": "2026-W43-6"}, {"date": "2026-10-24\n401"}):
            with self.subTest(value=value):
                self.edition(value)
                message = pick_problem(self.root, TODAY)
                self.assertIn("invalid date", message)
                self.assertNotIn("401", message)

    def test_future_output_is_not_treated_as_current(self):
        self.edition({"date": "2026-10-25"})
        self.assertIn("future date", pick_problem(self.root, TODAY))


class HealthCheckIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project with spaces"
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        for path in (self.project / "data", self.project / "tools",
                     self.project / ".venv/bin", self.home, self.bin):
            path.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TOOLS / "check_todays_pick.py", self.project / "tools/check_todays_pick.py")
        self.legacy = self.project / "data/todays_pick.claude.log"
        self.legacy.write_text("Failed to authenticate. API Error: 401 OAuth access token has expired.\n")
        (self.project / "data/wayback_submitted.txt").write_text("fixture\n")
        self.notifications = self.root / "notifications"
        self.env = {**os.environ, "HOME": str(self.home), "NOTIFY_FILE": str(self.notifications),
                    "FIXTURE_NOW": "2026-10-24T03:00:00+00:00", "FIXTURE_QUEUE": "0",
                    "FIXTURE_DEPLOY": "success", "FIXTURE_HTTP": "200", "FIXTURE_JOBS": ""}
        self.stub("date", "#!/bin/bash\nexec " + shlex.quote(sys.executable) + " - \"$@\" <<'PY_DATE'\n" +
                  "from datetime import datetime\nfrom zoneinfo import ZoneInfo\nimport os,sys\n" +
                  "now=datetime.fromisoformat(os.environ['FIXTURE_NOW'])\n" +
                  "fmt=sys.argv[-1].lstrip('+')\n" +
                  "print(int(now.timestamp()) if fmt == '%s' else now.astimezone(ZoneInfo(os.environ.get('TZ', 'UTC'))).strftime(fmt))\nPY_DATE\n")
        self.stub("launchctl", '#!/bin/bash\nprintf "%s\\n" "$FIXTURE_JOBS"\n')
        self.stub("pgrep", "#!/bin/bash\nexit 0\n")
        self.stub("gh", '#!/bin/bash\nprintf "%s\\n" "$FIXTURE_DEPLOY"\n')
        self.stub("curl", '#!/bin/bash\nprintf "%s" "$FIXTURE_HTTP"\n')
        self.stub("osascript", '#!/bin/bash\nprintf "%s\\n" "$*" >> "$NOTIFY_FILE"\n')
        queue = self.project / ".venv/bin/python"
        queue.write_text('#!/bin/bash\nprintf "queue: remaining: %s\\n" "$FIXTURE_QUEUE"\n')
        queue.chmod(0o755)
        source = (TOOLS / "health_check.sh").read_text()
        original_path = 'export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"'
        original_root = 'PROJ="/Volumes/iMac 1TB/Projects-Ext/qdaily_full_backup"'
        self.assertEqual(source.count(original_path), 1)
        self.assertEqual(source.count(original_root), 1)
        # Only relocate paths in this disposable fixture; execute all actual sections.
        source = source.replace(original_path, "export PATH=" + shlex.quote(str(self.bin) + os.pathsep + os.environ["PATH"]))
        source = source.replace(original_root, "PROJ=" + shlex.quote(str(self.project)))
        self.script = self.root / "health_check.sh"
        self.script.write_text(source)

    def stub(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def edition(self, day):
        (self.project / "data/daily_picks.json").write_text(json.dumps({"date": day, "editor_id": "codex-v1"}))

    def run_check(self):
        result = subprocess.run(["/bin/bash", str(self.script)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result.stdout

    def test_current_codex_output_does_not_send_legacy_auth_alert(self):
        self.edition("2026-10-24")
        output = self.run_check()
        self.assertIn("all healthy", output)
        self.assertFalse(self.notifications.exists())

    def test_stale_output_still_alerts_and_same_problem_deduplicates(self):
        self.edition("2026-10-18")
        output = self.run_check()
        self.assertIn("local output is stale", output)
        self.assertNotIn("401", output)
        first = self.notifications.read_text()
        self.assertIn("Codex task qdaily-today-s-pick", first)
        self.assertNotIn("OAuth", first)
        self.assertIn("already alerted today", self.run_check())
        self.assertEqual(first, self.notifications.read_text())

    def test_original_grace_period_is_preserved(self):
        self.edition("2026-10-18")
        self.env["FIXTURE_NOW"] = "2026-10-24T02:45:00+00:00"  # 10:45 Shanghai
        self.assertIn("all healthy", self.run_check())
        self.assertFalse(self.notifications.exists())

    def test_shanghai_calendar_date_is_used(self):
        self.edition("2026-10-24")
        # UTC is still Oct 24, Shanghai has crossed midnight. Grace period suppresses
        # an early alert; the same local date must be accepted at the first eligible hour.
        self.env["FIXTURE_NOW"] = "2026-10-24T16:30:00+00:00"
        self.assertIn("all healthy", self.run_check())
        self.assertFalse(self.notifications.exists())
        self.env["FIXTURE_NOW"] = "2026-10-25T03:00:00+00:00"
        output = self.run_check()
        self.assertIn("expected 2026-10-25", output)

    def test_unrelated_failure_alerts_are_not_suppressed(self):
        self.edition("2026-10-24")
        self.env.update(FIXTURE_DEPLOY="failure", FIXTURE_HTTP="503",
                        FIXTURE_JOBS="- 7 org.qdaily.example")
        output = self.run_check()
        for failure in ("deploy FAILED", "HTTP 503", "org.qdaily.example failing (exit 7)"):
            self.assertIn(failure, output)
        self.assertNotIn("Today's Pick", self.notifications.read_text())

    def test_existing_translation_check_is_unchanged(self):
        self.edition("2026-10-24")
        self.env["FIXTURE_QUEUE"] = "50"
        output = self.run_check()
        self.assertIn("Translation cron has never recorded a completed batch", output)
        self.assertNotIn("Today's Pick", output)

    def test_checker_failure_remains_visible_without_provider_guess(self):
        (self.project / "tools/check_todays_pick.py").unlink()
        output = self.run_check()
        self.assertIn("freshness check could not complete", output)
        self.assertNotIn("OAuth", output)


if __name__ == "__main__":
    unittest.main()
