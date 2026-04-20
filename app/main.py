from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .auth import require_token
from .models import EnqueueResponse, Job, JobListItem, JobStatus, VideoMeta
from .service import UploadService, save_upload_to_tempfile

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="youtube-remote", version="1.0.0")
service = UploadService()


@app.get("/health")
def health() -> dict[str, object]:
    ok, msg = service.config_ok()
    return {"ok": ok, "detail": msg}


@app.get("/jobs", response_model=list[JobListItem], dependencies=[Depends(require_token)])
def list_jobs() -> list[JobListItem]:
    return [
        JobListItem(
            id=j.id,
            status=j.status,
            created_at=j.created_at,
            filename=j.filename,
            video_id=j.video_id,
        )
        for j in service.list_jobs()
    ]


@app.get("/jobs/{job_id}", response_model=Job, dependencies=[Depends(require_token)])
def get_job(job_id: str) -> Job:
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/jobs/{job_id}/log", response_class=PlainTextResponse, dependencies=[Depends(require_token)])
def get_job_log(job_id: str) -> str:
    lines = service.get_log(job_id)
    if lines is None:
        raise HTTPException(status_code=404, detail="job not found")
    return "\n".join(lines)


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
async def cancel_job(job_id: str) -> dict[str, object]:
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    ok = await service.cancel(job_id)
    return {"ok": ok, "status": job.status}


@app.post("/upload", response_model=EnqueueResponse, status_code=202, dependencies=[Depends(require_token)])
async def upload(
    video: UploadFile = File(..., description="Video file to upload"),
    title: str | None = Form(None),
    description: str | None = Form(None),
    tags: str | None = Form(None, description="Comma-separated tags"),
    privacyStatus: str | None = Form(None, description="public | unlisted | private"),
    categoryId: str | None = Form(None),
    publishAt: str | None = Form(None),
    recordingDate: str | None = Form(None),
    playlistIds: str | None = Form(None, description="Comma-separated playlist IDs"),
    playlistTitles: str | None = Form(None, description="Comma-separated playlist titles"),
    language: str | None = Form(None),
    madeForKids: bool | None = Form(None),
    embeddable: bool | None = Form(None),
    publicStatsViewable: bool | None = Form(None),
    notifySubscribers: bool | None = Form(None),
    meta_json: str | None = Form(None, description="Full meta JSON; overrides other fields"),
    thumbnail: UploadFile | str | None = File(None),
) -> EnqueueResponse:
    ok, msg = service.config_ok()
    if not ok:
        raise HTTPException(status_code=503, detail=msg)

    if meta_json:
        try:
            meta = VideoMeta(**json.loads(meta_json))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid meta_json: {exc}") from exc
    else:
        meta = VideoMeta(
            title=title,
            description=description,
            tags=_split_csv(tags),
            privacyStatus=privacyStatus,
            categoryId=categoryId,
            publishAt=publishAt,
            recordingDate=recordingDate,
            playlistIds=_split_csv(playlistIds),
            playlistTitles=_split_csv(playlistTitles),
            language=language,
            madeForKids=madeForKids,
            embeddable=embeddable,
            publicStatsViewable=publicStatsViewable,
            notifySubscribers=notifySubscribers,
        )

    video_suffix = Path(video.filename or "upload.mp4").suffix or ".mp4"
    video_path, size = save_upload_to_tempfile(video, video_suffix)

    thumb_path: Path | None = None
    if isinstance(thumbnail, UploadFile) and thumbnail.filename:
        thumb_suffix = Path(thumbnail.filename).suffix or ".jpg"
        thumb_path, _ = save_upload_to_tempfile(thumbnail, thumb_suffix)

    job = await service.enqueue(
        filename=video.filename or "upload.mp4",
        size_bytes=size,
        video_path=video_path,
        meta=meta,
        thumbnail_path=thumb_path,
    )
    return EnqueueResponse(
        job_id=job.id,
        status=job.status,
        queue_position=service.queue_position(job.id),
    )


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [p.strip() for p in value.split(",") if p.strip()]
    return items or None
