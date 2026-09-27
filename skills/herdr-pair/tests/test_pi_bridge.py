"""Deterministic fake Pi compact events and installation-gate checks."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pi_compact_bridge", ROOT / "scripts/pi_compact_bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class PiBridgeTest(unittest.TestCase):
    def test_only_success_event_advances_and_injects_checkpoint(self):
        status = {"planner_pane": "pane-a", "session_id": "session-a",
                  "compact_queued": {"mode": "compact"}, "checkpoint": "/tmp/CHECKPOINT.md"}
        rolled = {"status": "rollover_recorded", "compaction_epoch": 2}
        with patch.object(bridge, "call", side_effect=[status, rolled]) as call, \
             patch.object(bridge, "checkpoint_context", return_value="checkpoint summary"):
            result = bridge.bridge("/work", "pane-a", "session-a", "session_compact")
        self.assertEqual(result["compaction_epoch"], 2)
        self.assertIn("checkpoint summary", result["context"])
        self.assertEqual(call.call_args_list[-1].args[1:5],
                         ("rollover", "--reason", "compact", "--new-session-id"))

    def test_pre_failure_mismatch_and_duplicate_are_noops(self):
        status = {"planner_pane": "pane-a", "session_id": "session-a",
                  "compact_queued": {"mode": "compact"}}
        for event in ("session_before_compact", "session_compact_failed"):
            with patch.object(bridge, "call") as call:
                self.assertEqual(bridge.bridge("/work", "pane-a", "session-a", event)["status"], "ignored")
                call.assert_not_called()
        for candidate in ({**status, "planner_pane": "other"},
                          {**status, "session_id": "other"},
                          {**status, "compact_queued": None}):
            with patch.object(bridge, "call", return_value=candidate) as call:
                self.assertEqual(bridge.bridge("/work", "pane-a", "session-a", "session_compact")["status"], "ignored")
                self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
