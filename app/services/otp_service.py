"""
验证码服务 - 生成、校验、限流。

不引入人机验证（TCaptcha 收费），仅靠多维限流防刷：
  - 同邮箱 60s 内最多 1 次
  - 同邮箱 1 小时内最多 5 次
  - 同 IP 1 小时内最多 10 次

验证码存 MySQL email_otps 表，带过期时间，避免内存丢失。
"""
import logging
import random
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import Config
from app.database import SessionLocal
from app.models.models import EmailOtp, LoginAttempt

logger = logging.getLogger(__name__)


class OtpService:
    """邮箱验证码生成 / 校验 / 限流"""

    @staticmethod
    def _get_session() -> Session:
        return SessionLocal()

    @staticmethod
    def check_rate_limit(email: str, ip_address: Optional[str], db: Session) -> Tuple[bool, str]:
        """
        检查发送频率是否超限。

        :return: (是否允许, 拒绝原因) ；允许时原因为空字符串
        """
        now = datetime.utcnow()
        since_60s = now - timedelta(seconds=60)
        since_1h = now - timedelta(hours=1)

        # 1. 同邮箱 60s 内最多 1 次
        recent = (
            db.query(EmailOtp)
            .filter(EmailOtp.email == email, EmailOtp.created_at >= since_60s)
            .count()
        )
        if recent >= Config.OTP_RATE_LIMIT_PER_EMAIL_60S:
            return False, "验证码发送过于频繁，请 60 秒后重试"

        # 2. 同邮箱 1 小时内最多 5 次
        hourly = (
            db.query(EmailOtp)
            .filter(EmailOtp.email == email, EmailOtp.created_at >= since_1h)
            .count()
        )
        if hourly >= Config.OTP_RATE_LIMIT_PER_EMAIL_1H:
            return False, "该邮箱本小时请求次数过多，请稍后再试"

        # 3. 同 IP 1 小时内最多 10 次（防分布式刷不同邮箱）
        if ip_address:
            ip_hourly = (
                db.query(EmailOtp)
                .filter(EmailOtp.ip_address == ip_address, EmailOtp.created_at >= since_1h)
                .count()
            )
            if ip_hourly >= Config.OTP_RATE_LIMIT_PER_IP_1H:
                return False, "请求过于频繁，请稍后再试"

        return True, ""

    @staticmethod
    def generate_and_store(email: str, purpose: str, ip_address: Optional[str], db: Session) -> str:
        """
        生成 6 位验证码并写入 email_otps 表。

        :return: 6 位数字验证码
        """
        code = f"{random.randint(0, 999999):06d}"
        expires_at = datetime.utcnow() + timedelta(minutes=Config.OTP_EXPIRE_MINUTES)
        otp = EmailOtp(
            email=email,
            code=code,
            purpose=purpose,
            is_used=False,
            expires_at=expires_at,
            ip_address=ip_address,
        )
        db.add(otp)
        db.commit()
        db.refresh(otp)
        logger.info("[OTP] generated email=%s purpose=%s id=%s", email, purpose, otp.id)
        return code

    @staticmethod
    def verify(email: str, code: str, purpose: str, db: Session) -> Tuple[bool, str]:
        """
        校验验证码（未过期、未使用、邮箱/purpose/码匹配）。

        成功则标记 is_used=True（一次性）。
        :return: (是否有效, 失败原因)
        """
        now = datetime.utcnow()
        otp = (
            db.query(EmailOtp)
            .filter(
                EmailOtp.email == email,
                EmailOtp.code == code,
                EmailOtp.purpose == purpose,
                EmailOtp.is_used == False,
                EmailOtp.expires_at > now,
            )
            .order_by(EmailOtp.created_at.desc())
            .first()
        )
        if not otp:
            return False, "验证码无效或已过期"
        otp.is_used = True
        db.commit()
        return True, ""

    # ── 登录失败限流 ──

    @staticmethod
    def record_login_attempt(identifier: str, ip_address: str, success: bool, db: Session) -> None:
        """记录一次登录尝试（成功/失败）"""
        attempt = LoginAttempt(
            identifier=identifier,
            ip_address=ip_address,
            success=success,
        )
        db.add(attempt)
        db.commit()

    @staticmethod
    def recent_fail_count(identifier: str, db: Session) -> int:
        """
        最近 15 分钟内该账号（username/email）的连续失败次数。
        连续：从最近一次成功往后数；若没有成功记录则数 15 分钟内全部失败。
        """
        window = datetime.utcnow() - timedelta(minutes=15)
        # 取窗口内按时间倒序的尝试
        attempts = (
            db.query(LoginAttempt)
            .filter(LoginAttempt.identifier == identifier, LoginAttempt.created_at >= window)
            .order_by(LoginAttempt.created_at.desc())
            .all()
        )
        fail_count = 0
        for a in attempts:
            if a.success:
                break  # 遇到成功就停，之前的失败不算"连续"
            fail_count += 1
        return fail_count

    @staticmethod
    def requires_login_otp(identifier: str, db: Session) -> bool:
        """该账号是否需要邮箱验证码才能登录（连续失败≥阈值）"""
        return OtpService.recent_fail_count(identifier, db) >= Config.LOGIN_FAIL_THRESHOLD
