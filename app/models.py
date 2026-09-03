from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VideoMeta(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    privacyStatus: str | None = Field(
        default=None,
        description="public | unlisted | private",
    )
    categoryId: str | None = None
    publishAt: str | None = None
    recordingDate: str | None = None
    playlistIds: list[str] | None = None
    playlistTitles: list[str] | None = None
    language: str | None = None
    madeForKids: bool | None = None
    embeddable: bool | None = None
    publicStatsViewable: bool | None = None
    notifySubscribers: bool | None = None

    def to_uploader_json(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class Job(BaseModel):
    id: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    filename: str
    size_bytes: int
    meta: VideoMeta
    channel: str = "unlisted"
    exit_code: int | None = None
    video_id: str | None = None
    video_url: str | None = None
    error: str | None = None


class JobListItem(BaseModel):
    id: str
    status: JobStatus
    created_at: datetime
    filename: str
    channel: str = "unlisted"
    video_id: str | None = None


class EnqueueResponse(BaseModel):
    job_id: str
    status: JobStatus
    queue_position: int
