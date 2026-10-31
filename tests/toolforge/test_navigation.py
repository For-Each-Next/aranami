"""Verify localized native navigation and current-page links."""

from __future__ import annotations

import re
import unittest

from webservice.pages import render_about


def _navigation(locale: str, path: str) -> str:
    """Render the shared header for one route and interface language.

    Returns:
        The global navigation markup without the unrelated footer.
    """
    rendered = render_about(
        None,
        locale,
        navigation={"path": path, "languages": []},
    )
    match = re.search(
        r'<nav class="global-navigation"[^>]*>(.*?)</nav>',
        rendered,
        flags=re.DOTALL,
    )
    assert match is not None
    return match[1]


class NavigationTests(unittest.TestCase):
    """Keep the tools disclosure usable as ordinary HTML."""

    def test_tools_links_live_in_localized_native_disclosure(
        self,
    ) -> None:
        """Keep newsletter and logs in the native tools menu."""
        for locale, tools, newsletter, logs in (
            ("en", "Tools", "Newsletter", "Run logs"),
            ("zh-hans", "工具", "专题简讯", "运行日志"),
            ("zh-hant", "工具", "專題簡訊", "執行日誌"),
        ):
            with self.subTest(locale=locale):
                navigation = _navigation(locale, "/")
                details = re.search(
                    r'<details class="navigation-tools">(.*?)</details>',
                    navigation,
                    flags=re.DOTALL,
                )
                assert details is not None
                assert f"<summary>{tools}</summary>" in details[1]
                assert f'<a href="/newsletter/">{newsletter}</a>' in details[1]
                assert f'<a href="/logs/">{logs}</a>' in details[1]
                direct = navigation[: details.start()]
                assert re.findall(r'<a href="([^"]+)"', direct) == [
                    "/",
                    "/about/",
                ]
                assert navigation.count('href="/newsletter/"') == 1
                assert navigation.count('href="/logs/"') == 1
                assert 'role="menu' not in navigation
                assert "aria-expanded" not in navigation

    def test_current_page_remains_on_its_destination_link(self) -> None:
        """Keep the current-page marker on its destination link."""
        for locale in ("en", "zh-hans", "zh-hant"):
            for path in ("/", "/logs/", "/about/", "/newsletter/"):
                with self.subTest(locale=locale, path=path):
                    navigation = _navigation(locale, path)
                    assert re.findall(
                        r'<a href="([^"]+)" aria-current="page">',
                        navigation,
                    ) == [path]


if __name__ == "__main__":
    unittest.main()
