"""baseline

基线迁移 —— 把现有数据库标记为 Alembic 的零点。

此迁移不执行任何 DDL（upgrade/downgrade 均为 pass）。
现有库已包含全部业务表（由历史 create_all() 建立），这里仅用
`alembic stamp head` 写入版本号，建立迁移链条的起点。

autogenerate 在生成本基线时检测到大量差异（遗留表 comic_event/shares、
漫展表单复数命名不一致 comic_event vs comic_events、索引/外键/默认值差异、
类型差异如 posts.images JSON vs Text 等），均【不在此处理】，留待后续按需
各自开独立迁移逐项解决，避免 baseline 成为大杂烩且不可逆。

Revision ID: 1fd93272c5db
Revises:
Create Date: 2026-06-19 17:03:43.596363

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '1fd93272c5db'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """基线迁移：不执行 DDL，仅作为迁移链起点。"""
    pass


def downgrade() -> None:
    """基线无法 downgrade（无前序版本）。"""
    pass
