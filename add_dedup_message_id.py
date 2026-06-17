"""
给 ws_ack_dedup 表添加 message_id 列
用于在重复 ACK 时返回原始消息 ID，避免客户端无限转圈
"""
from dotenv import load_dotenv
load_dotenv()

from app.database import engine
from sqlalchemy import text, inspect

def migrate():
    with engine.connect() as conn:
        insp = inspect(engine)
        columns = [col['name'] for col in insp.get_columns('ws_ack_dedup')]
        if 'message_id' not in columns:
            conn.execute(text(
                'ALTER TABLE ws_ack_dedup ADD COLUMN message_id INT NULL'
            ))
            conn.commit()
            print('Added message_id column to ws_ack_dedup')
        else:
            print('message_id column already exists in ws_ack_dedup')

if __name__ == '__main__':
    migrate()
