"""Provide redirect evidence for offline interface checks."""


def redirect_snapshot(count: int = 23) -> dict:
    """Build incremental scan results with stable public revision links.

    Args:
        count: Number of matching redirects in the saved worklist.

    Returns:
        Independent plain observations, including escaped-text probes.
    """
    stamp = "2026-11-04T01:00:00+00:00"
    return {
        "schema": 1,
        "wiki": "zhwiki",
        "status": "scanning",
        "checked_count": 200,
        "discovered_count": 20_000,
        "updated_at": stamp,
        "error": None,
        "unavailable_count": 1,
        "aliases": {
            "banner": ["Template:WikiProject Video games", "Template:VG"],
            "deletion": ["Template:Old vfd multi"],
        },
        "items": [
            {
                "page_id": number,
                "title": f"Game {number:02} & 中文 <example>",
                "url": f"https://zh.wikipedia.org/wiki/Game_{number}",
                "talk_page_id": number + 100,
                "talk_title": f"Talk:Game {number:02}",
                "talk_url": f"https://zh.wikipedia.org/wiki/Talk:Game_{number}",
                "current_revision": number + 1000,
                "current_url": (
                    "https://zh.wikipedia.org/w/index.php?"
                    f"oldid={number + 1000}"
                ),
                "content_revision": number + 2000,
                "content_url": (
                    "https://zh.wikipedia.org/w/index.php?"
                    f"oldid={number + 2000}"
                ),
                "content_timestamp": stamp,
                "banner_revision": number + 3000,
                "banner_url": (
                    "https://zh.wikipedia.org/w/index.php?"
                    f"oldid={number + 3000}"
                ),
                "banner_timestamp": stamp,
                "preview": (
                    "原條目簡介。 <script>never execute</script> & 中文。"
                ),
                "observed_at": stamp,
            }
            for number in range(1, count + 1)
        ],
    }
