"""Initialize read-only Django settings for HTTP and templates."""

import os

import django


def setup_django() -> None:
    """Load settings without opening stores or starting jobs."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webservice.settings")
    django.setup(set_prefix=False)
