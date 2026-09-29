from __future__ import annotations

import uuid
from datetime import timedelta
from functools import lru_cache

from minio import Minio
from minio.error import S3Error

from app.core.config import settings

BUCKET = settings.MINIO_BUCKET_DOCUMENTS


@lru_cache(maxsize=1)
def _client() -> Minio:
    """Server-side client: bucket creation, stats, anything the API does itself."""
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )


@lru_cache(maxsize=1)
def _signing_client() -> Minio:
    """Client used ONLY to mint pre-signed URLs.

    The host is baked into the signature, so a URL signed against the internal
    address ("minio:9000" in Docker) is unusable by a browser and cannot be
    rewritten afterwards without breaking the signature. Identical to _client()
    unless MINIO_PUBLIC_ENDPOINT is set, so local dev is unaffected.
    """
    return Minio(
        settings.minio_public_endpoint,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.minio_public_secure,
    )


def ensure_bucket() -> None:
    c = _client()
    if not c.bucket_exists(BUCKET):
        c.make_bucket(BUCKET)


def recording_key(case_id: str, recorder: str, ext: str = "mp4") -> str:
    """Generate a unique object key for a visit recording."""
    return f"recordings/{case_id}/{recorder}/{uuid.uuid4().hex}.{ext}"


def photo_key(case_id: str, photo_type: str, ext: str = "jpg") -> str:
    """Generate organized object key for a geo-tagged case photo.
    Path: collections/YYYY/MM/{case_id[:8]}/{photo_type}_{uuid8}.jpg
    """
    from datetime import date
    d = date.today()
    return f"collections/{d.year}/{d.month:02d}/{case_id[:8]}/{photo_type}_{uuid.uuid4().hex[:12]}.{ext}"


def submission_photo_key(case_id: str, photo_type: str, submission_id: str, ext: str = "jpg") -> str:
    """The same object for every retry of one offline visit's photo (I02).
    Path: collections/offline/{case_id[:8]}/{submission_id}/{photo_type}.jpg"""
    return f"collections/offline/{str(case_id)[:8]}/{submission_id}/{photo_type}.{ext}"


def download_bytes(key: str) -> bytes:
    """Download an object from MinIO into memory (for transcription)."""
    resp = _client().get_object(BUCKET, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def presigned_upload_url(key: str, content_type: str = "video/mp4", expires_minutes: int = 30) -> str:
    """Pre-signed PUT URL — client uploads directly to MinIO."""
    ensure_bucket()
    return _signing_client().presigned_put_object(
        bucket_name=BUCKET,
        object_name=key,
        expires=timedelta(minutes=expires_minutes),
    )


def presigned_download_url(key: str, expires_minutes: int = 60) -> str:
    """Pre-signed GET URL — for streaming/playback."""
    return _signing_client().presigned_get_object(
        bucket_name=BUCKET,
        object_name=key,
        expires=timedelta(minutes=expires_minutes),
    )


def key_exists(key: str) -> bool:
    try:
        _client().stat_object(BUCKET, key)
        return True
    except S3Error:
        return False
