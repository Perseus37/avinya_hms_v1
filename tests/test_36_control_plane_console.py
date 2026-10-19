from backend.control_plane.api.routers.ui_console import render_command_deck


def test_legacy_control_plane_console_uses_local_stylesheet():
    html = render_command_deck()

    assert '<link rel="stylesheet" href="/assets/legacy-console.css" />' in html
    assert "cdn.tailwindcss.com" not in html
