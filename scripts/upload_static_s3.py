"""
S3에 static/ 디렉토리 파일들을 업로드하는 배포 스크립트.

사용법:
    python scripts/upload_static_s3.py

필수 환경변수:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, S3_BUCKET_NAME
"""
import os
import boto3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

s3 = boto3.client(
    "s3",
    region_name=os.environ["AWS_REGION"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
)
BUCKET = os.environ["S3_BUCKET_NAME"]
STATIC_DIR = Path(__file__).parent.parent / "static"

CONTENT_TYPES = {
    ".js":  "application/javascript",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
}

for path in STATIC_DIR.iterdir():
    if path.is_file():
        ct = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        s3.upload_file(
            str(path), BUCKET, "static/" + path.name,
            ExtraArgs={"ContentType": ct},
        )
        print(f"Uploaded: s3://{BUCKET}/static/{path.name}")

print("Done.")
