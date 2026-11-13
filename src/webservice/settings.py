"""Configure Django pages without database or session writes."""

from pathlib import Path

DEBUG = False
ALLOWED_HOSTS = [
    "aranami.toolforge.org",
    "localhost",
    "127.0.0.1",
    "[::1]",
    "testserver",
]
INSTALLED_APPS = []
MIDDLEWARE = []
USE_TZ = True
TIME_ZONE = "UTC"
LOGGING_CONFIG = None
DATA_UPLOAD_MAX_MEMORY_SIZE = 4096
DATA_UPLOAD_MAX_NUMBER_FIELDS = 12
TEMPLATES = [
    {
        "NAME": "jinja2",
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [Path(__file__).parent / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {"autoescape": True, "auto_reload": False},
    },
]
# Each factory supplies request.urlconf; the fallback has no routes.
ROOT_URLCONF = ()
