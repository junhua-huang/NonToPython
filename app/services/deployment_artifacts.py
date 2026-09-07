"""Shared stdlib-only validation for trusted-operator release bundles.

This enforces packaging boundaries and integrity, not source-code trust or malware
screening. Uploaded Python is privileged code once the operator approves it.
"""
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile

DEFAULT_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_FILES = 20000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_HEX = re.compile(r"[a-fA-F0-9]{64}\Z")
_RESERVED = {"uploads", "logs", "log", "venv", "__pycache__", "node_modules", "runtime", "secrets", "credentials", "backups", "deploy", "tests"}


def _path_allowed(path, components):
    if not isinstance(path, str) or len(path) > 512 or "\\" in path:
        return False
    parts = path.split("/")
    if len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@+-]*", p) or p.lower() in _RESERVED for p in parts):
        return False
    root, rel = parts[0], "/".join(parts[1:])
    if root == "backend":
        return "backend" in components and (
            rel in {"requirements.txt", "alembic.ini", "alembic/script.py.mako"}
            or (parts[1] in {"app", "alembic"} and len(parts) > 2 and rel.endswith(".py"))
        )
    if root == "web":
        return "web" in components and Path(rel).suffix.lower() in {
            ".html", ".js", ".mjs", ".json", ".wasm", ".css", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico", ".ttf", ".otf", ".woff", ".woff2", ".bin", ".frag", ".vert", ".txt", ".webmanifest", ".symbols"
        }
    if root == "downloads" and len(parts) == 2:
        return ("android" in components and rel.endswith(".apk")) or ("windows" in components and rel.endswith(".zip"))
    return False


def validate_manifest(manifest, *, max_expanded_bytes=DEFAULT_MAX_EXPANDED_BYTES, max_files=DEFAULT_MAX_FILES):
    if not isinstance(manifest, dict) or set(manifest) != {"release_id", "version", "build_number", "components", "files", "bundle_sha256"}:
        raise ValueError("invalid_manifest_fields")
    if not isinstance(manifest["release_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", manifest["release_id"]):
        raise ValueError("invalid_release_id")
    if not isinstance(manifest["version"], str) or not re.fullmatch(r"[0-9]{1,10}\.[0-9]{1,10}\.[0-9]{1,10}", manifest["version"]):
        raise ValueError("invalid_version")
    if type(manifest["build_number"]) is not int or not 1 <= manifest["build_number"] <= 2147483647:
        raise ValueError("invalid_build_number")
    components = manifest["components"]
    if not isinstance(components, list) or not components or any(type(c) is not str for c in components) or len(set(components)) != len(components) or not set(components) <= {"backend", "web", "android", "windows"}:
        raise ValueError("invalid_components")
    if not isinstance(manifest["bundle_sha256"], str) or not _HEX.fullmatch(manifest["bundle_sha256"]):
        raise ValueError("invalid_bundle_hash")
    files = manifest["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= max_files:
        raise ValueError("invalid_file_count")
    seen, total = set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise ValueError("invalid_file_fields")
        path = item["path"]
        if not _path_allowed(path, components) or path.casefold() in seen:
            raise ValueError("unsafe_file_path")
        if type(item["size"]) is not int or item["size"] < 0 or not isinstance(item["sha256"], str) or not _HEX.fullmatch(item["sha256"]):
            raise ValueError("invalid_file_metadata")
        total += item["size"]
        if total > max_expanded_bytes:
            raise ValueError("expanded_quota_exceeded")
        seen.add(path.casefold())
    for component in components:
        required = {"web/index.html", "web/version.json"} if component == "web" else set()
        prefix = "backend/" if component == "backend" else "downloads/"
        suffix = ".apk" if component == "android" else ".zip" if component == "windows" else ""
        if component == "web":
            valid = required <= seen
        else:
            valid = any(p.startswith(prefix) and p.endswith(suffix) for p in seen)
        if not valid:
            raise ValueError("incomplete_component")
    return manifest


def parse_manifest(data, **limits):
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest_quota_exceeded")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_manifest_key")
            result[key] = value
        return result
    try:
        manifest = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid_manifest_json") from exc
    return validate_manifest(manifest, **limits)


def validate_bundle(bundle_path, manifest, *, max_bytes=DEFAULT_MAX_BYTES, max_expanded_bytes=DEFAULT_MAX_EXPANDED_BYTES, max_files=DEFAULT_MAX_FILES):
    validate_manifest(manifest, max_expanded_bytes=max_expanded_bytes, max_files=max_files)
    path = Path(bundle_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("bundle_quota_exceeded")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != manifest["bundle_sha256"].lower():
        raise ValueError("bundle_hash_mismatch")
    expected = {item["path"]: item for item in manifest["files"]}
    # Bound raw decompression too: tar metadata/padding must not hide a gzip bomb.
    budget = max_expanded_bytes + max_files * 4096 + 10240
    class BoundedReader:
        def __init__(self, source):
            self.source, self.count = source, 0
        def read(self, size=-1):
            chunk = self.source.read(min(size if size >= 0 else 65536, budget - self.count + 1))
            self.count += len(chunk)
            if self.count > budget:
                raise ValueError("expanded_quota_exceeded")
            return chunk
    seen = set()
    try:
        with gzip.open(path, "rb") as compressed:
            bounded = BoundedReader(compressed)
            with tarfile.open(fileobj=bounded, mode="r|") as archive:
                for member in archive:
                    if len(seen) >= max_files or not member.isfile() or member.issparse() or member.name in seen:
                        raise ValueError("unsafe_tar_member")
                    item = expected.get(member.name)
                    if item is None or member.size != item["size"]:
                        raise ValueError("tar_manifest_mismatch")
                    digest = hashlib.sha256()
                    with archive.extractfile(member) as source:
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                    if digest.hexdigest() != item["sha256"].lower():
                        raise ValueError("file_hash_mismatch")
                    seen.add(member.name)
            while bounded.read(65536):
                pass
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ValueError("invalid_bundle") from exc
    if seen != set(expected):
        raise ValueError("tar_manifest_mismatch")
    return manifest
