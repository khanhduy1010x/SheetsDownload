import functools
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from yt_dlp import YoutubeDL
from yt_dlp.utils import PostProcessingError

from mp4_output import NormalizeMp4PP
import downloader


class Mp4OutputTests(unittest.TestCase):
    def test_native_mp4_can_preserve_non_h264_codec_without_encoding(self):
        with tempfile.TemporaryDirectory() as folder, YoutubeDL({'quiet': True, 'ffmpeg_location': self.location}) as ydl:
            source = Path(folder) / 'native.mp4'
            self.generate(source, 'mpeg4', 'aac')
            before = source.read_bytes()
            pp = NormalizeMp4PP(ydl, preserve_native_mp4=True)
            with patch.object(pp, 'run_ffmpeg', side_effect=AssertionError('native MP4 must not be reencoded')):
                _, info = pp.run({'filepath': str(source), 'ext': 'mp4', 'vcodec': 'mpeg4'})
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(info['filepath'], str(source))
    def test_download_video_prefers_native_mp4_and_converts_only_fallback(self):
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        for native in (True, False):
            with self.subTest(native=native), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                self.generate(root / 'fallback.webm')
                if native:
                    self.generate(root / 'native.mp4', 'libx264', 'aac')
                server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=folder))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                formats = [{'format_id': 'webm', 'url': f'http://127.0.0.1:{server.server_port}/fallback.webm', 'ext': 'webm', 'height': 48, 'vcodec': 'vp9', 'acodec': 'opus', 'tbr': 9000}]
                if native:
                    formats.append({'format_id': 'mp4', 'url': f'http://127.0.0.1:{server.server_port}/native.mp4', 'ext': 'mp4', 'height': 48, 'vcodec': 'avc1', 'acodec': 'mp4a', 'tbr': 100})
                info = {'id': 'local', 'title': 'Local [video]', 'extractor': 'generic', 'formats': formats}
                ffmpeg_location = self.location

                class LocalYoutubeDL(YoutubeDL):
                    def __init__(self, options):
                        super().__init__({**options, 'ffmpeg_location': ffmpeg_location})

                    def extract_info(self, url, download=True):
                        return self.process_ie_result(info, download=download)

                try:
                    real_conversion = NormalizeMp4PP.run_ffmpeg
                    conversions = []

                    def track_conversion(pp, source, destination, options):
                        conversions.append(options)
                        return real_conversion(pp, source, destination, options)

                    with patch.object(downloader.yt_dlp, 'YoutubeDL', LocalYoutubeDL), patch.object(NormalizeMp4PP, 'run_ffmpeg', track_conversion):
                        final = downloader.download_video({'id': 'local'}, root / 'output', '7', lambda _: None, 'mp4')
                    self.assertEqual(len(conversions), 0 if native else 1)
                    self.assertEqual([p.resolve() for p in (root / 'output').iterdir()], [final])
                    with YoutubeDL({'quiet': True, 'ffmpeg_location': self.location}) as ydl:
                        self.check_media(NormalizeMp4PP(ydl), final)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    @classmethod
    def setUpClass(cls):
        location = os.environ.get('TEST_FFMPEG_LOCATION')
        cls.ffmpeg = str(Path(location) / 'ffmpeg.exe') if location else shutil.which('ffmpeg')
        if not cls.ffmpeg or not Path(cls.ffmpeg).is_file():
            raise unittest.SkipTest('FFmpeg is required for real media integration tests')
        cls.location = str(Path(cls.ffmpeg).parent)

    def generate(self, path, video='libvpx-vp9', audio='libopus'):
        args = [self.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'testsrc2=size=64x48:rate=5:duration=0.6']
        if audio:
            args += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.6']
        args += ['-c:v', video, '-pix_fmt', 'yuv420p', '-threads', '1']
        if audio:
            args += ['-c:a', audio]
        subprocess.run([*args, str(path)], check=True, capture_output=True, timeout=30)

    def check_media(self, processor, path, audio=True):
        video_stream, audio_stream = processor._streams(path)
        self.assertEqual(path.suffix, '.mp4')
        self.assertEqual(video_stream['codec_name'], 'h264')
        self.assertEqual(video_stream['pix_fmt'], 'yuv420p')
        self.assertEqual((video_stream['width'], video_stream['height']), (64, 48))
        if audio:
            self.assertEqual(audio_stream['codec_name'], 'aac')
        else:
            self.assertIsNone(audio_stream)
        duration = float(processor.get_metadata_object(str(path))['format']['duration'])
        self.assertGreater(duration, 0.4)

    def test_webm_mkv_and_nonstandard_mp4_become_compatible_mp4(self):
        for extension, video, audio in [('webm', 'libvpx-vp9', 'libopus'),
                                       ('mkv', 'libx264', 'aac'),
                                       ('mp4', 'mpeg4', 'aac'),
                                       ('mp4', 'libx264', 'libopus')]:
            with self.subTest(extension=extension, video=video, audio=audio):
                with tempfile.TemporaryDirectory() as folder, YoutubeDL({'quiet': True, 'ffmpeg_location': self.location}) as ydl:
                    source = Path(folder) / f'Video [test]？.{extension}'
                    self.generate(source, video, audio)
                    pp = NormalizeMp4PP(ydl)
                    delete, info = pp.run({'filepath': str(source), 'ext': extension})
                    final = Path(info['filepath'])
                    self.check_media(pp, final)
                    self.assertEqual(delete, [] if extension == 'mp4' else [str(source)])
                    self.assertFalse(list(Path(folder).glob('.mp4-normalize-*')))

    def test_compatible_mp4_is_preserved_without_reencoding(self):
        with tempfile.TemporaryDirectory() as folder, YoutubeDL({'quiet': True, 'ffmpeg_location': self.location}) as ydl:
            source = Path(folder) / 'already.mp4'
            self.generate(source, 'libx264', 'aac')
            before = source.read_bytes()
            pp = NormalizeMp4PP(ydl)
            with patch.object(pp, 'run_ffmpeg', side_effect=AssertionError('must not reencode')):
                delete, info = pp.run({'filepath': str(source), 'ext': 'mp4'})
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(delete, [])
            self.assertEqual(info['filepath'], str(source))

    def test_silent_video_remains_silent(self):
        with tempfile.TemporaryDirectory() as folder, YoutubeDL({'quiet': True, 'ffmpeg_location': self.location}) as ydl:
            source = Path(folder) / 'silent.webm'
            self.generate(source, audio=None)
            pp = NormalizeMp4PP(ydl)
            _, info = pp.run({'filepath': str(source), 'ext': 'webm'})
            self.check_media(pp, Path(info['filepath']), audio=False)

    def test_conversion_failure_preserves_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as folder, YoutubeDL({'quiet': True, 'ffmpeg_location': self.location}) as ydl:
            source = Path(folder) / 'source.webm'
            self.generate(source)
            before = source.read_bytes()
            pp = NormalizeMp4PP(ydl)
            with patch.object(pp, 'run_ffmpeg', side_effect=PostProcessingError('conversion failed')):
                with self.assertRaises(PostProcessingError):
                    pp.run({'filepath': str(source), 'ext': 'webm'})
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse(source.with_suffix('.mp4').exists())
            self.assertFalse(list(Path(folder).glob('.mp4-normalize-*')))

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder, YoutubeDL({'quiet': True, 'ffmpeg_location': self.location}) as ydl:
            source = Path(folder) / 'same.webm'
            self.generate(source)
            destination = source.with_suffix('.mp4')
            destination.write_bytes(b'existing user video')
            with self.assertRaisesRegex(PostProcessingError, 'không ghi đè'):
                NormalizeMp4PP(ydl).run({'filepath': str(source), 'ext': 'webm'})
            self.assertEqual(destination.read_bytes(), b'existing user video')
            self.assertTrue(source.is_file())

    def test_real_download_pipeline_reports_only_final_mp4(self):
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.generate(root / 'source.webm')
            server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=folder))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                completed = []
                options = {'quiet': True, 'noprogress': True, 'ffmpeg_location': self.location,
                           'outtmpl': str(root / 'output' / '7 %(title)s.%(ext)s'), 'post_hooks': [completed.append]}
                with YoutubeDL(options) as ydl:
                    pp = NormalizeMp4PP(ydl)
                    ydl.add_post_processor(pp)
                    ydl.process_info({'id': 'local', 'title': 'Local [video]', 'ext': 'webm',
                                      'url': f'http://127.0.0.1:{server.server_port}/source.webm',
                                      'extractor': 'generic', 'format_id': 'local'})
                    self.assertEqual(len(completed), 1)
                    final = Path(completed[0])
                    self.check_media(pp, final)
                    self.assertEqual(list((root / 'output').iterdir()), [final])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
