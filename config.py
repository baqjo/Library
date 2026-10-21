import os
import secrets


def _secret_key():
    """SECRET_KEY from the environment, otherwise a random key generated once and kept in instance/.secret_key
    (never a fixed default: anyone knowing it could forge sessions)."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
    path = os.path.join(folder, ".secret_key")
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError:
        key = ""
    if len(key) < 32:
        key = secrets.token_hex(32)
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
    return key


class Config:
    SECRET_KEY = _secret_key()
    uri = os.environ.get("DATABASE_URL", "sqlite:///library.db")
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = uri
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # Destiny catalog reports are ~12 MB of XML
    DEFAULT_LANG = "ar"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"  # blocks cross-site POSTs (CSRF) for the session cookie
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
