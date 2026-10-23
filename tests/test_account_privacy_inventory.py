import importlib.util
import json
from pathlib import Path
import unittest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine, event, select, func

spec = importlib.util.spec_from_file_location('account_privacy_inventory', Path(__file__).resolve().parents[1] / 'scripts/account_privacy_inventory.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AccountPrivacyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        meta = MetaData()
        self.users = Table('users', meta, Column('id', Integer, primary_key=True), Column('email', String), Column('username', String))
        self.messages = Table('messages', meta, Column('id', Integer, primary_key=True), Column('sender_id', Integer, ForeignKey('users.id'), nullable=False), Column('content', String))
        self.groups = Table('communities', meta, Column('id', Integer, primary_key=True), Column('owner_id', Integer, ForeignKey('users.id'), nullable=False))
        self.otps = Table('email_otps', meta, Column('id', Integer, primary_key=True), Column('email', String), Column('code', String))
        self.push = Table('push_logs', meta, Column('id', Integer, primary_key=True), Column('user_id', Integer))
        meta.create_all(self.engine)
        with self.engine.begin() as db:
            db.execute(self.users.insert(), [{'id': 1, 'email': 'private@operator.test', 'username': 'private-name'}, {'id': 2, 'email': 'other@operator.test', 'username': 'other'}])
            db.execute(self.messages.insert(), [{'id': 1, 'sender_id': 1, 'content': 'never-export-this-message'}, {'id': 2, 'sender_id': 2, 'content': 'other message'}])
            db.execute(self.groups.insert(), {'id': 1, 'owner_id': 1})
            db.execute(self.otps.insert(), {'id': 1, 'email': 'private@operator.test', 'code': '001122'})
            db.execute(self.push.insert(), {'id': 1, 'user_id': 1})
        self.statements = []
        event.listen(self.engine, 'before_cursor_execute', lambda conn, cursor, statement, parameters, context, executemany: self.statements.append(statement))

    def tearDown(self):
        self.engine.dispose()

    def test_counts_cover_foreign_keys_and_records_without_user_foreign_keys(self):
        with self.engine.connect() as db:
            report = module.inventory(db, 1)
            self.assertEqual(db.execute(select(func.count()).select_from(self.messages)).scalar_one(), 2)
        self.assertTrue(report['accountPresent'])
        self.assertEqual(next(x for x in report['references'] if x['table'] == 'messages')['count'], 1)
        self.assertEqual({x['table']: x['count'] for x in report['additionalRecords']}, {'push_logs': 1, 'email_otps': 1})
        self.assertEqual(report['manualDecisions'][0]['table'], 'communities')
        output = json.dumps(report)
        for secret in ['private@operator.test', 'private-name', 'never-export-this-message', '001122']:
            self.assertNotIn(secret, output)
        for statement in self.statements:
            self.assertNotIn(statement.lstrip().split()[0].upper(), {'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE'})

    def test_absent_account_is_not_reported_as_successfully_deleted(self):
        with self.engine.connect() as db:
            report = module.inventory(db, 999)
        self.assertFalse(report['accountPresent'])
        self.assertIn('not_a_deletion', report['scope'])
        self.assertEqual(report['references'], [])

    def test_invalid_account_id_is_rejected_before_any_query(self):
        with self.engine.connect() as db:
            for value in [0, -1, True, '1']:
                with self.assertRaises(ValueError):
                    module.inventory(db, value)
        self.assertEqual(self.statements, [])


if __name__ == '__main__':
    unittest.main()
