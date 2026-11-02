"""Regression tests for event starvation and closing with active workers."""
import queue
import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import app


def fake_window():
    return SimpleNamespace(
        events=queue.Queue(), closing=False, worker=None,
        stop_requested=threading.Event(), after=MagicMock(),
        poll_events=MagicMock(), finish_close=MagicMock(),
        start_button=MagicMock(), import_history_button=MagicMock(),
        worker_selector=MagicMock(), stop_button=MagicMock(),
        format_selector=MagicMock(),
        min_views=MagicMock(get=MagicMock(return_value='0')),
        min_duration=MagicMock(get=MagicMock(return_value='0')),
        summary=MagicMock(), progress=MagicMock(), table=MagicMock(),
        winfo_exists=MagicMock(return_value=True), destroy=MagicMock(),
    )


class AppTests(unittest.TestCase):
    def test_view_grouping_and_minute_conversion(self):
        self.assertEqual(app.format_views(1234567), '1.234.567')
        self.assertEqual(app.parse_views('100.000'), 100000)
        self.assertEqual(app.parse_views('100000'), 100000)
        self.assertEqual(app.minutes_to_seconds('5'), 300)
        self.assertEqual(app.minutes_to_seconds('1,5'), 90)
        self.assertEqual(app.minutes_to_seconds('1.5'), 90)
        for seconds in (0, 1, 20, 45, 60, 90, 300, 3601):
            self.assertEqual(app.minutes_to_seconds(app.seconds_to_minutes(seconds)), seconds)
        for value in ('-1', 'abc', 'NaN', 'Infinity'):
            with self.assertRaises(ValueError):
                app.minutes_to_seconds(value)
        with self.assertRaises(ValueError):
            app.parse_views('1.23')
    def test_format_choice_is_saved_and_forwarded_to_worker(self):
        for selected_format in ('original', 'mp4'):
            with self.subTest(output_format=selected_format), tempfile.TemporaryDirectory() as folder:
                window = fake_window()
                window.sheet_url = MagicMock(get=MagicMock(return_value='https://example.com/sheet'))
                window.folder = MagicMock(get=MagicMock(return_value=folder))
                window.max_workers = MagicMock(get=MagicMock(return_value='2'))
                window.min_views.get.return_value = '1.000'
                window.min_duration.get.return_value = '1,5'
                label = next(label for label, value in app.FORMAT_OPTIONS.items() if value == selected_format)
                window.output_format = MagicMock(get=MagicMock(return_value=label))
                window.run_worker = MagicMock()
                window.table.get_children.return_value = []
                settings = Path(folder) / 'settings.json'
                with patch.object(app, 'SETTINGS_FILE', settings), patch.object(app.threading, 'Thread') as thread:
                    app.DownloaderApp.start(window)
                self.assertEqual(json.loads(settings.read_text(encoding='utf-8'))['output_format'], selected_format)
                self.assertEqual(thread.call_args.kwargs['args'][3:], (selected_format, 1000, 90))
                self.assertEqual(json.loads(settings.read_text(encoding='utf-8'))['min_views'], 1000)
                self.assertEqual(json.loads(settings.read_text(encoding='utf-8'))['min_duration'], 90)
                window.format_selector.configure.assert_called_with(state='disabled')

    def test_worker_passes_selected_format_to_download_run(self):
        window = fake_window()
        with patch.object(app, 'run') as run:
            app.DownloaderApp.run_worker(window, 'sheet', Path('folder'), 2, 'mp4')
        self.assertEqual(run.call_args.kwargs['output_format'], 'mp4')

    def test_idle_restores_format_selection(self):
        window = fake_window()
        window.events.put({'type': 'idle'})
        app.DownloaderApp.poll_events(window)
        window.format_selector.configure.assert_called_once_with(state='readonly')

    def test_busy_queue_yields_to_window_after_at_most_100_events(self):
        window = fake_window()
        for _ in range(1000):
            window.events.put({"type": "progress", "stt": "1", "message": "50%"})
        window.table.exists.return_value = False
        with patch.object(app.time, "monotonic", return_value=1):
            app.DownloaderApp.poll_events(window)
        self.assertEqual(window.events.qsize(), 900)
        window.after.assert_called_once_with(100, window.poll_events)

    def test_malformed_event_does_not_disable_future_updates(self):
        window = fake_window()
        window.events.put({"type": "row"})
        with self.assertLogs(app.__name__, level="ERROR"):
            app.DownloaderApp.poll_events(window)
        window.after.assert_called_once_with(100, window.poll_events)

    def test_close_requests_stop_and_waits_for_worker(self):
        window = fake_window()
        window.worker = MagicMock()
        window.worker.is_alive.return_value = True
        app.DownloaderApp.request_close(window)
        self.assertTrue(window.closing)
        self.assertTrue(window.stop_requested.is_set())
        app.DownloaderApp.finish_close(window)
        window.destroy.assert_not_called()
        window.after.assert_called_with(100, window.finish_close)
        window.worker.is_alive.return_value = False
        app.DownloaderApp.finish_close(window)
        window.destroy.assert_called_once_with()

    def test_idle_during_close_does_not_reenable_start(self):
        window = fake_window()
        window.closing = True
        window.events.put({"type": "idle"})
        app.DownloaderApp.poll_events(window)
        window.start_button.configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
