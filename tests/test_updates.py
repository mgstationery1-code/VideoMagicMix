import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import main


class UpdateLockTests(unittest.TestCase):
    def setUp(self):
        self.previous_lock = main.update_lock_held
        self.previous_render_count = main.active_render_count
        self.previous_tasks = main.transcription_tasks.copy()
        main.update_lock_held = False
        main.active_render_count = 0
        main.transcription_tasks.clear()

    def tearDown(self):
        main.update_lock_held = self.previous_lock
        main.active_render_count = self.previous_render_count
        main.transcription_tasks.clear()
        main.transcription_tasks.update(self.previous_tasks)

    def call_lock(self, action="acquire", token="test-token"):
        with patch.dict(os.environ, {"VMM_INSTANCE_TOKEN": "test-token"}):
            return main.manage_update_lock(action=action, token=token)

    def test_update_lock_requires_local_instance_token(self):
        with self.assertRaises(HTTPException) as error:
            self.call_lock(token="wrong")
        self.assertEqual(error.exception.status_code, 403)

    def test_update_lock_rejects_active_render(self):
        main.active_render_count = 1
        with self.assertRaises(HTTPException) as error:
            self.call_lock()
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("渲染", error.exception.detail)

    def test_update_lock_rejects_active_transcription(self):
        main.transcription_tasks["task"] = {"status": "running"}
        with self.assertRaises(HTTPException) as error:
            self.call_lock()
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("识别", error.exception.detail)

    def test_render_is_rejected_while_update_lock_is_held(self):
        self.call_lock()
        guarded_render = main.render_guard(lambda: "rendered")
        with self.assertRaises(HTTPException) as error:
            guarded_render()
        self.assertEqual(error.exception.status_code, 409)
        self.call_lock(action="release")


if __name__ == "__main__":
    unittest.main()
