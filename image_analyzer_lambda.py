import json
import os
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal

import boto3

rekognition = boto3.client("rekognition")
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def to_decimal(obj):
    """DynamoDB rejects Python floats; convert via JSON round-trip."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def summarize_faces(faces):
    out = []
    for f in faces:
        emotions = sorted(f["Emotions"], key=lambda e: e["Confidence"], reverse=True)
        out.append({
            "age_range": f["AgeRange"],
            "smile": f["Smile"]["Value"],
            "eyeglasses": f["Eyeglasses"]["Value"],
            "top_emotion": emotions[0]["Type"] if emotions else None,
            "confidence": round(f["Confidence"], 1),
        })
    return out


def lambda_handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        image = {"S3Object": {"Bucket": bucket, "Name": key}}

        labels = rekognition.detect_labels(Image=image, MaxLabels=15, MinConfidence=80)
        faces = rekognition.detect_faces(Image=image, Attributes=["ALL"])
        text = rekognition.detect_text(Image=image)
        moderation = rekognition.detect_moderation_labels(Image=image, MinConfidence=60)

        label_items = [
            {"name": l["Name"], "confidence": round(l["Confidence"], 1)}
            for l in labels["Labels"]
        ]
        moderation_items = [
            {"name": m["Name"], "confidence": round(m["Confidence"], 1)}
            for m in moderation["ModerationLabels"]
        ]
        face_items = summarize_faces(faces["FaceDetails"])

        item = {
            "image_path": f"s3://{bucket}/{key}",
            "bucket": bucket,
            "key": key,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "labels": label_items,
            "label_names": [l["name"] for l in label_items],
            "face_count": len(face_items),
            "faces": face_items,
            "detected_text": [
                t["DetectedText"] for t in text["TextDetections"] if t["Type"] == "LINE"
            ],
            "moderation_flagged": len(moderation_items) > 0,
            "moderation_labels": moderation_items,
        }

        table.put_item(Item=to_decimal(item))

    return {"statusCode": 200}