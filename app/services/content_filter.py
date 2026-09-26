"""
内容审核敏感词过滤服务
支持中英文敏感词检测、字符变体识别、日志记录和动态词库管理
"""
import re
import logging
from typing import Tuple, List, Set

logger = logging.getLogger(__name__)


class ContentFilter:
    """敏感词过滤器，支持中文变体识别和动态词库管理"""

    _instance = None

    # ============================================================
    # 内置敏感词库（50+ 常见中英文敏感词）
    # ============================================================
    _BUILTIN_SENSITIVE_WORDS: Set[str] = {
        # 中文 - 色情类
        "色情", "淫秽", "裸体", "裸聊", "嫖娼", "卖淫", "妓女",
        "成人视频", "成人电影", "黄色网站", "色情网站", "一夜情",
        "约炮", "炮友", "性交", "口交", "肛交", "自慰", "手淫",
        # 中文 - 赌博类
        "赌博", "赌场", "赌球", "赌马", "六合彩", "时时彩",
        "百家乐", "老虎机", "德州扑克", "博彩", "赌资", "下注",
        # 中文 - 毒品类
        "毒品", "吸毒", "大麻", "海洛因", "冰毒", "摇头丸",
        "K粉", "可卡因", "鸦片", "吗啡", "麻古", "罂粟",
        # 中文 - 暴力/恐怖类
        "杀人", "绑架", "恐怖袭击", "爆炸", "枪支", "贩毒",
        # 中文 - 政治敏感类
        "台独", "港独", "藏独", "疆独", "法轮功",
        # 英文 - Adult content
        "porn", "xxx", "adult", "escort", "sex", "naked",
        "nude", "hentai", "fuck", "dick", "pussy", "bitch",
        # 英文 - Gambling
        "casino", "gambling", "poker", "blackjack", "roulette",
        # 英文 - Drugs
        "cocaine", "heroin", "weed", "marijuana", "meth",
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._sensitive_words: Set[str] = set(self._BUILTIN_SENSITIVE_WORDS)
        self._rebuild_pattern()

    # ============================================================
    # 字符变体映射（全角/半角、常见异体字）
    # ============================================================
    _CHAR_VARIANT_MAP = {
        # 全角字母 → 半角
        'Ａ': 'A', 'Ｂ': 'B', 'Ｃ': 'C', 'Ｄ': 'D', 'Ｅ': 'E',
        'Ｆ': 'F', 'Ｇ': 'G', 'Ｈ': 'H', 'Ｉ': 'I', 'Ｊ': 'J',
        'Ｋ': 'K', 'Ｌ': 'L', 'Ｍ': 'M', 'Ｎ': 'N', 'Ｏ': 'O',
        'Ｐ': 'P', 'Ｑ': 'Q', 'Ｒ': 'R', 'Ｓ': 'S', 'Ｔ': 'T',
        'Ｕ': 'U', 'Ｖ': 'V', 'Ｗ': 'W', 'Ｘ': 'X', 'Ｙ': 'Y',
        'Ｚ': 'Z',
        'ａ': 'a', 'ｂ': 'b', 'ｃ': 'c', 'ｄ': 'd', 'ｅ': 'e',
        'ｆ': 'f', 'ｇ': 'g', 'ｈ': 'h', 'ｉ': 'i', 'ｊ': 'j',
        'ｋ': 'k', 'ｌ': 'l', 'ｍ': 'm', 'ｎ': 'n', 'ｏ': 'o',
        'ｐ': 'p', 'ｑ': 'q', 'ｒ': 'r', 'ｓ': 's', 'ｔ': 't',
        'ｕ': 'u', 'ｖ': 'v', 'ｗ': 'w', 'ｘ': 'x', 'ｙ': 'y',
        'ｚ': 'z',
        # 全角数字 → 半角
        '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
        '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
        # 常见中文异体/形近字
        '祼': '裸',  # 祼（示字旁）与裸（衣字旁）形近
    }

    _VARIANT_NORMALIZE_TABLE = str.maketrans(_CHAR_VARIANT_MAP)

    @classmethod
    def _normalize_text(cls, text: str) -> str:
        """将文本中的全角字符和异体字转化为标准形式"""
        return text.translate(cls._VARIANT_NORMALIZE_TABLE)

    # ============================================================
    # 正则编译
    # ============================================================
    def _rebuild_pattern(self):
        """根据当前词库重新编译正则"""
        if not self._sensitive_words:
            self._pattern = None
            return
        escaped_words = [re.escape(w) for w in sorted(self._sensitive_words, key=len, reverse=True)]
        pattern_str = '|'.join(escaped_words)
        self._pattern = re.compile(pattern_str, re.IGNORECASE)

    # ============================================================
    # 核心方法
    # ============================================================
    def filter_content(self, text: str) -> Tuple[bool, str, list]:
        """
        过滤文本中的敏感词。

        Args:
            text: 待过滤的原始文本

        Returns:
            (是否通过, 过滤后文本（敏感词替换为***）, 命中的敏感词列表)
        """
        if not text or not self._pattern:
            return True, text or '', []

        # 归一化文本用于匹配
        normalized = self._normalize_text(text)
        matches = self._pattern.findall(normalized)

        if not matches:
            return True, text, []

        # 去重并保持原始词
        hit_words = list(dict.fromkeys(matches))
        filtered = self._pattern.sub('***', text)
        return False, filtered, hit_words

    def contains_sensitive(self, text: str) -> bool:
        """
        快速检测文本是否包含敏感词。

        Args:
            text: 待检测文本

        Returns:
            是否包含敏感词
        """
        if not text or not self._pattern:
            return False
        normalized = self._normalize_text(text)
        return bool(self._pattern.search(normalized))

    # ============================================================
    # 日志记录
    # ============================================================
    def log_block(self, user_id, original_text: str, hit_words: list):
        """记录拦截日志"""
        del original_text
        rule_count = len(hit_words)
        logger.warning(
            "content_moderation_rejected user_id=%s content_type=legacy_filter rule_count=%s",
            user_id or 'anonymous',
            rule_count,
        )

    # ============================================================
    # 动态词库管理
    # ============================================================
    def get_words(self) -> List[str]:
        """返回当前所有敏感词，按字母排序"""
        return sorted(self._sensitive_words)

    def add_word(self, word: str) -> bool:
        """添加敏感词。返回 True 表示新增，False 表示已存在。"""
        word = word.strip().lower()
        if not word:
            return False
        if word in self._sensitive_words:
            return False
        self._sensitive_words.add(word)
        self._rebuild_pattern()
        logger.info("sensitive_word_cache_updated action=add total=%d", len(self._sensitive_words))
        return True

    def remove_word(self, word: str) -> bool:
        """删除敏感词。返回 True 表示已删除，False 表示不存在。"""
        word = word.strip().lower()
        if word not in self._sensitive_words:
            return False
        self._sensitive_words.discard(word)
        self._rebuild_pattern()
        logger.info("sensitive_word_cache_updated action=remove total=%d", len(self._sensitive_words))
        return True

    def get_count(self) -> int:
        """返回当前敏感词总数"""
        return len(self._sensitive_words)


# 全局单例
content_filter = ContentFilter()