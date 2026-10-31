# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Exercise backend lifecycle directly; no microphone or HTTP server is opened."""
import threading
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from fastapi import HTTPException
from backend import main as api


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.original_token = api.TOKEN
        api.TOKEN = "test-only-token"
        self.addCleanup(setattr, api, "TOKEN", self.original_token)
        api.state.update(state="stopped", model="small", language="en", error=None,
                         loaded_model_name="small", model_available=True, final_text="", partial_text="")
        api.audio_queue = deque()
        api.audio_stream = None
        api.worker = None
        api.stop_event = threading.Event()
        api.model = Mock()

    def test_missing_token_is_denied(self):
        with self.assertRaises(HTTPException) as context:
            api.require_auth("")
        self.assertEqual(context.exception.status_code, 401)

    def test_unicode_token_is_denied_without_server_error(self):
        with self.assertRaises(HTTPException) as context:
            api.require_auth("سلام")
        self.assertEqual(context.exception.status_code, 401)

    def test_loading_session_rejects_second_start(self):
        api.state["state"] = "loading"
        with self.assertRaises(HTTPException) as context:
            api.start(api.StartRequest(language="en", model="small"), api.TOKEN)
        self.assertEqual(context.exception.status_code, 409)

    def test_stop_does_not_claim_inference_has_finished(self):
        api.state["state"] = "processing"
        api.worker = Mock()
        api.worker.is_alive.return_value = True
        result = api.stop(api.TOKEN)
        self.assertEqual(result["state"], "stopping")
        api.worker.join.assert_called_once_with(timeout=1)

    def test_audio_backlog_is_bounded(self):
        api.state["state"] = "listening"
        for _ in range(api.MAX_QUEUED_BLOCKS + 5):
            api.audio_callback(np.zeros((1024, 1), dtype=np.float32), 1024, None, None)
        self.assertEqual(len(api.audio_queue), api.MAX_QUEUED_BLOCKS)
        self.assertTrue(api.stop_event.is_set())
        self.assertIn("fell behind", api.state["error"])

    def test_final_audio_is_flushed_once_and_released(self):
        api.audio_queue.append(np.ones(16000, dtype=np.float32)*.02)
        api.model.transcribe.return_value = ([SimpleNamespace(text=" final phrase ")], None)
        api.stop_event.set()
        api.transcribe_worker()
        self.assertEqual(api.state["final_text"], "final phrase")
        self.assertEqual(api.state["state"], "stopped")
        self.assertFalse(api.audio_queue)
        api.model.transcribe.assert_called_once()

    def test_model_failure_stops_and_closes_the_microphone(self):
        api.audio_queue.append(np.ones(16000, dtype=np.float32)*.02)
        api.model.transcribe.side_effect = RuntimeError("test decoder failure")
        stream = api.audio_stream = Mock()
        api.stop_event.set()
        api.transcribe_worker()
        self.assertIn("decoder failure", api.state["error"])
        self.assertEqual(api.state["state"], "stopped")
        stream.close.assert_called_once()

    def test_microphone_start_failure_closes_stream(self):
        stream = Mock()
        stream.start.side_effect = RuntimeError("test device unavailable")
        with patch.object(api.sd, "InputStream", return_value=stream):
            with self.assertRaises(HTTPException):
                api.start(api.StartRequest(language="en", model="small"), api.TOKEN)
        stream.close.assert_called_once()
        self.assertEqual(api.state["state"], "stopped")

    def test_stop_while_loading_does_not_start_a_microphone(self):
        api.model = None
        def loading(name):
            api.stop(api.TOKEN)
        with patch.object(api, "load_local_model", side_effect=loading), patch.object(api.sd, "InputStream") as stream:
            result = api.start(api.StartRequest(language="en", model="small"), api.TOKEN)
        self.assertEqual(result["state"], "stopped")
        stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
