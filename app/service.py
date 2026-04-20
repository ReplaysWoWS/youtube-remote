from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from .models import Job, JobStatus, VideoMeta

log = logging.getLogger("youtube-remote.service")

UPLOADER_BIN = os.environ.get("UPLOADER_BIN", "/usr/local/bin/youtubeuploader")
SECRETS_PATH = os.environ.get("UPLOADER_SECRETS", "/config/client_secrets.json")
TOKEN_PATH = os.environ.get("UPLOADER_TOKEN", "/config/request.token")
WORK_DIR = Path(os.environ.get("UPLOADER_WORK_DIR", "/tmp/youtube-remote"))
MAX_LOG_LINES = int(os.environ.get("UPLOADER_MAX_LOG_LINES", "2000"))
MAX_JOB_HISTORY = int(os.environ.get("UPLOADER_MAX_JOB_HISTORY", "200"))

_VIDEO_ID_RE = re.compile(
    r"(?:Video ID:\s*|https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/))"
    r"([A-Za-z0-9_-]{11})"
)


class UploadService:
    """Serializes youtubeuploader invocations — one upload at a time."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._logs: dict[str, list[str]] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._queue: list[str] = []
        WORK_DIR.mkdir(parents=True, exist_ok=True)

    def config_ok(self) -> tuple[bool, str]:
        if not Path(UPLOADER_BIN).exists() and not shutil.which(UPLOADER_BIN):
            return False, f"uploader binary not found at {UPLOADER_BIN}"
        if not Path(SECRETS_PATH).exists():
            return False, f"client secrets not found at {SECRETS_PATH}"
        if not Path(TOKEN_PATH).exists():
            return False, f"token cache not found at {TOKEN_PATH}"
        return True, "ok"

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def get_log(self, job_id: str) -> list[str] | None:
        return self._logs.get(job_id)

    def queue_position(self, job_id: str) -> int:
        try:
            return self._queue.index(job_id)
        except ValueError:
            return -1

    async def enqueue(
        self,
        *,
        filename: str,
        size_bytes: int,
        video_path: Path,
        meta: VideoMeta,
        thumbnail_path: Path | None,
    ) -> Job:
        job_id = uuid.uuid4().hex
        job = Job(
            id=job_id,
            status=JobStatus.QUEUED,
            created_at=datetime.now(timezone.utc),
            filename=filename,
            size_bytes=size_bytes,
            meta=meta,
        )
        self._jobs[job_id] = job
        self._logs[job_id] = []
        self._queue.append(job_id)
        self._trim_history()
        asyncio.create_task(self._run(job_id, video_path, thumbnail_path))
        return job

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(timezone.utc)
            if job_id in self._queue:
                self._queue.remove(job_id)
            return True
        if job.status == JobStatus.RUNNING:
            proc = self._procs.get(job_id)
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            return True
        return False

    async def _run(self, job_id: str, video_path: Path, thumbnail_path: Path | None) -> None:
        job = self._jobs[job_id]
        async with self._lock:
            if job.status == JobStatus.CANCELLED:
                self._cleanup_files(video_path, thumbnail_path)
                if job_id in self._queue:
                    self._queue.remove(job_id)
                return

            if job_id in self._queue:
                self._queue.remove(job_id)

            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)

            meta_file: Path | None = None
            try:
                meta_dict = job.meta.to_uploader_json()
                if meta_dict:
                    meta_file = video_path.with_suffix(video_path.suffix + ".meta.json")
                    meta_file.write_text(json.dumps(meta_dict), encoding="utf-8")

                cmd = [
                    UPLOADER_BIN,
                    "-filename", str(video_path),
                    "-secrets", SECRETS_PATH,
                    "-cache", TOKEN_PATH,
                ]
                if meta_file is not None:
                    cmd += ["-metaJSON", str(meta_file)]
                if thumbnail_path is not None:
                    cmd += ["-thumbnail", str(thumbnail_path)]

                log.info("launching uploader job=%s cmd=%s", job_id, " ".join(cmd))
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(WORK_DIR),
                )
                self._procs[job_id] = proc

                assert proc.stdout is not None
                async for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    self._append_log(job_id, line)
                    self._scan_for_video_id(job, line)

                rc = await proc.wait()
                job.exit_code = rc
                if rc == 0:
                    job.status = JobStatus.SUCCEEDED
                elif job.status != JobStatus.CANCELLED:
                    job.status = JobStatus.FAILED
                    job.error = f"uploader exited with code {rc}"
            except Exception as exc:
                log.exception("job %s failed", job_id)
                job.status = JobStatus.FAILED
                job.error = str(exc)
            finally:
                job.finished_at = datetime.now(timezone.utc)
                self._procs.pop(job_id, None)
                if meta_file is not None:
                    meta_file.unlink(missing_ok=True)
                self._cleanup_files(video_path, thumbnail_path)

    def _append_log(self, job_id: str, line: str) -> None:
        buf = self._logs.setdefault(job_id, [])
        buf.append(line)
        if len(buf) > MAX_LOG_LINES:
            del buf[: len(buf) - MAX_LOG_LINES]

    def _scan_for_video_id(self, job: Job, line: str) -> None:
        if job.video_id:
            return
        m = _VIDEO_ID_RE.search(line)
        if m:
            job.video_id = m.group(1)
            job.video_url = f"https://youtu.be/{job.video_id}"

    def _cleanup_files(self, *paths: Path | None) -> None:
        for p in paths:
            if p is None:
                continue
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                log.warning("could not remove temp file %s", p)

    def _trim_history(self) -> None:
        while len(self._jobs) > MAX_JOB_HISTORY:
            oldest_id, _ = next(iter(self._jobs.items()))
            # never evict queued/running jobs
            if self._jobs[oldest_id].status in (JobStatus.QUEUED, JobStatus.RUNNING):
                break
            self._jobs.pop(oldest_id, None)
            self._logs.pop(oldest_id, None)


def save_upload_to_tempfile(upload_file, suffix: str) -> tuple[Path, int]:
    """Stream a Starlette UploadFile to disk without loading into memory."""
    fd, tmp = tempfile.mkstemp(prefix="yt_", suffix=suffix, dir=str(WORK_DIR))
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = upload_file.file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                total += len(chunk)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp), total
