"""Finalize downloads as MP4 with H.264 video and AAC audio."""
import os
import tempfile
from pathlib import Path

from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
from yt_dlp.utils import PostProcessingError


class NormalizeMp4PP(FFmpegPostProcessor):
    def __init__(self, downloader, progress=lambda message: None, preserve_native_mp4=False):
        super().__init__(downloader)
        self.progress = progress
        self.preserve_native_mp4 = preserve_native_mp4

    def _streams(self, path):
        streams = self.get_metadata_object(str(path)).get('streams', [])
        video = next((s for s in streams if s.get('codec_type') == 'video'), None)
        audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
        if video is None:
            raise PostProcessingError('Không tìm thấy hình ảnh trong video để xuất MP4.')
        return video, audio

    def run(self, info):
        source = Path(info['filepath'])
        video, audio = self._streams(source)
        selected_formats = info.get('requested_formats') or [info]
        native_mp4 = self.preserve_native_mp4 and any(
            item.get('ext') == 'mp4' and item.get('vcodec') != 'none'
            for item in selected_formats)
        copy_video = video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p'
        copy_audio = audio is None or audio.get('codec_name') == 'aac'
        if native_mp4:
            copy_video = copy_audio = True
        destination = source.with_suffix('.mp4')
        if source.suffix.lower() == '.mp4' and copy_video and copy_audio:
            info['ext'] = 'mp4'
            return [], info
        if source != destination and destination.exists():
            raise PostProcessingError(f'Đã có file MP4 cùng tên, không ghi đè: {destination.name}')

        if not copy_video:
            self.progress('Không có nguồn MP4 phù hợp; đang chuyển mã sang MP4...')
        elif not copy_audio:
            self.progress('Đang chuyển âm thanh sang AAC để hoàn tất MP4...')
        else:
            self.progress('Đang ghép luồng vào MP4, không chuyển mã...')
        # Work beside the source and publish only after FFmpeg and ffprobe pass.
        # An interrupted or failed conversion must leave the source intact.
        handle, temporary = tempfile.mkstemp(prefix='.mp4-normalize-', suffix='.mp4', dir=source.parent)
        os.close(handle)
        try:
            options = ['-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'copy' if copy_video else 'libx264']
            if not copy_video:
                options += ['-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                            '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-threads', '2']
            options += ['-c:a', 'copy' if copy_audio else 'aac']
            if not copy_audio:
                options += ['-b:a', '192k']
            options += ['-movflags', '+faststart', '-f', 'mp4']
            self.run_ffmpeg(str(source), temporary, options)
            out_video, out_audio = self._streams(temporary)
            if (not native_mp4 and (out_video.get('codec_name') != 'h264'
                    or (audio is not None and (out_audio is None or out_audio.get('codec_name') != 'aac')))):
                raise PostProcessingError('Chuẩn hóa MP4 thất bại: codec đầu ra không hợp lệ.')
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        info.update(filepath=str(destination), ext='mp4', vcodec=out_video.get('codec_name'), acodec=out_audio.get('codec_name') if out_audio else 'none')
        return ([str(source)] if source != destination else []), info
