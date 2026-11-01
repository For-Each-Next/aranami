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
        """Keep newsletter in Tools and omit diagnostic logs."""
        for locale, tools, newsletter, logs in (
            ("en", "Tools", "Newsletter drafts", "Run logs"),
            ("zh-hans", "工具", "简讯草稿", "运行日志"),
            ("zh-hant", "工具", "簡訊草稿", "執行日誌"),
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
                assert 'href="/logs/"' not in navigation
                about = render_about(None, locale)
                assert 'href="/logs/"' not in about
                assert logs not in about
                direct = navigation[: details.start()]
                assert re.findall(r'<a href="([^"]+)"', direct) == [
                    "/",
                    "/maintenance/",
                    "/reports/",
                ]
                assert navigation.count('href="/newsletter/"') == 1
                assert 'role="menu' not in navigation
                assert "aria-expanded" not in navigation

    def test_current_page_remains_on_its_destination_link(self) -> None:
        """Keep the current-page marker on its destination link."""
        for locale in ("en", "zh-hans", "zh-hant"):
            for path in (
                "/",
                "/maintenance/",
                "/reports/",
                "/about/",
                "/newsletter/",
            ):
                with self.subTest(locale=locale, path=path):
                    navigation = _navigation(locale, path)
                    assert re.findall(
                        r'<a href="([^"]+)" aria-current="page">',
                        navigation,
                    ) == ([] if path == "/about/" else [path])

    @staticmethod
    def test_footer_names_the_project_and_omits_logs() -> None:
        """Keep project and About footer links and omit logs."""
        for locale, project in (
            ("en", "Video games WikiProject"),
            ("zh-hans", "电子游戏专题"),
            ("zh-hant", "電子遊戲專題"),
        ):
            output = render_about(None, locale)
            footer = output.split("<footer>", 1)[1]
            assert 'href="/about/"' in footer
            assert 'href="/logs/"' not in footer
            assert f">{project}</a>" in footer
            assert 'href="/logs/"' not in output.split("<footer>", 1)[0]


if __name__ == "__main__":
    unittest.main()
