import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CommentLikeContractTest(unittest.TestCase):
    def test_comment_likes_do_not_reuse_post_like_unique_key(self):
        source = (ROOT / "app" / "routers" / "interactions.py").read_text(encoding="utf-8")
        comment_like_body = source.split('@router.post("/comments/{comment_id}/like")', 1)[1].split('@router.delete("/comments/{comment_id}/like")', 1)[0]

        self.assertIn("Like(user_id=user.id, comment_id=comment_id)", comment_like_body)
        self.assertNotIn("post_id=comment.post_id", comment_like_body)

    def test_post_like_queries_ignore_comment_like_rows(self):
        source = (ROOT / "app" / "routers" / "interactions.py").read_text(encoding="utf-8")
        post_like_section = source.split("# ==================== 评论点赞", 1)[0]

        self.assertIn("Like.comment_id.is_(None)", post_like_section)

    def test_post_like_count_counts_only_post_likes(self):
        source = (ROOT / "app" / "models" / "models.py").read_text(encoding="utf-8")
        get_like_count_body = source.split("def get_like_count(self):", 1)[1].split("def get_comment_count", 1)[0]

        self.assertIn("Like.comment_id.is_(None)", get_like_count_body)


if __name__ == "__main__":
    unittest.main()
