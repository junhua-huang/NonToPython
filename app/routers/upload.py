"""
上传路由 - FastAPI 重构版
"""
import os
import logging
from urllib.parse import unquote
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.core.config import Config
from app.utils import FileUploader
from app.services.media_moderation_route_helpers import (
    is_moderated_image_key,
    moderate_cos_image_or_raise,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---- 扩展名 → content_type 映射 ----
_EXT_CONTENT_MAP = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "mp4": "video/mp4", "avi": "video/x-msvideo", "mov": "video/quicktime",
    "wmv": "video/x-ms-wmv", "flv": "video/x-flv", "mkv": "video/x-matroska",
}

# ---- upload_type → subfolder 映射 ----
_UPLOAD_TYPE_FOLDER_MAP = {
    "avatar": "avatars",
    "cover": "covers",
    "post": "posts",
    "comic": "comic",
    "chat": "chat",
    "general": "general",
}


def _infer_subfolder(upload_type: str, user_id: int) -> str:
    """根据 upload_type 和 user_id 生成 subfolder"""
    base = _UPLOAD_TYPE_FOLDER_MAP.get(upload_type, "general")
    return f"{base}/{user_id}"


def _infer_content_type(file_type: str) -> str:
    """根据扩展名推断 content_type（不区分大小写）"""
    return _EXT_CONTENT_MAP.get(file_type.lower(), "application/octet-stream")


def _normalize_upload_extension(file_type: str) -> str:
    normalized = unquote(str(file_type or "").strip().lower()).lstrip(".")
    if normalized == "image":
        return "jpg"
    if normalized == "video":
        return "mp4"
    if normalized not in _EXT_CONTENT_MAP:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    return normalized


def _validate_final_filename(final_filename: str) -> str:
    if type(final_filename) is not str:
        raise HTTPException(status_code=400, detail="Invalid final filename")
    normalized = unquote(final_filename.strip())
    if (
        not normalized
        or normalized != os.path.basename(normalized)
        or "/" in normalized
        or "\\" in normalized
        or ".." in normalized
    ):
        raise HTTPException(status_code=400, detail="Invalid final filename")
    return normalized


def _validate_owned_upload_key(cos_key: str, user_id: int) -> str:
    if type(cos_key) is not str:
        raise HTTPException(status_code=400, detail="Invalid COS key")
    decoded = unquote(cos_key.strip())
    normalized = decoded.lstrip("/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized != cos_key.strip()
        or ".." in normalized
        or "\\" in normalized
        or any(not part for part in parts)
        or len(parts) < 3
    ):
        raise HTTPException(status_code=400, detail="Invalid COS key")
    if parts[0] not in set(_UPLOAD_TYPE_FOLDER_MAP.values()) or parts[1] != str(user_id):
        raise HTTPException(status_code=403, detail="Invalid COS key")
    return normalized


def _validate_owned_cos_url(url: str, user_id: int) -> str:
    cos_key = FileUploader.cos_key_from_url(url)
    if not cos_key:
        raise HTTPException(status_code=400, detail="Invalid COS URL")
    return _validate_owned_upload_key(cos_key, user_id)


def _delete_rejected_object(error: HTTPException, cos_key: str) -> bool:
    detail = error.detail if isinstance(error.detail, dict) else {}
    if detail.get("code") != "CONTENT_REJECTED":
        return False
    FileUploader.delete_file(FileUploader._get_cos_url(cos_key))
    return True


def _extract_file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@router.post("/presign")
def presign_upload(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    """生成客户端直传 COS 的预签名 URL

    前端发送 {filename, file_type, upload_type}
    - filename: 原始文件名（用于提取扩展名）
    - file_type: 扩展名如 "jpg"、"mp4"（优先使用）
    - upload_type: "avatar"/"cover"/"post"/"comic" 等
    返回 {presigned_url, public_url, cos_key}
    """
    filename = payload.get("filename", "")
    file_type = payload.get("file_type", "")
    upload_type = payload.get("upload_type", "general")

    decoded_filename = unquote(filename.strip()) if isinstance(filename, str) else ""
    if decoded_filename and (
        decoded_filename != os.path.basename(decoded_filename)
        or "/" in decoded_filename
        or "\\" in decoded_filename
        or ".." in decoded_filename
        or "%" in decoded_filename
    ):
        raise HTTPException(status_code=400, detail="Unsupported file type")
    filename_ext = decoded_filename.rsplit(".", 1)[-1] if "." in decoded_filename else ""
    normalized_filename_ext = _normalize_upload_extension(filename_ext) if filename_ext else ""
    # 从 filename 提取扩展名（兜底），并拒绝 filename/file_type 不一致的媒体声明。
    if not file_type and normalized_filename_ext:
        file_type = normalized_filename_ext
    if not file_type:
        file_type = "jpg"
    file_type = _normalize_upload_extension(file_type)
    if normalized_filename_ext and file_type != normalized_filename_ext:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    content_type = _infer_content_type(file_type)
    subfolder = _infer_subfolder(upload_type, user.id)
    count = payload.get("count", 1)

    logger.info(
        "[presign] user_id=%s upload_type=%s file_type=%s count=%s",
        user.id,
        upload_type,
        file_type,
        count,
    )

    if count < 1 or count > 20:
        raise HTTPException(status_code=400, detail="count must be between 1 and 20")

    if not Config.COS_BUCKET:
        raise HTTPException(status_code=503, detail="COS service not configured")

    results = FileUploader.generate_presigned_urls(subfolder, file_type, count)
    first = results[0] if results else {}

    # 检查是否有错误
    if "error" in first:
        logger.error("[presign] COS error user_id=%s", user.id)
        raise HTTPException(status_code=503, detail="预签名生成失败")

    presigned_url = first.get("upload_url", "")
    if not presigned_url:
        logger.error("[presign] pre-signed URL is empty user_id=%s count=%s", user.id, len(results))
        raise HTTPException(status_code=503, detail="预签名 URL 为空，请检查 COS 配置")

    logger.info("[presign] generated count=%s user_id=%s", len(results), user.id)

    return {
        "message": f"{len(results)} presigned URL(s) generated",
        "presigned_url": first.get("upload_url", ""),
        "public_url": first.get("access_url", ""),
        "cos_key": first.get("cos_key", ""),
        "content_type": content_type,
        "items": results,
    }


@router.post("/confirm")
def confirm_upload(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """客户端上传完成后确认，重命名临时文件"""
    cos_key = payload.get("cos_key")
    final_filename = payload.get("final_filename")
    # final_filename 为空时，用 cos_key 的原文件名兜底（cos_key 本身已是合法唯一路径）
    if cos_key and not final_filename:
        final_filename = os.path.basename(cos_key)
        logger.info("[confirm] derived stored object basename user_id=%s", user.id)
    has_object_ref = bool(cos_key)
    has_name = bool(final_filename)
    logger.info("[confirm] user_id=%s has_object_ref=%s has_name=%s", user.id, has_object_ref, has_name)
    if not cos_key or not final_filename:
        raise HTTPException(status_code=400, detail="cos_key and final_filename are required")
    cos_key = _validate_owned_upload_key(cos_key, user.id)
    final_filename = _validate_final_filename(final_filename)
    final_extension = _extract_file_extension(final_filename)
    if not final_extension or final_extension not in _EXT_CONTENT_MAP:
        raise HTTPException(status_code=400, detail="Invalid final filename")
    final_content_type = _infer_content_type(final_extension)

    try:
        moderate_cos_image_or_raise(
            cos_key,
            target_type="upload_confirm_image",
            actor_user_id=user.id,
            upload_type=cos_key.split("/", 1)[0] if "/" in cos_key else "general",
            is_public=not cos_key.startswith("chat/"),
            content_type=final_content_type,
            data_id=f"upload-confirm-{user.id}",
            db=db,
            route_key="POST /api/upload/confirm",
        )
    except HTTPException as moderation_error:
        _delete_rejected_object(moderation_error, cos_key)
        raise moderation_error

    result = FileUploader.confirm_upload(cos_key, final_filename)
    if result["success"]:
        logger.info("[confirm] success user_id=%s", user.id)
        return {"message": "Upload confirmed", "url": result["final_url"], "cos_key": result["final_cos_key"]}
    else:
        logger.error("[confirm] failed user_id=%s", user.id)
        raise HTTPException(status_code=500, detail="Upload confirmation failed")


@router.post("/avatar/confirm")
def confirm_avatar(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """确认头像上传并更新用户资料"""
    avatar_url = payload.get("url")
    has_ref = bool(avatar_url)
    logger.info("[avatar/confirm] user_id=%s has_ref=%s", user.id, has_ref)
    if not avatar_url:
        raise HTTPException(status_code=400, detail="url is required")

    avatar_cos_key = FileUploader.cos_key_from_url(avatar_url)
    if not avatar_cos_key:
        raise HTTPException(status_code=400, detail="Invalid COS URL")
    avatar_cos_key = _validate_owned_upload_key(avatar_cos_key, user.id)
    if not avatar_cos_key.startswith(f"avatars/{user.id}/"):
        raise HTTPException(status_code=403, detail="Invalid COS key")
    if not is_moderated_image_key(avatar_cos_key):
        raise HTTPException(status_code=400, detail="Invalid COS URL")
    moderate_cos_image_or_raise(
        avatar_cos_key,
        target_type="avatar_image",
        actor_user_id=user.id,
        upload_type="avatar",
        is_public=True,
        data_id=f"avatar-{user.id}",
        db=db,
        route_key="POST /api/upload/avatar/confirm",
    )

    current_user = db.query(User).filter(User.id == user.id).first()
    if current_user:
        logger.info("[avatar/confirm] updating user_id=%s", user.id)
        current_user.avatar_url = avatar_url
        db.commit()
        logger.info(f"[avatar/confirm] user {user.id} avatar updated successfully")
    else:
        logger.warning(f"[avatar/confirm] user {user.id} not found in DB")

    return {"message": "Avatar updated", "url": avatar_url}


@router.post("/cover/confirm")
def confirm_cover(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """确认封面上传并更新用户资料"""
    cover_url = payload.get("url")
    has_ref = bool(cover_url)
    logger.info("[cover/confirm] user_id=%s has_ref=%s", user.id, has_ref)
    if not cover_url:
        raise HTTPException(status_code=400, detail="url is required")

    cover_cos_key = FileUploader.cos_key_from_url(cover_url)
    if not cover_cos_key:
        raise HTTPException(status_code=400, detail="Invalid COS URL")
    cover_cos_key = _validate_owned_upload_key(cover_cos_key, user.id)
    if not cover_cos_key.startswith(f"covers/{user.id}/"):
        raise HTTPException(status_code=403, detail="Invalid COS key")
    if not is_moderated_image_key(cover_cos_key):
        raise HTTPException(status_code=400, detail="Invalid COS URL")
    moderate_cos_image_or_raise(
        cover_cos_key,
        target_type="cover_image",
        actor_user_id=user.id,
        upload_type="cover",
        is_public=True,
        data_id=f"cover-{user.id}",
        db=db,
        route_key="POST /api/upload/cover/confirm",
    )

    current_user = db.query(User).filter(User.id == user.id).first()
    if current_user:
        logger.info("[cover/confirm] updating user_id=%s", user.id)
        current_user.cover_photo_url = cover_url
        db.commit()
        logger.info(f"[cover/confirm] user {user.id} cover updated successfully")
    else:
        logger.warning(f"[cover/confirm] user {user.id} not found in DB")

    return {"message": "Cover updated", "url": cover_url}


@router.post("/delete")
def delete_file(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    """删除 COS 文件（同时接受 url 和 file_url 字段，兼容前端）"""
    url = payload.get("url") or payload.get("file_url") or ""
    if not url:
        raise HTTPException(status_code=400, detail="url or file_url is required")

    cos_key = _validate_owned_cos_url(url, user.id)
    result = FileUploader.delete_file(FileUploader._get_cos_url(cos_key))
    if result["success"]:
        return {"message": "File deleted successfully"}
    else:
        raise HTTPException(status_code=404, detail=result["error"])


@router.post("/multiple")
def upload_multiple(
    user: User = Depends(get_current_user),
):
    """批量上传（暂不支持，返回 501）"""
    raise HTTPException(status_code=501, detail="Batch upload not yet implemented")


@router.get("/info")
def get_file_info(
    url: str = Query(...),
    user: User = Depends(get_current_user),
):
    """获取 COS 文件信息"""
    cos_key = _validate_owned_cos_url(url, user.id)
    info = FileUploader.get_file_info(FileUploader._get_cos_url(cos_key))
    if info and info.get("exists"):
        return info
    else:
        raise HTTPException(status_code=404, detail="File not found")


@router.get("/uploads/{filename:path}")
def serve_upload(
    filename: str,
    user: User = Depends(get_current_user),
):
    """兼容旧 URL，302 重定向到 COS"""
    cos_key = _validate_owned_upload_key(filename, user.id)
    cos_url = FileUploader._get_cos_url(cos_key)
    return RedirectResponse(url=cos_url, status_code=302)
