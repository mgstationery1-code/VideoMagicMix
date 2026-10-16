import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.runtime import asr_worker_command, resource_root


class RuntimeTests(unittest.TestCase):
    def test_frozen_worker_dispatches_same_executable(self):
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(asr_worker_command("voice.wav", "result.json"),
                             [sys.executable, "--asr-worker", "voice.wav", "result.json"])

    def test_source_worker_uses_current_python(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(asr_worker_command("voice.wav", "result.json"),
                             [sys.executable, "-m", "backend.asr_worker", "voice.wav", "result.json"])

    def test_resource_directory_override_supports_spaces(self):
        with tempfile.TemporaryDirectory(prefix="VMM space ") as directory:
            with patch.dict("os.environ", {"VMM_RESOURCE_DIR": directory}):
                self.assertEqual(resource_root(), Path(directory).resolve())
