"""
Minimal GUI for the new prediction architecture.

Uses src.prediction.DailyPredictor. Supports daily runs, corrections toggle,
projected lineup toggle, context columns, and CSV export.
"""

from __future__ import annotations

import tkinter as tk
from datetime import date
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from src.models.dataclasses import PropCategory
from src.prediction import DailyPredictor


class BaseballPredictorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MLB Daily Predictor")
        self.root.geometry("1280x720")
        self.predictor = DailyPredictor()
        self._current_rows: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        header = ttk.Label(self.root, text="MLB Daily Predictor", font=("Segoe UI", 16, "bold"))
        header.pack(pady=10)

        controls = ttk.Frame(self.root)
        controls.pack(fill="x", padx=12, pady=6)

        ttk.Label(controls, text="Date:").pack(side="left")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(controls, textvariable=self.date_var, width=12).pack(side="left", padx=6)

        ttk.Label(controls, text="Category:").pack(side="left", padx=(10, 4))
        self.category_var = tk.StringVar(value="hrr")
        ttk.Combobox(
            controls,
            textvariable=self.category_var,
            values=["hits", "hrr", "hr", "fantasy", "strikeouts"],
            state="readonly",
            width=12,
        ).pack(side="left")

        self.corrections_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Apply corrections", variable=self.corrections_var).pack(
            side="left", padx=12
        )

        self.projected_lineups_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls,
            text="Include Projected/Predicted Lineups",
            variable=self.projected_lineups_var,
        ).pack(side="left", padx=8)

        ttk.Button(controls, text="Run", command=self.run_predictions).pack(side="left", padx=6)
        ttk.Button(controls, text="Export CSV", command=self.export_csv).pack(side="left", padx=4)

        columns = (
            "rank",
            "player",
            "team",
            "opponent",
            "opposing_pitcher",
            "lineup_status",
            "category",
            "projected",
            "confidence",
        )
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings")
        self.tree.pack(fill="both", expand=True, padx=12, pady=8)

        col_widths = {
            "rank": 45,
            "player": 180,
            "team": 90,
            "opponent": 90,
            "opposing_pitcher": 150,
            "lineup_status": 90,
            "category": 80,
            "projected": 80,
            "confidence": 80,
        }
        for col in columns:
            label = col.replace("_", " ").upper()
            self.tree.heading(col, text=label)
            self.tree.column(col, width=col_widths[col], anchor="center")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken").pack(fill="x", side="bottom")

    def run_predictions(self) -> None:
        target_date = self.date_var.get()
        category = self._map_category(self.category_var.get())
        use_projected = self.projected_lineups_var.get()
        self.status_var.set(f"Running predictions for {target_date}...")
        self.tree.delete(*self.tree.get_children())
        self._current_rows.clear()

        try:
            if category == "strikeouts":
                result = self.predictor.predict(
                    target_date,
                    hitter_categories=(),
                    include_pitchers=True,
                    apply_corrections=self.corrections_var.get(),
                    use_projected_lineups=use_projected,
                )
                ranked = self.predictor.rank_pitcher_projections(result.pitcher_projections, 30)
            else:
                result = self.predictor.predict(
                    target_date,
                    hitter_categories=(category,),
                    include_pitchers=False,
                    apply_corrections=self.corrections_var.get(),
                    use_projected_lineups=use_projected,
                )
                ranked = self.predictor.rank_hitter_projections(result.hitter_projections, category, 30)

            for i, p in enumerate(ranked, 1):
                row = {
                    "rank": i,
                    "player": p.player_name,
                    "team": p.team,
                    "opponent": p.opponent,
                    "opposing_pitcher": p.opposing_pitcher,
                    "lineup_status": p.lineup_status,
                    "category": p.category,
                    "projected": p.projected_value,
                    "confidence": p.confidence,
                }
                self._current_rows.append(row)
                self.tree.insert("", "end", values=tuple(row.values()))

            mode = "confirmed + projected" if use_projected else "confirmed only"
            self.status_var.set(f"Complete — {len(ranked)} players ({mode}) for {target_date}")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            self.status_var.set("Error")

    def export_csv(self) -> None:
        if not self._current_rows:
            messagebox.showwarning("No data", "Run predictions first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"predictions_{self.date_var.get()}.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            pd.DataFrame(self._current_rows).to_csv(path, index=False)
            messagebox.showinfo("Exported", path)

    @staticmethod
    def _map_category(category: str) -> PropCategory:
        if category == "hr":
            return "home_runs"
        return category  # type: ignore[return-value]