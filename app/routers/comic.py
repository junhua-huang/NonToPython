"""
漫展路由 - FastAPI 版本
从 C:\PythonProject\routes_comic.py 迁移（Flask → FastAPI）
"""
from datetime import datetime, date as date_cls
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_optional_user
from app.models.models import User

router = APIRouter()


# ============================================================
# 城市 & 标签
# ============================================================

@router.get("/cities")
def get_cities(db: Session = Depends(get_db)):
    """获取漫展城市列表"""
    sql = text("""
        SELECT id, name, province, sort_order
        FROM comic_cities
        ORDER BY sort_order ASC, id ASC
    """)
    rows = db.execute(sql).fetchall()
    return [{
        'id': r[0], 'name': r[1], 'province': r[2], 'sortOrder': r[3]
    } for r in rows]


@router.get("/tags")
def get_tags(db: Session = Depends(get_db)):
    """获取漫展标签列表"""
    sql = text("""
        SELECT id, name, tag_type
        FROM comic_tags
        ORDER BY tag_type ASC, id ASC
    """)
    rows = db.execute(sql).fetchall()
    return [{
        'id': r[0], 'name': r[1], 'tagType': r[2]
    } for r in rows]


# ============================================================
# 漫展列表 & 详情
# ============================================================

@router.get("/events")
def get_events(
    city: str = Query(""),
    page: int = Query(1),
    size: int = Query(10),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """获取漫展列表（支持按城市筛选 + 分页），含 isFollowed / isOwner"""
    city = city.strip()
    page = max(page, 1)
    size = min(max(size, 1), 50)

    where = "WHERE e.status IN (0, 1)"
    params = {}
    if city:
        where += " AND c.name = :city"
        params['city'] = city

    count_sql = text(f"""
        SELECT COUNT(*) FROM comic_events e
        JOIN comic_cities c ON e.city_id = c.id
        {where}
    """)
    total = db.execute(count_sql, params).scalar() or 0

    offset = (page - 1) * size
    data_sql = text(f"""
        SELECT e.id, e.name, e.start_date, e.end_date,
               e.venue, e.status, e.intro,
               e.creator_id,
               c.name AS city_name, c.id AS city_id,
               (SELECT image_url FROM comic_event_images
                WHERE event_id = e.id AND is_cover = 1 LIMIT 1) AS cover_image,
               (SELECT COUNT(*) FROM comic_event_follows WHERE event_id = e.id) AS follow_count,
               GROUP_CONCAT(DISTINCT t.name SEPARATOR ',') AS tag_names,
               u.username AS creator_name, u.avatar_url AS creator_avatar, e.created_at
        FROM comic_events e
        JOIN comic_cities c ON e.city_id = c.id
        LEFT JOIN users u ON e.creator_id = u.id
        LEFT JOIN comic_event_tag_rel tr ON tr.event_id = e.id
        LEFT JOIN comic_tags t ON t.id = tr.tag_id
        {where}
        GROUP BY e.id
        ORDER BY e.start_date ASC
        LIMIT :size OFFSET :offset
    """)
    params['size'] = size
    params['offset'] = offset
    rows = db.execute(data_sql, params).fetchall()

    user_id = current_user.id if current_user else None

    records = []
    for r in rows:
        tags = [t.strip() for t in (r[12] or '').split(',') if t.strip()]
        creator_id = r[7]
        is_owner = (user_id is not None and creator_id == user_id)
        is_followed = False
        if user_id is not None:
            fo = db.execute(
                text("SELECT 1 FROM comic_event_follows WHERE event_id = :eid AND user_id = :uid"),
                {'eid': r[0], 'uid': user_id}
            ).first()
            is_followed = fo is not None

        records.append({
            'id': r[0],
            'name': r[1],
            'startDate': r[2].strftime('%Y-%m-%d') if r[2] else None,
            'endDate': r[3].strftime('%Y-%m-%d') if r[3] else None,
            'venue': r[4] or '',
            'status': r[5],
            'statusText': {0: '即将开始', 1: '进行中', 2: '已结束'}.get(r[5], ''),
            'cityName': r[8] or '',
            'cityId': r[9],
            'coverImage': r[10],
            'followCount': r[11] or 0,
            'tags': tags,
            'isOwner': is_owner,
            'isFollowed': is_followed,
            'creatorName': r[13],
            'creatorAvatar': r[14],
            'createdAt': r[15].strftime('%Y-%m-%dT%H:%M:%S') if r[15] else None,
        })

    pages = (total + size - 1) // size if total > 0 else 0
    return {
        'records': records,
        'total': total,
        'page': page,
        'size': size,
        'pages': pages,
    }


@router.get("/events/{event_id}")
def get_event_detail(
    event_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    """获取漫展详情"""
    user_id = current_user.id if current_user else None

    sql = text("""
        SELECT e.id, e.name, e.city_id, e.venue,
               e.start_date, e.end_date, e.start_time, e.end_time,
               e.ticket_info, e.website, e.intro, e.status,
               c.name AS city_name,
               (SELECT COUNT(*) FROM comic_event_follows WHERE event_id = e.id) AS follow_count,
               GROUP_CONCAT(DISTINCT t.name SEPARATOR ',') AS tag_names,
               u.username AS creator_name, u.avatar_url AS creator_avatar, e.created_at
        FROM comic_events e
        JOIN comic_cities c ON e.city_id = c.id
        LEFT JOIN users u ON e.creator_id = u.id
        LEFT JOIN comic_event_tag_rel tr ON tr.event_id = e.id
        LEFT JOIN comic_tags t ON t.id = tr.tag_id
        WHERE e.id = :eid
        GROUP BY e.id
    """)
    row = db.execute(sql, {'eid': event_id}).first()
    if not row:
        raise HTTPException(status_code=404, detail='漫展不存在')

    tags = [t.strip() for t in (row[14] or '').split(',') if t.strip()]

    # 图片列表
    img_sql = text("""
        SELECT id, image_url, is_cover, sort_order
        FROM comic_event_images
        WHERE event_id = :eid
        ORDER BY is_cover DESC, sort_order ASC
    """)
    imgs = db.execute(img_sql, {'eid': event_id}).fetchall()
    images = [{
        'id': i[0], 'imageUrl': i[1], 'isCover': bool(i[2]), 'sortOrder': i[3]
    } for i in imgs]
    cover = next((i['imageUrl'] for i in images if i['isCover']), None) \
          or (images[0]['imageUrl'] if images else None)

    is_followed = False
    is_owner = False
    if user_id:
        fo = db.execute(
            text("SELECT 1 FROM comic_event_follows WHERE event_id = :eid AND user_id = :uid"),
            {'eid': event_id, 'uid': user_id}
        ).first()
        is_followed = fo is not None

        ow = db.execute(
            text("SELECT 1 FROM comic_events WHERE id = :eid AND creator_id = :uid"),
            {'eid': event_id, 'uid': user_id}
        ).first()
        is_owner = ow is not None

    return {
        'id': row[0],
        'name': row[1],
        'cityId': row[2],
        'cityName': row[12] or '',
        'venue': row[3] or '',
        'startDate': row[4].strftime('%Y-%m-%d') if row[4] else None,
        'endDate': row[5].strftime('%Y-%m-%d') if row[5] else None,
        'startTime': row[6].strftime('%H:%M') if row[6] else None,
        'endTime': row[7].strftime('%H:%M') if row[7] else None,
        'ticketInfo': row[8],
        'website': row[9],
        'intro': row[10],
        'status': row[11],
        'statusText': {0: '即将开始', 1: '进行中', 2: '已结束'}.get(row[11], ''),
        'coverImage': cover,
        'followCount': row[13] or 0,
        'isFollowed': is_followed,
        'isOwner': is_owner,
        'tags': tags,
        'images': images,
        'creatorName': row[15],
        'creatorAvatar': row[16],
        'createdAt': row[17].strftime('%Y-%m-%dT%H:%M:%S') if row[17] else None,
    }


# ============================================================
# 发布 / 编辑漫展
# ============================================================

def _parse_tags(tag_ids) -> List[int]:
    """解析标签 ID 列表"""
    if not tag_ids:
        return []
    if isinstance(tag_ids, str):
        return [int(x.strip()) for x in tag_ids.split(',') if x.strip()]
    if isinstance(tag_ids, list):
        return [int(x) for x in tag_ids]
    return []


def _calc_status(start_date: str, end_date: str) -> int:
    """根据日期计算漫展状态"""
    try:
        sd = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        ed = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
        today = date_cls.today()
        if sd and ed:
            if today < sd:
                return 0
            elif sd <= today <= ed:
                return 1
            else:
                return 2
    except Exception:
        pass
    return 0


@router.post("/events", status_code=201)
def create_event(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """发布漫展"""
    name = (payload.get('name') or '').strip()
    city_id = payload.get('cityId') or payload.get('city_id')
    venue = (payload.get('venue') or '').strip()
    intro = (payload.get('intro') or '').strip()
    start_date = payload.get('startDate') or payload.get('start_date')
    end_date = payload.get('endDate') or payload.get('end_date')
    start_time = payload.get('startTime') or payload.get('start_time')
    end_time = payload.get('endTime') or payload.get('end_time')
    ticket_info = payload.get('ticketInfo') or payload.get('ticket_info')
    website = payload.get('website')
    tag_ids = payload.get('tagIds') or payload.get('tag_ids') or []
    image_urls = payload.get('imageUrls') or payload.get('image_urls') or []

    if not name:
        raise HTTPException(status_code=400, detail='漫展名称不能为空')
    if not city_id:
        raise HTTPException(status_code=400, detail='请选择城市')

    now = datetime.utcnow()
    status_val = _calc_status(start_date, end_date)

    ins = text("""
        INSERT INTO comic_events
        (name, city_id, venue, start_date, end_date, start_time, end_time,
         ticket_info, website, intro, status, creator_id, created_at, updated_at)
        VALUES
        (:name, :city_id, :venue, :start_date, :end_date, :start_time, :end_time,
         :ticket_info, :website, :intro, :status, :creator_id, :now, :now)
    """)
    db.execute(ins, {
        'name': name, 'city_id': city_id, 'venue': venue,
        'start_date': start_date, 'end_date': end_date,
        'start_time': start_time, 'end_time': end_time,
        'ticket_info': ticket_info, 'website': website,
        'intro': intro, 'status': status_val,
        'creator_id': user.id, 'now': now,
    })
    db.flush()
    event_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()

    # 标签关联
    tag_id_list = _parse_tags(tag_ids)
    for tid in tag_id_list:
        db.execute(text(
            "INSERT IGNORE INTO comic_event_tag_rel (event_id, tag_id) VALUES (:eid, :tid)"
        ), {'eid': event_id, 'tid': tid})

    # 图片
    for idx, url in enumerate(image_urls):
        db.execute(text(
            "INSERT INTO comic_event_images (event_id, image_url, is_cover, sort_order) "
            "VALUES (:eid, :url, :is_cover, :sort)"
        ), {
            'eid': event_id, 'url': url,
            'is_cover': 1 if idx == 0 else 0, 'sort': idx
        })

    db.commit()
    return {'id': event_id, 'message': '发布成功'}


@router.put("/events/{event_id}")
def update_event(
    event_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑漫展"""
    # 权限校验
    ev = db.execute(
        text("SELECT creator_id FROM comic_events WHERE id = :eid"),
        {'eid': event_id}
    ).first()
    if not ev:
        raise HTTPException(status_code=404, detail='漫展不存在')
    if ev[0] != user.id:
        raise HTTPException(status_code=403, detail='无权编辑')

    name = (payload.get('name') or '').strip()
    city_id = payload.get('cityId') or payload.get('city_id')
    venue = (payload.get('venue') or '').strip()
    intro = (payload.get('intro') or '').strip()
    start_date = payload.get('startDate') or payload.get('start_date')
    end_date = payload.get('endDate') or payload.get('end_date')
    start_time = payload.get('startTime') or payload.get('start_time')
    end_time = payload.get('endTime') or payload.get('end_time')
    ticket_info = payload.get('ticketInfo') or payload.get('ticket_info')
    website = payload.get('website')
    tag_ids = payload.get('tagIds') or payload.get('tag_ids') or []
    image_urls = payload.get('imageUrls') or payload.get('image_urls') or []

    now = datetime.utcnow()
    upd = text("""
        UPDATE comic_events SET
            name = :name, city_id = :city_id, venue = :venue,
            start_date = :start_date, end_date = :end_date,
            start_time = :start_time, end_time = :end_time,
            ticket_info = :ticket_info, website = :website,
            intro = :intro, updated_at = :now
        WHERE id = :eid
    """)
    db.execute(upd, {
        'name': name, 'city_id': city_id, 'venue': venue,
        'start_date': start_date, 'end_date': end_date,
        'start_time': start_time, 'end_time': end_time,
        'ticket_info': ticket_info, 'website': website,
        'intro': intro, 'now': now, 'eid': event_id,
    })

    # 更新标签
    db.execute(text("DELETE FROM comic_event_tag_rel WHERE event_id = :eid"), {'eid': event_id})
    tag_id_list = _parse_tags(tag_ids)
    for tid in tag_id_list:
        db.execute(text(
            "INSERT IGNORE INTO comic_event_tag_rel (event_id, tag_id) VALUES (:eid, :tid)"
        ), {'eid': event_id, 'tid': tid})

    # 更新图片（先删后插）
    db.execute(text("DELETE FROM comic_event_images WHERE event_id = :eid"), {'eid': event_id})
    for idx, url in enumerate(image_urls):
        db.execute(text(
            "INSERT INTO comic_event_images (event_id, image_url, is_cover, sort_order) "
            "VALUES (:eid, :url, :is_cover, :sort)"
        ), {
            'eid': event_id, 'url': url,
            'is_cover': 1 if idx == 0 else 0, 'sort': idx
        })

    db.commit()
    return {'message': '保存成功'}


# ============================================================
# 关注 / 取消关注
# ============================================================

@router.post("/events/{event_id}/follow")
def toggle_follow(
    event_id: int,
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """关注 / 取消关注漫展"""
    uid = payload.get('userId') or user.id

    # 漫展存在？
    ev = db.execute(
        text("SELECT 1 FROM comic_events WHERE id = :eid"), {'eid': event_id}
    ).first()
    if not ev:
        raise HTTPException(status_code=404, detail='漫展不存在')

    exist = db.execute(text(
        "SELECT 1 FROM comic_event_follows WHERE event_id = :eid AND user_id = :uid"
    ), {'eid': event_id, 'uid': uid}).first()

    if exist:
        db.execute(text(
            "DELETE FROM comic_event_follows WHERE event_id = :eid AND user_id = :uid"
        ), {'eid': event_id, 'uid': uid})
        followed = False
    else:
        db.execute(text(
            "INSERT IGNORE INTO comic_event_follows (event_id, user_id, created_at) "
            "VALUES (:eid, :uid, :now)"
        ), {'eid': event_id, 'uid': uid, 'now': datetime.utcnow()})
        followed = True

    db.commit()
    cnt = db.execute(text(
        "SELECT COUNT(*) FROM comic_event_follows WHERE event_id = :eid"
    ), {'eid': event_id}).scalar() or 0

    return {'followed': followed, 'followCount': cnt}


# ============================================================
# 我发布的漫展
# ============================================================

@router.get("/my-events")
def get_my_events(
    page: int = Query(1),
    size: int = Query(10),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取当前用户发布的漫展（分页）"""
    page = max(page, 1)
    size = min(max(size, 1), 50)
    offset = (page - 1) * size

    count_sql = text("""
        SELECT COUNT(*) FROM comic_events WHERE creator_id = :uid
    """)
    total = db.execute(count_sql, {'uid': user.id}).scalar() or 0

    data_sql = text("""
        SELECT e.id, e.name, e.start_date, e.end_date,
               e.venue, e.status, e.intro,
               c.name AS city_name, c.id AS city_id,
               (SELECT image_url FROM comic_event_images
                WHERE event_id = e.id AND is_cover = 1 LIMIT 1) AS cover_image,
               (SELECT COUNT(*) FROM comic_event_follows WHERE event_id = e.id) AS follow_count,
               GROUP_CONCAT(DISTINCT t.name SEPARATOR ',') AS tag_names,
               u.username AS creator_name, u.avatar_url AS creator_avatar, e.created_at
        FROM comic_events e
        JOIN comic_cities c ON e.city_id = c.id
        LEFT JOIN users u ON e.creator_id = u.id
        LEFT JOIN comic_event_tag_rel tr ON tr.event_id = e.id
        LEFT JOIN comic_tags t ON t.id = tr.tag_id
        WHERE e.creator_id = :uid
        GROUP BY e.id
        ORDER BY e.created_at DESC
        LIMIT :size OFFSET :offset
    """)
    rows = db.execute(data_sql, {
        'uid': user.id, 'size': size, 'offset': offset
    }).fetchall()

    records = []
    for r in rows:
        tags = [t.strip() for t in (r[11] or '').split(',') if t.strip()]
        fo = db.execute(
            text("SELECT 1 FROM comic_event_follows WHERE event_id = :eid AND user_id = :uid"),
            {'eid': r[0], 'uid': user.id}
        ).first()
        records.append({
            'id': r[0],
            'name': r[1],
            'startDate': r[2].strftime('%Y-%m-%d') if r[2] else None,
            'endDate': r[3].strftime('%Y-%m-%d') if r[3] else None,
            'venue': r[4] or '',
            'status': r[5],
            'statusText': {0: '即将开始', 1: '进行中', 2: '已结束'}.get(r[5], ''),
            'cityName': r[7] or '',
            'cityId': r[8],
            'coverImage': r[9],
            'followCount': r[10] or 0,
            'tags': tags,
            'isOwner': True,
            'isFollowed': fo is not None,
            'creatorName': r[12],
            'creatorAvatar': r[13],
            'createdAt': r[14].strftime('%Y-%m-%dT%H:%M:%S') if r[14] else None,
        })

    pages = (total + size - 1) // size if total > 0 else 0
    return {
        'records': records,
        'total': total,
        'page': page,
        'size': size,
        'pages': pages,
    }


# ============================================================
# 我关注的漫展
# ============================================================

@router.get("/my-followed")
def get_my_followed(
    page: int = Query(1),
    size: int = Query(10),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取当前用户关注的漫展（分页）"""
    page = max(page, 1)
    size = min(max(size, 1), 50)
    offset = (page - 1) * size

    count_sql = text("""
        SELECT COUNT(*) FROM comic_event_follows f
        JOIN comic_events e ON f.event_id = e.id
        WHERE f.user_id = :uid
    """)
    total = db.execute(count_sql, {'uid': user.id}).scalar() or 0

    data_sql = text("""
        SELECT e.id, e.name, e.start_date, e.end_date,
               e.venue, e.status, e.intro,
               e.creator_id,
               c.name AS city_name, c.id AS city_id,
               (SELECT image_url FROM comic_event_images
                WHERE event_id = e.id AND is_cover = 1 LIMIT 1) AS cover_image,
               (SELECT COUNT(*) FROM comic_event_follows WHERE event_id = e.id) AS follow_count,
               GROUP_CONCAT(DISTINCT t.name SEPARATOR ',') AS tag_names,
               u.username AS creator_name, u.avatar_url AS creator_avatar, e.created_at
        FROM comic_event_follows f
        JOIN comic_events e ON f.event_id = e.id
        JOIN comic_cities c ON e.city_id = c.id
        LEFT JOIN users u ON e.creator_id = u.id
        LEFT JOIN comic_event_tag_rel tr ON tr.event_id = e.id
        LEFT JOIN comic_tags t ON t.id = tr.tag_id
        WHERE f.user_id = :uid
        GROUP BY e.id
        ORDER BY MAX(f.created_at) DESC
        LIMIT :size OFFSET :offset
    """)
    rows = db.execute(data_sql, {
        'uid': user.id, 'size': size, 'offset': offset
    }).fetchall()

    records = []
    for r in rows:
        tags = [t.strip() for t in (r[12] or '').split(',') if t.strip()]
        records.append({
            'id': r[0],
            'name': r[1],
            'startDate': r[2].strftime('%Y-%m-%d') if r[2] else None,
            'endDate': r[3].strftime('%Y-%m-%d') if r[3] else None,
            'venue': r[4] or '',
            'status': r[5],
            'statusText': {0: '即将开始', 1: '进行中', 2: '已结束'}.get(r[5], ''),
            'cityName': r[8] or '',
            'cityId': r[9],
            'coverImage': r[10],
            'followCount': r[11] or 0,
            'tags': tags,
            'isFollowed': True,
            'isOwner': r[7] == user.id,
            'creatorName': r[13],
            'creatorAvatar': r[14],
            'createdAt': r[15].strftime('%Y-%m-%dT%H:%M:%S') if r[15] else None,
        })

    pages = (total + size - 1) // size if total > 0 else 0
    return {
        'records': records,
        'total': total,
        'page': page,
        'size': size,
        'pages': pages,
    }
# ============================================================
# 漫展评论 API
# ============================================================

def _ensure_comic_comments_table(db: Session):
    """确保 comic_comments 表存在"""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS comic_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            parent_id INTEGER,
            reply_to_user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (event_id) REFERENCES comic_events(id),
            FOREIGN KEY (parent_id) REFERENCES comic_comments(id),
            FOREIGN KEY (reply_to_user_id) REFERENCES users(id)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS comic_comment_likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            comment_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (comment_id) REFERENCES comic_comments(id),
            UNIQUE(user_id, comment_id)
        )
    """))
    db.commit()


def _comic_comment_batch_enrich(db: Session, all_comments: list, current_user_id: int | None) -> list:
    """批量 enrich 漫展评论：收集所有 comment_id → 4 次聚合查询替代逐条 N*4 查询"""
    if not all_comments:
        return []

    # 展平嵌套的 replies
    flat = []
    for c in all_comments:
        flat.append(c)
        if c.get('replies'):
            flat.extend(c['replies'])

    if not flat:
        return all_comments

    comment_ids = [c['id'] for c in flat]
    user_ids = set(c['user_id'] for c in flat)
    reply_to_user_ids = set(c.get('reply_to_user_id') for c in flat if c.get('reply_to_user_id'))
    all_user_ids = user_ids | reply_to_user_ids

    # 1. 批量加载用户
    user_map = {}
    if all_user_ids:
        uid_list = list(all_user_ids)
        placeholders = ','.join([f':uid{i}' for i in range(len(uid_list))])
        params = {f'uid{i}': uid for i, uid in enumerate(uid_list)}
        users = db.execute(text(
            f"SELECT id, username, display_name, avatar FROM users WHERE id IN ({placeholders})"
        ), params).mappings().all()
        user_map = {u['id']: dict(u) for u in users}

    # 2. 批量 like_count
    like_count_map = {}
    if comment_ids:
        cid_list = list(comment_ids)
        placeholders = ','.join([f':cid{i}' for i in range(len(cid_list))])
        params = {f'cid{i}': cid for i, cid in enumerate(cid_list)}
        lcs = db.execute(text(
            f"SELECT comment_id, COUNT(*) as cnt FROM comic_comment_likes WHERE comment_id IN ({placeholders}) GROUP BY comment_id"
        ), params).mappings().all()
        like_count_map = {lc['comment_id']: lc['cnt'] for lc in lcs}

    # 3. 批量 is_liked
    liked_set = set()
    if comment_ids and current_user_id:
        cid_list = list(comment_ids)
        placeholders = ','.join([f':cid{i}' for i in range(len(cid_list))])
        params = {f'cid{i}': cid for i, cid in enumerate(cid_list)}
        params['uid'] = current_user_id
        liked_rows = db.execute(text(
            f"SELECT comment_id FROM comic_comment_likes WHERE comment_id IN ({placeholders}) AND user_id=:uid"
        ), params).mappings().all()
        liked_set = {r['comment_id'] for r in liked_rows}

    # 4. 批量 reply_count
    reply_count_map = {}
    if comment_ids:
        cid_list = list(comment_ids)
        placeholders = ','.join([f':cid{i}' for i in range(len(cid_list))])
        params = {f'cid{i}': cid for i, cid in enumerate(cid_list)}
        rcs = db.execute(text(
            f"SELECT parent_id, COUNT(*) as cnt FROM comic_comments WHERE parent_id IN ({placeholders}) GROUP BY parent_id"
        ), params).mappings().all()
        reply_count_map = {rc['parent_id']: rc['cnt'] for rc in rcs}

    # 填充字段
    for c in flat:
        c['user'] = user_map.get(c['user_id'])
        c['reply_to_user'] = user_map.get(c.get('reply_to_user_id')) if c.get('reply_to_user_id') else None
        c['like_count'] = like_count_map.get(c['id'], 0)
        c['is_liked'] = c['id'] in liked_set
        c['reply_count'] = reply_count_map.get(c['id'], 0)
        c['target_type'] = 'comic'

    return all_comments


@router.get("/events/{event_id}/comments")
def get_comic_comments(event_id: int, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                       db: Session = Depends(get_db), current_user: User = Depends(get_optional_user)):
    """获取漫展评论"""
    _ensure_comic_comments_table(db)
    uid = current_user.id if current_user else None
    offset = (page - 1) * size
    total = db.execute(text(
        "SELECT COUNT(*) as cnt FROM comic_comments WHERE event_id=:eid AND parent_id IS NULL"
    ), {'eid': event_id}).mappings().first()['cnt']
    rows = db.execute(text("""
        SELECT * FROM comic_comments
        WHERE event_id=:eid AND parent_id IS NULL
        ORDER BY created_at DESC
        LIMIT :lim OFFSET :off
    """), {'eid': event_id, 'lim': size, 'off': offset}).mappings().all()
    comments = [dict(r) for r in rows]
    for c in comments:
        subs = db.execute(text(
            "SELECT * FROM comic_comments WHERE parent_id=:pid ORDER BY created_at ASC LIMIT 3"
        ), {'pid': c['id']}).mappings().all()
        c['replies'] = [dict(s) for s in subs]
    comments = _comic_comment_batch_enrich(db, comments, uid)
    pages = (total + size - 1) // size if total > 0 else 0
    return {'comments': comments, 'total': total, 'page': page, 'size': size, 'pages': pages}


@router.post("/events/{event_id}/comments")
def post_comic_comment(event_id: int, content: str = Body(..., embed=True),
                       parent_id: int | None = Body(None, embed=True),
                       reply_to_user_id: int | None = Body(None, embed=True),
                       db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """发表漫展评论"""
    _ensure_comic_comments_table(db)
    content = content.strip()
    if not content or len(content) > 2000:
        raise HTTPException(status_code=400, detail="评论内容不能为空且不超过2000字")
    db.execute(text("""
        INSERT INTO comic_comments (content, user_id, event_id, parent_id, reply_to_user_id)
        VALUES (:content, :uid, :eid, :pid, :ruid)
    """), {'content': content, 'uid': current_user.id, 'eid': event_id,
           'pid': parent_id, 'ruid': reply_to_user_id})
    db.commit()
    cid = db.execute(text("SELECT last_insert_rowid()")).scalar()
    row = db.execute(text("SELECT * FROM comic_comments WHERE id=:id"), {'id': cid}).mappings().first()
    result = dict(row)
    enriched = _comic_comment_enrich(db, [result], current_user.id)
    return {'comment': enriched[0]}


@router.delete("/events/comments/{comment_id}")
def delete_comic_comment(comment_id: int, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """删除漫展评论"""
    _ensure_comic_comments_table(db)
    row = db.execute(text("SELECT user_id FROM comic_comments WHERE id=:id"), {'id': comment_id}).first()
    if not row:
        raise HTTPException(status_code=404, detail="评论不存在")
    if row[0] != current_user.id:
        raise HTTPException(status_code=403, detail="无权限")
    db.execute(text("DELETE FROM comic_comment_likes WHERE comment_id=:id"), {'id': comment_id})
    db.execute(text("DELETE FROM comic_comments WHERE id=:id"), {'id': comment_id})
    db.commit()
    return {'message': 'ok'}


@router.post("/events/comments/{comment_id}/like")
def like_comic_comment(comment_id: int, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """点赞/取消点赞漫展评论"""
    _ensure_comic_comments_table(db)
    existing = db.execute(text(
        "SELECT id FROM comic_comment_likes WHERE comment_id=:cid AND user_id=:uid"
    ), {'cid': comment_id, 'uid': current_user.id}).first()
    if existing:
        db.execute(text("DELETE FROM comic_comment_likes WHERE id=:id"), {'id': existing[0]})
        db.commit()
        return {'message': 'unliked', 'is_liked': False}
    else:
        db.execute(text(
            "INSERT INTO comic_comment_likes (user_id, comment_id) VALUES (:uid, :cid)"
        ), {'uid': current_user.id, 'cid': comment_id})
        db.commit()
        return {'message': 'liked', 'is_liked': True}


@router.get("/events/comments/{comment_id}/replies")
def get_comic_comment_replies(comment_id: int, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                              db: Session = Depends(get_db), current_user: User = Depends(get_optional_user)):
    """获取漫展评论子回复"""
    _ensure_comic_comments_table(db)
    uid = current_user.id if current_user else None
    offset = (page - 1) * size
    total = db.execute(text(
        "SELECT COUNT(*) as cnt FROM comic_comments WHERE parent_id=:pid"
    ), {'pid': comment_id}).mappings().first()['cnt']
    rows = db.execute(text("""
        SELECT * FROM comic_comments WHERE parent_id=:pid
        ORDER BY created_at ASC LIMIT :lim OFFSET :off
    """), {'pid': comment_id, 'lim': size, 'off': offset}).mappings().all()
    replies = [dict(r) for r in rows]
    replies = _comic_comment_enrich(db, replies, uid)
    return {'replies': replies, 'total': total, 'page': page, 'size': size}
