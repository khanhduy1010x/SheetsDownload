import csv
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import downloader


class DownloaderTests(unittest.TestCase):
    def test_mp4_selection_prefers_direct_source_then_falls_back_without_hd_escape(self):
        for scenario in ('native', 'combined', 'audio_fallback', 'webm_only'):
            with self.subTest(scenario=scenario):
                info = self.format_test_info([720], combined=scenario == 'combined')
                webm = {'format_id': 'webm720', 'url': 'http://127.0.0.1:1/video.webm', 'ext': 'webm', 'height': 720, 'width': 1280, 'vcodec': 'vp9', 'acodec': 'none', 'tbr': 9000}
                opus = {'format_id': 'opus', 'url': 'http://127.0.0.1:1/audio.webm', 'ext': 'webm', 'vcodec': 'none', 'acodec': 'opus', 'abr': 256}
                info['formats'] += [webm, opus]
                if scenario == 'webm_only':
                    info['formats'] = [webm, opus]
                elif scenario == 'audio_fallback':
                    info['formats'] = [f for f in info['formats'] if f['format_id'] != 'audio']
                with downloader.yt_dlp.YoutubeDL({'quiet': True, 'format': downloader.MP4_DOWNLOAD_FORMAT}) as ydl:
                    selected = ydl.process_ie_result(info, download=False)
                formats = selected.get('requested_formats', [selected])
                video = next(f for f in formats if f['vcodec'] != 'none')
                self.assertEqual(video['vcodec'], 'vp9' if scenario == 'webm_only' else 'avc1')
                self.assertEqual(video['height'], 720)
                if scenario == 'native':
                    self.assertEqual(formats[-1]['acodec'], 'mp4a')
        with downloader.yt_dlp.YoutubeDL({'quiet': True, 'format': downloader.MP4_DOWNLOAD_FORMAT, 'logger': MagicMock()}) as ydl:
            with self.assertRaises(downloader.yt_dlp.utils.ExtractorError):
                ydl.process_ie_result(self.format_test_info([1080, 2160]), download=False)

    def test_mp4_postprocessor_is_only_enabled_for_mp4_choice(self):
        for output_format in ('original', 'mp4'):
            with self.subTest(output_format=output_format), tempfile.TemporaryDirectory() as folder:
                final = Path(folder) / '3 Final.mp4'
                final.write_bytes(b'finished')
                fake = MagicMock()
                fake.__enter__.return_value = fake
                fake.extract_info.return_value = {'filepath': str(final)}
                fake.prepare_filename.return_value = str(final)
                with patch.object(downloader.yt_dlp, 'YoutubeDL', return_value=fake) as factory:
                    self.assertEqual(downloader.download_video({'id': 'test'}, Path(folder), '3', lambda _: None, output_format), final.resolve())
                self.assertEqual(fake.add_post_processor.call_count, int(output_format == 'mp4'))
                self.assertEqual(factory.call_args.args[0]['format'], downloader.MP4_DOWNLOAD_FORMAT if output_format == 'mp4' else downloader.DOWNLOAD_FORMAT)

    def test_mp4_choice_does_not_report_unconverted_webm_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / '3 Final.webm'
            source.write_bytes(b'not converted')
            fake = MagicMock()
            fake.__enter__.return_value = fake
            fake.extract_info.return_value = {'filepath': str(source)}
            fake.prepare_filename.return_value = str(source)
            with patch.object(downloader.yt_dlp, 'YoutubeDL', return_value=fake):
                with self.assertRaisesRegex(RuntimeError, 'Không tìm thấy file sau tải'):
                    downloader.download_video({'id': 'test'}, Path(folder), '3', lambda _: None, 'mp4')

    def setUp(self):
        state_temp = tempfile.TemporaryDirectory()
        self.addCleanup(state_temp.cleanup)
        self.state_dir = Path(state_temp.name) / "state"
        state_patch = patch.object(downloader, "get_state_dir", return_value=self.state_dir)
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def check_download_path(self, folder, prepared, result, final_hook=None):
        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.prepare_filename.return_value = str(prepared)

        def create_downloader(options):
            def extract(*args, **kwargs):
                if final_hook is not None:
                    for hook in options["post_hooks"]:
                        hook(str(final_hook))
                return result
            fake.extract_info.side_effect = extract
            return fake

        with patch.object(downloader.yt_dlp, "YoutubeDL", side_effect=create_downloader):
            return downloader.download_video({"id": "test"}, folder, "3", lambda _: None)

    def test_download_finds_literal_brackets_and_merged_extension(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            final = folder / "3 [Official] Video.mkv"
            final.write_bytes(b"merged")
            prepared = final.with_suffix(".webm")
            self.assertEqual(self.check_download_path(folder, prepared, {}), final.resolve())

    def test_download_uses_completed_hook_after_filename_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            prepared = folder / "3 Old title.webm"
            prepared.write_bytes(b"old file")
            final = folder / "3 Final [Title].mkv"
            final.write_bytes(b"merged")
            self.assertEqual(self.check_download_path(folder, prepared, {}, final), final.resolve())

    def test_download_uses_final_filepath_from_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            final = folder / "3 Final title.mp4"
            final.write_bytes(b"done")
            self.assertEqual(self.check_download_path(folder, folder / "3 Before.webm", {"filepath": str(final)}), final.resolve())

    def test_download_finds_existing_literal_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            final = folder / "3 Video [4K].mp4"
            final.write_bytes(b"existing")
            self.assertEqual(self.check_download_path(folder, final, {}), final.resolve())

    def test_download_does_not_report_partial_or_other_video_as_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            prepared = folder / "3 [Official] Video.mp4"
            (folder / (prepared.name + ".part")).write_bytes(b"partial")
            (folder / "3 O Video.mp4").write_bytes(b"glob false match")
            (folder / "3 [Official] Video.f137.mp4").write_bytes(b"unmerged stream")
            (folder / "3 Other.mp4").write_bytes(b"other")
            with self.assertRaisesRegex(RuntimeError, "Không tìm thấy file sau tải"):
                self.check_download_path(folder, prepared, {})

    def test_sheet_url_and_title_normalization(self):
        url = "https://docs.google.com/spreadsheets/d/abc-123/edit#gid=456"
        self.assertEqual(downloader.sheet_csv_url(url), "https://docs.google.com/spreadsheets/d/abc-123/export?format=csv&gid=456")
        self.assertEqual(downloader.normalize_title("CRÉE EL ECOSISTEMA: JURÁSICO!"), downloader.normalize_title("cree el ecosistema - jurásico"))
        self.assertTrue(downloader.title_in_filenames("CREÉ LA RAZA MÁS EGOÍSTA DE SPORE", {downloader.normalize_title("3 CRÉE LA RAZA MÁS EGOÍSTA DE SPORE")}))

    def test_full_title_matches_anywhere_in_customer_filename(self):
        names = [
            "123013 Phim hoa giay.mp4", "Phim hoa giay 123123.mp4",
            "01Phim hoa giay.mp4", "1Phim hoa giay.mp4",
            "Phim hoa giay.mp4", "Luu tru - Phim hoa giay - ban cu.mkv",
            "123Phim hoa giay456.WEBM", "(01) PHIM HOA GIAY.mp4",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(downloader.title_in_filenames("Phim hoa giay", {downloader.normalize_title(Path(name).stem)}))

    def test_partial_or_reordered_title_does_not_match(self):
        for name in ["123 Phim hoa", "Phim khac hoa giay", "Giay hoa phim", "123456"]:
            with self.subTest(name=name):
                self.assertFalse(downloader.title_in_filenames("Phim hoa giay", {downloader.normalize_title(name)}))
        self.assertFalse(downloader.title_in_filenames("!!!", {"anything"}))
        self.assertTrue(downloader.title_in_filenames("1917", {"01 1917 123"}))

    def test_existing_scan_includes_subfolders_and_ignores_incomplete_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            nested = folder / "Video cu"
            nested.mkdir()
            (nested / "01Phim ABC.MP4").write_bytes(b"old")
            (folder / "001 Phim DEF.mkv").write_bytes(b"old")
            (folder / "02Phim ABC.webm").write_bytes(b"other channel")
            (folder / "Phim Khong STT.mp4").write_bytes(b"unprefixed")
            (folder / "01Phim Partial.mp4.part").write_bytes(b"partial")
            (folder / "01Phim Note.txt").write_text("note")
            self.assertEqual(downloader.existing_filenames(folder), {
                "01phim abc", "001 phim def", "02phim abc", "phim khong stt",
            })

    def test_sheet_rejects_duplicate_row_keys(self):
        payload = '1,,,,https://www.youtube.com/@first\n1,,,,https://www.youtube.com/@second\n'
        response = SimpleNamespace(headers={"content-type": "text/csv"}, content=payload.encode(), text=payload, raise_for_status=lambda: None)
        with patch.object(downloader.requests, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "STT có giá trị trùng"):
                downloader.load_sheet_rows("https://docs.google.com/spreadsheets/d/sample/edit")

    def test_legacy_log_is_imported_and_survives_deleted_video_and_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = folder / "01 Title cut short.mp4"
            path.write_bytes(b"old")
            log = downloader.RunLog(folder)
            log.append(downloader.ChannelRow("01", "channel", 1), "Đã tải", {"id": "old-id", "title": "Original full video title"}, str(path))
            history = downloader.DownloadHistory(self.state_dir)
            history.import_log(log.csv_path)
            path.unlink()
            log.csv_path.unlink()
            names, ids = downloader.DownloadHistory(self.state_dir).load()
            self.assertIn("original full video title", names)
            self.assertIn("old-id", ids)

    def test_watch_history_html_reads_links_entities_and_deduplicates(self):
        path = self.state_dir.parent / "watch-history.html"
        path.write_text('''<html><body>
            Đã xem <a href="https://www.youtube.com/watch?v=abcdefghijk&amp;list=ignored">Phim &amp; nhạc</a>
            Watched <a href="https://youtu.be/abcdefghijk">Repeat</a>
            <a href="https://www.youtube.com/shorts/12345678901"><span>Short</span></a>
            <a href="https://m.youtube.com/watch?v=ABCDEFGHIJK">Mobile</a>
            <a href="https://www.youtube.com/live/abcdefghij_">Live</a>
            <a href="https://www.youtube.com/channel/UCabc">Channel</a>
            <a href="https://www.youtube.com/results?search_query=film">Search</a>
            <a href="https://youtube.com.evil.test/watch?v=wrongwrong1">Ignore</a>
            <script>"<a href='https://www.youtube.com/watch?v=script12345'>Fake</a>"</script>
            </body></html>''', encoding="utf-8-sig")
        videos, records = downloader.read_watch_history(path)
        self.assertEqual(records, 5)
        self.assertEqual(set(videos), {"abcdefghijk", "12345678901", "ABCDEFGHIJK", "abcdefghij_"})
        self.assertEqual(videos["abcdefghijk"], "Phim & nhạc")

    def test_watch_history_json_and_repeat_import_survive_source_deletion(self):
        path = self.state_dir.parent / "watch-history.json"
        path.write_text(json.dumps([
            {"title": "Watched Film", "titleUrl": "https://www.youtube.com/watch?v=abcdefghijk"},
            {"title": "Watched Film again", "titleUrl": "https://www.youtube.com/watch?v=abcdefghijk"},
            {"title": "Searched", "titleUrl": "https://www.youtube.com/results?search_query=film"},
            {"title": "Invalid", "titleUrl": "https://www.youtube.com/watch?v=short"}, None,
        ]), encoding="utf-8")
        history = downloader.DownloadHistory(self.state_dir)
        result = history.import_watch_history(path)
        self.assertEqual((result.records, result.unique_videos, result.added_videos), (2, 1, 1))
        self.assertEqual(history.import_watch_history(path).added_videos, 0)
        path.unlink()
        names, ids = downloader.DownloadHistory(self.state_dir).load()
        self.assertEqual(ids, {"abcdefghijk"})
        self.assertEqual(names, set())  # Watched videos skip by ID, not unrelated matching titles.

    def test_invalid_watch_history_does_not_overwrite_existing_history(self):
        history = downloader.DownloadHistory(self.state_dir)
        history.record_download({"id": "existing", "title": "Old film"}, self.state_dir / "old.mp4")
        for filename, content in [("search-history.html", '<a href="https://www.youtube.com/results?search_query=film">Search</a>'), ("bad.json", "not json"), ("wrong.json", '{"Browser History": []}'), ("archive.zip", "zip")]:
            with self.subTest(filename=filename):
                path = self.state_dir.parent / filename
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    history.import_watch_history(path)
                self.assertEqual(history.load()[1], {"existing"})

    def test_video_url_parser_rejects_unrelated_hosts_and_invalid_ids(self):
        for url in ["https://youtube.com.evil.test/watch?v=abcdefghijk", "https://notyoutube.com/watch?v=abcdefghijk", "javascript:alert(1)", "https://www.youtube.com/watch?v=short", "https://youtu.be/abcdefghijk/extra"]:
            with self.subTest(url=url):
                self.assertIsNone(downloader.youtube_video_id(url))
        self.assertEqual(downloader.youtube_video_id("https://www.youtube.com/embed/abcdefghijk"), "abcdefghijk")

    def test_run_skips_imported_watched_id_and_downloads_next_video(self):
        path = self.state_dir.parent / "watch-history.html"
        path.write_text('<a href="https://www.youtube.com/watch?v=abcdefghijk">Already watched</a>', encoding="utf-8")
        downloader.DownloadHistory(self.state_dir).import_watch_history(path)
        path.unlink()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            next_video = {"id": "12345678901", "title": "New film"}

            def fake_download(video, target, stt, progress):
                path = target / f"{stt} {video['title']}.mp4"
                path.write_bytes(b"done")
                return path

            with patch.object(downloader, "load_sheet_rows", return_value=[downloader.ChannelRow("1", "channel", 1)]), patch.object(downloader, "channel_videos", return_value=[{"id": "abcdefghijk", "title": "Now renamed"}, next_video]), patch.object(downloader, "video_details", return_value=next_video) as details, patch.object(downloader, "download_video", side_effect=fake_download) as download, patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, lambda _: None)
            details.assert_called_once_with("12345678901")
            self.assertEqual(download.call_args.args[0]["id"], "12345678901")

    def test_new_watch_table_preserves_existing_database(self):
        self.state_dir.mkdir()
        db_path = self.state_dir / "history.sqlite3"
        with downloader.sqlite3.connect(db_path) as db:
            db.execute("CREATE TABLE matches (value TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE videos (video_id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL, completed_at TEXT NOT NULL)")
            db.execute("INSERT INTO matches VALUES (?)", ("old film",))
            db.execute("INSERT INTO videos VALUES (?, ?, ?, ?)", ("old-id", "Old film", "deleted.mp4", ""))
        db.close()
        history = downloader.DownloadHistory(self.state_dir)
        self.assertEqual(history.load(), ({"old film"}, {"old-id"}))

    def test_run_skips_title_regardless_of_stt_and_surrounding_text(self):
        for case, (name, sheet_stt) in enumerate([
            ("123013 Phim ABC.mp4", "9"), ("Phim ABC 123123.mp4", "8"),
            ("1Phim ABC.mp4", "2"), ("01Phim ABC.mp4", "7"),
            ("Phim ABC.mp4", "01"), ("Cu - Phim ABC - da tai.mp4", "6"),
        ]):
            with self.subTest(name=name, sheet_stt=sheet_stt), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                nested = folder / "Video cu"
                nested.mkdir()
                old_file = nested / name
                old_file.write_bytes(b"customer original")
                rows = [downloader.ChannelRow(sheet_stt, "https://www.youtube.com/@channel", 2)]
                details = {"old": {"id": "old", "title": "Phim ABC"}, "new": {"id": "new", "title": "Phim DEF"}}
                events = []

                def fake_download(video, target, stt, progress):
                    path = target / f"{stt} {video['title']}.mp4"
                    path.write_bytes(b"new video")
                    return path

                with patch.object(downloader, "get_state_dir", return_value=self.state_dir / str(case)), patch.object(downloader, "load_sheet_rows", return_value=rows), patch.object(downloader, "channel_videos", return_value=[{"id": "old"}, {"id": "new"}]), patch.object(downloader, "video_details", side_effect=lambda video_id: details[video_id]), patch.object(downloader, "download_video", side_effect=fake_download) as download, patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                    downloader.run("sheet", folder, events.append)
                    self.assertEqual(download.call_count, 1)
                    self.assertEqual(download.call_args.args[0]["id"], "new")
                    downloader.run("sheet", folder, events.append)
                    self.assertEqual(download.call_count, 1)
                    self.assertTrue(any(event.get("status") == "Không còn video mới" for event in events))
                self.assertEqual(old_file.read_bytes(), b"customer original")

    def test_history_survives_deleting_all_videos_and_changing_output_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "videos"
            second = Path(temporary) / "new videos"
            first.mkdir()
            second.mkdir()
            (first / "Phim hoa giay 123123.mp4").write_bytes(b"customer original")
            rows = [downloader.ChannelRow("9", "https://www.youtube.com/@channel", 1)]
            videos = [{"id": "old"}, {"id": "downloaded"}, {"id": "next"}]
            titles = {"old": "Phim hoa giay", "downloaded": "Phim ABC", "next": "Phim DEF"}
            downloaded = []

            def fake_download(video, target, stt, progress):
                path = target / f"{stt} {video['title']}.mp4"
                path.write_bytes(b"done")
                downloaded.append(video["id"])
                return path

            with patch.object(downloader, "load_sheet_rows", return_value=rows), patch.object(downloader, "channel_videos", return_value=videos), patch.object(downloader, "video_details", side_effect=lambda video_id: {"id": video_id, "title": titles[video_id]}), patch.object(downloader, "download_video", side_effect=fake_download), patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", first, lambda _: None)
                self.assertEqual(downloaded, ["downloaded"])
                for path in first.iterdir():
                    self.assertIn(path.suffix, downloader.VIDEO_SUFFIXES)
                    path.unlink()
                # A fresh run uses the DB, not the previous in-memory index.
                downloader.run("sheet", second, lambda _: None)
                self.assertEqual(downloaded, ["downloaded", "next"])
            self.assertEqual(list(first.iterdir()), [])
            self.assertEqual([p.name for p in second.iterdir()], ["9 Phim DEF.mp4"])
            self.assertTrue((self.state_dir / "history.sqlite3").is_file())
            self.assertTrue((self.state_dir / "download_log.csv").is_file())

    def test_known_video_id_is_skipped_after_title_change_and_file_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = folder / "1 Original title.mp4"
            path.write_bytes(b"done")
            downloader.DownloadHistory(self.state_dir).record_download({"id": "known", "title": "Original title"}, path)
            path.unlink()
            events = []
            with patch.object(downloader, "load_sheet_rows", return_value=[downloader.ChannelRow("1", "channel", 1)]), patch.object(downloader, "channel_videos", return_value=[{"id": "known", "title": "Changed title"}]), patch.object(downloader, "video_details") as details, patch.object(downloader, "download_video") as download, patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, events.append)
            details.assert_not_called()
            download.assert_not_called()
            self.assertTrue(any(e.get("status") == "Không còn video mới" for e in events))

    def test_import_legacy_success_after_deletion_does_not_import_failed_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            log = downloader.RunLog(folder)
            row = downloader.ChannelRow("1", "channel", 1)
            log.append(row, "Đã tải", {"id": "done", "title": "Successful film"}, str(folder / "already deleted.mp4"))
            log.append(row, "Lỗi", {"id": "failed", "title": "Failed film"})
            history = downloader.DownloadHistory(self.state_dir)
            history.import_log(log.csv_path)
            names, ids = history.load()
            self.assertEqual(ids, {"done"})
            self.assertIn("successful film", names)
            self.assertNotIn("failed film", names)

    def test_failed_download_is_retried_and_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            video = {"id": "retry", "title": "Retry film"}
            attempts = []

            def fake_download(video, target, stt, progress):
                attempts.append(stt)
                # Fail both the first call and its automatic retry. The next
                # manual run must still be able to download this video.
                if len(attempts) <= 2:
                    (target / "1 Retry film.mp4.part").write_bytes(b"partial")
                    raise RuntimeError("Network disconnected")
                path = target / "1 Retry film.mp4"
                path.write_bytes(b"done")
                return path

            with patch.object(downloader, "load_sheet_rows", return_value=[downloader.ChannelRow("1", "channel", 1)]), patch.object(downloader, "channel_videos", return_value=[video]), patch.object(downloader, "video_details", return_value=video), patch.object(downloader, "download_video", side_effect=fake_download), patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, lambda _: None)
                names, ids = downloader.DownloadHistory(self.state_dir).load()
                self.assertNotIn("retry", ids)
                self.assertFalse(downloader.title_in_filenames("Retry film", names))
                downloader.run("sheet", folder, lambda _: None)
            self.assertEqual(len(attempts), 2)
            self.assertIn("retry", downloader.DownloadHistory(self.state_dir).load_failed_ids())
            self.assertNotIn("retry", downloader.DownloadHistory(self.state_dir).load()[1])

    def test_history_write_failure_stops_run_without_reporting_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            video = {"id": "failed-history", "title": "Film"}
            path = folder / "1 Film.mp4"
            path.write_bytes(b"done")
            events = []
            with patch.object(downloader, "existing_filenames", return_value=set()), patch.object(downloader, "load_sheet_rows", return_value=[downloader.ChannelRow("1", "channel", 1)]), patch.object(downloader, "channel_videos", return_value=[video]), patch.object(downloader, "video_details", return_value=video), patch.object(downloader, "download_video", return_value=path), patch.object(downloader.DownloadHistory, "record_download", side_effect=downloader.sqlite3.OperationalError("History unavailable")), patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, events.append)
            self.assertFalse(any(e.get("status") == "Đã tải" for e in events))
            self.assertTrue(any(e.get("type") == "fatal" for e in events))

    def test_sheet_reads_stt_and_channel_from_columns_a_and_e(self):
        payload = 'STT,ID TikTok,,Khác,YouTube\n3,abc,,x,https://www.youtube.com/@channel3/videos\n4,def,,x,https://www.youtube.com/@channel4\n'
        response = type("Response", (), {"headers": {"content-type": "text/csv"}, "content": payload.encode(), "text": payload, "raise_for_status": lambda self: None})()
        with patch.object(downloader.requests, "get", return_value=response):
            rows = downloader.load_sheet_rows("https://docs.google.com/spreadsheets/d/sample/edit#gid=0")
        self.assertEqual([(row.stt, row.sheet_row) for row in rows], [("3", 2), ("4", 3)])

    def test_channel_uses_youtube_popular_playlist_order(self):
        channel_id = "UCDr1qzSKj_Xwx1qLGjpnXTw"
        playlist_id = "UULP" + channel_id[2:]
        fake_downloader = MagicMock()
        fake_downloader.__enter__.return_value = fake_downloader
        fake_downloader.extract_info.side_effect = [
            {"channel_id": channel_id},
            {"id": playlist_id, "entries": [
                {"id": "first", "title": "First in Popular", "view_count": 10},
                {"id": "second", "title": "Second in Popular", "view_count": 100},
            ]},
        ]
        with patch.object(downloader.yt_dlp, "YoutubeDL", return_value=fake_downloader):
            videos = downloader.channel_videos("https://www.youtube.com/@Frank_848/videos")
        self.assertEqual([video["id"] for video in videos], ["first", "second"])
        self.assertEqual(fake_downloader.extract_info.call_args_list[1].args[0], f"https://www.youtube.com/playlist?list={playlist_id}")

    def test_low_disk_space_stops_before_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100)):
                with self.assertRaises(downloader.LowDiskSpaceError):
                    downloader.ensure_disk_space(Path(temporary))

    def test_existing_names_are_saved_before_download_is_blocked_by_disk_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "123 Phim hoa giay.mp4").write_bytes(b"old")
            with patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100)):
                with self.assertRaises(downloader.LowDiskSpaceError):
                    downloader.run("sheet", folder, lambda _: None)
            names, _ = downloader.DownloadHistory(self.state_dir).load()
            self.assertTrue(downloader.title_in_filenames("Phim hoa giay", names))

    def test_real_format_selection_caps_separate_video_at_720p(self):
        for heights, expected in [([360, 720, 1080, 2160], 720), ([360, 480, 1080], 480)]:
            with self.subTest(heights=heights):
                info = self.format_test_info(heights)
                with downloader.yt_dlp.YoutubeDL({"format": downloader.DOWNLOAD_FORMAT, "quiet": True}) as ydl:
                    result = ydl.process_ie_result(info, download=False)
                selected = result.get("requested_formats", [result])
                self.assertEqual(next(f["height"] for f in selected if f.get("vcodec") != "none"), expected)

    @staticmethod
    def format_test_info(heights, combined=False):
        formats = [
            {"format_id": str(height), "url": f"http://127.0.0.1:1/{height}.mp4", "ext": "mp4", "height": height, "width": height * 16 // 9, "vcodec": "avc1", "acodec": "mp4a" if combined else "none"}
            for height in heights
        ]
        if not combined:
            formats.append({"format_id": "audio", "url": "http://127.0.0.1:1/audio.m4a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a"})
        return {"id": "test", "title": "Format test", "extractor": "generic", "extractor_key": "Generic", "formats": formats}

    def test_real_format_selection_combined_fallback_and_no_hd_escape(self):
        with downloader.yt_dlp.YoutubeDL({"format": downloader.DOWNLOAD_FORMAT, "quiet": True}) as ydl:
            result = ydl.process_ie_result(self.format_test_info([360, 720, 1080], combined=True), download=False)
            self.assertEqual(result["height"], 720)
        with downloader.yt_dlp.YoutubeDL({"format": downloader.DOWNLOAD_FORMAT, "quiet": True, "logger": MagicMock()}) as ydl:
            with self.assertRaises(downloader.yt_dlp.utils.ExtractorError):
                ydl.process_ie_result(self.format_test_info([1080, 2160]), download=False)

    def test_parallel_channels_do_not_download_the_same_title_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            rows = [downloader.ChannelRow(str(i), f"https://www.youtube.com/@{i}", i) for i in (1, 2)]
            barrier = threading.Barrier(2)
            downloaded = []

            def details(video_id):
                if video_id.endswith("shared"):
                    barrier.wait(timeout=3)
                    return {"id": video_id, "title": "Phim hoa giay"}
                return {"id": video_id, "title": "Phim khac " + video_id}

            def fake_download(video, target, stt, progress):
                time.sleep(0.03)
                path = target / f"{stt} {video['title']}.mp4"
                path.write_bytes(b"done")
                downloaded.append(video["title"])
                return path

            with patch.object(downloader, "load_sheet_rows", return_value=rows), patch.object(downloader, "channel_videos", side_effect=lambda url: [{"id": url[-1] + "shared"}, {"id": url[-1] + "next"}]), patch.object(downloader, "video_details", side_effect=details), patch.object(downloader, "download_video", side_effect=fake_download), patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, lambda _: None, max_workers=2)
            self.assertEqual(downloaded.count("Phim hoa giay"), 1)
            self.assertEqual(len(downloaded), 2)

    def test_parallel_failed_title_is_released_for_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            rows = [downloader.ChannelRow(str(i), f"https://www.youtube.com/@{i}", i) for i in (1, 2)]
            barrier = threading.Barrier(2)
            attempts = []
            events = []

            def details(video_id):
                barrier.wait(timeout=3)
                return {"id": video_id, "title": "Phim hoa giay"}

            def fake_download(video, target, stt, progress):
                attempts.append(stt)
                if len(attempts) == 1:
                    raise RuntimeError("Temporary download failure")
                path = target / f"{stt} {video['title']}.mp4"
                path.write_bytes(b"done")
                return path

            with patch.object(downloader, "load_sheet_rows", return_value=rows), patch.object(downloader, "channel_videos", side_effect=lambda url: [{"id": url[-1]}]), patch.object(downloader, "video_details", side_effect=details), patch.object(downloader, "download_video", side_effect=fake_download), patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, events.append, max_workers=2)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(sum(e.get("status") == "Đã tải" for e in events), 1)

    def test_parallel_downloads_reserve_shared_disk_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            budget = downloader.DiskBudget(Path(temporary))
            with patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=5 * downloader.GIB)):
                first = budget.reserve(2 * downloader.GIB)
                with self.assertRaises(downloader.LowDiskSpaceError):
                    budget.reserve(2 * downloader.GIB)
                budget.release(first)
                self.assertEqual(budget.reserve(2 * downloader.GIB), first)

    def test_old_file_is_skipped_and_rows_follow_sheet_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "3 VÍDEO MÁS VISTO.mp4").write_bytes(b"old")
            rows = [downloader.ChannelRow("3", "https://www.youtube.com/@channel3", 2), downloader.ChannelRow("6", "https://www.youtube.com/@channel6", 3)]
            videos = {
                rows[0].url: [{"id": "a", "title": "translated A", "view_count": 900}, {"id": "b", "title": "translated B", "view_count": 600}],
                rows[1].url: [{"id": "c", "title": "translated C", "view_count": 500}],
            }
            titles = {"a": "VÍDEO MÁS VISTO", "b": "Segundo vídeo", "c": "Otra película"}
            downloaded = []
            events = []

            def fake_download(video, target, stt, progress):
                target.mkdir(parents=True, exist_ok=True)
                path = target / f"{stt} {video['title']}.mp4"
                path.write_bytes(b"new")
                downloaded.append(video["id"])
                return path

            with patch.object(downloader, "load_sheet_rows", return_value=rows), patch.object(downloader, "channel_videos", side_effect=lambda url: videos[url]), patch.object(downloader, "video_details", side_effect=lambda video_id: {"id": video_id, "title": titles[video_id], "view_count": 1}), patch.object(downloader, "download_video", side_effect=fake_download), patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, events.append)
                self.assertEqual(downloaded, ["b", "c"])
                downloader.run("sheet", folder, events.append)
                self.assertEqual(downloaded, ["b", "c"])
                (folder / "3 Segundo vídeo.mp4").unlink()
                downloader.run("sheet", folder, events.append)
                self.assertEqual(downloaded, ["b", "c"])
            with (self.state_dir / "download_log.csv").open(encoding="utf-8-sig", newline="") as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual([record["stt"] for record in records[:2]], ["3", "6"])
            self.assertEqual([record["video_title"] for record in records[:2]], ["Segundo vídeo", "Otra película"])
            self.assertFalse((folder / "3 Segundo vídeo.mp4").exists())

    def test_two_channels_download_concurrently_and_log_both(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            rows = [downloader.ChannelRow(str(index), f"https://www.youtube.com/@channel{index}", index) for index in (1, 2, 3)]
            barrier = threading.Barrier(2)
            lock = threading.Lock()
            active = 0
            maximum_active = 0
            events = []

            def fake_download(video, target, stt, progress):
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                if stt in {"1", "2"}:
                    barrier.wait(timeout=3)
                time.sleep(0.02)
                path = target / f"{stt} Video {stt}.mp4"
                path.write_bytes(b"done")
                with lock:
                    active -= 1
                return path

            with patch.object(downloader, "load_sheet_rows", return_value=rows), patch.object(downloader, "channel_videos", side_effect=lambda url: [{"id": url[-1], "title": "translated", "view_count": 10}]), patch.object(downloader, "video_details", side_effect=lambda video_id: {"id": video_id, "title": f"Video {video_id}", "view_count": 10, "estimated_bytes": 1}), patch.object(downloader, "download_video", side_effect=fake_download), patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, events.append, max_workers=2)

            self.assertEqual(maximum_active, 2)
            self.assertEqual([event["stt"] for event in events if event["type"] == "row" and event["status"] == "Đang kiểm tra"], ["1", "2", "3"])
            with (self.state_dir / "download_log.csv").open(encoding="utf-8-sig", newline="") as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual({record["stt"] for record in records}, {"1", "2", "3"})

    def test_stop_waits_for_active_channels_without_scheduling_more(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            rows = [downloader.ChannelRow(str(index), f"https://www.youtube.com/@channel{index}", index) for index in (1, 2, 3)]
            stop_requested = threading.Event()
            first_two_started = threading.Barrier(2)
            events = []

            def fake_download(video, target, stt, progress):
                first_two_started.wait(timeout=3)
                stop_requested.set()
                path = target / f"{stt} Video {stt}.mp4"
                path.write_bytes(b"done")
                return path

            with patch.object(downloader, "load_sheet_rows", return_value=rows), patch.object(downloader, "channel_videos", side_effect=lambda url: [{"id": url[-1], "title": "translated", "view_count": 10}]), patch.object(downloader, "video_details", side_effect=lambda video_id: {"id": video_id, "title": f"Video {video_id}", "view_count": 10, "estimated_bytes": 1}), patch.object(downloader, "download_video", side_effect=fake_download), patch.object(downloader.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * downloader.GIB)):
                downloader.run("sheet", folder, events.append, stop_requested.is_set, max_workers=2)

            started = [event["stt"] for event in events if event["type"] == "row" and event["status"] == "Đang kiểm tra"]
            self.assertEqual(started, ["1", "2"])
            self.assertEqual(events[-1]["type"], "stopped")


if __name__ == "__main__":
    unittest.main()
