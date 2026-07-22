"""
漫展路由 - FastAPI 版本
从 C:\PythonProject\routes_comic.py 迁移（Flask → FastAPI）
"""
from datetime import datetime, date as date_cls
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_optional_user
from app.models.models import (
    User, ComicEvent, ComicCity, ComicEventImage, ComicEventTagRel, ComicTag, ComicEventFollow,
    ComicComment, ComicLike, ComicCommentLike,
)
from app.services.moderation_route_helpers import moderate_route_fields
from app.services.moderation_service import moderation_service

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

    where = "WHERE e.end_date >= CURDATE()"
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
               e.creator_id, e.like_count, e.comment_count,
               c.name AS city_name, c.id AS city_id,
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

    # 批量查询图片和当前用户关注状态，避免列表页逐行查询。
    event_ids = [r[0] for r in rows]
    followed_event_ids = set()
    if user_id is not None and event_ids:
        follow_sql = text(
            "SELECT event_id FROM comic_event_follows WHERE user_id = :uid AND event_id IN :event_ids"
        ).bindparams(bindparam('event_ids', expanding=True))
        followed_rows = db.execute(follow_sql, {'uid': user_id, 'event_ids': event_ids}).fetchall()
        followed_event_ids = {row[0] for row in followed_rows}

    images_map: dict = {}
    if event_ids:
        imgs = db.query(ComicEventImage).filter(
            ComicEventImage.event_id.in_(event_ids)
        ).order_by(
            ComicEventImage.event_id,
            ComicEventImage.is_cover.desc(),
            ComicEventImage.sort_order.asc()
        ).all()
        for img in imgs:
            images_map.setdefault(img.event_id, []).append({
                "id": img.id, "imageUrl": img.image_url,
                "isCover": bool(img.is_cover), "sortOrder": img.sort_order,
            })

    records = []
    for r in rows:
        event_id = r[0]
        tags = [t.strip() for t in (r[13] or '').split(',') if t.strip()]
        creator_id = r[7]
        is_owner = (user_id is not None and creator_id == user_id)
        is_followed = event_id in followed_event_ids

        images = images_map.get(event_id, [])
        cover_image = next((i["imageUrl"] for i in images if i["isCover"]), None) \
                  or (images[0]["imageUrl"] if images else None)

        # 实时重算状态（数据库里的 status 是创建时算的，不会随时间更新）
        real_status, real_status_text = _recalc_status_text(r[2], r[3])

        records.append({
            'id': event_id,
            'name': r[1],
            'startDate': r[2].strftime('%Y-%m-%d') if r[2] else None,
            'endDate': r[3].strftime('%Y-%m-%d') if r[3] else None,
            'venue': r[4] or '',
            'status': real_status,
            'statusText': real_status_text,
            'cityName': r[10] or '',
            'cityId': r[11],
            'likeCount': r[8] or 0,
            'commentCount': r[9] or 0,
            'followCount': r[12] or 0,
            'coverImage': cover_image,
            'images': images,
            'tags': tags,
            'isOwner': is_owner,
            'isFollowed': is_followed,
            'creatorName': r[14],
            'creatorAvatar': r[15],
            'createdAt': r[16].strftime('%Y-%m-%dT%H:%M:%S') if r[16] else None,
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
               e.like_count, e.comment_count,
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

    tags = [t.strip() for t in (row[16] or '').split(',') if t.strip()]

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
    is_liked = False
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

        lk = db.query(ComicLike).filter(
            ComicLike.event_id == event_id, ComicLike.user_id == user_id
        ).first()
        is_liked = lk is not None

    # 实时重算状态（数据库里的 status 是创建时算的，不会随时间更新）
    real_status, real_status_text = _recalc_status_text(row[4], row[5])

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
        'status': real_status,
        'statusText': real_status_text,
        'coverImage': cover,
        'followCount': row[13] or 0,
        'likeCount': row[14] or 0,
        'commentCount': row[15] or 0,
        'isFollowed': is_followed,
        'isLiked': is_liked,
        'isOwner': is_owner,
        'tags': tags,
        'images': images,
        'creatorName': row[17],
        'creatorAvatar': row[18],
        'createdAt': row[19].strftime('%Y-%m-%dT%H:%M:%S') if row[19] else None,
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


def _calc_status(start_date, end_date) -> int:
    """根据日期计算漫展状态

    兼容 str（'YYYY-MM-DD'）与 date 对象两种输入。
    """
    def _to_date(val):
        if val is None or val == '':
            return None
        if isinstance(val, date_cls):
            return val
        if isinstance(val, datetime):
            return val.date()
        try:
            return datetime.strptime(str(val), '%Y-%m-%d').date()
        except Exception:
            return None

    try:
        sd = _to_date(start_date)
        ed = _to_date(end_date)
        today = date_cls.today()
        if sd and ed:
            if today < sd:
                return 0
            elif sd <= today <= ed:
                return 1
            else:
                return 2
        if sd and today < sd:
            return 0
        if sd and today >= sd:
            return 2
    except Exception:
        pass
    return 0


_STATUS_TEXT_MAP = {0: '即将开始', 1: '进行中', 2: '已结束'}


def _recalc_status_text(start_date, end_date):
    """根据当前日期实时重算 (status, statusText)。

    漫展创建时只计算一次 status 存入数据库，之后不会自动更新。
    列表/详情接口返回前调用本函数，确保状态标签随时间正确变化。
    """
    status = _calc_status(start_date, end_date)
    return status, _STATUS_TEXT_MAP.get(status, '')


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
    moderate_route_fields(
        moderation_service,
        "POST /api/comic/events",
        {
            "name": name,
            "venue": venue,
            "ticket_info": ticket_info,
            "website": website,
            "intro": intro,
        },
        actor_user_id=user.id,
        is_public=True,
    )

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
    moderate_route_fields(
        moderation_service,
        "PUT /api/comic/events/{event_id}",
        {
            "name": name,
            "venue": venue,
            "ticket_info": ticket_info,
            "website": website,
            "intro": intro,
        },
        actor_user_id=user.id,
        is_public=True,
    )

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
    # 安全：忽略客户端传入的 userId，始终使用当前登录用户的 id，
    # 防止越权替他人关注/取关。
    uid = user.id

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
               e.venue, e.status, e.intro, e.like_count, e.comment_count,
               c.name AS city_name, c.id AS city_id,
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

    # 批量查询图片列表
    event_ids = [r[0] for r in rows]
    images_map: dict = {}
    if event_ids:
        imgs = db.query(ComicEventImage).filter(
            ComicEventImage.event_id.in_(event_ids)
        ).order_by(
            ComicEventImage.event_id,
            ComicEventImage.is_cover.desc(),
            ComicEventImage.sort_order.asc()
        ).all()
        for img in imgs:
            images_map.setdefault(img.event_id, []).append({
                "id": img.id, "imageUrl": img.image_url,
                "isCover": bool(img.is_cover), "sortOrder": img.sort_order,
            })

    records = []
    for r in rows:
        event_id = r[0]
        tags = [t.strip() for t in (r[12] or '').split(',') if t.strip()]
        fo = db.execute(
            text("SELECT 1 FROM comic_event_follows WHERE event_id = :eid AND user_id = :uid"),
            {'eid': event_id, 'uid': user.id}
        ).first()

        images = images_map.get(event_id, [])
        cover_image = next((i["imageUrl"] for i in images if i["isCover"]), None) \
                  or (images[0]["imageUrl"] if images else None)

        # 实时重算状态
        real_status, real_status_text = _recalc_status_text(r[2], r[3])

        records.append({
            'id': event_id,
            'name': r[1],
            'startDate': r[2].strftime('%Y-%m-%d') if r[2] else None,
            'endDate': r[3].strftime('%Y-%m-%d') if r[3] else None,
            'venue': r[4] or '',
            'status': real_status,
            'statusText': real_status_text,
            'cityName': r[9] or '',
            'cityId': r[10],
            'likeCount': r[7] or 0,
            'commentCount': r[8] or 0,
            'followCount': r[11] or 0,
            'coverImage': cover_image,
            'images': images,
            'tags': tags,
            'isOwner': True,
            'isFollowed': fo is not None,
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
               e.venue, e.status, e.intro, e.like_count, e.comment_count,
               e.creator_id,
               c.name AS city_name, c.id AS city_id,
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

    # 批量查询图片列表
    event_ids = [r[0] for r in rows]
    images_map: dict = {}
    if event_ids:
        imgs = db.query(ComicEventImage).filter(
            ComicEventImage.event_id.in_(event_ids)
        ).order_by(
            ComicEventImage.event_id,
            ComicEventImage.is_cover.desc(),
            ComicEventImage.sort_order.asc()
        ).all()
        for img in imgs:
            images_map.setdefault(img.event_id, []).append({
                "id": img.id, "imageUrl": img.image_url,
                "isCover": bool(img.is_cover), "sortOrder": img.sort_order,
            })

    records = []
    for r in rows:
        event_id = r[0]
        tags = [t.strip() for t in (r[13] or '').split(',') if t.strip()]

        images = images_map.get(event_id, [])
        cover_image = next((i["imageUrl"] for i in images if i["isCover"]), None) \
                  or (images[0]["imageUrl"] if images else None)

        # 实时重算状态
        real_status, real_status_text = _recalc_status_text(r[2], r[3])

        records.append({
            'id': event_id,
            'name': r[1],
            'startDate': r[2].strftime('%Y-%m-%d') if r[2] else None,
            'endDate': r[3].strftime('%Y-%m-%d') if r[3] else None,
            'venue': r[4] or '',
            'status': real_status,
            'statusText': real_status_text,
            'cityName': r[10] or '',
            'cityId': r[11],
            'likeCount': r[7] or 0,
            'commentCount': r[8] or 0,
            'followCount': r[12] or 0,
            'coverImage': cover_image,
            'images': images,
            'tags': tags,
            'isFollowed': True,
            'isOwner': r[9] == user.id,
            'creatorName': r[14],
            'creatorAvatar': r[15],
            'createdAt': r[16].strftime('%Y-%m-%dT%H:%M:%S') if r[16] else None,
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
# 漫展评论 + 点赞 API（ORM 版）
# ============================================================

@router.get("/events/{event_id}/comments")
def get_comic_comments(
    event_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    """获取漫展一级评论（分页），含前3条子回复"""
    # 验证漫展存在
    ev = db.query(ComicEvent).filter(ComicEvent.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="漫展不存在")

    uid = current_user.id if current_user else None
    offset = (page - 1) * size

    total = db.query(ComicComment).filter(
        ComicComment.event_id == event_id,
        ComicComment.parent_id.is_(None),
    ).count()

    comments = (
        db.query(ComicComment)
        .filter(ComicComment.event_id == event_id, ComicComment.parent_id.is_(None))
        .order_by(ComicComment.created_at.desc())
        .offset(offset)
        .limit(size)
        .all()
    )

    result = []
    for c in comments:
        d = c.to_dict()
        # 预取前3条子回复
        top_replies = (
            db.query(ComicComment)
            .filter(ComicComment.parent_id == c.id)
            .order_by(ComicComment.created_at.asc())
            .limit(3)
            .all()
        )
        d["replies"] = [r.to_dict() for r in top_replies]
        if c.reply_count > 3:
            d["replies_has_more"] = True
        result.append(d)

    result = _comic_batch_enrich(db, result, uid)
    pages = (total + size - 1) // size if total > 0 else 0
    return {"comments": result, "total": total, "page": page, "size": size, "pages": pages}


@router.post("/events/{event_id}/comments", status_code=201)
def post_comic_comment(
    event_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """发表漫展评论 / 回复"""
    ev = db.query(ComicEvent).filter(ComicEvent.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="漫展不存在")

    content = (payload.get("content") or "").strip()
    parent_id = payload.get("parentId") or payload.get("parent_id")
    reply_to_user_id = payload.get("replyToUserId") or payload.get("reply_to_user_id")

    if not content or len(content) > 2000:
        raise HTTPException(status_code=400, detail="评论内容不能为空且不超过2000字")
    moderate_route_fields(
        moderation_service,
        "POST /api/comic/events/{event_id}/comments",
        {"content": content},
        actor_user_id=current_user.id,
        is_public=True,
    )

    if parent_id:
        parent = db.query(ComicComment).filter(ComicComment.id == parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="父评论不存在")
        parent.reply_count += 1

    comment = ComicComment(
        content=content,
        user_id=current_user.id,
        event_id=event_id,
        parent_id=parent_id,
        reply_to_user_id=reply_to_user_id,
    )
    db.add(comment)
    ev.comment_count += 1
    db.commit()
    db.refresh(comment)

    enriched = _comic_batch_enrich(db, [comment.to_dict()], current_user.id)
    return {"comment": enriched[0]}


@router.delete("/events/comments/{comment_id}")
def delete_comic_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除漫展评论"""
    comment = db.query(ComicComment).filter(ComicComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="评论不存在")
    if comment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 更新漫展评论计数
    ev = db.query(ComicEvent).filter(ComicEvent.id == comment.event_id).first()
    if ev and ev.comment_count > 0:
        ev.comment_count -= 1

    # 删除关联点赞
    db.query(ComicCommentLike).filter(ComicCommentLike.comment_id == comment_id).delete()
    # 删除子回复
    db.query(ComicComment).filter(ComicComment.parent_id == comment_id).delete()

    db.delete(comment)
    db.commit()
    return {"message": "ok"}


@router.get("/events/comments/{comment_id}/replies")
def get_comic_comment_replies(
    comment_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    """获取漫展评论子回复（分页）"""
    uid = current_user.id if current_user else None
    offset = (page - 1) * size

    total = db.query(ComicComment).filter(ComicComment.parent_id == comment_id).count()
    replies = (
        db.query(ComicComment)
        .filter(ComicComment.parent_id == comment_id)
        .order_by(ComicComment.created_at.asc())
        .offset(offset)
        .limit(size)
        .all()
    )

    result = [r.to_dict() for r in replies]
    result = _comic_batch_enrich(db, result, uid)
    return {"replies": result, "total": total, "page": page, "size": size}


@router.post("/events/comments/{comment_id}/like")
def toggle_comic_comment_like(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """点赞/取消点赞漫展评论"""
    comment = db.query(ComicComment).filter(ComicComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="评论不存在")

    existing = db.query(ComicCommentLike).filter(
        ComicCommentLike.comment_id == comment_id,
        ComicCommentLike.user_id == current_user.id,
    ).first()

    if existing:
        db.delete(existing)
        if comment.like_count > 0:
            comment.like_count -= 1
        db.commit()
        return {"is_liked": False, "like_count": comment.like_count}
    else:
        like = ComicCommentLike(user_id=current_user.id, comment_id=comment_id)
        db.add(like)
        comment.like_count += 1
        db.commit()
        return {"is_liked": True, "like_count": comment.like_count}


@router.post("/events/{event_id}/like")
def toggle_comic_event_like(
    event_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """点赞/取消点赞漫展"""
    ev = db.query(ComicEvent).filter(ComicEvent.id == event_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="漫展不存在")

    existing = db.query(ComicLike).filter(
        ComicLike.event_id == event_id,
        ComicLike.user_id == current_user.id,
    ).first()

    if existing:
        db.delete(existing)
        if ev.like_count > 0:
            ev.like_count -= 1
        db.commit()
        return {"is_liked": False, "like_count": ev.like_count}
    else:
        like = ComicLike(user_id=current_user.id, event_id=event_id)
        db.add(like)
        ev.like_count += 1
        db.commit()
        return {"is_liked": True, "like_count": ev.like_count}


# — 批量 enrich 工具 —

def _comic_batch_enrich(db: Session, all_comments: list, current_user_id: int | None) -> list:
    """批量填充评论的 user / like_count / is_liked / reply_count"""
    if not all_comments:
        return []

    # 展平嵌套 replies
    flat = []
    for c in all_comments:
        flat.append(c)
        if c.get("replies"):
            flat.extend(c["replies"])

    if not flat:
        return all_comments

    comment_ids = [c["id"] for c in flat]
    user_ids = {c["user_id"] for c in flat}
    reply_to_ids = {c.get("reply_to_user_id") for c in flat if c.get("reply_to_user_id")}
    all_user_ids = list(user_ids | reply_to_ids)

    # 1. 用户
    user_map = {}
    if all_user_ids:
        users = db.query(User).filter(User.id.in_(all_user_ids)).all()
        user_map = {u.id: u.to_dict() for u in users}

    # 2. like_count
    like_count_map: dict = {}
    if comment_ids:
        rows = db.execute(text(
            "SELECT comment_id, COUNT(*) AS cnt FROM comic_comment_likes "
            "WHERE comment_id IN :cids GROUP BY comment_id"
        ).bindparams(bindparam('cids', expanding=True)), {"cids": comment_ids}).fetchall()
        like_count_map = {r[0]: r[1] for r in rows}

    # 3. is_liked
    liked_set: set = set()
    if comment_ids and current_user_id:
        rows = db.execute(text(
            "SELECT comment_id FROM comic_comment_likes "
            "WHERE comment_id IN :cids AND user_id = :uid"
        ).bindparams(bindparam('cids', expanding=True)), {"cids": comment_ids, "uid": current_user_id}).fetchall()
        liked_set = {r[0] for r in rows}

    # 4. reply_count
    reply_count_map: dict = {}
    if comment_ids:
        rows = db.execute(text(
            "SELECT parent_id, COUNT(*) AS cnt FROM comic_comments "
            "WHERE parent_id IN :cids GROUP BY parent_id"
        ).bindparams(bindparam('cids', expanding=True)), {"cids": comment_ids}).fetchall()
        reply_count_map = {r[0]: r[1] for r in rows}

    for c in flat:
        c["user"] = user_map.get(c["user_id"])
        c["like_count"] = like_count_map.get(c["id"], 0)
        c["is_liked"] = c["id"] in liked_set
        c["reply_count"] = reply_count_map.get(c["id"], 0)
        if c.get("reply_to_user_id"):
            c["reply_to_user"] = user_map.get(c["reply_to_user_id"])

    return all_comments
