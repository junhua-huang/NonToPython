"""
上传路由 - FastAPI 重构版
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.core.config import Config
from app.utils import FileUploader

logger = logging.getLogger(__name__)
router = APIRouter()


# ---- 扩展名 → content_type 映射 ----
_EXT_CONTENT_MAP = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "svg": "image/svg+xml",
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

    # 从 filename 提取扩展名（兜底）
    if not file_type and "." in filename:
        file_type = filename.rsplit(".", 1)[-1]
    if not file_type:
        file_type = "jpg"

    content_type = _infer_content_type(file_type)
    subfolder = _infer_subfolder(upload_type, user.id)
    count = payload.get("count", 1)

    logger.info(f"[presign] user_id={user.id} upload_type={upload_type} file_type={file_type} filename={filename} subfolder={subfolder}")

    if count < 1 or count > 20:
        raise HTTPException(status_code=400, detail="count must be between 1 and 20")

    if not Config.COS_BUCKET_NAME:
        raise HTTPException(status_code=503, detail="COS service not configured")

    results = FileUploader.generate_presigned_urls(subfolder, file_type, count)
    first = results[0] if results else {}

    logger.info(f"[presign] generated {len(results)} URL(s) for user_id={user.id}, cos_key={first.get('cos_key', 'N/A')}")

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
):
    """客户端上传完成后确认，重命名临时文件"""
    cos_key = payload.get("cos_key")
    final_filename = payload.get("final_filename")
    logger.info(f"[confirm] user_id={user.id} cos_key={cos_key} final_filename={final_filename}")
    if not cos_key or not final_filename:
        raise HTTPException(status_code=400, detail="cos_key and final_filename are required")

    result = FileUploader.confirm_upload(cos_key, final_filename)
    if result["success"]:
        logger.info(f"[confirm] success, final_url={result.get('final_url')} final_cos_key={result.get('final_cos_key')}")
        return {"message": "Upload confirmed", "url": result["final_url"], "cos_key": result["final_cos_key"]}
    else:
        logger.error(f"[confirm] failed: {result.get('error')}")
        raise HTTPException(status_code=500, detail=result["error"])


@router.post("/avatar/confirm")
def confirm_avatar(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """确认头像上传并更新用户资料"""
    avatar_url = payload.get("url")
    logger.info(f"[avatar/confirm] user_id={user.id} url={avatar_url}")
    if not avatar_url:
        raise HTTPException(status_code=400, detail="url is required")

    current_user = db.query(User).filter(User.id == user.id).first()
    if current_user:
        logger.info(f"[avatar/confirm] updating user {user.id} avatar_url: {current_user.avatar_url} -> {avatar_url}")
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
    logger.info(f"[cover/confirm] user_id={user.id} url={cover_url}")
    if not cover_url:
        raise HTTPException(status_code=400, detail="url is required")

    current_user = db.query(User).filter(User.id == user.id).first()
    if current_user:
        logger.info(f"[cover/confirm] updating user {user.id} cover_photo_url: {current_user.cover_photo_url} -> {cover_url}")
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

    result = FileUploader.delete_file(url)
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
    info = FileUploader.get_file_info(url)
    if info and info.get("exists"):
        return info
    else:
        raise HTTPException(status_code=404, detail="File not found")


@router.get("/uploads/{filename:path}")
def serve_upload(filename: str):
    """兼容旧 URL，302 重定向到 COS"""
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        raise HTTPException(status_code=400, detail="Invalid file path")
    cos_url = FileUploader._get_cos_url(filename)
    return RedirectResponse(url=cos_url, status_code=302)
