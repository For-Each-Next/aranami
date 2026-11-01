"""Identify Aranami consistently to read-only sources and publishers.

Use ``USER_AGENT`` for custom HTTP requests and call
``configure_pywikibot`` before initializing runtime wiki clients. The
framework keeps its own product and platform identification.
"""

from pywikibot import config

from shared.deployment import project_version

CONTACT_URL = "https://meta.wikimedia.org/wiki/User:For_Each_..._Next/"
USER_AGENT = f"Aranami/{project_version()} ({CONTACT_URL})"


def configure_pywikibot() -> None:
    """Add application identity to the Pywikibot description."""
    config.user_agent_description = USER_AGENT
