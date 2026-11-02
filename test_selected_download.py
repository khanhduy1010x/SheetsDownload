"""Selected videos use the existing downloader and completed-download history."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import selected_download
from downloader import DownloadHistory
from video_queue import SelectedVideoQueue


class SelectedDownloadTests(unittest.TestCase):
    def test_download_records_history_and_skips_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state"
            output = Path(folder) / "videos"
            queue = SelectedVideoQueue(state)
            queue.add([{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "Selected"}], "download")
            events = []

            def fake_download(details, target, video_id, progress, output_format):
                path = target / f"{video_id} Selected.mp4"
                path.write_bytes(b"video")
                return path

            details = {"id": "dQw4w9WgXcQ", "title": "Selected", "estimated_bytes": 1}
            with patch.object(selected_download, "get_state_dir", return_value=state), \
                 patch.object(selected_download, "video_details", return_value=details), \
                 patch.object(selected_download, "download_video", side_effect=fake_download) as download:
                selected_download.run_selected_queue(queue, output, events.append, lambda: False)
                self.assertEqual(queue.list()[0]["status"], "done")
                self.assertIn("dQw4w9WgXcQ", DownloadHistory(state).downloaded_ids())
                self.assertEqual(download.call_count, 1)
                queue.remove(["dQw4w9WgXcQ"])
                queue.add([{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "Selected"}], "download")
                selected_download.run_selected_queue(queue, output, events.append, lambda: False)
                self.assertEqual(queue.list()[0]["status"], "skipped")
                self.assertEqual(download.call_count, 1)


if __name__ == "__main__":
    unittest.main()
