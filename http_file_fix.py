"""Release our own HTTP output handles before finalizing downloads on Windows."""
import os

import yt_dlp.downloader as downloaders
from yt_dlp.downloader import fragment, http


class SafeHttpFD(http.HttpFD):
    """Handle the HTTP 416 completion path that skips upstream stream.close()."""

    def real_download(self, filename, info_dict):
        self._owned_output_streams = []
        try:
            return super().real_download(filename, info_dict)
        finally:
            # Exhausted network retries can also leave an output stream open.
            try:
                for _, stream in self._owned_output_streams:
                    if not stream.closed:
                        stream.close()
            finally:
                self._owned_output_streams = []

    def sanitize_open(self, filename, open_mode):
        stream, actual_path = super().sanitize_open(filename, open_mode)
        if actual_path != '-':
            self._owned_output_streams.append((self._path_key(actual_path), stream))
        return stream, actual_path

    @staticmethod
    def _path_key(path):
        return os.path.normcase(os.path.abspath(path))

    def try_rename(self, old_filename, new_filename):
        if old_filename != new_filename and old_filename != '-':
            source = self._path_key(old_filename)
            for path, stream in self._owned_output_streams:
                if path == source and not stream.closed:
                    stream.close()
        return super().try_rename(old_filename, new_filename)


def install_http_file_fix():
    # yt-dlp selects its HTTP class here; fragment downloads keep a separate
    # imported reference. Install once before starting any worker threads.
    downloaders.HttpFD = SafeHttpFD
    fragment.HttpFD = SafeHttpFD

