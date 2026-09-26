"""
内容审核服务 - 对帖子和评论内容进行安全检查

功能：
- 垃圾信息检测（大量链接、重复字符等模式）
- 不适当内容检测（预定义关键词列表）
- 仇恨言论检测
- 敏感词过滤与替换
- 审核日志记录
"""
import re
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class ContentModeration:
    """内容审核器"""

    # ============================================================
    # 垃圾信息检测模式
    # ============================================================
    _SPAM_PATTERNS = [
        # 大量 URL 链接（3个以上）
        (re.compile(r'(https?://[^\s]+)', re.IGNORECASE), 3, "spam"),
        # 连续重复字符超过 8 次（如 aaaaaaaa）
        (re.compile(r'(.)\1{11,}'), 1, "spam"),  # 12+ 连续相同字符（避免中文正常重复表达被误判）
        # 纯符号/表情刷屏（连续5个以上非文字字符组）
        (re.compile(r'[!！?？~～]{5,}'), 1, "spam"),
        # 全大写字母超过80%（至少20字符）
        (re.compile(r'[A-Z]{20,}'), 1, "spam"),
        # 重复词/短语（同一个3词以上短语出现3次以上）
        (re.compile(r'(\b\w+(?:\s+\w+){2,3}\b)\s*\1\s*\1'), 1, "spam"),
        # 常见垃圾信息关键词
        (re.compile(
            r'(buy now|click here|free money|make money fast|cash bonus|limited offer|'
            r'act now|guaranteed|no fees|free access|click below|special promotion|'
            r'100% free|exclusive deal|order now|best price|cheap|discount code)',
            re.IGNORECASE
        ), 1, "spam"),
    ]

    # ============================================================
    # 不适当内容关键词
    # ============================================================
    _INAPPROPRIATE_KEYWORDS: List[str] = [
        # 仇恨言论
        "hate speech", "种族歧视", "性别歧视", "地域歧视", "宗教歧视",
        "nazi", "法西斯", "种族主义",
        # 暴力威胁
        "kill you", "kill yourself", "去死", "弄死你", "威胁",
        "murder", "massacre", "terrorist",
        # 严重骚扰
        "dox", "人肉", "人身攻击",
        # 非法内容
        "hacking", "crack serial", "盗版",
        "drug dealer", "fake id", "counterfeit",
    ]

    # ============================================================
    # 仇恨言论关键词
    # ============================================================
    _HATE_SPEECH_PATTERNS = [
        re.compile(
            r'\b(nigger|chink|kike|wetback|raghead|faggot|tranny|retard|'
            r'黑鬼|白皮猪|支那|鬼子|棒子|阿三)\b',
            re.IGNORECASE
        ),
    ]

    @classmethod
    def check_content(cls, text: str) -> Dict:
        """
        对文本内容进行全面审核。

        Args:
            text: 待审核的文本内容

        Returns:
            {
                "approved": bool,       # 是否通过审核
                "reasons": list,        # 不通过的原因列表（spam/inappropriate/hate_speech）
                "filtered_text": str    # 过滤后的文本（通过时同原文，不通过时替换敏感部分）
            }
        """
        if not text or not text.strip():
            return {"approved": True, "reasons": [], "filtered_text": text or ""}

        reasons: List[str] = []
        filtered_text = text

        # 1. 敏感词检查（使用 ContentFilter 单例）
        from app.services.content_filter import content_filter
        has_sensitive = content_filter.contains_sensitive(text)
        if has_sensitive:
            reasons.append("inappropriate")
            passed, filtered_text, hit_words = content_filter.filter_content(text)
            if hit_words:
                rule_count = len(hit_words)
                logger.debug(
                    "content_moderation_legacy_filter_hit rule_count=%s",
                    rule_count,
                )

        # 2. 垃圾信息检测
        if cls.contains_spam(text):
            reasons.append("spam")

        # 3. 不适当内容检测（独立于敏感词的关键词）
        if cls.contains_inappropriate(text):
            if "inappropriate" not in reasons:
                reasons.append("inappropriate")

        # 4. 仇恨言论检测
        if cls._contains_hate_speech(text):
            reasons.append("hate_speech")

        approved = len(reasons) == 0
        if not approved:
            # 确保 filtered_text 是过滤后的
            if filtered_text == text and has_sensitive:
                passed, filtered_text, _ = content_filter.filter_content(text)

        return {
            "approved": approved,
            "reasons": list(dict.fromkeys(reasons)),  # 去重保序
            "filtered_text": filtered_text,
        }

    @classmethod
    def contains_spam(cls, text: str) -> bool:
        """
        检测文本是否包含垃圾信息。

        检测项：
        - 大量 URL 链接（3个以上）
        - 连续重复字符（8次以上）
        - 纯符号刷屏
        - 全大写字母（80%以上且>=20字符）
        - 重复短语刷屏
        - 常见垃圾信息关键词

        Args:
            text: 待检测文本

        Returns:
            是否检测到垃圾信息
        """
        if not text:
            return False

        for pattern, threshold, _ in cls._SPAM_PATTERNS:
            matches = pattern.findall(text)
            if len(matches) >= threshold:
                return True

        # 额外检测：全大写字母占比超过80%
        if len(text) >= 20:
            alpha_chars = [c for c in text if c.isalpha()]
            if alpha_chars:
                upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
                if upper_ratio > 0.8:
                    return True

        return False

    @classmethod
    def contains_inappropriate(cls, text: str) -> bool:
        """
        检测文本是否包含不适当内容（基于预定义关键词列表）。

        覆盖范围：仇恨言论、暴力威胁、骚扰、非法内容

        Args:
            text: 待检测文本

        Returns:
            是否包含不适当内容
        """
        if not text:
            return False

        text_lower = text.lower()
        for keyword in cls._INAPPROPRIATE_KEYWORDS:
            if keyword.lower() in text_lower:
                return True

        return False

    @classmethod
    def _contains_hate_speech(cls, text: str) -> bool:
        """检测是否包含仇恨言论"""
        if not text:
            return False
        for pattern in cls._HATE_SPEECH_PATTERNS:
            if pattern.search(text):
                return True
        return False

    @classmethod
    def filter_content(cls, text: str) -> str:
        """
        将检测到的不适当内容用星号替换。
        使用 ContentFilter 的敏感词过滤 + 垃圾信息关键词替换。

        Args:
            text: 原始文本

        Returns:
            过滤后的文本
        """
        if not text:
            return text or ""

        from app.services.content_filter import content_filter
        passed, filtered_text, _ = content_filter.filter_content(text)

        # 额外替换垃圾信息关键词
        for pattern, _, _ in cls._SPAM_PATTERNS:
            if len(pattern.findall(filtered_text)) >= 1:
                filtered_text = pattern.sub('***', filtered_text)

        # 替换仇恨言论
        for pattern in cls._HATE_SPEECH_PATTERNS:
            filtered_text = pattern.sub('***', filtered_text)

        return filtered_text

    # ============================================================
    # 审核日志
    # ============================================================
    @staticmethod
    def log_moderation(user_id, content_type: str, original_text: str, reasons: List[str]):
        """
        记录审核拦截日志。

        Args:
            user_id: 用户ID
            content_type: 内容类型（post / comment）
            original_text: 原始文本内容
            reasons: 拦截原因列表
        """
        del original_text
        rule_count = len(reasons)
        logger.warning(
            "content_moderation_rejected user_id=%s content_type=%s rule_count=%s",
            user_id or 'anonymous',
            content_type,
            rule_count,
        )


# 模块级便捷函数
check_content = ContentModeration.check_content
contains_spam = ContentModeration.contains_spam
contains_inappropriate = ContentModeration.contains_inappropriate
filter_content = ContentModeration.filter_content
log_moderation = ContentModeration.log_moderation
