"""Best-effort Neon Object Storage upload + run_artifacts metadata.

Neon buckets are S3-*compatible*: the official client is still boto3/botocore
against ``AWS_ENDPOINT_URL_S3`` from ``neon env pull`` / ``neon deploy``.
Those credentials are Neon branch credentials, not an Amazon S3 account.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from insightai.utils.logging import Logging


VIDEO_BUCKET = "insight-videos"
SCREENSHOT_BUCKET = "insight-screenshots"


def _neon_storage_configured() -> bool:
    return bool(
        os.environ.get("AWS_ACCESS_KEY_ID")
        and os.environ.get("AWS_SECRET_ACCESS_KEY")
        and os.environ.get("AWS_ENDPOINT_URL_S3")
    )


def _neon_storage_client():
    """S3 API client pointed at this branch's Neon Object Storage endpoint."""
    import boto3
    from botocore.client import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL_S3"],
        region_name=os.environ.get("AWS_REGION") or "auto",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        # Neon requires path-style addressing.
        config=Config(s3={"addressing_style": "path"}),
    )


def _insert_artifact_row(
    *,
    thread_id: str,
    step: int | None,
    kind: str,
    bucket: str,
    object_key: str,
    content_type: str,
    byte_size: int,
) -> None:
    run_log = Logging(thread_id=thread_id, step=step)
    if not (os.environ.get("DATABASE_URL") or "").strip():
        run_log.warning(f"DATABASE_URL unset; skipping run_artifacts row for {object_key}")
        return
    try:
        from insightai.db import get_database

        get_database().insert_run_artifact(
            thread_id=thread_id,
            step=step,
            kind=kind,
            bucket=bucket,
            object_key=object_key,
            content_type=content_type,
            byte_size=byte_size,
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to insert run_artifacts row for %s (table may be missing)",
            object_key,
            exc_info=True,
        )


def upload_bytes(
    body: bytes,
    *,
    bucket: str,
    object_key: str,
    content_type: str,
    kind: str,
    thread_id: str,
    step: int | None = None,
) -> dict[str, Any] | None:
    """Upload in-memory bytes to a Neon bucket. Best-effort; returns artifact dict or None."""
    run_log = Logging(thread_id=thread_id, step=step)
    if not body:
        run_log.info(f"Skip upload; empty body for {object_key}")
        return None
    if not _neon_storage_configured():
        run_log.info(
            f"Neon Object Storage env unset (AWS_* from neon env pull); "
            f"skipping upload to {bucket}/{object_key}"
        )
        return None
    try:
        client = _neon_storage_client()
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=body,
            ContentType=content_type,
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to upload bytes to Neon bucket %s/%s",
            bucket,
            object_key,
            exc_info=True,
        )
        return None

    _insert_artifact_row(
        thread_id=thread_id,
        step=step,
        kind=kind,
        bucket=bucket,
        object_key=object_key,
        content_type=content_type,
        byte_size=len(body),
    )
    run_log.info(f"Uploaded bytes to Neon bucket {bucket}/{object_key}")
    return {"kind": kind, "bucket": bucket, "object_key": object_key}


def upload_file(
    path: Path,
    *,
    bucket: str,
    object_key: str,
    content_type: str,
    kind: str,
    thread_id: str,
    step: int | None = None,
) -> dict[str, Any] | None:
    """Upload a local file to a Neon Object Storage bucket and record metadata.

    Returns ``{bucket, object_key, kind}`` on success, or ``None`` when skipped
    or failed. Evidence is best-effort and must not fail the QA run.
    """
    run_log = Logging(thread_id=thread_id, step=step)
    if not path.is_file() or path.stat().st_size <= 0:
        run_log.warning(f"Skip upload; file missing or empty: {path}")
        return None
    if not _neon_storage_configured():
        run_log.warning(
            f"Neon Object Storage env unset (AWS_* from neon env pull); "
            f"skipping upload of {path} to {bucket}/{object_key}"
        )
        return None
    byte_size = path.stat().st_size
    try:
        client = _neon_storage_client()
        client.upload_file(
            Filename=str(path),
            Bucket=bucket,
            Key=object_key,
            ExtraArgs={"ContentType": content_type},
        )
        run_log.info(f"Uploaded file to Neon bucket {bucket}/{object_key}")
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to upload %s to Neon bucket %s/%s",
            path,
            bucket,
            object_key,
            exc_info=True,
        )
        return None

    _insert_artifact_row(
        thread_id=thread_id,
        step=step,
        kind=kind,
        bucket=bucket,
        object_key=object_key,
        content_type=content_type,
        byte_size=byte_size,
    )
    return {"kind": kind, "bucket": bucket, "object_key": object_key}


def presign_get(
    *,
    bucket: str,
    object_key: str,
    expires_in: int = 900,
) -> str | None:
    """Short-lived GET URL for a private Neon object. Not stored on findings."""
    if not _neon_storage_configured():
        return None
    try:
        from botocore.client import Config
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=os.environ["AWS_ENDPOINT_URL_S3"],
            region_name=os.environ.get("AWS_REGION") or "auto",
            aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": object_key},
            ExpiresIn=max(60, min(expires_in, 3600)),
        )
    except Exception:
    
        return None
