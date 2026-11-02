import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import downloader as d


class FiltersAndFailuresTests(unittest.TestCase):
    def run_case(self, root, videos, failures=(), **options):
        events, attempts, inspections = [], [], []

        def details(video_id):
            inspections.append(video_id)
            return next(v for v in videos if v['id'] == video_id)

        def download(video, folder, stt, progress):
            attempts.append(video['id'])
            if video['id'] in failures:
                raise RuntimeError('Download failed')
            target = folder / f"{stt} {video['title']}.mp4"
            target.write_bytes(b'done')
            return target

        with patch.object(d, 'get_state_dir', return_value=root / 'state'), patch.object(d, 'load_sheet_rows', return_value=[d.ChannelRow('1', 'channel', 1)]), patch.object(d, 'channel_videos', return_value=videos), patch.object(d, 'video_details', side_effect=details), patch.object(d, 'download_video', side_effect=download), patch.object(d.shutil, 'disk_usage', return_value=SimpleNamespace(free=100*d.GIB)):
            d.run('sheet', root, events.append, **options)
        return attempts, inspections, events

    def test_filters_require_both_thresholds_and_allow_equal_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            videos = [dict(id='views-low', title='A', view_count=99, duration=120),
                      dict(id='too-short', title='B', view_count=1000, duration=59),
                      dict(id='unknown', title='C', view_count=1000, duration=None),
                      dict(id='match', title='D', view_count=100, duration=60)]
            attempts, _, events = self.run_case(root, videos, min_views=100, min_duration=60)
            self.assertEqual(attempts, ['match'])
            self.assertTrue(any(e.get('status') == 'Đã tải' for e in events))
            self.assertEqual(d.DownloadHistory(root/'state').load_failed_ids(), set())
            # Rejected filters are not permanent failures: relaxing can select them.
            self.assertEqual(self.run_case(root, videos)[0], ['views-low'])

    def test_failed_video_is_skipped_for_next_video_and_remembered_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            videos = [dict(id='bad', title='Broken'), dict(id='ok', title='Works')]
            attempts, _, events = self.run_case(root, videos, failures={'bad'})
            self.assertEqual(attempts, ['bad', 'bad', 'ok'])
            self.assertEqual(sum(e.get('completed', False) for e in events), 1)
            history = d.DownloadHistory(root/'state')
            self.assertEqual(history.load_failed_ids(), {'bad'})
            self.assertEqual(history.load()[1], {'ok'})
            # Move/delete successful file; both durable sets still prevent retry.
            (root/'1 Works.mp4').unlink()
            attempts, inspected, _ = self.run_case(root, videos)
            self.assertEqual(attempts, [])
            self.assertEqual(inspected, [])

    def test_history_failure_during_blacklist_stops_without_trying_next(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(d.DownloadHistory, 'record_failure', side_effect=d.sqlite3.OperationalError('locked')):
                attempts, _, events = self.run_case(root, [dict(id='bad', title='Broken'), dict(id='next', title='Next')], failures={'bad'})
            self.assertEqual(attempts, ['bad', 'bad'])
            self.assertTrue(any(e.get('type') == 'fatal' for e in events))

    def test_disk_failure_is_not_recorded_as_video_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(d.DiskBudget, 'reserve', side_effect=d.LowDiskSpaceError('full')):
                attempts, _, events = self.run_case(root, [dict(id='valid', title='Valid')])
            self.assertEqual(attempts, [])
            self.assertEqual(d.DownloadHistory(root/'state').load_failed_ids(), set())
            self.assertTrue(any(e.get('type') == 'fatal' for e in events))

    def test_schema_upgrade_preserves_existing_success_and_watch_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = d.DownloadHistory(root)
            history.record_download(dict(id='old', title='Old'), root/'old.mp4')
            history.record_failure(dict(id='bad', title='Bad'), 'failed')
            restored = d.DownloadHistory(root)
            self.assertEqual(restored.load_failed_ids(), {'bad'})
            self.assertIn('old', restored.load()[1])

