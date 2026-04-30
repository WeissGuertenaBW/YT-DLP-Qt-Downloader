"""
YT-DLP Qt Desktop Downloader

Recommended on macOS:
    brew install ffmpeg
    python3 -m pip install -U PySide6 yt-dlp pillow

Run from Terminal:
    python3 YT-DLP_Qt_downloader.py

Build into macOS app:
    python -m PyInstaller \
      --name "YT-DLP Qt Downloader" \
      --windowed \
      --onedir \
      --clean \
      --icon ytdlp_icon.icns \
      YT-DLP_Qt_downloader.py

Notes:
    - This app is meant for normal public videos/playlists/channels.
    - It does not use browser login cookies.
    - In packaged PyInstaller mode, it uses yt-dlp's Python API so the app does
      not accidentally relaunch itself when downloading.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal, QObject, QEvent, QTimer
from PySide6.QtGui import QPixmap, QCursor, QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


APP_TITLE = "YT-DLP Qt Downloader"

SUPPORTED_FORMATS = [
    "Default",
    "mp4",
    "mkv",
    "webm",
    "mov",
    "avi",
    "flv",
    "mp3",
    "m4a",
    "wav",
    "opus",
    "aac",
    "flac",
    "ogg",
]

VIDEO_FORMATS = {"mp4", "mkv", "webm", "mov", "avi", "flv"}
AUDIO_FORMATS = {"mp3", "m4a", "wav", "opus", "aac", "flac", "ogg"}

QUALITY_OPTIONS = [
    "Best available",
    "8K / 4320p max",
    "4K / 2160p max",
    "1440p max",
    "1080p max",
    "720p max",
    "480p max",
    "Audio only",
]

SAMPLE_RATE_OPTIONS = [
    "Best available",
    "192 kHz max",
    "176.4 kHz max",
    "96 kHz max",
    "88.2 kHz max",
    "48 kHz max",
    "44.1 kHz max",
    "32 kHz max",
    "22.05 kHz max",
]


@dataclass
class AuthSettings:
    login_choice: str = "Not logged in"

    def build_ytdlp_cookie_options(self) -> Dict[str, Any]:
        return {}

    def description(self) -> str:
        return "not logged in"


@dataclass
class VideoItem:
    index: int
    title: str
    url: str
    duration: str = ""
    uploader: str = ""
    resolution: str = "Unknown"
    filesize: str = "Unknown"
    sample_rate: str = "Unknown"
    selected: bool = True
    status: str = "Ready"
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    downloaded: str = ""
    thumbnail_url: str = ""
    playlist_title: str = ""
    channel_title: str = ""
    is_header: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)


class DependencyManager:
    @staticmethod
    def run_command(command: List[str], timeout: int = 120) -> Tuple[bool, str]:
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            return result.returncode == 0, result.stdout.strip()
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def has_python_yt_dlp() -> bool:
        try:
            import yt_dlp  # noqa
            return True
        except ImportError:
            return False

    @staticmethod
    def ensure_yt_dlp() -> Tuple[bool, str]:
        if DependencyManager.has_python_yt_dlp():
            return True, "yt-dlp Python package is installed."

        ok, output = DependencyManager.run_command(
            [sys.executable, "-m", "pip", "install", "yt-dlp"],
            timeout=300,
        )

        if ok and DependencyManager.has_python_yt_dlp():
            return True, "yt-dlp installed successfully."

        return False, output

    @staticmethod
    def update_yt_dlp() -> Tuple[bool, str]:
        if not DependencyManager.has_python_yt_dlp():
            return False, "yt-dlp Python package is not installed yet."

        return DependencyManager.run_command(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
            timeout=300,
        )

    @staticmethod
    def check_ffmpeg() -> Tuple[bool, str]:
        """
        Return the ffmpeg folder, not only the ffmpeg binary.

        yt-dlp and ffprobe are happiest when we point to a directory containing
        both ffmpeg and ffprobe.
        """
        candidate_bins = [
            shutil.which("ffmpeg"),
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/local/bin/ffmpeg",
        ]

        for ffmpeg_bin in candidate_bins:
            if not ffmpeg_bin:
                continue

            ffmpeg_path = Path(ffmpeg_bin)
            if not ffmpeg_path.exists():
                continue

            folder = ffmpeg_path.parent
            ffprobe_path = folder / "ffprobe"

            if ffprobe_path.exists():
                return True, str(folder)

            return False, (
                f"ffmpeg found at {ffmpeg_path}, but ffprobe was not found next to it. "
                "Run: brew reinstall ffmpeg"
            )

        return False, "ffmpeg not found. Install with: brew install ffmpeg"

    @staticmethod
    def check_aria2c() -> Tuple[bool, str]:
        """
        aria2c is optional. When available, yt-dlp can use it as an external
        downloader for faster fragmented downloads.
        """
        candidate_bins = [
            shutil.which("aria2c"),
            "/opt/homebrew/bin/aria2c",
            "/usr/local/bin/aria2c",
            "/opt/local/bin/aria2c",
        ]

        for aria_bin in candidate_bins:
            if not aria_bin:
                continue

            aria_path = Path(aria_bin)
            if aria_path.exists():
                return True, str(aria_path)

        return False, "aria2c not found. Optional speed boost: brew install aria2"

    @staticmethod
    def ffprobe_path(ffmpeg_folder: str) -> str:
        return str(Path(ffmpeg_folder) / "ffprobe")

    @staticmethod
    def ffmpeg_path(ffmpeg_folder: str) -> str:
        return str(Path(ffmpeg_folder) / "ffmpeg")


class YTDLPHelper:
    @staticmethod
    def import_ytdlp():
        import yt_dlp
        return yt_dlp

    @staticmethod
    def seconds_to_hms(seconds: Optional[int]) -> str:
        if seconds is None:
            return ""

        try:
            seconds = int(seconds)
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60

            if hours:
                return f"{hours}:{minutes:02d}:{secs:02d}"

            return f"{minutes}:{secs:02d}"
        except Exception:
            return ""

    @staticmethod
    def bytes_to_human(value: Optional[float]) -> str:
        if not value:
            return "Unknown"

        try:
            value = float(value)
            units = ["B", "KB", "MB", "GB", "TB"]
            index = 0

            while value >= 1024 and index < len(units) - 1:
                value /= 1024
                index += 1

            return f"{value:.2f} {units[index]}"
        except Exception:
            return "Unknown"

    @staticmethod
    def estimate_best_filesize(info: Dict[str, Any]) -> str:
        direct = info.get("filesize") or info.get("filesize_approx")

        if direct:
            return YTDLPHelper.bytes_to_human(direct)

        formats = info.get("formats") or []

        best_video_size = 0
        best_audio_size = 0
        best_single_size = 0

        for fmt in formats:
            size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
            try:
                size = int(size or 0)
            except Exception:
                size = 0

            if not size:
                continue

            has_video = fmt.get("vcodec") and fmt.get("vcodec") != "none"
            has_audio = fmt.get("acodec") and fmt.get("acodec") != "none"

            if has_video and has_audio:
                best_single_size = max(best_single_size, size)
            elif has_video:
                best_video_size = max(best_video_size, size)
            elif has_audio:
                best_audio_size = max(best_audio_size, size)

        # For bestvideo+bestaudio downloads, the final size is usually close to
        # best video-only + best audio-only. This is better than showing the small
        # default preview format size.
        combined = best_video_size + best_audio_size
        if combined:
            return YTDLPHelper.bytes_to_human(combined)

        if best_single_size:
            return YTDLPHelper.bytes_to_human(best_single_size)

        return "Unknown"

    @staticmethod
    def best_available_resolution_text(info: Dict[str, Any]) -> str:
        """
        Return the highest real video resolution advertised in formats.
        YouTube's top-level width/height can point to a low preview/default stream,
        so table/preview should prefer this instead.
        """
        formats = info.get("formats") or []

        best_width = 0
        best_height = 0
        best_score = -1.0

        for fmt in formats:
            has_video = fmt.get("vcodec") and fmt.get("vcodec") != "none"
            if not has_video:
                continue

            try:
                width = int(fmt.get("width") or 0)
                height = int(fmt.get("height") or 0)
                fps = float(fmt.get("fps") or 0)
                tbr = float(fmt.get("tbr") or 0)
            except Exception:
                width = 0
                height = 0
                fps = 0.0
                tbr = 0.0

            # Prefer height, then width, then FPS, then bitrate.
            score = (height * 10_000_000) + (width * 10_000) + (fps * 100) + tbr

            if height and score > best_score:
                best_score = score
                best_width = width
                best_height = height

        if best_width and best_height:
            return f"{best_width}x{best_height}"

        if best_height:
            return f"{best_height}p"

        # Fallback for extractors that only fill top-level fields.
        return YTDLPHelper.resolution_text(info)

    @staticmethod
    def best_available_sample_rate_text(info: Dict[str, Any]) -> str:
        """
        Return the highest audio sample rate advertised in formats.
        """
        formats = info.get("formats") or []
        best_asr = 0

        for fmt in formats:
            has_audio = fmt.get("acodec") and fmt.get("acodec") != "none"
            if not has_audio:
                continue

            try:
                asr = int(fmt.get("asr") or 0)
            except Exception:
                asr = 0

            best_asr = max(best_asr, asr)

        if best_asr:
            if best_asr >= 1000:
                khz = best_asr / 1000.0
                if abs(khz - round(khz)) < 0.05:
                    return f"{int(round(khz))} kHz"
                return f"{khz:.1f} kHz"
            return f"{best_asr} Hz"

        return YTDLPHelper.sample_rate_text(info)

    @staticmethod
    def resolution_text(info: Dict[str, Any]) -> str:
        width = info.get("width")
        height = info.get("height")

        if width and height:
            return f"{width}x{height}"

        if height:
            return f"{height}p"

        return "Unknown"

    @staticmethod
    def extract_youtube_id(value: str) -> str:
        if not value:
            return ""

        patterns = [
            r"v=([A-Za-z0-9_-]{11})",
            r"youtu\.be/([A-Za-z0-9_-]{11})",
            r"/shorts/([A-Za-z0-9_-]{11})",
            r"/embed/([A-Za-z0-9_-]{11})",
        ]

        for pattern in patterns:
            match = re.search(pattern, value)
            if match:
                return match.group(1)

        if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
            return value

        return ""

    @staticmethod
    def thumbnail_url(info: Dict[str, Any], fallback_url: str = "") -> str:
        thumb = info.get("thumbnail") or ""
        if thumb:
            return thumb

        thumbnails = info.get("thumbnails") or []
        if thumbnails:
            for candidate in reversed(thumbnails):
                if isinstance(candidate, dict):
                    thumb = candidate.get("url") or ""
                    if thumb:
                        return thumb

        video_id = (
            info.get("id")
            or YTDLPHelper.extract_youtube_id(info.get("url") or "")
            or YTDLPHelper.extract_youtube_id(info.get("webpage_url") or "")
            or YTDLPHelper.extract_youtube_id(fallback_url)
        )

        if video_id:
            return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        return ""

    @staticmethod
    def sample_rate_text(info: Dict[str, Any]) -> str:
        direct = info.get("asr") or info.get("audio_sample_rate")

        if direct:
            try:
                value = float(direct)
                if value >= 1000:
                    value = value / 1000.0
                if abs(value - round(value)) < 0.05:
                    return f"{int(round(value))} kHz"
                return f"{value:.1f} kHz"
            except Exception:
                return str(direct)

        formats = info.get("formats") or []
        best_asr = 0

        for fmt in formats:
            asr = fmt.get("asr") or 0
            try:
                asr = int(asr)
            except Exception:
                asr = 0

            has_audio = fmt.get("acodec") and fmt.get("acodec") != "none"

            if has_audio:
                best_asr = max(best_asr, asr)

        if best_asr:
            if best_asr >= 1000:
                value = best_asr / 1000.0
                if abs(value - round(value)) < 0.05:
                    return f"{int(round(value))} kHz"
                return f"{value:.1f} kHz"
            return f"{best_asr} Hz"

        return "Unknown"

    @staticmethod
    def sample_rate_filter(sample_rate_choice: str) -> str:
        match = re.search(r"(192|176\.4|96|88\.2|48|44\.1|32|22\.05)", sample_rate_choice)
        if not match:
            return ""

        value = float(match.group(1))
        hz = int(round(value * 1000))
        return f"[asr<={hz}]"

    @staticmethod
    def safe_filename(value: str, fallback: str = "Untitled") -> str:
        value = value or fallback
        value = re.sub(r'[\\/:*?"<>|]+', " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        value = value.strip(". ")
        return value or fallback

    @staticmethod
    def apply_metadata_to_item(item: VideoItem, info: Dict[str, Any]) -> None:
        if not info:
            return

        resolution = YTDLPHelper.best_available_resolution_text(info)
        if resolution != "Unknown":
            item.resolution = resolution

        sample_rate = YTDLPHelper.best_available_sample_rate_text(info)
        if sample_rate != "Unknown":
            item.sample_rate = sample_rate

        filesize = YTDLPHelper.estimate_best_filesize(info)
        if filesize != "Unknown":
            item.filesize = filesize

        if not item.thumbnail_url:
            item.thumbnail_url = YTDLPHelper.thumbnail_url(info, item.url)

        item.raw = info

    @staticmethod
    def make_output_template(download_root: Path, item: VideoItem, sort_mode: str) -> str:
        title = YTDLPHelper.safe_filename(item.title)
        playlist_title = YTDLPHelper.safe_filename(item.playlist_title, "")
        channel_title = YTDLPHelper.safe_filename(item.channel_title, "")

        target_root = download_root
        if channel_title:
            target_root = target_root / channel_title
        if playlist_title:
            target_root = target_root / playlist_title

        if sort_mode == "Download in order":
            filename = f"{item.index}. {title}.%(ext)s"
            return str(target_root / filename)

        if sort_mode == "Chronological":
            filename = "%(upload_date>%Y-%m-%d|unknown-date)s " + title + ".%(ext)s"
            return str(target_root / filename)

        return str(target_root / f"{title}.%(ext)s")

    @staticmethod
    def build_format_selector(
        quality_choice: str,
        output_format: str,
        sample_rate_choice: str = "Best available",
    ) -> str:
        """
        Best quality notes:
        - For true "Best available", do not prefer mp4 streams first.
          YouTube often puts higher resolutions in webm/vp9/av1 video-only streams.
        - We pick bestvideo+bestaudio first, then ffmpeg merges/remuxes.
        """
        audio_filter = YTDLPHelper.sample_rate_filter(sample_rate_choice)

        if quality_choice == "Audio only" or output_format in AUDIO_FORMATS:
            if audio_filter:
                return f"bestaudio{audio_filter}/bestaudio/best"
            return "bestaudio/best"

        match = re.search(r"(4320|2160|1440|1080|720|480)p", quality_choice)
        height_filter = ""
        if match:
            height = int(match.group(1))
            height_filter = f"[height<={height}]"

        if output_format == "Default":
            # Let yt-dlp choose the best working formats and container. This avoids
            # forcing MP4 when the best stream is WebM/VP9/AV1.
            if audio_filter:
                return (
                    f"bestvideo{height_filter}+bestaudio{audio_filter}/"
                    f"bestvideo{height_filter}+bestaudio/"
                    f"best{height_filter}/best"
                )
            return (
                f"bestvideo{height_filter}+bestaudio/"
                f"best{height_filter}/best"
            )

        if output_format == "mp4":
            if quality_choice == "Best available":
                if audio_filter:
                    return (
                        f"bestvideo+bestaudio{audio_filter}/"
                        f"bestvideo+bestaudio/"
                        f"best[ext=mp4][vcodec!=none][acodec!=none]/"
                        f"best"
                    )
                return (
                    f"bestvideo+bestaudio/"
                    f"best[ext=mp4][vcodec!=none][acodec!=none]/"
                    f"best"
                )

            if audio_filter:
                return (
                    f"bestvideo{height_filter}+bestaudio{audio_filter}/"
                    f"bestvideo{height_filter}+bestaudio/"
                    f"bestvideo{height_filter}[ext=mp4]+bestaudio[ext=m4a]/"
                    f"best{height_filter}[ext=mp4][vcodec!=none][acodec!=none]/"
                    f"best{height_filter}/best"
                )

            return (
                f"bestvideo{height_filter}+bestaudio/"
                f"bestvideo{height_filter}[ext=mp4]+bestaudio[ext=m4a]/"
                f"best{height_filter}[ext=mp4][vcodec!=none][acodec!=none]/"
                f"best{height_filter}/best"
            )

        if audio_filter:
            return (
                f"bestvideo{height_filter}+bestaudio{audio_filter}/"
                f"bestvideo{height_filter}+bestaudio/"
                f"best{height_filter}/best"
            )

        return (
            f"bestvideo{height_filter}+bestaudio/"
            f"best{height_filter}/best"
        )


class MediaVerifier:
    @staticmethod
    def probe_streams(file_path: Path, ffmpeg_folder: str) -> Tuple[bool, str, Dict[str, Any]]:
        ffprobe = DependencyManager.ffprobe_path(ffmpeg_folder)
        empty = {"video": 0, "audio": 0, "width": 0, "height": 0, "sample_rate": 0}

        if not Path(ffprobe).exists():
            return False, f"ffprobe not found at {ffprobe}", empty

        if not file_path.exists():
            return False, f"Final file was not found: {file_path}", empty

        cmd = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,sample_rate",
            "-of",
            "json",
            str(file_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=60,
            )
        except Exception as exc:
            return False, f"ffprobe failed to run: {exc}", empty

        if result.returncode != 0:
            return False, f"ffprobe exited with code {result.returncode}: {result.stdout.strip()}", empty

        try:
            data = json.loads(result.stdout or "{}")
        except Exception as exc:
            return False, f"ffprobe returned unreadable JSON: {exc}", empty

        streams = data.get("streams") or []
        details: Dict[str, Any] = dict(empty)

        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video":
                details["video"] += 1
                details["width"] = max(details["width"], int(stream.get("width") or 0))
                details["height"] = max(details["height"], int(stream.get("height") or 0))
            elif codec_type == "audio":
                details["audio"] += 1
                try:
                    details["sample_rate"] = max(details["sample_rate"], int(stream.get("sample_rate") or 0))
                except Exception:
                    pass

        return True, "ffprobe stream check complete.", details

    @staticmethod
    def verify_final_file(file_path: Path, output_format: str, ffmpeg_folder: str) -> Tuple[bool, str]:
        ok, msg, details = MediaVerifier.probe_streams(file_path, ffmpeg_folder)
        if not ok:
            return False, msg

        if output_format in VIDEO_FORMATS:
            if details["video"] >= 1 and details["audio"] >= 1:
                resolution = "Unknown resolution"
                if details.get("width") and details.get("height"):
                    resolution = f"{details['width']}x{details['height']}"
                return True, (
                    f"Verified merged file: {file_path.name} "
                    f"({details['video']} video stream, {details['audio']} audio stream, {resolution})."
                )
            return False, (
                f"Merge verification failed for {file_path.name}: "
                f"found {details['video']} video stream(s) and {details['audio']} audio stream(s). "
                "A proper video download should contain both video and audio."
            )

        if output_format in AUDIO_FORMATS:
            if details["audio"] >= 1:
                return True, f"Verified audio file: {file_path.name} ({details['audio']} audio stream)."
            return False, (
                f"Audio verification failed for {file_path.name}: "
                f"found {details['audio']} audio stream(s)."
            )

        return True, f"Verification skipped for unsupported output format: {output_format}"


class MediaMetadataUpdater:
    @staticmethod
    def apply_final_probe_to_item(item: VideoItem, final_file_path: Path, ffmpeg_folder: str) -> None:
        ok, _msg, details = MediaVerifier.probe_streams(final_file_path, ffmpeg_folder)
        if not ok:
            return

        width = details.get("width") or 0
        height = details.get("height") or 0
        if width and height:
            item.resolution = f"{width}x{height}"

        sample_rate = details.get("sample_rate") or 0
        if sample_rate:
            if sample_rate >= 1000:
                khz = sample_rate / 1000.0
                if abs(khz - round(khz)) < 0.05:
                    item.sample_rate = f"{int(round(khz))} kHz"
                else:
                    item.sample_rate = f"{khz:.1f} kHz"
            else:
                item.sample_rate = f"{sample_rate} Hz"

        try:
            item.filesize = YTDLPHelper.bytes_to_human(final_file_path.stat().st_size)
        except Exception:
            pass


class ManualMerger:
    @staticmethod
    def find_separate_streams(final_file_path: Path) -> Tuple[Optional[Path], Optional[Path]]:
        folder = final_file_path.parent
        expected_stem = final_file_path.stem

        if not folder.exists():
            return None, None

        candidates = list(folder.glob(f"{expected_stem}.f*.*"))
        candidates += list(folder.glob(f"{expected_stem}.*"))

        video_file: Optional[Path] = None
        audio_file: Optional[Path] = None

        video_exts = {".mp4", ".webm", ".mkv"}
        audio_exts = {".m4a", ".webm", ".opus", ".mp3", ".aac", ".wav"}

        for path in candidates:
            if path == final_file_path or not path.is_file():
                continue

            name = path.name.lower()
            suffix = path.suffix.lower()
            has_format_tag = ".f" in name

            if has_format_tag and suffix in video_exts and video_file is None:
                video_file = path
            elif has_format_tag and suffix in audio_exts and audio_file is None:
                audio_file = path

        return video_file, audio_file

    @staticmethod
    def merge_video_audio(
        video_file: Path,
        audio_file: Path,
        final_file_path: Path,
        output_format: str,
        ffmpeg_folder: str,
    ) -> Tuple[bool, str]:
        ffmpeg = Path(DependencyManager.ffmpeg_path(ffmpeg_folder))

        if not ffmpeg.exists():
            return False, f"Manual merge failed: ffmpeg not found at {ffmpeg}"

        final_file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = final_file_path.with_name(final_file_path.stem + ".manual_merge_tmp." + output_format)

        if temp_output.exists():
            try:
                temp_output.unlink()
            except Exception:
                pass

        cmd = [
            str(ffmpeg),
            "-y",
            "-i",
            str(video_file),
            "-i",
            str(audio_file),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(temp_output),
        ]

        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=600,
            )
        except Exception as exc:
            return False, f"Manual merge failed to run: {exc}"

        if result.returncode != 0:
            return False, "Manual merge failed:\n" + result.stdout[-2000:]

        try:
            if final_file_path.exists():
                final_file_path.unlink()
            temp_output.rename(final_file_path)
        except Exception as exc:
            return False, f"Manual merge created temp file but could not move it into place: {exc}"

        return True, f"Manual ffmpeg merge created: {final_file_path}"


class AnalyzeWorker(QObject):
    finished = Signal(list)
    preview = Signal(object)
    log = Signal(str)
    error = Signal(str)
    status = Signal(str)
    progress = Signal(int, str)
    done = Signal()

    def __init__(self, url: str, deep_analyze: bool = False):
        super().__init__()
        self.url = url
        self.deep_analyze = deep_analyze
        self.stop_requested = False

    def stop(self) -> None:
        self.stop_requested = True

    def normalize_entry_url(self, entry: Dict[str, Any]) -> str:
        entry_url = entry.get("url") or entry.get("webpage_url") or ""
        entry_id = entry.get("id") or ""

        if entry_url.startswith("http"):
            return entry_url

        if entry_id and re.match(r"^(PL|UU|UC|OLAK|RD|LL|FL)", entry_id):
            return f"https://www.youtube.com/playlist?list={entry_id}"

        if entry_url and re.match(r"^(PL|UU|UC|OLAK|RD|LL|FL)", entry_url):
            return f"https://www.youtube.com/playlist?list={entry_url}"

        if entry_url:
            return f"https://www.youtube.com/watch?v={entry_url}"

        if entry_id:
            return f"https://www.youtube.com/watch?v={entry_id}"

        return self.url

    def looks_like_playlist(self, entry: Dict[str, Any]) -> bool:
        entry_type = str(entry.get("_type") or "").lower()
        ie_key = str(entry.get("ie_key") or "").lower()
        url = str(entry.get("url") or entry.get("webpage_url") or "")

        if "playlist" in entry_type:
            return True
        if "playlist" in ie_key:
            return True
        if "youtube.com/playlist?list=" in url or url.startswith("playlist?list="):
            return True

        return False

    def run(self) -> None:
        self.status.emit("Analyzing...")
        self.progress.emit(3, "Step 1/7: Starting analyzer")
        self.log.emit("Step 1/7: Starting analyzer")
        self.log.emit(f"Analyzing: {self.url}")

        try:
            yt_dlp = YTDLPHelper.import_ytdlp()

            if self.deep_analyze:
                extract_flat_value = False
                self.log.emit("Analyze mode: deep. This checks each video for richer metadata, so playlists can be slower.")
            else:
                extract_flat_value = "in_playlist"
                self.log.emit("Analyze mode: fast. Playlist/channel scan is quicker, but resolution may stay Unknown until download.")

            self.progress.emit(8, "Step 2/7: Loading yt-dlp options")
            self.log.emit("Step 2/7: Loading yt-dlp options")

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": extract_flat_value,
                "ignoreerrors": True,
                "noplaylist": False,
            }

            self.progress.emit(15, "Step 3/7: Contacting YouTube and waiting for metadata")
            self.log.emit("Step 3/7: Contacting YouTube and waiting for metadata")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)

            if self.stop_requested:
                self.status.emit("Analyze canceled")
                self.log.emit("Analyze canceled.")
                return

            self.progress.emit(58, "Step 4/7: Metadata received, checking link type")
            self.log.emit("Step 4/7: Metadata received, checking link type")

            if not info:
                raise RuntimeError("yt-dlp returned no information.")

            items: List[VideoItem] = []
            is_list = info.get("_type") in {"playlist", "multi_video"} or "entries" in info

            if is_list:
                entries = [entry for entry in (info.get("entries") or []) if entry]
                top_title = info.get("title") or "Playlist / Channel"
                channel_title = info.get("channel") or info.get("uploader") or top_title

                self.progress.emit(62, f"Step 5/7: List detected: {top_title}")
                self.log.emit(f"Step 5/7: List detected: {top_title}")
                self.log.emit(f"Found {len(entries)} top-level item(s).")

                input_url_lower = self.url.lower()
                input_is_watch_playlist = "watch?" in input_url_lower and "list=" in input_url_lower
                has_playlist_children = any(self.looks_like_playlist(entry) for entry in entries)
                should_expand_playlists = has_playlist_children and not input_is_watch_playlist

                total_top_entries = max(len(entries), 1)

                if should_expand_playlists:
                    self.log.emit("Channel-style scan detected. Expanding each playlist into its videos.")

                    for playlist_number, playlist_entry in enumerate(entries, start=1):
                        if self.stop_requested:
                            self.status.emit("Analyze canceled")
                            self.log.emit("Analyze canceled while expanding playlists.")
                            return

                        playlist_url = self.normalize_entry_url(playlist_entry)
                        playlist_title = playlist_entry.get("title") or f"Playlist {playlist_number}"
                        percent = 62 + int((playlist_number / total_top_entries) * 32)
                        self.progress.emit(percent, f"Step 6/7: Expanding playlist {playlist_number} of {total_top_entries}: {playlist_title}")
                        self.log.emit(f"Expanding playlist {playlist_number}/{total_top_entries}: {playlist_title}")

                        try:
                            playlist_opts = {
                                "quiet": True,
                                "no_warnings": True,
                                "extract_flat": "in_playlist",
                                "ignoreerrors": True,
                                "noplaylist": False,
                            }
                            with yt_dlp.YoutubeDL(playlist_opts) as playlist_ydl:
                                playlist_info = playlist_ydl.extract_info(playlist_url, download=False)
                        except Exception as playlist_exc:
                            self.log.emit(f"Could not expand playlist '{playlist_title}': {playlist_exc}")
                            continue

                        video_entries = [entry for entry in ((playlist_info or {}).get("entries") or []) if entry]
                        resolved_playlist_title = (playlist_info or {}).get("title") or playlist_title
                        resolved_channel_title = (playlist_info or {}).get("channel") or (playlist_info or {}).get("uploader") or channel_title

                        header = VideoItem(
                            index=0,
                            title=f"▼ {resolved_playlist_title} ({len(video_entries)} videos)",
                            url=playlist_url,
                            duration="",
                            uploader=resolved_channel_title,
                            resolution="",
                            filesize="",
                            sample_rate="",
                            selected=False,
                            status="Playlist Header",
                            progress=0.0,
                            thumbnail_url=YTDLPHelper.thumbnail_url(playlist_entry, playlist_url),
                            playlist_title=resolved_playlist_title,
                            channel_title=resolved_channel_title,
                            is_header=True,
                            raw=playlist_info or playlist_entry,
                        )
                        items.append(header)

                        for video_index, video_entry in enumerate(video_entries, start=1):
                            video_url = self.normalize_entry_url(video_entry)
                            item = VideoItem(
                                index=video_index,
                                title=video_entry.get("title") or f"Video {video_index}",
                                url=video_url,
                                duration=YTDLPHelper.seconds_to_hms(video_entry.get("duration")),
                                uploader=video_entry.get("uploader") or resolved_channel_title or "",
                                resolution=YTDLPHelper.best_available_resolution_text(video_entry),
                                filesize=YTDLPHelper.estimate_best_filesize(video_entry),
                                sample_rate=YTDLPHelper.best_available_sample_rate_text(video_entry),
                                selected=True,
                                thumbnail_url=YTDLPHelper.thumbnail_url(video_entry, video_url),
                                playlist_title=resolved_playlist_title,
                                channel_title=resolved_channel_title,
                                raw=video_entry,
                            )
                            items.append(item)

                    self.log.emit(f"Expanded channel into {sum(1 for item in items if not getattr(item, 'is_header', False))} video item(s).")

                else:
                    list_title = top_title
                    self.progress.emit(62, f"Step 5/7: Playlist detected: {list_title}")
                    self.log.emit(f"Step 5/7: Playlist detected: {list_title}")
                    self.log.emit(f"Found {len(entries)} item(s).")

                    for idx, entry in enumerate(entries, start=1):
                        if self.stop_requested:
                            self.status.emit("Analyze canceled")
                            self.log.emit("Analyze canceled while building rows.")
                            return

                        percent = 65 + int((idx / total_top_entries) * 30)
                        self.progress.emit(percent, f"Step 6/7: Building row {idx} of {total_top_entries}")

                        entry_url = self.normalize_entry_url(entry)

                        item = VideoItem(
                            index=idx,
                            title=entry.get("title") or f"Video {idx}",
                            url=entry_url or self.url,
                            duration=YTDLPHelper.seconds_to_hms(entry.get("duration")),
                            uploader=entry.get("uploader") or info.get("uploader") or "",
                            resolution=YTDLPHelper.best_available_resolution_text(entry),
                            filesize=YTDLPHelper.estimate_best_filesize(entry),
                            sample_rate=YTDLPHelper.best_available_sample_rate_text(entry),
                            selected=True,
                            thumbnail_url=YTDLPHelper.thumbnail_url(entry, entry_url),
                            playlist_title=list_title,
                            channel_title="",
                            raw=entry,
                        )

                        items.append(item)

            else:
                self.progress.emit(75, "Step 5/7: Single video detected, preparing preview")
                self.log.emit("Step 5/7: Single video detected, preparing preview")

                item = VideoItem(
                    index=1,
                    title=info.get("title") or "Video",
                    url=info.get("webpage_url") or self.url,
                    duration=YTDLPHelper.seconds_to_hms(info.get("duration")),
                    uploader=info.get("uploader") or "",
                    resolution=YTDLPHelper.best_available_resolution_text(info),
                    filesize=YTDLPHelper.estimate_best_filesize(info),
                    sample_rate=YTDLPHelper.best_available_sample_rate_text(info),
                    selected=True,
                    thumbnail_url=YTDLPHelper.thumbnail_url(info, self.url),
                    playlist_title="",
                    channel_title="",
                    raw=info,
                )

                items.append(item)

            self.progress.emit(100, "Step 7/7: Analysis complete")
            self.log.emit("Step 7/7: Analysis complete")

            self.finished.emit(items)

            if items:
                first_preview = next((item for item in items if not getattr(item, "is_header", False)), items[0])
                self.preview.emit(first_preview)

            self.status.emit("Ready")

        except Exception as exc:
            self.status.emit("Analyze failed")
            self.error.emit(f"Analyze failed: {exc}")
            self.log.emit(traceback.format_exc())

        finally:
            self.done.emit()


class ThumbnailWorker(QObject):
    finished = Signal(bytes)
    error = Signal(str)
    done = Signal()

    def __init__(self, thumbnail_url: str):
        super().__init__()
        self.thumbnail_url = thumbnail_url

    def run(self) -> None:
        try:
            request = urllib.request.Request(
                self.thumbnail_url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                data = response.read()
            self.finished.emit(data)
        except Exception as exc:
            self.error.emit(f"Thumbnail preview failed: {exc}")
        finally:
            self.done.emit()


class DownloadWorker(QObject):
    item_update = Signal(int, object)
    log = Signal(str)
    error = Signal(str)
    status = Signal(str)
    done = Signal()

    def __init__(
        self,
        items: List[VideoItem],
        download_dir: str,
        output_format: str,
        quality_choice: str,
        sample_rate_choice: str,
        archive_enabled: bool,
        subtitle_enabled: bool,
        thumbnail_enabled: bool,
        auth_settings: AuthSettings,
        sort_mode: str = "Download in order",
    ):
        super().__init__()
        self.items = items
        self.download_dir = download_dir
        self.output_format = output_format
        self.quality_choice = quality_choice
        self.sample_rate_choice = sample_rate_choice
        self.archive_enabled = archive_enabled
        self.subtitle_enabled = subtitle_enabled
        self.thumbnail_enabled = thumbnail_enabled
        self.auth_settings = auth_settings
        self.sort_mode = sort_mode

        self.stop_requested = False
        self.last_progress_update = 0.0
        self.current_process: Optional[subprocess.Popen] = None
        self.current_item_index: Optional[int] = None

    def stop(self) -> None:
        self.stop_requested = True
        proc = getattr(self, "current_process", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    if proc.poll() is None:
                        proc.kill()
            except Exception:
                pass

    def run(self) -> None:
        self.status.emit("Downloading...")
        try:
            for idx, item in enumerate(self.items):
                if self.stop_requested:
                    break

                if getattr(item, "is_header", False):
                    continue

                self.current_item_index = idx
                item.status = "Starting"
                item.progress = 0.0
                item.speed = ""
                item.eta = ""
                item.downloaded = ""
                self.item_update.emit(idx, item)

                self.download_one(idx, item)

            self.status.emit("Ready")
            if self.stop_requested:
                self.log.emit("Download queue stopped safely. Unfinished files can be resumed later.")
            else:
                self.log.emit("Download queue finished.")

        except Exception as exc:
            self.status.emit("Download failed")
            self.error.emit(f"Download failed: {exc}")
            self.log.emit(traceback.format_exc())

        finally:
            self.current_item_index = None
            self.done.emit()

    def run_python_api_download(
        self,
        idx: int,
        item: VideoItem,
        output_template: str,
        format_selector: str,
        ffmpeg_path: str,
    ) -> Optional[Path]:
        """
        PyInstaller fix:
        In a packaged .app, sys.executable points to the app itself, not a normal
        Python interpreter. Running [sys.executable, "-m", "yt_dlp"] relaunches
        another copy of this GUI. So packaged builds use yt-dlp's Python API.
        """
        yt_dlp = YTDLPHelper.import_ytdlp()
        final_file_path: Optional[Path] = None

        def progress_hook(data: Dict[str, Any]) -> None:
            nonlocal final_file_path

            if self.stop_requested:
                raise KeyboardInterrupt("Stop requested")

            status = data.get("status")

            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                downloaded = data.get("downloaded_bytes") or 0

                if total:
                    item.progress = max(0.0, min(100.0, downloaded * 100.0 / total))

                item.speed = YTDLPHelper.bytes_to_human(data.get("speed") or 0).replace("Unknown", "")
                if item.speed:
                    item.speed += "/s"

                eta = data.get("eta")
                item.eta = YTDLPHelper.seconds_to_hms(eta) if eta is not None else ""
                item.status = "Downloading"

                now = time.time()
                # Always emit periodic updates even when yt-dlp cannot estimate total yet.
                if now - self.last_progress_update >= 0.20 or item.progress >= 100:
                    self.last_progress_update = now
                    self.item_update.emit(idx, item)

            elif status == "finished":
                filename = data.get("filename") or ""
                if filename:
                    final_file_path = Path(filename).expanduser()
                    item.downloaded = final_file_path.name
                item.status = "Processing"
                item.progress = 100.0
                self.item_update.emit(idx, item)

        ydl_opts: Dict[str, Any] = {
            "format": format_selector,
            "outtmpl": output_template,
            "noplaylist": True,
            "continuedl": True,
            "retries": 20,
            "fragment_retries": 20,
            "concurrent_fragment_downloads": 16,
            "http_chunk_size": 10485760,
            "socket_timeout": 30,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": False,
            "ffmpeg_location": ffmpeg_path,
        }

        if self.output_format in VIDEO_FORMATS:
            ydl_opts["merge_output_format"] = self.output_format

        if self.output_format in AUDIO_FORMATS:
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self.output_format,
                    "preferredquality": "0",
                }
            ]

        if self.thumbnail_enabled:
            ydl_opts["writethumbnail"] = True

        # Important:
        # Do not use aria2c inside packaged/PyInstaller app mode. yt-dlp's Python
        # progress hooks do not receive reliable byte-level progress from external
        # downloaders, so the UI can look stuck at 0%. The built-in downloader gives
        # accurate progress and still uses concurrent fragments.
        aria_ok, aria2c_path = DependencyManager.check_aria2c()
        if aria_ok and not bool(getattr(sys, "frozen", False)):
            ydl_opts["external_downloader"] = aria2c_path
            ydl_opts["external_downloader_args"] = {
                "aria2c": [
                    "-x", "16",
                    "-s", "16",
                    "-k", "1M",
                    "--file-allocation=none",
                    "--summary-interval=0",
                ]
            }

        if self.subtitle_enabled:
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = ["en", "zh-Hans", "zh-Hant", "ja"]

        self.log.emit("Packaged app mode: using yt-dlp Python API so the app does not relaunch itself.")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(item.url, download=True)

        if isinstance(info, dict):
            requested = info.get("requested_downloads") or []
            for download_info in requested:
                filepath = download_info.get("filepath") or download_info.get("filename")
                if filepath:
                    final_file_path = Path(filepath).expanduser()

            if final_file_path is None:
                filepath = info.get("filepath") or info.get("_filename") or info.get("filename")
                if filepath:
                    final_file_path = Path(filepath).expanduser()

        return final_file_path

    def verification_format(self) -> str:
        """
        Default video downloads should be verified as video+audio, while
        Default + Audio only should be verified as audio.
        """
        if self.output_format == "Default":
            if self.quality_choice == "Audio only":
                return "mp3"
            return "mp4"
        return self.output_format

    def download_one(self, idx: int, item: VideoItem) -> None:
        yt_dlp = YTDLPHelper.import_ytdlp()

        download_root = Path(self.download_dir).expanduser()
        download_root.mkdir(parents=True, exist_ok=True)

        ffmpeg_ok, ffmpeg_path = DependencyManager.check_ffmpeg()

        needs_ffmpeg = (
            self.output_format == "Default"
            or self.output_format in VIDEO_FORMATS
            or self.output_format in AUDIO_FORMATS
        )
        if needs_ffmpeg and not ffmpeg_ok:
            item.status = "Failed"
            self.item_update.emit(idx, item)
            self.log.emit("ERROR: ffmpeg/ffprobe is required for best-quality merge or audio/video conversion.")
            self.log.emit(ffmpeg_path)
            self.log.emit("Install/fix it with: brew install ffmpeg   or   brew reinstall ffmpeg")
            return

        aria_ok, aria2c_path = DependencyManager.check_aria2c()

        env = os.environ.copy()
        if ffmpeg_ok:
            env["PATH"] = ffmpeg_path + os.pathsep + env.get("PATH", "")
        if aria_ok:
            env["PATH"] = str(Path(aria2c_path).parent) + os.pathsep + env.get("PATH", "")

        format_selector = YTDLPHelper.build_format_selector(
            self.quality_choice,
            self.output_format,
            self.sample_rate_choice,
        )

        output_template = YTDLPHelper.make_output_template(download_root, item, self.sort_mode)

        try:
            self.log.emit(f"Checking media details: {item.title}")
            detail_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
            }
            if ffmpeg_ok:
                detail_opts["ffmpeg_location"] = ffmpeg_path
            with yt_dlp.YoutubeDL(detail_opts) as detail_ydl:
                detail_info = detail_ydl.extract_info(item.url, download=False)
            YTDLPHelper.apply_metadata_to_item(item, detail_info or {})
            self.log.emit(
                f"Best available advertised by yt-dlp: resolution={item.resolution}, "
                f"sample rate={item.sample_rate}, estimated size={item.filesize}"
            )
            self.item_update.emit(idx, item)
        except Exception as detail_exc:
            self.log.emit(f"Could not refresh media details before download: {detail_exc}")

        self.log.emit(f"Downloading: {item.title}")
        self.log.emit(f"Format selector: {format_selector}")
        if self.output_format == "Default":
            self.log.emit("Format mode: Default. yt-dlp chooses the best working container/codec combination.")
        self.log.emit("Best-quality mode: yt-dlp downloads best video + best audio, then ffmpeg merges them when needed.")
        self.log.emit("Verification mode: after download, ffprobe checks the final file for real audio/video streams.")
        self.log.emit("Note: YouTube usually tops out around 44.1/48 kHz audio sample rate; 96/192 kHz options only work when the source actually provides them.")

        if ffmpeg_ok:
            self.log.emit(f"ffmpeg/ffprobe folder: {ffmpeg_path}")

        if bool(getattr(sys, "frozen", False)):
            self.log.emit("Packaged app mode: using built-in yt-dlp downloader so progress updates stay accurate.")
            if aria_ok:
                self.log.emit("aria2c is installed, but it is disabled inside the packaged app because it hides live progress from yt-dlp.")
        elif aria_ok:
            self.log.emit(f"Speed booster active in Terminal mode: aria2c at {aria2c_path}")
        else:
            self.log.emit("Speed booster inactive: install with 'brew install aria2' for faster fragmented downloads.")

        # Packaged PyInstaller app: use Python API to avoid relaunching the GUI.
        if bool(getattr(sys, "frozen", False)):
            item.status = "Downloading"
            self.item_update.emit(idx, item)

            try:
                final_file_path = self.run_python_api_download(
                    idx=idx,
                    item=item,
                    output_template=output_template,
                    format_selector=format_selector,
                    ffmpeg_path=ffmpeg_path,
                )

                if final_file_path is None:
                    raise RuntimeError("yt-dlp finished, but the Python API did not report a final file path.")

                item.status = "Verifying"
                item.progress = 100.0
                item.eta = ""
                self.item_update.emit(idx, item)

                verified, verify_msg = MediaVerifier.verify_final_file(
                    final_file_path,
                    self.verification_format(),
                    ffmpeg_path,
                )
                self.log.emit(verify_msg)

                if not verified:
                    raise RuntimeError(verify_msg)

                MediaMetadataUpdater.apply_final_probe_to_item(item, final_file_path, ffmpeg_path)
                item.status = "Done"
                item.progress = 100.0
                item.eta = ""
                item.downloaded = final_file_path.name
                self.item_update.emit(idx, item)
                return

            except KeyboardInterrupt:
                item.status = "Stopped"
                self.item_update.emit(idx, item)
                return

            except Exception as exc:
                item.status = "Failed"
                self.item_update.emit(idx, item)
                self.log.emit(f"Failed: {item.title} | {exc}")
                return

        # Normal terminal mode: use yt-dlp CLI.
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--newline",
            "--progress",
            "--no-playlist",
            "--continue",
            "--retries",
            "20",
            "--fragment-retries",
            "20",
            "--concurrent-fragments",
            "16",
            "--http-chunk-size",
            "10M",
            "--socket-timeout",
            "30",
            "--check-formats",
            "--print",
            "after_move:FINAL_FILE:%(filepath)s",
            "-f",
            format_selector,
            "-o",
            output_template,
        ]

        if ffmpeg_ok:
            cmd.extend(["--ffmpeg-location", ffmpeg_path])

        if self.output_format in VIDEO_FORMATS:
            cmd.extend(["--merge-output-format", self.output_format])

        if self.output_format in AUDIO_FORMATS:
            cmd.extend(["-x", "--audio-format", self.output_format])

        if self.thumbnail_enabled:
            cmd.append("--write-thumbnail")

        if self.subtitle_enabled:
            cmd.extend([
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                "en,zh-Hans,zh-Hant,ja",
            ])

        if aria_ok:
            cmd.extend([
                "--downloader",
                aria2c_path,
                "--downloader-args",
                "aria2c:-x 16 -s 16 -k 1M --file-allocation=none --summary-interval=0",
            ])

        cmd.append(item.url)

        item.status = "Downloading"
        self.item_update.emit(idx, item)
        final_file_path: Optional[Path] = None

        try:
            creationflags = 0
            if sys.platform.startswith("win"):
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

            self.current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                bufsize=1,
                creationflags=creationflags,
            )

            assert self.current_process.stdout is not None

            for raw_line in self.current_process.stdout:
                if self.stop_requested:
                    try:
                        self.current_process.terminate()
                    except Exception:
                        pass
                    raise KeyboardInterrupt("Stop requested")

                line = raw_line.strip()
                if not line:
                    continue

                if line.startswith("FINAL_FILE:"):
                    path_text = line.replace("FINAL_FILE:", "", 1).strip()
                    if path_text:
                        final_file_path = Path(path_text).expanduser()
                        item.downloaded = final_file_path.name
                        self.item_update.emit(idx, item)
                        self.log.emit(f"Final output path reported: {final_file_path}")
                    continue

                aria_progress_match = re.search(r"\((\d+(?:\.\d+)?)%\)", line)
                if aria_progress_match and "[#" in line:
                    try:
                        item.progress = float(aria_progress_match.group(1))
                    except Exception:
                        pass

                    speed_match = re.search(r"DL:([0-9.]+[A-Za-z]+i?B)", line)
                    if speed_match:
                        item.speed = speed_match.group(1) + "/s"

                    eta_match = re.search(r"ETA:([0-9a-zA-Z:]+)", line)
                    if eta_match:
                        item.eta = eta_match.group(1)

                    item.status = "Downloading"

                    now = time.time()
                    if now - self.last_progress_update >= 0.20 or item.progress >= 100:
                        self.last_progress_update = now
                        self.item_update.emit(idx, item)

                    continue

                progress_match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
                if progress_match:
                    try:
                        item.progress = float(progress_match.group(1))
                    except Exception:
                        pass

                    size_match = re.search(r"of\s+~?([0-9.]+\s*[A-Za-z]+i?B)", line)
                    if size_match:
                        item.filesize = size_match.group(1).replace(" ", "")

                    speed_match = re.search(r"at\s+([0-9.]+\s*[A-Za-z]+i?B/s)", line)
                    if speed_match:
                        item.speed = speed_match.group(1).replace(" ", "")

                    eta_match = re.search(r"ETA\s+([0-9:]+)", line)
                    item.eta = eta_match.group(1) if eta_match else ""

                    item.status = "Downloading"

                    now = time.time()
                    if now - self.last_progress_update >= 0.20 or item.progress >= 100:
                        self.last_progress_update = now
                        self.item_update.emit(idx, item)

                    continue

                if line.startswith("[Merger]") or line.startswith("[ExtractAudio]") or line.startswith("[Fixup") or line.startswith("[Video"):
                    item.status = "Processing"
                    self.item_update.emit(idx, item)
                    self.log.emit(line)
                    continue

                if (
                    line.startswith("ERROR:")
                    or line.startswith("WARNING:")
                    or line.startswith("[download] Destination")
                    or line.startswith("[download] 100%")
                    or "has already been downloaded" in line
                    or "Deleting original file" in line
                ):
                    self.log.emit(line)

            return_code = self.current_process.wait()

            if self.stop_requested:
                raise KeyboardInterrupt("Stop requested")

            if return_code != 0:
                raise RuntimeError(f"yt-dlp CLI exited with code {return_code}")

            item.status = "Verifying"
            item.progress = 100.0
            item.eta = ""
            self.item_update.emit(idx, item)

            if final_file_path is None:
                raise RuntimeError(
                    "yt-dlp finished, but it did not report the final file path. "
                    "The app cannot verify the merge without the final path."
                )

            verified, verify_msg = MediaVerifier.verify_final_file(
                final_file_path,
                self.verification_format(),
                ffmpeg_path,
            )
            self.log.emit(verify_msg)

            if not verified and self.output_format in VIDEO_FORMATS:
                self.log.emit("yt-dlp did not produce a valid merged file. Trying manual ffmpeg merge fallback...")
                video_file, audio_file = ManualMerger.find_separate_streams(final_file_path)

                if video_file and audio_file:
                    self.log.emit(f"Found separate video stream: {video_file.name}")
                    self.log.emit(f"Found separate audio stream: {audio_file.name}")

                    merge_format = self.output_format
                    if merge_format == "Default":
                        merge_format = final_file_path.suffix.lstrip(".") or "mkv"

                    merge_ok, merge_msg = ManualMerger.merge_video_audio(
                        video_file,
                        audio_file,
                        final_file_path,
                        merge_format,
                        ffmpeg_path,
                    )
                    self.log.emit(merge_msg)

                    if merge_ok:
                        verified, verify_msg = MediaVerifier.verify_final_file(
                            final_file_path,
                            self.output_format,
                            ffmpeg_path,
                        )
                        self.log.emit(verify_msg)

                        if verified:
                            try:
                                video_file.unlink(missing_ok=True)
                                audio_file.unlink(missing_ok=True)
                                self.log.emit("Cleaned up separate stream files after successful manual merge.")
                            except Exception as cleanup_exc:
                                self.log.emit(f"Could not clean up separate stream files: {cleanup_exc}")
                else:
                    self.log.emit("Manual merge fallback could not find separate video/audio stream files.")

            if not verified:
                raise RuntimeError(verify_msg)

            MediaMetadataUpdater.apply_final_probe_to_item(item, final_file_path, ffmpeg_path)
            item.status = "Done"
            item.progress = 100.0
            item.eta = ""
            item.downloaded = final_file_path.name
            self.item_update.emit(idx, item)

        except KeyboardInterrupt:
            item.status = "Stopped"
            self.item_update.emit(idx, item)

        except Exception as exc:
            item.status = "Failed"
            self.item_update.emit(idx, item)
            self.log.emit(f"Failed: {item.title} | {exc}")

        finally:
            self.current_process = None


class UpdateWorker(QObject):
    finished = Signal(bool, str)
    status = Signal(str)
    done = Signal()

    def run(self) -> None:
        self.status.emit("Updating yt-dlp...")
        ok, msg = DependencyManager.update_yt_dlp()
        self.finished.emit(ok, msg)
        self.done.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(APP_TITLE)
        self.resize(1750, 980)
        self.setMinimumSize(1450, 820)

        self.items: List[VideoItem] = []
        self.collapsed_playlist_keys: set[str] = set()

        self.download_worker: Optional[DownloadWorker] = None
        self.download_thread: Optional[QThread] = None
        self.is_downloading = False
        self.stop_requested_by_user = False

        self.is_closing = False
        self.force_close_allowed = False
        self.close_started_at = 0.0
        self.close_timeout_seconds = 10.0
        self.close_poll_timer = QTimer(self)
        self.close_poll_timer.timeout.connect(self.finish_close_when_workers_done)

        self.active_worker_refs: List[Tuple[QThread, QObject]] = []
        self.thumbnail_request_id = 0
        self.previewed_item: Optional[VideoItem] = None
        self.previewed_thumbnail_key = ""

        self.analyze_started_at = 0.0
        self.analyze_current_text = "Analyzer idle"
        self.analyze_timer = QTimer(self)
        self.analyze_timer.timeout.connect(self.update_analyze_elapsed)

        self.setup_ui()

        QApplication.instance().installEventFilter(self)

        self.check_dependencies()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            clicked_widget = QApplication.widgetAt(QCursor.pos())

            if clicked_widget is not None and not self.is_child_of(clicked_widget, self.table):
                self.table.clearSelection()

        return super().eventFilter(watched, event)

    def is_child_of(self, widget: QObject, parent: QObject) -> bool:
        current = widget
        while current is not None:
            if current is parent:
                return True
            current = current.parent()
        return False

    def current_auth_settings(self) -> AuthSettings:
        return AuthSettings(login_choice="Not logged in")

    def create_worker_thread(self, worker: QObject) -> QThread:
        thread = QThread()
        worker.moveToThread(thread)

        self.active_worker_refs.append((thread, worker))

        def cleanup_finished_thread() -> None:
            self.active_worker_refs[:] = [
                pair for pair in self.active_worker_refs if pair[0] is not thread
            ]

        thread.finished.connect(lambda: QTimer.singleShot(1000, cleanup_finished_thread))
        return thread

    def setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)

        header_layout = QHBoxLayout()
        root_layout.addLayout(header_layout)

        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        self.analyze_step_label = QLabel("Analyzer idle")
        self.analyze_step_label.setMinimumWidth(680)
        self.analyze_step_label.setStyleSheet("color: #333333;")
        self.analyze_step_label.setVisible(False)
        header_layout.addWidget(self.analyze_step_label)

        self.analyze_progress = QProgressBar()
        self.analyze_progress.setFixedWidth(300)
        self.analyze_progress.setRange(0, 100)
        self.analyze_progress.setValue(0)
        self.analyze_progress.setVisible(False)
        header_layout.addWidget(self.analyze_progress)

        self.status_label = QLabel("Starting...")
        self.status_label.setStyleSheet("color: gray;")
        header_layout.addWidget(self.status_label)

        link_group = QGroupBox("Link")
        root_layout.addWidget(link_group)

        link_layout = QHBoxLayout(link_group)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste video, playlist, or channel link here...")
        self.url_input.returnPressed.connect(self.analyze_link)
        link_layout.addWidget(self.url_input, stretch=1)

        self.analyze_button = QPushButton("Analyze Link")
        self.analyze_button.clicked.connect(self.analyze_link)
        link_layout.addWidget(self.analyze_button)

        self.paste_button = QPushButton("Paste")
        self.paste_button.clicked.connect(self.paste_clipboard)
        link_layout.addWidget(self.paste_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_all)
        link_layout.addWidget(self.clear_button)

        options_group = QGroupBox("Download Options")
        root_layout.addWidget(options_group)

        options_layout = QVBoxLayout(options_group)

        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(8)
        options_layout.addLayout(folder_layout)

        self.folder_input = QLineEdit(str(Path.home() / "Downloads"))
        self.choose_folder_button = QPushButton("Choose...")
        self.choose_folder_button.clicked.connect(self.choose_folder)

        folder_layout.addWidget(QLabel("Folder:"))
        folder_layout.addWidget(self.folder_input, stretch=1)
        folder_layout.addWidget(self.choose_folder_button)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(12)
        options_layout.addLayout(controls_layout)

        self.format_combo = QComboBox()
        self.format_combo.addItems(SUPPORTED_FORMATS)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITY_OPTIONS)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Download in order", "No order", "Chronological"])

        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems(SAMPLE_RATE_OPTIONS)
        self.sample_rate_combo.setMinimumWidth(130)

        self.subtitles_check = QCheckBox("Subtitles")
        self.subtitles_check.setChecked(False)

        self.thumbnail_check = QCheckBox("Thumbnail file")
        self.thumbnail_check.setChecked(False)

        self.update_button = QPushButton("Update yt-dlp")
        self.update_button.clicked.connect(self.update_ytdlp)

        controls_layout.addWidget(QLabel("Format:"))
        controls_layout.addWidget(self.format_combo)

        controls_layout.addWidget(QLabel("Quality:"))
        controls_layout.addWidget(self.quality_combo)

        controls_layout.addWidget(QLabel("Sample Rate:"))
        controls_layout.addWidget(self.sample_rate_combo)

        controls_layout.addWidget(QLabel("Sort:"))
        controls_layout.addWidget(self.sort_combo)

        controls_layout.addWidget(self.subtitles_check)
        controls_layout.addWidget(self.thumbnail_check)
        controls_layout.addStretch()
        controls_layout.addWidget(self.update_button)

        preview_group = QGroupBox("Preview")
        root_layout.addWidget(preview_group)

        preview_layout = QHBoxLayout(preview_group)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.setSpacing(14)

        self.thumbnail_label = QLabel("No thumbnail")
        self.thumbnail_label.setFixedSize(220, 130)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setStyleSheet(
            "border: 1px solid #777; background: #222; color: #dddddd;"
        )
        preview_layout.addWidget(self.thumbnail_label)

        info_panel = QWidget()
        info_panel.setMaximumWidth(900)
        info_panel.setMinimumWidth(560)

        info_layout = QGridLayout(info_panel)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setHorizontalSpacing(14)
        info_layout.setVerticalSpacing(7)
        info_layout.setColumnMinimumWidth(0, 110)
        info_layout.setColumnMinimumWidth(1, 420)
        info_layout.setColumnStretch(0, 0)
        info_layout.setColumnStretch(1, 1)

        preview_layout.addWidget(info_panel, stretch=0)
        preview_layout.addStretch(1)

        self.preview_title = QLabel("Analyze a link to preview it here.")
        self.preview_title.setWordWrap(True)
        self.preview_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #000000;")

        self.preview_uploader = QLabel("-")
        self.preview_duration = QLabel("-")
        self.preview_resolution = QLabel("-")
        self.preview_sample_rate = QLabel("-")
        self.preview_size = QLabel("-")

        label_style = "color: #333333; font-weight: bold; font-size: 14px;"
        value_style = "color: #000000; font-size: 14px;"

        uploader_label = QLabel("Uploader:")
        duration_label = QLabel("Duration:")
        resolution_label = QLabel("Resolution:")
        sample_rate_label = QLabel("Sample Rate:")
        size_label = QLabel("Est. Size:")

        for label in [uploader_label, duration_label, resolution_label, sample_rate_label, size_label]:
            label.setStyleSheet(label_style)

        for value_label in [
            self.preview_uploader,
            self.preview_duration,
            self.preview_resolution,
            self.preview_sample_rate,
            self.preview_size,
        ]:
            value_label.setStyleSheet(value_style)

        info_layout.addWidget(self.preview_title, 0, 0, 1, 2)
        info_layout.addWidget(uploader_label, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(self.preview_uploader, 1, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(duration_label, 2, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(self.preview_duration, 2, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(resolution_label, 3, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(self.preview_resolution, 3, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(sample_rate_label, 4, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(self.preview_sample_rate, 4, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(size_label, 5, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        info_layout.addWidget(self.preview_size, 5, 1, alignment=Qt.AlignmentFlag.AlignLeft)

        main_layout = QHBoxLayout()
        root_layout.addLayout(main_layout, stretch=1)

        left_layout = QVBoxLayout()
        main_layout.addLayout(left_layout, stretch=4)

        self.table = QTableWidget(0, 13)

        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        self.table.setHorizontalHeaderLabels(
            [
                "Select",
                "#",
                "Title",
                "Duration",
                "Uploader",
                "Resolution",
                "Sample Rate",
                "Est. Size",
                "Status",
                "Progress",
                "Speed",
                "ETA",
                "Downloaded",
            ]
        )

        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 50)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 170)
        self.table.setColumnWidth(5, 110)
        self.table.setColumnWidth(6, 115)
        self.table.setColumnWidth(7, 110)
        self.table.setColumnWidth(8, 120)
        self.table.setColumnWidth(9, 150)
        self.table.setColumnWidth(10, 110)
        self.table.setColumnWidth(11, 80)
        self.table.setColumnWidth(12, 180)

        self.table.cellClicked.connect(self.preview_clicked_row)
        self.table.cellDoubleClicked.connect(self.toggle_row_selection)

        left_layout.addWidget(self.table)

        action_layout = QHBoxLayout()
        left_layout.addLayout(action_layout)

        self.select_all_button = QPushButton("Select All")
        self.select_all_button.clicked.connect(self.select_all)
        action_layout.addWidget(self.select_all_button)

        self.select_none_button = QPushButton("Select None")
        self.select_none_button.clicked.connect(self.select_none)
        action_layout.addWidget(self.select_none_button)

        action_layout.addStretch()

        self.stop_button = QPushButton("Stop All")
        self.stop_button.clicked.connect(self.stop_downloads)
        action_layout.addWidget(self.stop_button)

        self.resume_button = QPushButton("Resume")
        self.resume_button.clicked.connect(self.resume_downloads)
        self.resume_button.setEnabled(True)
        action_layout.addWidget(self.resume_button)

        self.download_button = QPushButton("Download Selected")
        self.download_button.clicked.connect(self.download_selected)
        action_layout.addWidget(self.download_button)

        log_group = QGroupBox("Log")
        main_layout.addWidget(log_group, stretch=2)

        log_layout = QVBoxLayout(log_group)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        log_layout.addWidget(self.log_box)

    def make_table_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def log(self, text: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_box.appendPlainText(f"[{timestamp}] {text}")

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def check_dependencies(self) -> None:
        ok, msg = DependencyManager.ensure_yt_dlp()
        self.log(msg)

        if not ok:
            self.set_status("yt-dlp setup needed")
            QMessageBox.warning(self, APP_TITLE, msg)
            return

        ffmpeg_ok, ffmpeg_msg = DependencyManager.check_ffmpeg()
        if ffmpeg_ok:
            self.log(f"ffmpeg/ffprobe found: {ffmpeg_msg}")
        else:
            self.log(f"WARNING: {ffmpeg_msg}")
            self.log("Best-quality merged video requires ffmpeg and ffprobe.")

        aria_ok, aria_msg = DependencyManager.check_aria2c()
        if aria_ok:
            self.log(f"aria2c speed booster found: {aria_msg}")
        else:
            self.log(f"Optional speed booster not active: {aria_msg}")

        self.set_status("Ready")

    def paste_clipboard(self) -> None:
        self.url_input.setText(QApplication.clipboard().text())

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Download Folder", self.folder_input.text())
        if folder:
            self.folder_input.setText(folder)

    def clear_all(self) -> None:
        self.cancel_analyze()

        if self.download_worker:
            self.download_worker.stop()

        self.items = []
        self.collapsed_playlist_keys.clear()
        self.table.clearSpans()
        self.table.setRowCount(0)
        self.url_input.clear()
        self.reset_preview()
        self.analyze_progress.setVisible(False)
        self.analyze_step_label.setVisible(False)
        self.analyze_timer.stop()
        self.analyze_button.setEnabled(True)
        self.download_button.setEnabled(True)
        self.download_button.setText("Download Selected")
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Stop All")
        self.resume_button.setEnabled(True)
        self.is_downloading = False
        self.stop_requested_by_user = False
        self.set_status("Ready")
        self.log("Cleared.")

    def cancel_analyze(self) -> None:
        worker = getattr(self, "analyze_worker", None)
        if worker:
            worker.stop()
            self.log("Analyze cancel requested.")
            self.analyze_worker = None

    def reset_preview(self) -> None:
        self.previewed_item = None
        self.previewed_thumbnail_key = ""
        self.thumbnail_label.setPixmap(QPixmap())
        self.thumbnail_label.setText("No thumbnail")
        self.thumbnail_label.setStyleSheet(
            "border: 1px solid #777; background: #222; color: #dddddd;"
        )
        self.preview_title.setText("Analyze a link to preview it here.")
        self.preview_uploader.setText("-")
        self.preview_duration.setText("-")
        self.preview_resolution.setText("-")
        self.preview_sample_rate.setText("-")
        self.preview_size.setText("-")

    def analyze_link(self) -> None:
        url = self.url_input.text().strip()

        if not url:
            QMessageBox.warning(self, APP_TITLE, "Paste a video, playlist, or channel link first.")
            return

        self.analyze_button.setEnabled(False)
        self.analyze_started_at = time.time()
        self.analyze_current_text = "Step 0/7: Queued"
        self.analyze_progress.setValue(0)
        self.analyze_progress.setVisible(True)
        self.analyze_step_label.setVisible(True)
        self.analyze_step_label.setText("Step 0/7: Queued")
        self.analyze_timer.start(1000)

        self.cancel_analyze()

        worker = AnalyzeWorker(url)
        thread = self.create_worker_thread(worker)
        self.analyze_worker = worker
        self.analyze_thread = thread

        thread.started.connect(worker.run)
        worker.finished.connect(self.on_analysis_finished)
        worker.preview.connect(self.update_preview)
        worker.log.connect(self.log)
        worker.error.connect(self.show_error)
        worker.status.connect(self.set_status)
        worker.progress.connect(self.set_analyze_progress)

        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: self.analyze_button.setEnabled(True))
        thread.finished.connect(self.finish_analyze_progress)
        thread.finished.connect(lambda: setattr(self, "analyze_worker", None))

        thread.start()

    def set_analyze_progress(self, value: int, text: str) -> None:
        self.analyze_progress.setValue(max(0, min(100, int(value))))
        self.analyze_current_text = text
        self.update_analyze_elapsed()
        self.log(text)

    def update_analyze_elapsed(self) -> None:
        if not self.analyze_started_at:
            return

        elapsed = int(time.time() - self.analyze_started_at)
        self.analyze_step_label.setText(f"{self.analyze_current_text} | {elapsed}s elapsed")

    def finish_analyze_progress(self) -> None:
        self.analyze_timer.stop()
        if self.analyze_progress.value() >= 100:
            self.analyze_step_label.setText("Analysis complete")
            QTimer.singleShot(2500, lambda: self.analyze_progress.setVisible(False))
            QTimer.singleShot(2500, lambda: self.analyze_step_label.setVisible(False))
        else:
            self.analyze_step_label.setText("Analysis stopped or failed")
            QTimer.singleShot(4500, lambda: self.analyze_progress.setVisible(False))
            QTimer.singleShot(4500, lambda: self.analyze_step_label.setVisible(False))

    def on_analysis_finished(self, items: List[VideoItem]) -> None:
        self.items = items
        self.collapsed_playlist_keys.clear()
        self.populate_table()
        self.log(f"Loaded {len(items)} item(s).")

    def populate_table(self) -> None:
        self.table.clearSpans()

        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                old_widget = self.table.cellWidget(row, col)
                if old_widget is not None:
                    self.table.removeCellWidget(row, col)
                    old_widget.deleteLater()

        self.table.setRowCount(len(self.items))

        for row, item in enumerate(self.items):
            self.set_table_row(row, item)

        self.apply_playlist_visibility()
        self.refresh_preview_after_list_change()

    def refresh_preview_after_list_change(self) -> None:
        """
        Keep the preview panel in sync when the table is rebuilt or refreshed.
        Prefer the item already being previewed. If it no longer exists, preview
        the first visible non-header video row. If only headers exist, preview
        the first visible header.
        """
        if not self.items:
            self.reset_preview()
            return

        if self.previewed_item in self.items:
            self.update_preview(self.previewed_item, refresh_thumbnail=False)
            return

        for row, item in enumerate(self.items):
            if not self.table.isRowHidden(row) and not getattr(item, "is_header", False):
                self.update_preview(item)
                return

        for row, item in enumerate(self.items):
            if not self.table.isRowHidden(row):
                self.update_preview(item)
                return

    def playlist_key(self, item: VideoItem) -> str:
        channel = getattr(item, "channel_title", "") or ""
        playlist = getattr(item, "playlist_title", "") or item.title or ""
        return f"{channel}||{playlist}"

    def is_playlist_collapsed(self, item: VideoItem) -> bool:
        return self.playlist_key(item) in self.collapsed_playlist_keys

    def set_table_row(self, row: int, item: VideoItem) -> None:
        if getattr(item, "is_header", False):
            for col in range(self.table.columnCount()):
                old_widget = self.table.cellWidget(row, col)
                if old_widget is not None:
                    self.table.removeCellWidget(row, col)
                    old_widget.deleteLater()
                old_item = self.table.item(row, col)
                if old_item is not None:
                    self.table.takeItem(row, col)

            self.table.setSpan(row, 0, 1, self.table.columnCount())

            clean_title = re.sub(r"^[▶▼][ ]*", "", item.title or "Playlist")
            arrow = "▶" if self.is_playlist_collapsed(item) else "▼"
            header_item = self.make_table_item(f"{arrow} {clean_title}")
            header_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            header_item.setBackground(QBrush(QColor("#2b2b2b")))
            header_item.setForeground(QBrush(QColor("#ffffff")))
            self.table.setItem(row, 0, header_item)
            self.table.setRowHeight(row, 32)
            return

        select_item = self.make_table_item("Yes" if item.selected else "No")
        select_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        progress = QProgressBar()
        progress.setValue(int(item.progress))
        progress.setFormat(f"{item.progress:.1f}%")

        values = [
            select_item,
            self.make_table_item(str(item.index)),
            self.make_table_item(item.title),
            self.make_table_item(item.duration),
            self.make_table_item(item.uploader),
            self.make_table_item(item.resolution),
            self.make_table_item(item.sample_rate),
            self.make_table_item(item.filesize),
            self.make_table_item(item.status),
        ]

        for col, table_item in enumerate(values):
            self.table.setItem(row, col, table_item)

        self.table.setCellWidget(row, 9, progress)
        self.table.setItem(row, 10, self.make_table_item(item.speed))
        self.table.setItem(row, 11, self.make_table_item(item.eta))
        self.table.setItem(row, 12, self.make_table_item(item.downloaded))
        self.apply_row_style(row, item)

    def apply_row_style(self, row: int, item: VideoItem) -> None:
        if getattr(item, "is_header", False):
            return

        if item.selected:
            background = QBrush(QColor("#ffffff"))
            foreground = QBrush(QColor("#000000"))
            progress_style = """
                QProgressBar {
                    border: 1px solid #111111;
                    border-radius: 3px;
                    background: #ffffff;
                    color: #000000;
                    text-align: center;
                    font-weight: bold;
                }
                QProgressBar::chunk {
                    background: #d0d0d0;
                }
            """
        else:
            background = QBrush(QColor("#141414"))
            foreground = QBrush(QColor("#f0f0f0"))
            progress_style = """
                QProgressBar {
                    border: 1px solid #444444;
                    border-radius: 3px;
                    background: #222222;
                    color: #eeeeee;
                    text-align: center;
                }
                QProgressBar::chunk {
                    background: #555555;
                }
            """

        for col in range(self.table.columnCount()):
            cell = self.table.item(row, col)
            if cell:
                cell.setBackground(background)
                cell.setForeground(foreground)

        progress = self.table.cellWidget(row, 9)
        if isinstance(progress, QProgressBar):
            progress.setStyleSheet(progress_style)

    def update_table_item(self, row: int, item: VideoItem) -> None:
        if row < 0 or row >= self.table.rowCount():
            return

        if getattr(item, "is_header", False):
            self.set_table_row(row, item)
            return

        if self.table.item(row, 0):
            self.table.item(row, 0).setText("Yes" if item.selected else "No")
        if self.table.item(row, 5):
            self.table.item(row, 5).setText(item.resolution)
        if self.table.item(row, 6):
            self.table.item(row, 6).setText(item.sample_rate)
        if self.table.item(row, 7):
            self.table.item(row, 7).setText(item.filesize)
        if self.table.item(row, 8):
            self.table.item(row, 8).setText(item.status)

        progress = self.table.cellWidget(row, 9)
        if isinstance(progress, QProgressBar):
            progress.setValue(int(item.progress))
            progress.setFormat(f"{item.progress:.1f}%")

        if self.table.item(row, 10):
            self.table.item(row, 10).setText(item.speed)
        if self.table.item(row, 11):
            self.table.item(row, 11).setText(item.eta)
        if self.table.item(row, 12):
            self.table.item(row, 12).setText(item.downloaded)

        self.apply_row_style(row, item)

    def toggle_playlist_header(self, row: int) -> None:
        if row < 0 or row >= len(self.items):
            return

        item = self.items[row]
        if not getattr(item, "is_header", False):
            return

        key = self.playlist_key(item)
        if key in self.collapsed_playlist_keys:
            self.collapsed_playlist_keys.remove(key)
        else:
            self.collapsed_playlist_keys.add(key)

        self.set_table_row(row, item)
        self.apply_playlist_visibility()
        self.update_preview(item)

    def apply_playlist_visibility(self) -> None:
        collapsed = False

        for row, item in enumerate(self.items):
            if getattr(item, "is_header", False):
                collapsed = self.is_playlist_collapsed(item)
                self.table.setRowHidden(row, False)
            else:
                self.table.setRowHidden(row, collapsed)

    def preview_clicked_row(self, row: int, column: int) -> None:
        if 0 <= row < len(self.items):
            if getattr(self.items[row], "is_header", False):
                self.toggle_playlist_header(row)
                return
            self.update_preview(self.items[row])

    def toggle_row_selection(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self.items):
            return

        if getattr(self.items[row], "is_header", False):
            self.toggle_playlist_header(row)
            return

        self.items[row].selected = not self.items[row].selected
        self.update_table_item(row, self.items[row])
        self.update_preview(self.items[row])

    def select_all(self) -> None:
        for item in self.items:
            if not getattr(item, "is_header", False):
                item.selected = True
        self.populate_table()

    def select_none(self) -> None:
        for item in self.items:
            if not getattr(item, "is_header", False):
                item.selected = False
        self.populate_table()

    def update_preview_info(self, item: VideoItem) -> None:
        self.preview_title.setText(item.title or "Untitled")
        self.preview_uploader.setText(item.uploader or "Unknown")
        self.preview_duration.setText(item.duration or "Unknown")
        self.preview_resolution.setText(item.resolution or "Unknown")
        self.preview_sample_rate.setText(item.sample_rate or "Unknown")
        self.preview_size.setText(item.filesize or "Unknown")

    def update_preview(self, item: VideoItem, refresh_thumbnail: bool = True) -> None:
        previous_item = self.previewed_item
        self.previewed_item = item
        self.update_preview_info(item)

        thumb_url = item.thumbnail_url or ""
        if not thumb_url and item.raw:
            thumb_url = YTDLPHelper.thumbnail_url(item.raw, item.url)

        thumbnail_key = f"{id(item)}||{thumb_url}"

        # Do not reload the thumbnail when only metadata changed. This prevents
        # flickering and repeated thumbnail network requests during progress updates.
        if not refresh_thumbnail or (previous_item is item and thumbnail_key == self.previewed_thumbnail_key):
            return

        self.previewed_thumbnail_key = thumbnail_key

        if not thumb_url:
            self.thumbnail_label.setPixmap(QPixmap())
            self.thumbnail_label.setText("No thumbnail")
            self.thumbnail_label.setStyleSheet(
                "border: 1px solid #777; background: #222; color: #dddddd;"
            )
            return

        self.thumbnail_label.setPixmap(QPixmap())
        self.thumbnail_label.setText("Loading...")
        self.thumbnail_label.setStyleSheet(
            "border: 1px solid #777; background: #222; color: #dddddd;"
        )

        self.thumbnail_request_id += 1
        request_id = self.thumbnail_request_id

        worker = ThumbnailWorker(thumb_url)
        thread = self.create_worker_thread(worker)

        thread.started.connect(worker.run)

        def handle_thumbnail(data: bytes) -> None:
            if request_id == self.thumbnail_request_id:
                self.on_thumbnail_loaded(data)

        worker.finished.connect(handle_thumbnail)
        worker.error.connect(self.log)
        worker.done.connect(thread.quit)

        thread.start()

    def on_thumbnail_loaded(self, data: bytes) -> None:
        pixmap = QPixmap()
        pixmap.loadFromData(data)

        if pixmap.isNull():
            self.thumbnail_label.setPixmap(QPixmap())
            self.thumbnail_label.setText("No thumbnail")
            self.thumbnail_label.setStyleSheet(
                "border: 1px solid #777; background: #222; color: #dddddd;"
            )
            return

        pixmap = pixmap.scaled(
            220,
            130,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.thumbnail_label.setText("")
        self.thumbnail_label.setStyleSheet("border: 1px solid #777; background: #111;")
        self.thumbnail_label.setPixmap(pixmap)

    def update_ytdlp(self) -> None:
        self.update_button.setEnabled(False)

        worker = UpdateWorker()
        thread = self.create_worker_thread(worker)

        thread.started.connect(worker.run)
        worker.status.connect(self.set_status)
        worker.finished.connect(self.on_update_finished)

        worker.done.connect(thread.quit)
        thread.finished.connect(lambda: self.update_button.setEnabled(True))

        thread.start()

    def on_update_finished(self, ok: bool, msg: str) -> None:
        if ok:
            self.log("yt-dlp update complete.")
            if msg:
                self.log(msg[-800:])
        else:
            self.log("WARNING: yt-dlp update failed.")
            self.log(msg)
        self.set_status("Ready")

    def download_selected(self) -> None:
        selected_with_rows = [
            (table_row, item)
            for table_row, item in enumerate(self.items)
            if item.selected and not getattr(item, "is_header", False)
        ]

        if not selected_with_rows:
            QMessageBox.warning(self, APP_TITLE, "No videos selected.")
            return

        self.start_download_queue(selected_with_rows)

    def resume_downloads(self) -> None:
        failed_first_statuses = {"Failed", "Stopped"}
        failed_items = [
            (table_row, item)
            for table_row, item in enumerate(self.items)
            if item.selected
            and not getattr(item, "is_header", False)
            and item.status in failed_first_statuses
        ]
        unfinished_items = [
            (table_row, item)
            for table_row, item in enumerate(self.items)
            if item.selected
            and not getattr(item, "is_header", False)
            and item.status not in {"Done", "Failed", "Stopped", "Playlist Header"}
        ]

        resumable = []
        seen = set()
        for row, item in failed_items + unfinished_items:
            key = id(item)
            if key not in seen:
                seen.add(key)
                resumable.append((row, item))

        if not resumable:
            QMessageBox.information(self, APP_TITLE, "Nothing left to resume.")
            return

        self.log("Resuming failed/stopped/unfinished selected videos. Failed items go first.")
        self.start_download_queue(resumable)

    def start_download_queue(self, selected_with_rows: List[Tuple[int, VideoItem]]) -> None:
        folder = self.folder_input.text().strip()
        if not folder:
            QMessageBox.warning(self, APP_TITLE, "Choose a download folder first.")
            return

        selected_items = [item for _, item in selected_with_rows]
        self.download_row_map = [row for row, _ in selected_with_rows]

        self.is_downloading = True
        self.stop_requested_by_user = False

        self.download_button.setEnabled(False)
        self.download_button.setText("Downloading...")
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Stop All")
        self.resume_button.setEnabled(False)

        self.download_worker = DownloadWorker(
            items=selected_items,
            download_dir=folder,
            output_format=self.format_combo.currentText(),
            quality_choice=self.quality_combo.currentText(),
            sample_rate_choice=self.sample_rate_combo.currentText(),
            archive_enabled=False,
            subtitle_enabled=self.subtitles_check.isChecked(),
            thumbnail_enabled=self.thumbnail_check.isChecked(),
            auth_settings=self.current_auth_settings(),
            sort_mode=self.sort_combo.currentText(),
        )

        self.download_thread = self.create_worker_thread(self.download_worker)

        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.item_update.connect(self.on_download_item_update)
        self.download_worker.log.connect(self.log)
        self.download_worker.error.connect(self.show_error)
        self.download_worker.status.connect(self.set_status)
        self.download_worker.done.connect(self.on_download_done)

        self.download_worker.done.connect(self.download_thread.quit)

        self.download_thread.start()

    def on_download_item_update(self, queue_row: int, item: VideoItem) -> None:
        if hasattr(self, "download_row_map") and queue_row < len(self.download_row_map):
            table_row = self.download_row_map[queue_row]
        else:
            table_row = queue_row

        self.update_table_item(table_row, item)

        if 0 <= table_row < len(self.items):
            selected_rows = self.table.selectionModel().selectedRows()
            if item is self.previewed_item:
                self.update_preview(item, refresh_thumbnail=False)
            elif selected_rows and selected_rows[0].row() == table_row:
                self.update_preview(item, refresh_thumbnail=True)

    def on_download_done(self) -> None:
        self.is_downloading = False
        self.download_button.setEnabled(True)
        self.download_button.setText("Download Selected")
        self.stop_button.setText("Stop All")
        self.stop_button.setEnabled(True)
        self.resume_button.setEnabled(True)
        self.set_status("Ready")

        self.show_download_summary_popup()
        self.download_worker = None

    def show_download_summary_popup(self) -> None:
        if self.is_closing:
            return

        selected_download_items = [
            item for item in self.items
            if item.selected and not getattr(item, "is_header", False)
        ]

        if not selected_download_items:
            return

        done_items = [item for item in selected_download_items if item.status == "Done"]
        failed_items = [item for item in selected_download_items if item.status == "Failed"]
        stopped_items = [item for item in selected_download_items if item.status == "Stopped"]
        unfinished_items = [
            item for item in selected_download_items
            if item.status not in {"Done", "Failed", "Stopped"}
        ]

        title = "Download Complete"
        lines = [
            f"Done: {len(done_items)}",
            f"Failed: {len(failed_items)}",
            f"Stopped/unfinished: {len(stopped_items) + len(unfinished_items)}",
        ]

        if failed_items:
            lines.append("")
            lines.append("Failed items:")
            for index, item in enumerate(failed_items[:20], start=1):
                lines.append(f"{index}. {item.title}")
            if len(failed_items) > 20:
                lines.append(f"...and {len(failed_items) - 20} more")
            lines.append("")
            lines.append("Use the Resume button to retry failed items first.")

        if stopped_items and not failed_items:
            lines.append("")
            lines.append("Downloads were stopped before the queue finished.")
            lines.append("Use the Resume button to continue.")

        if failed_items:
            QMessageBox.warning(self, title, "\n".join(lines))
        else:
            QMessageBox.information(self, title, "\n".join(lines))

    def stop_downloads(self) -> None:
        self.cancel_analyze()

        if self.download_worker and self.is_downloading:
            self.stop_requested_by_user = True
            self.download_worker.stop()
            self.stop_button.setEnabled(False)
            self.stop_button.setText("Stopping...")
            self.log("Stop requested. Current file may finish a network fragment first.")
        else:
            self.log("Nothing is currently downloading.")

    def show_error(self, text: str) -> None:
        self.log(f"ERROR: {text}")
        if not self.is_closing:
            QMessageBox.critical(self, APP_TITLE, text)

    def begin_graceful_close(self) -> None:
        if self.is_closing:
            return

        self.is_closing = True
        self.close_started_at = time.time()
        self.set_status("Closing safely...")
        self.log("Close requested. Stopping active work safely before quitting.")

        self.analyze_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.update_button.setEnabled(False)

        self.cancel_analyze()

        if self.download_worker:
            self.stop_requested_by_user = True
            self.download_worker.stop()
            self.log("Download stop requested for graceful app close.")

        for item in self.items:
            if not getattr(item, "is_header", False) and item.status in {"Ready", "Starting", "Downloading", "Processing", "Verifying"}:
                item.status = "Stopped"

        self.populate_table()
        self.close_poll_timer.start(250)

    def force_close_workers(self) -> None:
        """
        Last-resort shutdown.

        Normal close asks workers to stop first. If a QThread is stuck inside a
        network call or native library longer than close_timeout_seconds, waiting
        forever makes the app look frozen. On app shutdown only, terminate the
        remaining threads so the window can actually close.
        """
        self.log("Close timeout reached. Force-stopping remaining worker threads.")

        if self.download_worker:
            try:
                self.download_worker.stop()
            except Exception:
                pass

        worker = getattr(self, "analyze_worker", None)
        if worker:
            try:
                worker.stop()
            except Exception:
                pass

        for thread, _worker in list(self.active_worker_refs):
            if not thread.isRunning():
                continue

            try:
                thread.requestInterruption()
            except Exception:
                pass

            try:
                thread.quit()
                if thread.wait(500):
                    continue
            except Exception:
                pass

            try:
                thread.terminate()
                thread.wait(1000)
            except Exception:
                pass

        self.active_worker_refs.clear()
        self.download_worker = None
        self.download_thread = None
        self.is_downloading = False

    def workers_are_done(self) -> bool:
        for thread, _worker in list(self.active_worker_refs):
            if thread.isRunning():
                return False
        return True

    def finish_close_when_workers_done(self) -> None:
        if self.workers_are_done():
            self.close_poll_timer.stop()
            self.force_close_allowed = True
            self.log("All workers stopped. Closing app now.")
            self.close()
            return

        if self.close_started_at and (time.time() - self.close_started_at) >= self.close_timeout_seconds:
            self.close_poll_timer.stop()
            self.force_close_workers()
            self.force_close_allowed = True
            self.log("Forced shutdown complete. Closing app now.")
            self.close()

    def closeEvent(self, event) -> None:
        if self.force_close_allowed:
            event.accept()
            return

        active_threads = any(thread.isRunning() for thread, _worker in list(self.active_worker_refs))

        if active_threads or self.download_worker or self.is_downloading:
            event.ignore()
            self.begin_graceful_close()

            # If the user tries to close again after the graceful close has begun,
            # shorten the remaining wait. This makes the second close feel like
            # "please quit now" without instantly killing threads on the first click.
            if self.is_closing and self.close_started_at:
                elapsed = time.time() - self.close_started_at
                if elapsed > 2.0:
                    self.close_timeout_seconds = min(self.close_timeout_seconds, elapsed + 2.0)
            return

        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
