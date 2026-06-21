import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.config import Config


class CorsConfigTest(unittest.TestCase):
    def test_production_cors_defaults_to_nonto_domains(self):
        env = {k: v for k, v in os.environ.items() if k != "CORS_ORIGINS"}
        env["APP_ENV"] = "production"
        with patch.dict(os.environ, env, clear=True):
            settings = Config.get_cors_settings()

        self.assertEqual(settings["allow_origins"], [
            "https://www.nonto.online",
            "https://nonto.online",
        ])
        self.assertIsNone(settings["allow_origin_regex"])

    def test_development_cors_allows_localhost_and_private_lan(self):
        env = {k: v for k, v in os.environ.items() if k != "CORS_ORIGINS"}
        env["APP_ENV"] = "development"
        with patch.dict(os.environ, env, clear=True):
            settings = Config.get_cors_settings()

        self.assertEqual(settings["allow_origins"], [])
        self.assertIsNotNone(settings["allow_origin_regex"])
        self.assertIn("localhost", settings["allow_origin_regex"])
        self.assertIn("192\\.168", settings["allow_origin_regex"])

    def test_development_cors_allows_flutter_web_zero_host_preflight(self):
        env = {k: v for k, v in os.environ.items() if k != "CORS_ORIGINS"}
        env["APP_ENV"] = "development"
        with patch.dict(os.environ, env, clear=True):
            settings = Config.get_cors_settings()

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

        with TestClient(app) as client:
            response = client.options(
                "/api/auth/login",
                headers={
                    "Origin": "http://0.0.0.0:8080",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,authorization",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://0.0.0.0:8080",
        )
        self.assertIn("POST", response.headers.get("access-control-allow-methods", ""))
        self.assertIn("content-type", response.headers.get("access-control-allow-headers", "").lower())
        self.assertIn("authorization", response.headers.get("access-control-allow-headers", "").lower())

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
