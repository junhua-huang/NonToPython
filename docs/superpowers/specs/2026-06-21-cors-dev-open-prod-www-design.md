# CORS Dev Open / Production WWW-only Design

## Goal

Fix backend CORS so local and LAN development clients can call the API from any HTTP/HTTPS origin, while production remains restricted to the public Nonto frontend.

## Requirements

- Development (`APP_ENV` not equal to `production`) allows every browser origin using `http://` or `https://`, including localhost, LAN IPs, public IPs, and arbitrary ports.
- Production (`APP_ENV=production`) allows only `https://www.nonto.online`.
- Keep `allow_credentials=True`, so development must use `allow_origin_regex` rather than `allow_origins=["*"]`.
- Preserve `CORS_ORIGINS` as an explicit environment override for controlled deployments.
- Do not run or apply any database migration.

## Design

Update `Config.DEFAULT_PRODUCTION_CORS_ORIGINS` to contain only `https://www.nonto.online`.

Replace the development LAN-only regex with a broad HTTP/HTTPS origin regex:

```python
r"^https?://.+$"
```

`Config.get_cors_settings()` keeps the existing precedence:

1. If `CORS_ORIGINS` is set, use the comma-separated explicit list.
2. If `APP_ENV=production`, use the production allow-list.
3. Otherwise, allow all HTTP/HTTPS origins through regex.

`app/main.py` keeps the current `CORSMiddleware` wiring and continues to pass both `allow_origins` and `allow_origin_regex` from config.

## Tests

Update `tests/test_cors_config.py` to assert:

- Production defaults to only `https://www.nonto.online`.
- Development uses no static origin list and has a broad origin regex.
- Development preflight succeeds for arbitrary origins such as `http://203.0.113.10:8080`.
- Production preflight allows `https://www.nonto.online`.
- Production preflight does not allow `https://nonto.online`.
- `CORS_ORIGINS` still overrides defaults.

## Non-goals

- No database migrations.
- No auth changes.
- No router changes.
- No frontend changes.
