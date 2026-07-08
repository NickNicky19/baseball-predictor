"""
Minimal GUI for the new prediction architecture.

Uses src.prediction.DailyPredictor. Runs every prop category in a single
DailyPredictor.predict() call and displays each category on its own tab.

Design note (collection-window safe):
    This module is display/orchestration only. It calls the SAME
    DailyPredictor.predict() and PropEngine math as before — it just requests
    all categories in one call (hitter_categories=(...) + include_pitchers)
    instead of one category per run. No projection logic is touched, and the
    single returned DailyPrediction is archived once so the outcome recorder
    captures every category for the date (fixing the one-category-per-file
    problem where only the last GUI run survived).
"""

from __future__ import annotations

import tkinter as tk
from datetime import date
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from src.models.dataclasses import PropCategory
from src.prediction import DailyPredictor

# Tabs shown in the GUI, in display order.
#   key:   internal PropCategory used by the model / archive
#   label: tab text shown to the user
#   kind:  "hitter" or "pitcher" — decides which projection list + ranker
# The keys here are the ONLY categories requested from the model. Fantasy is
# intentionally omitted from the default run (it isn't a prop you're grading);
# add {"key": "fantasy", ...} here if you ever want it back.
TABS: tuple[dict[str, str], ...] = (
    {"key": "hits", "label": "Hits", "kind": "hitter"},
    {"key": "hrr", "label": "HRR", "kind": "hitter"},
    {"key": "home_runs", "label": "Home Runs", "kind": "hitter"},
    {"key": "strikeouts", "label": "Pitcher K", "kind": "pitcher"},
)

HITTER_CATEGORIES: tuple[PropCategory, ...] = tuple(
    t["key"] for t in TABS if t["kind"] == "hitter"  # type: ignore[misc]
)
INCLUDE_PITCHERS: bool = any(t["kind"] == "pitcher" for t in TABS)

TOP_N_HITTERS = 30
TOP_N_PITCHERS = 30

COLUMNS = (
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
COL_WIDTHS = {
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
NUMERIC_COLS = {"rank", "projected", "confidence"}


class CategoryTab:
    """One notebook tab: a Treeview plus its own sort state and row cache.

    Purely presentational. Holds the ranked rows for a single category and
    handles click-to-sort and repaint. Never calls the model.
    """

    def __init__(self, notebook: ttk.Notebook, spec: dict[str, str]):
        self.key = spec["key"]
        self.label = spec["label"]
        self.kind = spec["kind"]
        self.rows: list[dict] = []
        self._sort_col: str | None = None
        self._sort_desc: bool = False

        self.frame = ttk.Frame(notebook)
        notebook.add(self.frame, text=self.label)

        self.tree = ttk.Treeview(self.frame, columns=COLUMNS, show="headings")
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)

        for col in COLUMNS:
            heading = col.replace("_", "  ").upper()
            self.tree.heading(col, text=heading, command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=COL_WIDTHS[col], anchor="center")

    def set_rows(self, rows: list[dict]) -> None:
        """Replace the tab's rows and repaint (keeps rank order as given)."""
        self.rows = rows
        self._sort_col = None
        self._sort_desc = False
        self._repaint(renumber=False)
        # Reset header text (strip any sort arrows from a previous run).
        for col in COLUMNS:
            self.tree.heading(col, text=col.replace("_", "  ").upper())

    def clear(self) -> None:
        self.rows = []
        self.tree.delete(*self.tree.get_children())

    def _sort_by(self, col: str) -> None:
        """Re-sort displayed rows by a column. Read-only; never re-runs the model.

        Clicking the same column toggles ascending/descending. Confidence is an
        UNVALIDATED model self-estimate until calibration data confirms it.
        """
        if not self.rows:
            return
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = col in NUMERIC_COLS  # numbers default big-first

        def key(row: dict):
            v = row.get(col, "")
            if col in NUMERIC_COLS:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return float("-inf")
            return str(v).lower()

        self.rows.sort(key=key, reverse=self._sort_desc)
        self._repaint(renumber=(col != "rank"))

        arrow = " v" if self._sort_desc else " ^"
        for c in COLUMNS:
            base = c.replace("_", "  ").upper()
            self.tree.heading(c, text=base + (arrow if c == col else ""))

    def _repaint(self, renumber: bool) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self.rows, 1):
            if renumber and "rank" in row:
                row["rank"] = i
            self.tree.insert("", "end", values=tuple(row[c] for c in COLUMNS))


class BaseballPredictorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MLB Daily Predictor")
        self.root.geometry("1280x720")
        self.predictor = DailyPredictor()
        self.tabs: dict[str, CategoryTab] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        header = ttk.Label(self.root, text="MLB Daily Predictor", font=("Segoe UI", 16, "bold"))
        header.pack(pady=10)

        controls = ttk.Frame(self.root)
        controls.pack(fill="x", padx=12, pady=6)

        ttk.Label(controls, text="Date:").pack(side="left")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(controls, textvariable=self.date_var, width=12).pack(side="left", padx=6)

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

        ttk.Button(controls, text="Run all categories", command=self.run_predictions).pack(
            side="left", padx=6
        )
        ttk.Button(controls, text="Export active tab", command=self.export_active_tab).pack(
            side="left", padx=4
        )
        ttk.Button(controls, text="Export all", command=self.export_all).pack(side="left", padx=4)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=8)
        for spec in TABS:
            tab = CategoryTab(self.notebook, spec)
            self.tabs[tab.key] = tab

        self.status_var = tk.StringVar(value="Ready — press Run all categories to generate a slate")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken").pack(
            fill="x", side="bottom"
        )

    def run_predictions(self) -> None:
        target_date = self.date_var.get()
        use_projected = self.projected_lineups_var.get()
        self.status_var.set(f"Running all categories for {target_date}...")
        self.root.update_idletasks()
        for tab in self.tabs.values():
            tab.clear()

        try:
            # ONE call generates every category. Same model, same math as
            # running each category separately — just requested together so the
            # archive holds all of them for this date.
            result = self.predictor.predict(
                target_date,
                hitter_categories=HITTER_CATEGORIES,
                include_pitchers=INCLUDE_PITCHERS,
                apply_corrections=self.corrections_var.get(),
                use_projected_lineups=use_projected,
            )

            counts: list[str] = []
            for tab in self.tabs.values():
                if tab.kind == "pitcher":
                    ranked = self.predictor.rank_pitcher_projections(
                        result.pitcher_projections, TOP_N_PITCHERS
                    )
                else:
                    ranked = self.predictor.rank_hitter_projections(
                        result.hitter_projections, tab.key, TOP_N_HITTERS
                    )
                tab.set_rows([self._row(i, p) for i, p in enumerate(ranked, 1)])
                counts.append(f"{tab.label}: {len(ranked)}")

            mode = "confirmed + projected" if use_projected else "confirmed only"
            self.status_var.set(
                f"Complete for {target_date} ({mode}) — " + ", ".join(counts)
            )
        except Exception as exc:
            messagebox.showerror("Prediction failed", str(exc))
            self.status_var.set(f"Error: {exc}")

    @staticmethod
    def _row(rank: int, p) -> dict:
        return {
            "rank": rank,
            "player": p.player_name,
            "team": p.team,
            "opponent": p.opponent,
            "opposing_pitcher": p.opposing_pitcher,
            "lineup_status": p.lineup_status,
            "category": p.category,
            "projected": p.projected_value,
            "confidence": p.confidence,
        }

    def _active_tab(self) -> CategoryTab | None:
        current = self.notebook.select()
        for tab in self.tabs.values():
            if str(tab.frame) == current:
                return tab
        return None

    def export_active_tab(self) -> None:
        tab = self._active_tab()
        if tab is None or not tab.rows:
            messagebox.showwarning("No data", "Run predictions first, then pick a tab to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"predictions_{self.date_var.get()}_{tab.key}.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            pd.DataFrame(tab.rows).to_csv(path, index=False)
            messagebox.showinfo("Exported", path)

    def export_all(self) -> None:
        all_rows = [row for tab in self.tabs.values() for row in tab.rows]
        if not all_rows:
            messagebox.showwarning("No data", "Run predictions first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"predictions_{self.date_var.get()}_all.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            pd.DataFrame(all_rows).to_csv(path, index=False)
            messagebox.showinfo("Exported", path)
