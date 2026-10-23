import builtins
import contextlib
import io
import unittest
from unittest.mock import patch

import windows_launcher


class LauncherTests(unittest.TestCase):
    def test_missing_dependency_stops_before_credential_prompt(self):
        real_import = builtins.__import__
        def missing_fastapi(name, *args, **kwargs):
            if name == 'app.main':
                raise ModuleNotFoundError("No module named 'fastapi'", name='fastapi')
            return real_import(name, *args, **kwargs)
        output = io.StringIO()
        with patch('builtins.__import__', side_effect=missing_fastapi), patch('builtins.input') as prompt, patch('getpass.getpass') as secret, contextlib.redirect_stdout(output):
            self.assertEqual(windows_launcher.main(), 1)
        prompt.assert_not_called()
        secret.assert_not_called()
        self.assertIn('setup_windows.cmd', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())
