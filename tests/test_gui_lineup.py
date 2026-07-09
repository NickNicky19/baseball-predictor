"""Smoke tests for GUI projected-lineup toggle and per-tab table columns.

Updated for the tabbed layout (each category renders in its own CategoryTab,
each holding its own Treeview) introduced with the "Run all categories"
change. Previously the GUI had a single app.tree; columns now live on every
tab's tree.
"""

from __future__ import annotations

import tkinter as tk

import pytest

from gui import BaseballPredictorGUI


def _make_app():
    root = tk.Tk()
    root.withdraw()
    return root, BaseballPredictorGUI(root)


def test_gui_has_projected_lineup_checkbox():
    try:
        root, app = _make_app()
    except tk.TclError:
        pytest.skip("No display available for Tk")
    try:
        assert hasattr(app, "projected_lineups_var")
        assert app.projected_lineups_var.get() is False
    finally:
        root.destroy()


def test_gui_tabs_expose_expected_columns():
    try:
        root, app = _make_app()
    except tk.TclError:
        pytest.skip("No display available for Tk")
    try:
        assert hasattr(app, "tabs") and app.tabs, "GUI should build category tabs"
        # Every category tab's tree must carry the matchup columns.
        for key, tab in app.tabs.items():
            columns = tab.tree["columns"]
            for expected in ("team", "opponent", "opposing_pitcher", "lineup_status"):
                assert expected in columns, f"tab {key!r} missing column {expected!r}"
    finally:
        root.destroy()
