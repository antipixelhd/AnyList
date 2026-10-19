"""Exercise the installed rotation policy on disposable logs, never real logs."""
import gzip
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('logrotate'), 'logrotate is required')
class RetentionTests(unittest.TestCase):
    def test_daily_rotation_keeps_a_week_and_expires_old_entries_even_when_idle(self):
        policy = Path(__file__).with_name('logrotate.conf').read_text()
        self.assertIn('daily', policy)
        self.assertIn('ifempty', policy)
        self.assertIn('maxage 30', policy)
        with tempfile.TemporaryDirectory(prefix='anylist-retention-') as name:
            root = Path(name)
            log = root / 'application.log'
            policy = re.sub(r'^.*?\{', str(log) + ' {', policy, count=1, flags=re.S)
            policy = re.sub(r'    postrotate\n.*?    endscript\n', '', policy, flags=re.S)
            policy = policy.replace('    su syslog adm\n', '').replace('0640 syslog adm', '0600')
            config = root / 'logrotate.conf'
            config.write_text(policy)
            log.write_text('original-test-entry\n')

            def contents():
                result = ''
                for path in root.glob('application.log*'):
                    result += gzip.open(path, 'rt').read() if path.suffix == '.gz' else path.read_text()
                return result

            for day in range(1, 31):
                subprocess.run(['logrotate', '--force', '--state', str(root / 'state'), str(config)],
                               check=True, capture_output=True, text=True)
                if day in (7, 29):
                    self.assertIn('original-test-entry', contents())
            self.assertNotIn('original-test-entry', contents())


if __name__ == '__main__':
    unittest.main()
