#!/usr/bin/env python3
"""Read-only account-deletion inventory. Prints counts, never record contents or credentials."""
import argparse
import json
from pathlib import Path
import sys


def inventory(connection, account_id):
    from sqlalchemy import MetaData, Table, func, inspect, or_, select
    if type(account_id) is not int or account_id <= 0:
        raise ValueError('A positive test account ID is required.')
    inspector = inspect(connection)
    metadata = MetaData()
    users = Table('users', metadata, autoload_with=connection)
    exists = bool(connection.execute(select(func.count()).select_from(users).where(users.c.id == account_id)).scalar_one())
    report = {'schemaVersion': 1, 'scope': 'read_only_counts_not_a_deletion_or_completion_receipt',
              'accountPresent': exists, 'references': [], 'additionalRecords': [], 'manualDecisions': [],
              'unverified': ['object_storage_orphans_and_versions', 'database_disk_and_snapshot_backups',
                             'proxy_application_and_provider_logs', 'legal_preservation_and_other_users_copies']}
    if not exists:
        return report
    for name in sorted(inspector.get_table_names()):
        if name == 'users':
            continue
        for foreign_key in inspector.get_foreign_keys(name):
            if foreign_key.get('referred_table') != 'users':
                continue
            columns = foreign_key['constrained_columns']
            if len(columns) != 1 or foreign_key.get('referred_columns') != ['id']:
                report['manualDecisions'].append({'table': name, 'reason': 'composite_or_non_id_user_reference'})
                continue
            table = metadata.tables.get(name)
            if table is None:
                table = Table(name, metadata, autoload_with=connection)
            column = table.c[columns[0]]
            count = connection.execute(select(func.count()).select_from(table).where(column == account_id)).scalar_one()
            item = {'table': name, 'column': column.name, 'count': count, 'nullable': column.nullable,
                    'onDelete': foreign_key.get('options', {}).get('ondelete', 'NO_ACTION')}
            report['references'].append(item)
            if count and (column.name in {'owner_id', 'creator_id', 'admin_user_id', 'uploaded_by', 'created_by'}):
                report['manualDecisions'].append({'table': name, 'column': column.name, 'count': count,
                    'reason': 'review_shared_ownership_or_operational_history_before_deleting'})
    available = set(inspector.get_table_names())
    if 'push_logs' in available:
        table = metadata.tables.get('push_logs')
        if table is None:
            table = Table('push_logs', metadata, autoload_with=connection)
        if 'user_id' in table.c:
            count = connection.execute(select(func.count()).select_from(table).where(table.c.user_id == account_id)).scalar_one()
            report['additionalRecords'].append({'table': 'push_logs', 'count': count, 'match': 'user_id_without_required_foreign_key'})
    for name, column_name, identifiers in [('email_otps', 'email', ['email']), ('login_attempts', 'identifier', ['email', 'username'])]:
        if name not in available:
            continue
        table = metadata.tables.get(name)
        if table is None:
            table = Table(name, metadata, autoload_with=connection)
        if column_name not in table.c:
            continue
        conditions = [table.c[column_name] == select(users.c[key]).where(users.c.id == account_id).scalar_subquery()
                      for key in identifiers if key in users.c]
        if conditions:
            count = connection.execute(select(func.count()).select_from(table).where(or_(*conditions))).scalar_one()
            report['additionalRecords'].append({'table': name, 'count': count, 'match': 'account_identifiers_without_exporting_values'})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--account-id', type=int, required=True, help='Use a dedicated test account ID')
    args = parser.parse_args()
    if not args.env_file.is_file() or args.account_id <= 0:
        parser.exit(2, 'A readable server environment file and positive test account ID are required.\n')
    engine = None
    try:
        from dotenv import load_dotenv
        from sqlalchemy import create_engine
        load_dotenv(args.env_file, override=False)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.core.config import Config
        engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, connect_args={'connect_timeout': 5})
        with engine.connect() as connection:
            report = inventory(connection, args.account_id)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception:
        parser.exit(2, 'Inventory failed; no exception details, credentials or account data were exported.\n')
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    main()
