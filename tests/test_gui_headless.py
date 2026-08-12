"""The GUI module imports cleanly and degrades on a headless host."""

import os


def test_import_has_no_side_effects():
    # Importing must not require tkinter or a display.
    from feedhub import gui
    assert hasattr(gui, "main")
    assert hasattr(gui, "build_app")
    assert "light" in gui.PALETTES and "dark" in gui.PALETTES


def test_main_headless_returns_zero(monkeypatch):
    from feedhub import gui
    # Ensure no display is available so main() takes the headless path.
    monkeypatch.delenv("DISPLAY", raising=False)
    rc = gui.main()
    assert rc == 0


def test_asset_path_none_for_missing():
    from feedhub import gui
    assert gui.asset_path("definitely-not-a-real-asset.xyz") is None
