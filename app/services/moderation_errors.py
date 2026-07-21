from enum import Enum

from fastapi import HTTPException


class ErrorCode(str, Enum):
    CONTENT_REJECTED = "CONTENT_REJECTED"
    MODERATION_UNAVAILABLE = "MODERATION_UNAVAILABLE"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"


_ERROR_CONTRACT = {
    ErrorCode.CONTENT_REJECTED: (422, "内容未通过审核", False),
    ErrorCode.MODERATION_UNAVAILABLE: (
        503,
        "内容审核服务暂不可用，请稍后重试",
        True,
    ),
    ErrorCode.ACCOUNT_DISABLED: (403, "账号已停用", False),
}


class AppContractError(Exception):
    def __init__(self, code: ErrorCode, cause: Exception | None = None):
        self.error_code = code
        self.status_code, self.public_message, self.retryable = _ERROR_CONTRACT[code]
        self.cause = cause
        super().__init__(self.public_message)


class ContentRejected(AppContractError):
    def __init__(self):
        super().__init__(ErrorCode.CONTENT_REJECTED)


class ModerationUnavailable(AppContractError):
    def __init__(self, cause: Exception | None = None):
        super().__init__(ErrorCode.MODERATION_UNAVAILABLE, cause)


def content_rejected() -> ContentRejected:
    return ContentRejected()


def to_http_exception(error: AppContractError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={
            "code": error.error_code.value,
            "message": error.public_message,
            "retryable": error.retryable,
        },
    )
