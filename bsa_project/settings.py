"""
Django settings for bsa_project.
Reads secrets from a .env file so nothing sensitive lives in code.
"""
from pathlib import Path
import os
from dotenv import load_dotenv
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

# Load variables from .env into the environment
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-key")
DEBUG = os.getenv("DEBUG", "True") == "True"
ALLOWED_HOSTS = ["*"] if DEBUG else []
AUTH_USER_MODEL = "accounts.User"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "statements",
    "accounts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "accounts.authentication.JWTAuthenticationMiddleware", 
]

ROOT_URLCONF = "bsa_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "bsa_project.wsgi.application"

# ---- MySQL database ----
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_NAME", "bsa_db"),
        "USER": os.getenv("DB_USER", "root"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
        },
    }
}

# Session security
SESSION_COOKIE_AGE = 28800          # 8 hours
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = False       # set True in production with HTTPS
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# Uploaded files go here
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Path to tesseract binary (used by the OCR parser)
# TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")

TESSERACT_CMD = ""  # leave blank if tesseract is on PATH; else set full path e.g. "/usr/bin/tesseract"
OCR_PDF_DPI = 200    # lower = faster but less accurate; raise to 300 if rows are garbled

# =================================
# ── Password hashing: bcrypt, cost factor 12 ──────────────────────────────────
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
BCRYPT_ROUNDS = 12   # used by jwt_utils.py when calling bcrypt directly for JWT-side checks if needed

# ── JWT settings ────────────────────────────────────────────────────────────
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", SECRET_KEY)  # use a separate secret in production .env
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRY_HOURS = 8     # configurable per spec — default 8 hours
JWT_REFRESH_TOKEN_EXPIRY_DAYS = 7

JWT_ACCESS_COOKIE_NAME  = "bsa_access_token"
JWT_REFRESH_COOKIE_NAME = "bsa_refresh_token"

# Cookies must be HttpOnly + Secure (set True once on HTTPS in production) + SameSite=Strict
JWT_COOKIE_SECURE   = False   # set True in production behind HTTPS
JWT_COOKIE_SAMESITE = "Strict"

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/login/"

# Force bcrypt rounds = 12 (Django's hasher defaults to 12 already, this makes it explicit/auditable)
from django.contrib.auth.hashers import BCryptSHA256PasswordHasher
BCryptSHA256PasswordHasher.rounds = 12



# =================================
import os
STATIC_URL = 'static/'
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]

# ── Celery (background task queue) ────────────────────────────────────────
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# ── Channels (WebSocket support) ──────────────────────────────────────────
ASGI_APPLICATION = "bsa_project.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("localhost", 6379)],
        },
    },
}

INSTALLED_APPS += ["channels"]

INSTALLED_APPS += ["django_celery_beat"]
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"