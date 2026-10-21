import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_inventory', Path(__file__).resolve().parents[1] / 'scripts/release_inventory.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseInventoryTests(unittest.TestCase):
    def config(self):
        return SimpleNamespace(DB_HOST='localhost', DB_PASS='secret-db-canary',
            COS_REGION='ap-guangzhou', COS_ID='secret-id-canary', COS_KEY='secret-key-canary',
            COS_BUCKET='private-bucket-canary', COS_CI_IMAGE_AUDIT_ENABLED=True,
            SMTP_HOST='smtp.qq.com', SMTP_USER='private-account-canary', SMTP_PASSWORD='secret-smtp-canary',
            SMTP_USE_SSL=True, APNS_BUNDLE_ID='online.nonto.native', APNS_KEY_ID='secret-apns-id-canary',
            APNS_TEAM_ID='secret-team-canary', APNS_PRIVATE_KEY_PATH='/private/key-canary.p8',
            SQLALCHEMY_DATABASE_URI='mysql://private-uri-canary')

    def test_report_never_serializes_credentials_accounts_paths_or_bucket_names(self):
        data = module.inventory(self.config())
        encoded = json.dumps(data)
        self.assertNotIn('canary', encoded)
        self.assertTrue(data['database']['hostIsLocal'])
        self.assertIsNone(data['database']['physicalRegion'])
        self.assertEqual(data['mail']['smtpHost'], 'smtp.qq.com')

    def test_unread_database_override_does_not_claim_audit_is_active(self):
        for status in ('not_requested', 'failed'):
            report = module.inventory(self.config(), database_status=status)
            self.assertIsNone(report['media']['imageAuditEffectiveEnabled'])
        self.assertTrue(module.inventory(self.config(), database_status='read')['media']['imageAuditEffectiveEnabled'])
        self.assertFalse(module.inventory(self.config(), database_status='read', image_setting=False)['media']['imageAuditEffectiveEnabled'])

    def test_credential_like_host_or_region_is_not_exported(self):
        config = self.config()
        config.SMTP_HOST = 'smtp://user:password@smtp.qq.com'
        config.COS_REGION = 'https://private.invalid/?token=secret'
        config.APNS_BUNDLE_ID = '/private/key.p8'
        data = module.inventory(config)
        self.assertIsNone(data['mail']['smtpHost'])
        self.assertIsNone(data['media']['configuredRegion'])
        self.assertIsNone(data['push']['bundleID'])

    def test_snapshot_does_not_query_database_or_touch_application_startup(self):
        with patch.object(module, 'read_image_setting', side_effect=AssertionError('Unexpected database access')):
            module.inventory(self.config())
