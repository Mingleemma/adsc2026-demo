"""
API for the gallery page (behind a Lambda Function URL).

GET  -> list analyzed images from DynamoDB, each with a presigned view URL
POST -> {filename, content_type} -> presigned POST so the browser can upload
        straight to s3://BUCKET_NAME/uploads/ (JPEG/PNG only, size-capped)

Env vars: BUCKET_NAME (required), TABLE_NAME (default ImageInsights), MAX_ITEMS (default 50)
"""
import json
import os
import re
import uuid
from decimal import Decimal

import boto3
from botocore.config import Config

BUCKET = os.environ["BUCKET_NAME"]
TABLE_NAME = os.environ.get("TABLE_NAME", "ImageInsights")
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "50"))
REGION = os.environ["AWS_REGION"]

VIEW_TTL = 3600                      # seconds a gallery image link stays valid
UPLOAD_TTL = 300                     # seconds an upload permission stays valid
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # Rekognition accepts up to 15 MB from S3
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}

table = boto3.resource("dynamodb").Table(TABLE_NAME)
# Regional endpoint avoids the redirect that breaks browser uploads on new buckets.
s3 = boto3.client(
    "s3",
    region_name=REGION,
    endpoint_url=f"https://s3.{REGION}.amazonaws.com",
    config=Config(signature_version="s3v4"),
)


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"Not serializable: {type(o)}")


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def _scan_all():
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def list_images():
    items = _scan_all()
    items.sort(key=lambda i: i.get("analyzed_at", ""), reverse=True)
    items = items[:MAX_ITEMS]
    for item in items:
        item["image_url"] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": item["bucket"], "Key": item["key"]},
            ExpiresIn=VIEW_TTL,
        )
    return _resp(200, {"images": items})


def create_upload(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return _resp(400, {"error": "Invalid request."})

    content_type = body.get("content_type")
    if content_type not in ALLOWED_TYPES:
        return _resp(400, {"error": "Only JPEG and PNG images are supported."})

    # Sanitise the name and force an extension that matches the S3 trigger suffixes.
    base = os.path.splitext(os.path.basename(str(body.get("filename", "image"))))[0]
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", base)[:60] or "image"
    key = f"uploads/{uuid.uuid4().hex[:8]}-{safe}{ALLOWED_TYPES[content_type]}"

    post = s3.generate_presigned_post(
        Bucket=BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, MAX_UPLOAD_BYTES],
        ],
        ExpiresIn=UPLOAD_TTL,
    )
    return _resp(200, {"url": post["url"], "fields": post["fields"], "key": key})


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "POST":
        return create_upload(event)
    return list_images()