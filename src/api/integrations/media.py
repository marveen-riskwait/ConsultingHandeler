"""Media storage for chat attachments: Cloudinary when configured, local disk
otherwise.

Set CLOUDINARY_URL (cloudinary://key:secret@cloud) to store voice notes,
videos, images and files on Cloudinary. Without it, files are written to the
repo-level ./uploads/ directory under a random name and served back by
GET /api/media/<name> — enough for demos and single-node deployments.
"""
import os
import uuid

UPLOADS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "uploads"))

# Message kind from the MIME type prefix.
_KIND_BY_PREFIX = (("image/", "IMAGE"), ("audio/", "AUDIO"), ("video/", "VIDEO"))


def kind_for(mimetype):
    for prefix, kind in _KIND_BY_PREFIX:
        if (mimetype or "").startswith(prefix):
            return kind
    return "FILE"


def cloudinary_enabled():
    return bool(os.getenv("CLOUDINARY_URL"))


def _store_local(file_storage, mimetype, kind, note=None):
    """Random unguessable name, original extension kept."""
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    ext = os.path.splitext(file_storage.filename or "")[1][:10] or ""
    name = uuid.uuid4().hex + ext
    file_storage.save(os.path.join(UPLOADS_DIR, name))
    out = {"url": f"/api/media/{name}", "media_type": mimetype,
           "kind": kind, "provider": "local"}
    if note:
        out["note"] = note
    return out


def store(file_storage):
    """Persist an uploaded file. Returns {url, media_type, kind, provider}.

    Cloudinary failures (bad/placeholder credentials, network) fall back to
    local storage instead of failing the message — a misconfigured .env must
    never break chat media.
    """
    mimetype = file_storage.mimetype or "application/octet-stream"
    kind = kind_for(mimetype)

    if cloudinary_enabled():
        try:
            import cloudinary
            import cloudinary.uploader
            cloudinary.config(secure=True)  # reads CLOUDINARY_URL from env
            uploaded = cloudinary.uploader.upload(
                file_storage, resource_type="auto", folder="compliance-os/chat")
            return {"url": uploaded["secure_url"], "media_type": mimetype,
                    "kind": kind, "provider": "cloudinary"}
        except Exception as exc:
            # The stream may be partially consumed by the failed attempt.
            try:
                file_storage.stream.seek(0)
            except Exception:
                pass
            return _store_local(
                file_storage, mimetype, kind,
                note=f"Cloudinary failed ({type(exc).__name__}: {exc}) — "
                     "stored locally. Check CLOUDINARY_URL in .env.")

    return _store_local(file_storage, mimetype, kind)


# --- signed access -----------------------------------------------------------
# The media route is not public: a file is reachable only through a short-lived
# signed URL. The signature travels in the query string, so it works with
# <img src> / <iframe src> (which cannot carry an Authorization header) — and
# it is precisely how object stores like Cloudflare R2 presign URLs, so moving
# there later changes the backend, not this contract.
import hashlib
import hmac
import time

SIGNED_URL_TTL = int(os.getenv("MEDIA_URL_TTL", "600"))  # seconds


def _secret():
    return (os.getenv("JWT_SECRET_KEY") or os.getenv("FLASK_APP_KEY")
            or "change-me-in-production").encode()


def _key_of(url):
    """The storage key behind a /api/media/<key> path (ignores any query)."""
    path = (url or "").split("?", 1)[0]
    return path.rsplit("/", 1)[-1]


def sign_url(url, ttl=None):
    """Turn a stored /api/media/<key> path into a time-limited signed URL.

    Called by serializers when handing a file to an already-authorized user, so
    the link they receive is a short-lived bearer token to that one file.
    """
    if not url or "/api/media/" not in url:
        return url
    key = _key_of(url)
    exp = int(time.time()) + (ttl or SIGNED_URL_TTL)
    sig = hmac.new(_secret(), f"{key}:{exp}".encode(),
                   hashlib.sha256).hexdigest()[:32]
    return f"/api/media/{key}?exp={exp}&sig={sig}"


def verify_signed(key, exp, sig):
    """True if <key> may be served: signature valid and not expired."""
    try:
        exp = int(exp or 0)
    except (TypeError, ValueError):
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(_secret(), f"{key}:{exp}".encode(),
                        hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expected, sig or "")


def open_local(key):
    """Path to a locally-stored key, or None. The R2/S3 backend will stream
    from the bucket instead — same caller, different source."""
    path = os.path.join(UPLOADS_DIR, key)
    return path if os.path.isfile(path) else None
