import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.config import Config


class CorsConfigTest(unittest.TestCase):
    def _build_client(self, settings):
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings["allow_origins"],
            allow_origin_regex=settings["allow_origin_regex"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.post("/api/auth/login")
        def login():
            return {"ok": True}

        return TestClient(app)

    def _preflight(self, client, origin):
        return client.options(
            "/api/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,authorization",
            },
        )

    def test_production_cors_defaults_to_www_nonto_domain_only(self):
        env = {k: v for k, v in os.environ.items() if k != "CORS_ORIGINS"}
        env["APP_ENV"] = "production"
        with patch.dict(os.environ, env, clear=True):
            settings = Config.get_cors_settings()

        self.assertEqual(settings["allow_origins"], ["https://www.nonto.online"])
        self.assertIsNone(settings["allow_origin_regex"])

    def test_production_cors_allows_www_nonto_preflight(self):
        env = {k: v for k, v in os.environ.items() if k != "CORS_ORIGINS"}
        env["APP_ENV"] = "production"
        with patch.dict(os.environ, env, clear=True):
            settings = Config.get_cors_settings()

        with self._build_client(settings) as client:
            response = self._preflight(client, "https://www.nonto.online")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://www.nonto.online",
        )

    def test_production_cors_rejects_apex_nonto_preflight(self):
        env = {k: v for k, v in os.environ.items() if k != "CORS_ORIGINS"}
        env["APP_ENV"] = "production"
        with patch.dict(os.environ, env, clear=True):
            settings = Config.get_cors_settings()

        with self._build_client(settings) as client:
            response = self._preflight(client, "https://nonto.online")

        self.assertNotEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    def test_development_cors_allows_any_http_or_https_origin(self):
        env = {k: v for k, v in os.environ.items() if k != "CORS_ORIGINS"}
        env["APP_ENV"] = "development"
        with patch.dict(os.environ, env, clear=True):
            settings = Config.get_cors_settings()

        self.assertEqual(settings["allow_origins"], [])
        self.assertEqual(settings["allow_origin_regex"], r"^https?://.+$")

        with self._build_client(settings) as client:
            public_ip_response = self._preflight(client, "http://203.0.113.10:8080")
            https_response = self._preflight(client, "https://example.dev:9443")

        self.assertEqual(public_ip_response.status_code, 200)
        self.assertEqual(
            public_ip_response.headers.get("access-control-allow-origin"),
            "http://203.0.113.10:8080",
        )
        self.assertEqual(https_response.status_code, 200)
        self.assertEqual(
            https_response.headers.get("access-control-allow-origin"),
            "https://example.dev:9443",
        )
        self.assertIn("POST", public_ip_response.headers.get("access-control-allow-methods", ""))
        self.assertIn(
            "content-type",
            public_ip_response.headers.get("access-control-allow-headers", "").lower(),
        )
        self.assertIn(
            "authorization",
            public_ip_response.headers.get("access-control-allow-headers", "").lower(),
        )

    def test_cors_origins_env_overrides_defaults(self):
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "CORS_ORIGINS": "https://www.nonto.online, https://admin.nonto.online",
        }, clear=True):
            settings = Config.get_cors_settings()

        self.assertEqual(settings["allow_origins"], [
            "https://www.nonto.online",
            "https://admin.nonto.online",
        ])
        self.assertIsNone(settings["allow_origin_regex"])


if __name__ == "__main__":
    unittest.main()
