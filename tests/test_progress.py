import io
import unittest

from kta.progress import ProgressReporter


class ProgressReporterTests(unittest.TestCase):
    def test_redirected_progress_emits_readable_stage_lines(self):
        output = io.StringIO()
        reporter = ProgressReporter(stream=output)

        reporter.update("Scanning market data 1/2: TEST1")
        reporter.update("Scout evaluating 2 triggered signals")
        reporter.finish("Run complete", success=True)

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "[kta] Scanning market data 1/2: TEST1",
                "[kta] Scout evaluating 2 triggered signals",
                "[kta] ✓ Run complete",
            ],
        )


if __name__ == "__main__":
    unittest.main()
