"""S3-compatible storage (Cloudflare R2 in production, plan §11). boto3 is sync, so calls run in a thread."""

import asyncio
from typing import Any


class S3Storage:
    def __init__(self, client: Any, bucket: str):
        self._client = client
        self._bucket = bucket

    @classmethod
    def create(cls, endpoint_url: str, access_key: str, secret_key: str, bucket: str, region: str = "auto"):
        import boto3

        client = boto3.client("s3", endpoint_url=endpoint_url or None, aws_access_key_id=access_key,
                              aws_secret_access_key=secret_key, region_name=region)
        return cls(client, bucket)

    async def put(self, key: str, data: bytes, mime: str) -> None:
        await asyncio.to_thread(self._client.put_object, Bucket=self._bucket, Key=key, Body=data, ContentType=mime)

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def url(self, key: str, expires_s: int = 3600) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url, "get_object",
            Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_s,
        )
