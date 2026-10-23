import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import law_monitor as app


class MonitorTests(unittest.TestCase):
    def test_baseline_change_and_order(self):
        db = app.connect(':memory:')
        self.assertEqual(app.record(db, '법', [{'a': '1'}, {'a': '2'}]), '기준 생성')
        self.assertEqual(app.record(db, '법', [{'a': '2'}, {'a': '1'}]), '변경 없음')
        self.assertEqual(app.record(db, '법', [{'a': '3'}]), '변경 감지')
        db.close()

    def test_api_exact_match_and_single_result(self):
        payload = {'LawSearch': {'totalCnt': '1', 'law': {'법령명한글': '산업안전보건법', '시행일자': '20260101'}}}
        response = io.BytesIO(json.dumps(payload).encode())
        with patch('urllib.request.urlopen', return_value=response):
            rows = app.fetch('산업안전보건법', 3, 'test')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['구분'], '현행')

    def test_invalid_response_rejected(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{}')):
            with self.assertRaises(RuntimeError):
                app.fetch('법', 3, 'test')

    def test_cli_check_export_and_failure_keeps_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'db.sqlite3')
            output = Path(tmp) / 'result.csv'
            def run(*args):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    return app.main(['--db', path, *args])
            self.assertEqual(run('add', '산업안전보건법'), 0)
            rows = [{'구분': '현행', '법령명한글': '산업안전보건법', '시행일자': '20260101'}]
            with patch.dict('os.environ', LAW_OC='test'), patch.object(app, 'fetch', side_effect=[rows, []]):
                self.assertEqual(run('check', '--csv', str(output)), 0)
            self.assertIn('산업안전보건법', output.read_text(encoding='utf-8-sig'))
            with patch.dict('os.environ', LAW_OC='test'), patch.object(app, 'fetch', side_effect=RuntimeError('실패')):
                self.assertEqual(run('check'), 1)
            db = app.connect(path)
            self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 1)
            db.close()

    def test_missing_oc_fails(self):
        with patch.dict('os.environ', LAW_OC=''), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(['--db', ':memory:', 'check']), 1)


if __name__ == '__main__':
    unittest.main()
