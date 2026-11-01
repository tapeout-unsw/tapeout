import unittest

from tapeout_host.runs import RunRecord


class RunRecordTests(unittest.TestCase):
    def test_event_stream_delivers_result_and_finishes(self):
        record = RunRecord("id", "binary", "x.bin", None)
        record.emit("uart", {"text": "ok"})
        record.finish("passed", {"completed": True})
        self.assertEqual(list(record.stream()), record.events)
        self.assertEqual(record.state, "passed")
        self.assertEqual(record.result, {"completed": True})
        self.assertEqual(record.events[-1]["type"], "result")

    def test_finish_rejects_nonterminal_state(self):
        record = RunRecord("id", "binary", "x.bin", None)
        with self.assertRaisesRegex(ValueError, "terminal state"):
            record.finish("running", {})


if __name__ == "__main__":
    unittest.main()
