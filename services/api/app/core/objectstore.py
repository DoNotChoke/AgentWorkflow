"""Object storage tương thích S3 (§4.7)

Nơi cất những thứ quá lớn cho PostgreSQL: payload trace thô, phản hồi provider
cỡ lớn, tệp đính kèm, artifact sinh ra từ workflow. PostgreSQL giữ tham chiếu
và metadata, không giữ chính khối dữ liệu.

Module này cố ý **không biết** payload là gì. Nó nhận bucket, khoá, bytes —
việc đặt khoá theo tenant và diễn giải nội dung thuộc về domain sở hữu dữ liệu
đó (§18, §19).

``boto3`` là client **đồng bộ**. Gọi thẳng trong một coroutine sẽ chặn event
loop của cả tiến trình cho tới khi S3 trả lời, nên mọi lời gọi mạng ở đây đi
qua ``asyncio.to_thread``. Client cấp thấp của boto3 là thread-safe, nên một
instance dùng chung cho mọi thread là đúng cách dùng, không phải một chỗ tiết
kiệm.

Mã hoá at-rest là việc của bucket (SSE), không phải của tầng ứng dụng: contract
4 cấm phần lớn domain — trong đó có ``trace``, người dùng đầu tiên của module
này — import ``app.core.crypto``.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Final

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.core.errors import AppError, ErrorCategory, ValidationError
from app.core.logging import get_logger

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

log = get_logger(__name__)

_client: S3Client | None = None

_CONNECT_TIMEOUT_SECONDS: Final = 3.0
_READ_TIMEOUT_SECONDS: Final = 10.0

#: Mã lỗi S3 nghĩa là "không có gì ở khoá này". ``NoSuchBucket`` cố ý KHÔNG nằm
#: đây: bucket vắng mặt là lỗi cấu hình, không phải một đối tượng chưa được ghi.
_MISSING_CODES: Final = frozenset({"NoSuchKey", "404"})

#: Khoá hợp lệ: chữ, số, và ``. _ - /``, không mở đầu bằng dấu phân cách.
_KEY: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")


class ObjectStoreError(AppError):
    """Object storage không hoàn thành được thao tác.

    Tồn tại để người gọi bắt được **một** loại lỗi mà không phải import
    botocore — nếu không, mọi domain muốn xử lý lỗi lưu trữ đều kéo theo một
    dependency hạ tầng vào chỗ không cần nó.
    """

    category = ErrorCategory.INTERNAL


# ─────────────────────────────────────────────────────────────────────────────
# Vòng đời client
# ─────────────────────────────────────────────────────────────────────────────


def init_object_store(settings: Settings) -> None:
    """Dựng client dùng chung. Gọi một lần lúc khởi động tiến trình."""
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                # Bucket nằm trong đường dẫn, không phải trong tên miền con.
                # MinIO và phần lớn bản cài đặt tự quản chỉ hiểu kiểu này; kiểu
                # virtual-host đòi wildcard DNS mà máy phát triển không có.
                s3={"addressing_style": "path"},
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                read_timeout=_READ_TIMEOUT_SECONDS,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )


def get_object_store() -> S3Client:
    if _client is None:
        raise RuntimeError("Object store chưa được khởi tạo. Gọi init_object_store() lúc startup.")
    return _client


async def close_object_store() -> None:
    global _client
    if _client is not None:
        await asyncio.to_thread(_client.close)
        _client = None


# ─────────────────────────────────────────────────────────────────────────────
# Khoá
# ─────────────────────────────────────────────────────────────────────────────


def ensure_key_safe(key: str) -> None:
    """Chặn khoá đi ra ngoài tiền tố mà người gọi định ghi vào.

    S3 không có thư mục, nên ``..`` ở đây không phải path traversal theo nghĩa
    của một hệ thống tệp. Thứ nó thật sự phá là **tiền tố**: khoá được ghép từ
    ``tenant_id`` cộng phần đuôi, và một phần đuôi mở đầu bằng ``/`` hay mang
    ``..`` sẽ trỏ sang tiền tố của tenant khác sau khi bị chuẩn hoá ở đâu đó
    trên đường đi — proxy, CDN, hay một client S3 khác. Tiền tố tenant là ranh
    giới §19 ở tầng lưu trữ; nó phải giữ được ngay cả khi người gọi sai.
    """
    if not _KEY.match(key) or ".." in key or "//" in key:
        raise ValidationError(f"Khoá object storage không hợp lệ: {key!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Đọc và ghi
# ─────────────────────────────────────────────────────────────────────────────


async def put_object(
    *,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str = "application/json",
) -> None:
    """Ghi một đối tượng, đè lên đối tượng cũ nếu khoá đã tồn tại.

    Đè là hành vi đúng cho khoá đánh theo nội dung (băm của chính payload): ghi
    lại cùng một khoá nghĩa là ghi lại đúng những byte đã có ở đó.
    """
    ensure_key_safe(key)
    client = get_object_store()
    try:
        await asyncio.to_thread(
            client.put_object,
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
    except (ClientError, BotoCoreError) as exc:
        raise ObjectStoreError(
            f"Không ghi được vào object storage: {type(exc).__name__}.", cause=exc
        ) from exc


async def get_object(*, bucket: str, key: str) -> bytes | None:
    """Đọc một đối tượng. ``None`` nghĩa là khoá đó không có gì.

    Khoá vắng mặt không phải lỗi: một payload đã hết hạn theo lifecycle rule
    của bucket là chuyện bình thường, và người gọi cần phân biệt nó với "hạ
    tầng lưu trữ đang hỏng" — cái sau ném ``ObjectStoreError``.
    """
    ensure_key_safe(key)
    client = get_object_store()
    try:
        response = await asyncio.to_thread(client.get_object, Bucket=bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in _MISSING_CODES:
            return None
        raise ObjectStoreError(
            f"Không đọc được từ object storage: {type(exc).__name__}.", cause=exc
        ) from exc
    except BotoCoreError as exc:
        raise ObjectStoreError(
            f"Không đọc được từ object storage: {type(exc).__name__}.", cause=exc
        ) from exc


async def object_store_healthy(*, bucket: str) -> bool:
    """Bucket có tồn tại và truy cập được không. Không bao giờ ném lỗi."""
    try:
        await asyncio.to_thread(get_object_store().head_bucket, Bucket=bucket)
    except (ClientError, BotoCoreError, RuntimeError, OSError):
        return False
    return True
