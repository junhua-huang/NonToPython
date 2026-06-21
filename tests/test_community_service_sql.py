import unittest

from sqlalchemy.dialects import mysql

from app.models.community import Community
from app.services.community_service import CommunityService


class _FakeQuery:
    def __init__(self):
        self.order_clauses = []

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *clauses):
        self.order_clauses.extend(clauses)
        return self

    def all(self):
        return []


class _FakeDb:
    def __init__(self):
        self.query_obj = _FakeQuery()

    def query(self, model):
        assert model is Community
        return self.query_obj


class CommunityServiceSqlTest(unittest.TestCase):
    def test_list_my_communities_uses_mysql_compatible_ordering(self):
        db = _FakeDb()

        CommunityService.list_my_communities(db, user_id=150, manage_only=False)

        compiled = "\n".join(
            str(clause.compile(dialect=mysql.dialect()))
            for clause in db.query_obj.order_clauses
        )
        self.assertNotIn("NULLS LAST", compiled.upper())
        self.assertIn("communities.updated_at IS NULL", compiled)
        self.assertIn("communities.updated_at DESC", compiled)
        self.assertIn("communities.created_at DESC", compiled)


if __name__ == "__main__":
    unittest.main()
