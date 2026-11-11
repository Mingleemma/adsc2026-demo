"""
GET API for the gallery page.
Scans the ImageInsights table and returns each item with a short-lived
presigned S3 URL so the browser can display the (private) image.

Env vars: TABLE_NAME (default ImageInsights), MAX_ITEMS (default 50)
Deploy behind a Lambda Function URL (auth NONE for the demo, CORS set on the URL).
"""
import json
import os
from decimal import Decimal

import boto3
from botocore.config import Config

TABLE_NAME = os.environ.get("TABLE_NAME", "ImageInsights")
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "50"))
URL_TTL = 3600  # seconds

table = boto3.resource("dynamodb").Table(TABLE_NAME)
s3 = boto3.client("s3", config=Config(signature_version="s3v4"))


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"Not serializable: {type(o)}")


def _scan_all():
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def lambda_handler(event, context):
    items = _scan_all()
    items.sort(key=lambda i: i.get("analyzed_at", ""), reverse=True)
    items = items[:MAX_ITEMS]

    for item in items:
        item["image_url"] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": item["bucket"], "Key": item["key"]},
            ExpiresIn=URL_TTL,
        )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"images": items}, default=_json_default),
    }
