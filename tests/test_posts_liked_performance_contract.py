import unittest


class PostsLikedPerformanceContractTest(unittest.TestCase):
    def test_liked_posts_query_joins_posts_in_one_query(self):
        with open('app/routers/posts.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('db.query(Post, Like.created_at.label("liked_at"))', source)
        self.assertIn('.join(Like, Like.post_id == Post.id)', source)
        self.assertNotIn('for like in likes:', source)
        self.assertNotIn('like.post', source)


if __name__ == '__main__':
    unittest.main()
