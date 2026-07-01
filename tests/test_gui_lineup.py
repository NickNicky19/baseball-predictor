"""Smoke tests for GUI projected-lineup toggle and table columns."""

from __future__ import annotations

import tkinter as tk

from gui import BaseballPredictorGUI


def test_gui_has_projected_lineup_checkbox_and_columns():
    root = tk.Tk()
    root.withdraw()
    try:
        app = BaseballPredictorGUI(root)
        assert hasattr(app, "projected_lineups_var")
        assert app.projected_lineups_var.get() is False

        columns = app.tree["columns"]
        assert "team" in columns
        assert "opponent" in columns
        assert "opposing_pitcher" in columns
        assert "lineup_status" in columns
    finally:
        root.destroy()