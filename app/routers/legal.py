"""Public, offline-bundle-matching legal pages; no authentication or trackers."""
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, RedirectResponse

router = APIRouter(tags=["Legal"])
LEGAL_ROOT = Path(__file__).resolve().parents[1] / "static" / "legal"
ALLOWED_FILES = {"index.html", "privacy.html", "terms.html", "community.html", "manifest.json"}


def legal_file(name):
    if name not in ALLOWED_FILES:
        raise HTTPException(status_code=404, detail="Document not found")
    path = LEGAL_ROOT / name
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Document unavailable")
    content = path.read_bytes()
    return Response(content, media_type="application/json" if name.endswith(".json") else "text/html",
                    headers={"Cache-Control":"no-cache", "ETag":'"'+hashlib.sha256(content).hexdigest()+'"',
                             "X-Content-Type-Options":"nosniff", "Referrer-Policy":"no-referrer",
                             "Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})


@router.get("/api/legal/", include_in_schema=False)
def legal_index():
    return legal_file("index.html")


@router.get("/api/legal/{name}", include_in_schema=False)
def legal_document(name: str):
    aliases = {"privacy":"privacy.html", "terms":"terms.html", "community":"community.html"}
    return legal_file(aliases.get(name, name))


@router.get("/PrivacyPolicy/", include_in_schema=False)
def legacy_privacy():
    # Nginx intercepts *.html as static files; canonical public URLs are extensionless.
    return RedirectResponse("/api/legal/privacy", status_code=307)


@router.get("/TermsOfService/", include_in_schema=False)
def legacy_terms():
    return RedirectResponse("/api/legal/terms", status_code=307)


@router.get("/CommunityGuidelines/", include_in_schema=False)
def legacy_community():
    return RedirectResponse("/api/legal/community", status_code=307)
