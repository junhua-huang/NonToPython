from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.models import AppRelease, User
from app.services.admin_audit_service import record_required_admin_audit


PLATFORMS = Literal['android', 'ios', 'windows', 'web']
UPDATE_ACTIONS = Literal['download', 'store', 'refresh']

logger = logging.getLogger(__name__)
public_router = APIRouter(prefix='/api/app', tags=['App'])
admin_router = APIRouter(prefix='/api/admin/app-releases', tags=['Admin App Releases'])


class AppReleaseCreate(BaseModel):
    platform: PLATFORMS
    channel: str = Field(default='stable', min_length=1, max_length=32, pattern=r'^[a-z0-9][a-z0-9._-]*$')
    version_name: str = Field(min_length=1, max_length=64)
    build_number: int = Field(ge=1)
    minimum_supported_build_number: int = Field(default=0, ge=0)
    force_update: bool = False
    update_action: UPDATE_ACTIONS = 'download'
    download_url: str = Field(min_length=1, max_length=2048)
    release_notes: list[str] = Field(default_factory=list, max_length=20)
    published_at: datetime | None = None
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    file_size: int | None = Field(default=None, ge=0)
    enabled: bool = True
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('version_name')
    @classmethod
    def validate_version_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('version_name is required')
        return value

    @field_validator('release_notes')
    @classmethod
    def validate_release_notes(cls, value: list[str]) -> list[str]:
        notes: list[str] = []
        for note in value:
            if not isinstance(note, str):
                raise ValueError('release_notes must contain strings')
            normalized = note.strip()
            if normalized:
                notes.append(normalized[:500])
        return notes

    @field_validator('download_url')
    @classmethod
    def validate_download_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme != 'https' or not parsed.netloc:
            raise ValueError('download_url must be an HTTPS URL')
        return value

    @field_validator('sha256')
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
            raise ValueError('sha256 must be a 64-character hexadecimal value')
        return value

    @field_validator('minimum_supported_build_number')
    @classmethod
    def validate_minimum_build(cls, value: int, info):
        build_number = info.data.get('build_number')
        if build_number is not None and value > build_number:
            raise ValueError('minimum_supported_build_number cannot exceed build_number')
        return value

    @field_validator('reason')
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('reason is required')
        return value


class AppReleasePatch(BaseModel):
    channel: str | None = Field(default=None, min_length=1, max_length=32, pattern=r'^[a-z0-9][a-z0-9._-]*$')
    version_name: str | None = Field(default=None, min_length=1, max_length=64)
    build_number: int | None = Field(default=None, ge=1)
    minimum_supported_build_number: int | None = Field(default=None, ge=0)
    force_update: bool | None = None
    update_action: UPDATE_ACTIONS | None = None
    download_url: str | None = Field(default=None, min_length=1, max_length=2048)
    release_notes: list[str] | None = Field(default=None, max_length=20)
    published_at: datetime | None = None
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    file_size: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def reject_null_updates(self):
        nullable_fields = {'sha256', 'file_size', 'release_notes'}
        for field_name in self.model_fields_set - nullable_fields:
            if field_name != 'reason' and getattr(self, field_name) is None:
                raise ValueError(f'{field_name} cannot be null')
        return self

    @field_validator('version_name')
    @classmethod
    def validate_patch_version_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError('version_name is required')
        return value

    @field_validator('release_notes')
    @classmethod
    def validate_patch_notes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [note.strip()[:500] for note in value if isinstance(note, str) and note.strip()]

    @field_validator('download_url')
    @classmethod
    def validate_patch_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme != 'https' or not parsed.netloc:
            raise ValueError('download_url must be an HTTPS URL')
        return value

    @field_validator('sha256')
    @classmethod
    def validate_patch_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
            raise ValueError('sha256 must be a 64-character hexadecimal value')
        return value

    @field_validator('reason')
    @classmethod
    def validate_patch_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('reason is required')
        return value


class AppReleaseDelete(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def validate_delete_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('reason is required')
        return value


def _utc_naive(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _empty_public_result(platform: str, channel: str) -> dict:
    return {
        'platform': platform,
        'channel': channel,
        'release_id': None,
        'latest_version': None,
        'latest_build_number': None,
        'minimum_supported_build_number': None,
        'update_available': False,
        'force_update': False,
        'update_action': None,
        'download_url': None,
        'release_notes': [],
        'published_at': None,
        'sha256': None,
        'file_size': None,
    }


def _release_query(db: Session, platform: str, channel: str) -> AppRelease | None:
    return (
        db.query(AppRelease)
        .filter(
            AppRelease.platform == platform,
            AppRelease.channel == channel,
            AppRelease.enabled.is_(True),
        )
        .order_by(
            AppRelease.build_number.desc(),
            AppRelease.published_at.desc(),
            AppRelease.id.desc(),
        )
        .first()
    )


def _version_service_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            'code': 'VERSION_CONFIG_UNAVAILABLE',
            'message': '版本服务暂不可用',
            'retryable': True,
        },
    )


@public_router.get('/version')
def check_version(
    platform: PLATFORMS,
    channel: Annotated[str, Query(min_length=1, max_length=32, pattern=r'^[a-z0-9][a-z0-9._-]*$')] = 'stable',
    current_version: Annotated[str, Query(min_length=1, max_length=64)] = '0.0.0',
    current_build_number: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
):
    del current_version  # Build number is the authoritative comparison value.
    try:
        release = _release_query(db, platform, channel)
        if release is None:
            return _empty_public_result(platform, channel)
        return release.to_public_dict(current_build_number=current_build_number)
    except Exception:
        logger.error('app_version_lookup_failed')
        raise _version_service_unavailable() from None


def _serialize_release_input(payload: AppReleaseCreate) -> dict:
    values = payload.model_dump(exclude={'reason'})
    values['published_at'] = _utc_naive(values.get('published_at'))
    values['release_notes'] = json.dumps(values['release_notes'], ensure_ascii=False)
    return values


def _build_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            'code': 'APP_RELEASE_BUILD_CONFLICT',
            'message': 'build number 与已有发布冲突或未保持单调递增',
        },
    )


def _highest_build(
    db: Session,
    *,
    platform: str,
    channel: str,
    exclude_release_id: int | None = None,
    enabled_only: bool = False,
) -> int | None:
    query = db.query(AppRelease).filter(
        AppRelease.platform == platform,
        AppRelease.channel == channel,
    )
    if exclude_release_id is not None:
        query = query.filter(AppRelease.id != exclude_release_id)
    if enabled_only:
        query = query.filter(AppRelease.enabled.is_(True))
    row = query.order_by(AppRelease.build_number.desc()).with_for_update().first()
    return row.build_number if row is not None else None


def _validate_create_build(db: Session, payload: AppReleaseCreate) -> None:
    highest = _highest_build(
        db,
        platform=payload.platform,
        channel=payload.channel,
    )
    if highest is not None and payload.build_number <= highest:
        raise _build_conflict()


def _validate_patch_build(
    db: Session,
    *,
    release: AppRelease,
    platform: str,
    channel: str,
    build_number: int,
    enabled: bool,
    sequence_changed: bool,
) -> None:
    if build_number < release.build_number:
        raise _build_conflict()

    highest = _highest_build(
        db,
        platform=platform,
        channel=channel,
        exclude_release_id=release.id,
    )
    if sequence_changed and highest is not None and build_number <= highest:
        raise _build_conflict()

    if enabled and not release.enabled:
        enabled_highest = _highest_build(
            db,
            platform=platform,
            channel=channel,
            exclude_release_id=release.id,
            enabled_only=True,
        )
        if enabled_highest is not None and build_number <= enabled_highest:
            raise _build_conflict()


@admin_router.get('')
def list_releases(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del admin
    query = db.query(AppRelease).order_by(AppRelease.created_at.desc(), AppRelease.id.desc())
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        'items': [row.to_admin_dict() for row in rows],
        'total': total,
        'page': page,
        'page_size': page_size,
    }


@admin_router.post('', status_code=201)
def create_release(
    payload: AppReleaseCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    release = AppRelease(**_serialize_release_input(payload))
    try:
        _validate_create_build(db, payload)
        db.add(release)
        db.flush()
        record_required_admin_audit(
            db,
            admin_user_id=admin.id,
            action='create_app_release',
            target_type='app_release',
            target_id=release.id,
            reason=payload.reason,
            request=request,
        )
        db.commit()
        db.refresh(release)
        return release.to_admin_dict()
    except IntegrityError:
        db.rollback()
        raise _build_conflict() from None
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail='发布版本创建失败') from None


@admin_router.get('/{release_id}')
def get_release(
    release_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del admin
    release = db.query(AppRelease).filter(AppRelease.id == release_id).first()
    if release is None:
        raise HTTPException(status_code=404, detail='发布版本不存在')
    return release.to_admin_dict()


@admin_router.patch('/{release_id}')
def patch_release(
    release_id: int,
    payload: AppReleasePatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    release = (
        db.query(AppRelease)
        .filter(AppRelease.id == release_id)
        .with_for_update()
        .first()
    )
    if release is None:
        raise HTTPException(status_code=404, detail='发布版本不存在')

    values = payload.model_dump(exclude_unset=True, exclude={'reason'})
    if 'release_notes' in values:
        values['release_notes'] = json.dumps(values['release_notes'], ensure_ascii=False)
    if 'published_at' in values:
        values['published_at'] = _utc_naive(values['published_at'])

    next_channel = values.get('channel', release.channel)
    next_build = values.get('build_number', release.build_number)
    next_minimum = values.get(
        'minimum_supported_build_number', release.minimum_supported_build_number
    )
    if next_minimum > next_build:
        raise HTTPException(
            status_code=422,
            detail='minimum_supported_build_number cannot exceed build_number',
        )
    sequence_changed = next_channel != release.channel or next_build != release.build_number

    try:
        _validate_patch_build(
            db,
            release=release,
            platform=release.platform,
            channel=next_channel,
            build_number=next_build,
            enabled=values.get('enabled', release.enabled),
            sequence_changed=sequence_changed,
        )
        for key, value in values.items():
            setattr(release, key, value)
        db.flush()
        record_required_admin_audit(
            db,
            admin_user_id=admin.id,
            action='update_app_release',
            target_type='app_release',
            target_id=release.id,
            reason=payload.reason,
            request=request,
        )
        db.commit()
        db.refresh(release)
        return release.to_admin_dict()
    except IntegrityError:
        db.rollback()
        raise _build_conflict() from None
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail='发布版本更新失败') from None


@admin_router.delete('/{release_id}')
def delete_release(
    release_id: int,
    payload: AppReleaseDelete,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    release = (
        db.query(AppRelease)
        .filter(AppRelease.id == release_id)
        .with_for_update()
        .first()
    )
    if release is None:
        raise HTTPException(status_code=404, detail='发布版本不存在')

    try:
        db.delete(release)
        record_required_admin_audit(
            db,
            admin_user_id=admin.id,
            action='delete_app_release',
            target_type='app_release',
            target_id=release.id,
            reason=payload.reason,
            request=request,
        )
        db.commit()
        return {'deleted': True, 'id': release_id}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail='发布版本删除失败') from None
