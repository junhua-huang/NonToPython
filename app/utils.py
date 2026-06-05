"""
工具函数 - FastAPI 重构版
"""
import os
import uuid
import hashlib
import time
from datetime import datetime

from app.core.config import Config
from qcloud_cos import CosConfig, CosS3Client


class FileUploader:
    """腾讯云 COS 文件上传器"""

    _cos_client = None

    @classmethod
    def _get_cos_client(cls):
        if cls._cos_client is None and Config.COS_BUCKET_NAME:
            cos_config = CosConfig(
                Region=Config.COS_REGION,
                SecretId=Config.COS_SECRET_ID,
                SecretKey=Config.COS_SECRET_KEY,
            )
            cls._cos_client = CosS3Client(cos_config)
        return cls._cos_client

    @classmethod
    def _get_cos_url(cls, filename: str) -> str:
        if Config.COS_DOMAIN:
            return f"https://{Config.COS_DOMAIN}/{filename}"
        return f"https://{Config.COS_BUCKET_NAME}.cos.{Config.COS_REGION}.myqcloud.com/{filename}"

    @classmethod
    def generate_presigned_urls(cls, subfolder: str, file_type: str = "image", count: int = 1):
        """
        生成预签名上传 URL
        
        Args:
            subfolder: 子文件夹路径
            file_type: 文件类型 (image/video)
            count: 生成数量
        
        Returns:
            list: 预签名 URL 信息列表
        """
        client = cls._get_cos_client()
        if not client:
            return [{"error": "COS not configured"} for _ in range(count)]

        _image_exts = {"image", "jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "tiff", "tif", "ico", "heic", "heif", "avif", "apng", "jfif", "pjpeg", "pjp"}
        ext = ".jpg" if file_type.lower() in _image_exts else ".mp4"
        results = []
        for _ in range(count):
            unique_id = uuid.uuid4().hex[:8]
            filename = f"{unique_id}{ext}"
            cos_key = f"{subfolder}/{datetime.utcnow().strftime('%Y%m%d')}/{filename}"

            try:
                presigned_url = client.get_presigned_url(
                    Method='PUT',
                    Bucket=Config.COS_BUCKET_NAME,
                    Key=cos_key,
                    Expired=3600,
                )
                results.append({
                    "upload_url": presigned_url,
                    "presigned_url": presigned_url,
                    "access_url": cls._get_cos_url(cos_key),
                    "cos_key": cos_key,
                })
            except Exception as e:
                results.append({"error": str(e), "cos_key": cos_key})

        return results

    @classmethod
    def confirm_upload(cls, cos_key: str, final_filename: str):
        """
        确认上传完成
        
        Args:
            cos_key: 原始 COS key
            final_filename: 最终文件名
        
        Returns:
            dict: 包含 success, final_url, final_cos_key
        """
        client = cls._get_cos_client()
        if not client:
            return {"success": False, "error": "COS not configured"}

        dir_path = os.path.dirname(cos_key)
        final_cos_key = f"{dir_path}/{final_filename}"

        try:
            # 复制并删除原文件
            copy_source = {
                'Bucket': Config.COS_BUCKET_NAME,
                'Key': cos_key,
                'Region': Config.COS_REGION,
            }
            client.copy_object(
                Bucket=Config.COS_BUCKET_NAME,
                Key=final_cos_key,
                CopySource=copy_source,
            )
            client.delete_object(
                Bucket=Config.COS_BUCKET_NAME,
                Key=cos_key,
            )
            return {
                "success": True,
                "final_url": cls._get_cos_url(final_cos_key),
                "final_cos_key": final_cos_key,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def delete_file(cls, url: str):
        """
        删除 COS 文件
        
        Args:
            url: 文件 URL
        
        Returns:
            dict: 包含 success/error
        """
        client = cls._get_cos_client()
        if not client:
            return {"success": False, "error": "COS not configured"}

        # 从 URL 提取 cos_key
        try:
            if Config.COS_DOMAIN and Config.COS_DOMAIN in url:
                cos_key = url.split(f"https://{Config.COS_DOMAIN}/")[-1]
            else:
                prefix = f"https://{Config.COS_BUCKET_NAME}.cos.{Config.COS_REGION}.myqcloud.com/"
                cos_key = url.split(prefix)[-1]

            client.delete_object(Bucket=Config.COS_BUCKET_NAME, Key=cos_key)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def save_file(cls, file, file_type: str = "image", subfolder: str = ""):
        """
        直接上传文件到 COS（同步模式）
        
        Args:
            file: UploadFile 对象（支持 .file.read()）
            file_type: 文件类型 (image/video)
            subfolder: 子文件夹路径
        
        Returns:
            dict: {"success": bool, "url": str, "cos_key": str} 或 {"success": False, "error": str}
        """
        client = cls._get_cos_client()
        if not client:
            return {"success": False, "error": "COS not configured"}

        _image_exts = {"image", "jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "tiff", "tif", "ico", "heic", "heif", "avif", "apng", "jfif", "pjpeg", "pjp"}
        ext = ".jpg" if file_type.lower() in _image_exts else ".mp4"
        unique_id = uuid.uuid4().hex[:8]
        filename = f"{unique_id}{ext}"
        cos_key = f"{subfolder}/{datetime.utcnow().strftime('%Y%m%d')}/{filename}"

        try:
            contents = file.file.read()
            file.file.seek(0)
            client.put_object(
                Bucket=Config.COS_BUCKET_NAME,
                Key=cos_key,
                Body=contents,
            )
            return {
                "success": True,
                "url": cls._get_cos_url(cos_key),
                "cos_key": cos_key,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def get_file_info(cls, url: str):
        """
        获取 COS 文件信息
        
        Args:
            url: 文件 URL
        
        Returns:
            dict: 文件信息或 None
        """
        client = cls._get_cos_client()
        if not client:
            return None

        try:
            if Config.COS_DOMAIN and Config.COS_DOMAIN in url:
                cos_key = url.split(f"https://{Config.COS_DOMAIN}/")[-1]
            else:
                prefix = f"https://{Config.COS_BUCKET_NAME}.cos.{Config.COS_REGION}.myqcloud.com/"
                cos_key = url.split(prefix)[-1]

            response = client.head_object(Bucket=Config.COS_BUCKET_NAME, Key=cos_key)
            return {
                "exists": True,
                "cos_key": cos_key,
                "content_length": response.get("Content-Length"),
                "content_type": response.get("Content-Type"),
                "last_modified": response.get("Last-Modified"),
            }
        except Exception:
            return None