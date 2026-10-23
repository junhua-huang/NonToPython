#!/usr/bin/env python3
"""Export minimal restore exclusions to an operator-protected file, never stdout."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New protected path, outside rolling database backups")
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.exit(2, "A readable server environment file is required.\n")
    try:
        from dotenv import load_dotenv
        load_dotenv(args.env_file)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from sqlalchemy import select
        from app.database import SessionLocal
        from app.models.privacy import AccountErasure
        with SessionLocal() as db:
            rows = db.execute(select(AccountErasure.id, AccountErasure.user_id, AccountErasure.created_at)).all()
        payload = {"schemaVersion":1, "purpose":"account_erasure_restore_exclusions",
                   "entries":[{"id":row.id, "user_id":row.user_id, "created_at":row.created_at.isoformat()} for row in rows]}
        # O_EXCL refuses overwrite/symlink targets; 0600 avoids accidental shared access.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        print(json.dumps({"exported_entries":len(rows), "contains_email_or_content":False}))
    except Exception:
        parser.exit(1, "Erasure ledger export failed. Check permissions and server configuration privately.\n")


if __name__ == "__main__":
    main()
