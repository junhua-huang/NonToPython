"""
邮件服务 - 通过 SMTP 发送邮箱验证码。

使用 aiosmtplib 异步发送，不阻塞 FastAPI 主流程。
QQ 邮箱用 SSL 465 端口，授权码非登录密码。
"""
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

import aiosmtplib

from app.core.config import Config

logger = logging.getLogger(__name__)


class EmailService:
    """SMTP 邮件发送（异步）"""

    @staticmethod
    async def send_email(to: str, subject: str, html_body: str) -> bool:
        """
        发送一封 HTML 邮件。

        :return: 是否成功（失败仅记日志，不抛异常，避免影响主流程）
        """
        if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
            logger.warning("[EMAIL] SMTP 未配置，跳过发送（to=%s subject=%s）", to, subject)
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr(("南图 NonTo", Config.SMTP_FROM or Config.SMTP_USER))
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=Config.SMTP_HOST,
                port=Config.SMTP_PORT,
                username=Config.SMTP_USER,
                password=Config.SMTP_PASSWORD,
                use_tls=Config.SMTP_USE_SSL,  # SSL 直接握手（465 端口）
                timeout=10,
            )
            logger.info("[EMAIL] sent to=%s subject=%s", to, subject)
            return True
        except Exception as e:
            logger.warning("[EMAIL] send failed to=%s: %s", to, e)
            return False

    @staticmethod
    async def send_otp_email(to_email: str, code: str, purpose: str) -> bool:
        """
        发送验证码邮件。

        :param purpose: register / reset_password / login
        """
        purpose_text = {
            "register": "注册账号",
            "reset_password": "重置密码",
            "login": "登录验证",
        }.get(purpose, "身份验证")

        subject = f"【南图】您的{purpose_text}验证码"
        html = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
  <h2 style="color:#1DA1F2;margin:0 0 16px;">南图 NonTo</h2>
  <p style="color:#536471;font-size:14px;margin:0 0 20px;">您正在进行<strong>{purpose_text}</strong>操作，验证码为：</p>
  <div style="text-align:center;margin:24px 0;">
    <span style="display:inline-block;font-size:32px;font-weight:800;letter-spacing:8px;color:#1DA1F2;background:#eff3f4;padding:16px 32px;border-radius:8px;">{code}</span>
  </div>
  <p style="color:#536471;font-size:14px;margin:0 0 8px;">验证码 {Config.OTP_EXPIRE_MINUTES} 分钟内有效，请勿告知他人。</p>
  <p style="color:#8899a6;font-size:12px;margin:8px 0 0;">若非本人操作，请忽略此邮件。</p>
  <hr style="border:none;border-top:1px solid #eff3f4;margin:24px 0;">
  <p style="color:#8899a6;font-size:12px;margin:0;">此邮件由系统自动发送，请勿回复。</p>
</div>"""
        return await EmailService.send_email(to_email, subject, html)
