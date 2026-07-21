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


def store(file_storage):
    """Persist an uploaded file. Returns {url, media_type, kind, provider}."""
    mimetype = file_storage.mimetype or "application/octet-stream"
    kind = kind_for(mimetype)

    if cloudinary_enabled():
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(secure=True)  # reads CLOUDINARY_URL from env
        uploaded = cloudinary.uploader.upload(
            file_storage, resource_type="auto", folder="compliance-os/chat")
        return {"url": uploaded["secure_url"], "media_type": mimetype,
                "kind": kind, "provider": "cloudinary"}

    # Local fallback: random unguessable name, original extension kept.
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    ext = os.path.splitext(file_storage.filename or "")[1][:10] or ""
    name = uuid.uuid4().hex + ext
    file_storage.save(os.path.join(UPLOADS_DIR, name))
    return {"url": f"/api/media/{name}", "media_type": mimetype,
            "kind": kind, "provider": "local"}
