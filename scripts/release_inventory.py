#!/usr/bin/env python3
"""Read-only, allowlisted deployment inventory. Never exports credentials or user data."""
import argparse
import json
import re
import sys
from pathlib import Path


def public_host(value):
    """Only emit a plain DNS host; reject URLs, paths and credential-like values."""
    value = str(value).strip().lower()
    return value if re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', value) else None


def inventory(config, *, image_setting=None, database_status='not_requested'):
    region = str(getattr(config, 'COS_REGION', '')).strip()
    bundle = str(getattr(config, 'APNS_BUNDLE_ID', '')).strip()
    database_setting = image_setting if type(image_setting) is bool else None
    return {
        'schemaVersion': 1,
        'scope': 'configuration_snapshot_not_operational_or_legal_certification',
        'database': {
            'hostIsLocal': str(getattr(config, 'DB_HOST', '')).strip().lower() in ('localhost', '127.0.0.1', '::1'),
            'physicalRegion': None,
            'settingsRead': database_status,
        },
        'media': {
            'implementation': 'tencent_cos',
            'bucketConfigured': bool(getattr(config, 'COS_BUCKET_NAME', '') or getattr(config, 'COS_BUCKET', '')),
            'credentialsConfigured': bool(getattr(config, 'COS_ID', '') and getattr(config, 'COS_KEY', '')),
            'configuredRegion': region if re.fullmatch(r'[a-z]{2}-[a-z0-9-]{1,40}', region) else None,
            'imageAuditEnvironmentEnabled': bool(getattr(config, 'COS_CI_IMAGE_AUDIT_ENABLED', False)),
            'imageAuditDatabaseOverride': database_setting,
            'imageAuditEffectiveEnabled': database_setting if database_setting is not None else (
                bool(getattr(config, 'COS_CI_IMAGE_AUDIT_ENABLED', False)) if database_status == 'read' else None),
            'note': 'Database overrides environment. Configuration does not prove successful image or video review.',
        },
        'mail': {
            'smtpHost': public_host(getattr(config, 'SMTP_HOST', '')),
            'credentialsConfigured': bool(getattr(config, 'SMTP_USER', '') and getattr(config, 'SMTP_PASSWORD', '')),
            'sslConfigured': bool(getattr(config, 'SMTP_USE_SSL', False)),
        },
        'push': {
            'provider': 'apple_apns',
            'bundleID': bundle if re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', bundle) else None,
            'credentialsConfigured': bool(getattr(config, 'APNS_KEY_ID', '') and getattr(config, 'APNS_TEAM_ID', '') and getattr(config, 'APNS_PRIVATE_KEY_PATH', '')),
        },
        'requiresSeparateEvidence': [
            'hosting_provider_and_physical_region', 'provider_contracts_and_cross_border_arrangements',
            'database_media_log_and_backup_retention', 'account_deletion_and_media_cleanup',
            'text_image_video_and_message_moderation', 'mail_receipt_and_rights_request_response',
            'real_name_and_minor_protection', 'public_policy_deployment_and_store_privacy_labels',
        ],
    }


def read_image_setting(config):
    # Import neither app.main (startup) nor app.database (application session).
    from sqlalchemy import create_engine, text
    engine = create_engine(config.SQLALCHEMY_DATABASE_URI, connect_args={'connect_timeout': 5})
    try:
        with engine.connect() as connection:
            value = connection.execute(text(
                "SELECT value FROM admin_settings WHERE `key` = :key"
            ), {'key': 'image_moderation_enabled'}).scalar()
            if value is None:
                return None
            if str(value).strip().lower() not in ('true', 'false'):
                raise ValueError('Invalid moderation override')
            return str(value).strip().lower() == 'true'
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True, help='The same dotenv file used by the server; inherited environment takes precedence')
    parser.add_argument('--database', action='store_true', help='Read only the image moderation override with one SELECT; no user records')
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.exit(2, 'Environment file is missing; no inventory generated.\n')
    try:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.core.config import Config
        setting, status = None, 'not_requested'
        if args.database:
            try:
                setting = read_image_setting(Config)
                status = 'read'
            except Exception:
                # Exception text may contain DB credentials; never print it.
                status = 'failed'
        report = inventory(Config, image_setting=setting, database_status=status)
    except Exception:
        parser.exit(2, 'Could not load configuration; no credentials or exception details were exported.\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if status == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
