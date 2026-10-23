"""Encrypted incident snapshots; AWS credentials use the standard IAM provider chain."""
import hashlib
import json
import os
from uuid import uuid4

import boto3
from botocore.config import Config

from app.security.redaction import redact_text


def redact_snapshot(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_snapshot(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_snapshot(item) for key, item in value.items()}
    return value


class ArchiveUnavailable(Exception):
    pass


def archive_incident(tenant_id: str, incident_id: int, snapshot: dict) -> dict:
    bucket = os.getenv("S3_ARCHIVE_BUCKET", "")
    if not bucket:
        raise ArchiveUnavailable()
    encryption = os.getenv("S3_SSE", "AES256")
    kms_key = os.getenv("S3_KMS_KEY_ID", "")
    if encryption not in {"AES256", "aws:kms"} or (encryption == "aws:kms" and not kms_key):
        raise ArchiveUnavailable()
    # Hash tenant identifiers so slashes and user-controlled names cannot alter prefixes.
    tenant_prefix = hashlib.sha256(tenant_id.encode()).hexdigest()
    key = f"tenants/{tenant_prefix}/incidents/{incident_id}/{uuid4().hex}.json"
    body = json.dumps(redact_snapshot(snapshot), sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(body).hexdigest()
    options = {"ServerSideEncryption": encryption}
    if encryption == "aws:kms":
        options["SSEKMSKeyId"] = kms_key
    try:
        client = boto3.client("s3", config=Config(
            connect_timeout=5, read_timeout=10, retries={"max_attempts": 2},
        ))
        result = client.put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="application/json",
            Metadata={"sha256": digest}, **options,
        )
    except Exception:
        raise ArchiveUnavailable() from None
    return {"key": key, "sha256": digest, "version_id": result.get("VersionId")}
