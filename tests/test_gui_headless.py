"""The GUI module imports cleanly and degrades on a headless host."""

import pytest
import sys
import os


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows CI has a real display; main() would open a window and block")
def test_import_has_no_side_effects():
    # Importing must not require tkinter or a display.
    from feedhub import gui
    assert hasattr(gui, "main")
    assert hasattr(gui, "build_app")
    # The vendored Aura kit now owns all theming; the house PALETTES dict is
    # gone and the app only declares its per-app accent colour.
    assert not hasattr(gui, "PALETTES")
    assert isinstance(gui.ACCENT, str) and gui.ACCENT.startswith("#")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows CI has a real display; main() would open a window and block")
def test_main_headless_returns_zero(monkeypatch):
    from feedhub import gui
    # Ensure no display is available so main() takes the headless path.
    monkeypatch.delenv("DISPLAY", raising=False)
    rc = gui.main()
    assert rc == 0


def test_asset_path_none_for_missing():
    from feedhub import gui
    assert gui.asset_path("definitely-not-a-real-asset.xyz") is None
