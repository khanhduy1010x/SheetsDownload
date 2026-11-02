import csv
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import dns_recovery
import downloader


class DnsRecoveryTests(unittest.TestCase):
    def test_parallel_repairs_share_success(self):
        recovery = dns_recovery.DnsRecovery()
        with patch.object(dns_recovery, "flush_dns_as_admin", return_value="OK") as flush:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: recovery.repair(), range(8)))
            self.assertTrue(all(success for success, _ in results))
            flush.assert_called_once_with()

    def test_cancelled_repair_is_cached_then_allowed_after_cooldown(self):
        recovery = dns_recovery.DnsRecovery()
        with patch.object(dns_recovery, "flush_dns_as_admin", side_effect=RuntimeError("UAC canceled")) as flush, patch.object(dns_recovery.time, "monotonic", side_effect=[0, 59, 61, 61]):
            self.assertFalse(recovery.repair()[0])
            self.assertFalse(recovery.repair()[0])
            self.assertFalse(recovery.repair()[0])
            self.assertEqual(flush.call_count, 2)

    @unittest.skipUnless(dns_recovery.os.name == "nt", "Windows API")
    def test_windows_elevation_command_and_failure_paths(self):
        for scenario in ("success", "cancel", "exit_failure", "timeout"):
            with self.subTest(scenario=scenario):
                kernel, shell = MagicMock(), MagicMock()

                def system_directory(buffer, size):
                    buffer.value = "C:\\Windows\\System32"
                    return len(buffer.value)

                def launch(pointer):
                    info = pointer._obj
                    self.assertEqual(info.lpVerb, "runas")
                    self.assertEqual(info.lpFile, "C:\\Windows\\System32\\ipconfig.exe")
                    self.assertEqual(info.lpParameters, "/flushdns")
                    if scenario == "cancel":
                        return False
                    info.hProcess = 123
                    return True

                def exit_code(handle, pointer):
                    pointer._obj.value = 1 if scenario == "exit_failure" else 0
                    return True

                kernel.GetSystemDirectoryW.side_effect = system_directory
                kernel.WaitForSingleObject.return_value = 258 if scenario == "timeout" else 0
                kernel.GetExitCodeProcess.side_effect = exit_code
                shell.ShellExecuteExW.side_effect = launch
                with patch.object(dns_recovery.ctypes, "WinDLL", side_effect=[kernel, shell]), patch.object(dns_recovery.ctypes, "get_last_error", return_value=1223):
                    if scenario == "success":
                        self.assertIn("thành công", dns_recovery.flush_dns_as_admin())
                    else:
                        with self.assertRaises(RuntimeError):
                            dns_recovery.flush_dns_as_admin()
                if scenario != "cancel":
                    kernel.CloseHandle.assert_called_once_with(123)

    def run_scenario(self, stage, message, repair_success=True, persistent=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = {"id": "test-id", "title": "Test video"}
            row = downloader.ChannelRow("1", "https://www.youtube.com/@test", 1)
            events = []
            error = downloader.yt_dlp.utils.DownloadError(message)

            def save(*args):
                path = root / "video.mp4"
                path.write_bytes(b"test")
                return path

            with patch.object(downloader, "get_state_dir", return_value=root / "state"), patch.object(downloader, "load_sheet_rows", return_value=[row]), patch.object(downloader, "channel_videos", return_value=[video]) as channel, patch.object(downloader, "video_details", return_value=video) as details, patch.object(downloader, "download_video", side_effect=save) as download, patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)), patch.object(dns_recovery, "flush_dns_as_admin", return_value="DNS OK", side_effect=None if repair_success else RuntimeError("UAC canceled")) as flush:
                operation = {"channel": channel, "details": details, "download": download}[stage]
                original = operation.side_effect
                calls = 0

                def fail_then_succeed(*args):
                    nonlocal calls
                    calls += 1
                    if calls == 1 or persistent:
                        raise error
                    return original(*args) if original else operation.return_value

                operation.side_effect = fail_then_succeed
                downloader.run("sheet", root, events.append)
                with (root / "state" / "download_log.csv").open(encoding="utf-8-sig", newline="") as stream:
                    records = list(csv.DictReader(stream))
                known_ids = downloader.DownloadHistory(root / "state").load()[1]
                return calls, flush.call_count, records, known_ids

    def test_bot_error_retries_each_stage_and_records_history(self):
        for stage in ("channel", "details", "download"):
            for apostrophe in ("'", "’"):
                with self.subTest(stage=stage, apostrophe=apostrophe):
                    calls, flushes, records, ids = self.run_scenario(stage, f"ERROR: Sign in to confirm you{apostrophe}re not a bot. Use cookies.")
                    self.assertEqual((calls, flushes), (2, 1))
                    self.assertEqual([record["status"] for record in records], ["Phát hiện lỗi bot", "Xóa DNS", "Thử lại 1/1", "Đã tải"])
                    self.assertIn("test-id", ids)

    def test_persistent_bot_error_has_no_retry_loop_or_success_history(self):
        calls, flushes, records, ids = self.run_scenario("download", "Sign in to confirm you're not a bot", persistent=True)
        self.assertEqual((calls, flushes), (2, 1))
        self.assertEqual(records[-1]["status"], "Lỗi")
        self.assertNotIn("test-id", ids)

    def test_cancel_does_not_retry_or_record_download(self):
        calls, flushes, records, ids = self.run_scenario("details", "Sign in to confirm you're not a bot", repair_success=False)
        self.assertEqual((calls, flushes), (1, 1))
        self.assertEqual(records[-2]["status"], "Lỗi xóa DNS")
        self.assertIn("UAC canceled", records[-1]["message"])
        self.assertNotIn("test-id", ids)

    def test_unrelated_error_does_not_request_admin(self):
        calls, flushes, records, ids = self.run_scenario("channel", "HTTP Error 403: Forbidden")
        self.assertEqual((calls, flushes), (2, 0))
        self.assertEqual(records[-1]["status"], "Đã tải")

    def test_generic_error_retries_each_stage_once(self):
        for stage in ("channel", "details", "download"):
            with self.subTest(stage=stage):
                calls, flushes, records, ids = self.run_scenario(stage, "Connection timed out")
                self.assertEqual((calls, flushes), (2, 0))
                self.assertEqual([record["status"] for record in records], ["Thử lại 1/1", "Đã tải"])
                self.assertIn("test-id", ids)

    def test_persistent_generic_error_stops_after_one_retry(self):
        calls, flushes, records, ids = self.run_scenario("download", "HTTP Error 403", persistent=True)
        self.assertEqual((calls, flushes), (2, 0))
        self.assertEqual(records[-1]["status"], "Lỗi")
        self.assertNotIn("test-id", ids)

    def test_private_video_is_skipped_without_retry(self):
        calls, flushes, records, ids = self.run_scenario("details", "Private video", persistent=True)
        self.assertEqual((calls, flushes), (1, 0))
        self.assertEqual(records[-1]["status"], "Không còn video mới")


if __name__ == "__main__":
    unittest.main()
