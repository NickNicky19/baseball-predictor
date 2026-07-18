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

import glob
import json
import os
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from src.models.dataclasses import PropCategory
from src.evaluation.prediction_health import display_health_flags
from src.prediction import DailyPredictor
from src.utils.model_version import model_version

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

CONFIG_DIR = Path("config")
LIVE_CONFIG = CONFIG_DIR / "config.json"

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
    "input_health",
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
    "input_health": 310,
}
NUMERIC_COLS = {"rank", "projected"}


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

        Clicking the same column toggles ascending/descending.  Input health
        consists of factual source/fallback flags, never a confidence score.
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
        # *** THE PREDICTOR IS NO LONGER BUILT ONCE FROM config.json. ***
        #
        # It was `DailyPredictor()` -- which silently defaults to
        # config/config.json. So the GUI could ONLY ever show the PROMOTED model,
        # and every candidate comparison had to be run from the shell. Worse: the
        # user had no way to see WHICH model produced the numbers on screen.
        #
        # DailyPredictor already accepts config_path. The GUI simply never passed
        # it. This exposes what already existed -- no new plumbing.
        #
        # The predictor is now rebuilt on each run, because the selected config
        # can change between runs and a stale engine would silently serve the old
        # model's numbers.
        self.predictor: DailyPredictor | None = None
        self._active_model_version: str = "(none)"
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

        # --- CONFIG SELECTOR ---------------------------------------------
        # Every config/*.json is offered. config.json (the LIVE, promoted model)
        # is the default. A candidate (config.pa.json, config.kbb.json, ...) can
        # be selected WITHOUT touching the live config -- which is the whole
        # point of the gate discipline: you compare a candidate to the promoted
        # model, you do not overwrite the promoted model to look at a candidate.
        ttk.Label(controls, text="Model:").pack(side="left", padx=(12, 0))
        self.config_var = tk.StringVar(value=LIVE_CONFIG.name)
        self.config_combo = ttk.Combobox(
            controls, textvariable=self.config_var, width=20, state="readonly",
            values=self._discover_configs(),
        )
        self.config_combo.pack(side="left", padx=6)
        self.config_combo.bind("<<ComboboxSelected>>", self._on_config_change)

        self.corrections_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls, text="Apply corrections", variable=self.corrections_var,
            command=self._warn_corrections,
        ).pack(side="left", padx=12)

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

        # The ACTIVE MODEL VERSION, always visible.
        #
        # Numbers on a screen with no model attached to them are how you end up
        # comparing two slates and not knowing which model produced which. The
        # version hash is the ONLY thing that identifies a model -- config.json
        # can change under you (it did: 43a43880e377 -> 674785f83e7a when the
        # fitted PA distribution was promoted) and the projections change with it.
        self.model_var = tk.StringVar(value="model: (not loaded)")
        model_bar = ttk.Frame(self.root)
        model_bar.pack(fill="x", side="bottom")
        ttk.Label(model_bar, textvariable=self.model_var, relief="sunken",
                  anchor="w").pack(fill="x")
        self._refresh_model_label()

        self.status_var = tk.StringVar(value="Ready — press Run all categories to generate a slate")
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken").pack(
            fill="x", side="bottom"
        )

    @staticmethod
    def _discover_configs() -> list[str]:
        """Every config/*.json, with the LIVE config first."""
        if not CONFIG_DIR.is_dir():
            return [LIVE_CONFIG.name]
        names = sorted(p.name for p in CONFIG_DIR.glob("*.json"))
        if LIVE_CONFIG.name in names:
            names.remove(LIVE_CONFIG.name)
            names.insert(0, LIVE_CONFIG.name)
        return names or [LIVE_CONFIG.name]

    def _selected_config_path(self) -> Path:
        return CONFIG_DIR / self.config_var.get()

    def _refresh_model_label(self) -> None:
        """Show the model_version hash of the SELECTED config.

        Read straight from the file, not from a cached predictor -- the file can
        change on disk (a promotion rewrites config.json) and a stale label would
        be worse than no label.
        """
        p = self._selected_config_path()
        try:
            cfg = json.loads(p.read_text(encoding="utf-8-sig"))
            ver = model_version(cfg)
            live = " (LIVE)" if p.name == LIVE_CONFIG.name else " — CANDIDATE, not promoted"
            self.model_var.set(f"model: {ver}   [{p.name}]{live}")
        except Exception as exc:
            self.model_var.set(f"model: (could not read {p.name}: {exc})")

    def _on_config_change(self, _evt=None) -> None:
        # Drop the cached predictor -- it was built from the OLD config and would
        # silently serve the old model's numbers.
        self.predictor = None
        self._refresh_model_label()
        self.status_var.set(
            f"Model switched to {self.config_var.get()} — press Run to generate."
        )

    def _warn_corrections(self) -> None:
        """`apply_corrections` is not a neutral toggle.

        PROJECT_CONTEXT, verbatim: "DO NOT ENABLE BIAS CORRECTIONS YET."
        And bias_corrections.json carries xwoba_scale = 7.81 -- a healthy
        calibration multiplier is near 1.0. A 7.8x multiplier means the
        correction layer is BRUTE-FORCING a mis-specified model.

        It is MORE dangerous now, not less: those corrections were fitted against
        a model with a +10.3% hits error (a +7.4% bip error from the K/BB defect
        COMPOUNDED with a +2.7% xBA error). Both are being fixed. Corrections
        fitted to compensate for a broken model will OVER-CORRECT a fixed one.

        This does not disable the checkbox -- the user may have a reason. It makes
        sure the reason is a CHOICE and not an accident.
        """
        if not self.corrections_var.get():
            return
        ok = messagebox.askokcancel(
            "Bias corrections — are you sure?",
            "PROJECT_CONTEXT says: \"DO NOT ENABLE BIAS CORRECTIONS YET.\"\n\n"
            "bias_corrections.json has xwoba_scale = 7.81. A healthy calibration "
            "multiplier is near 1.0 — a 7.8x multiplier means the correction layer "
            "is brute-forcing a mis-specified model.\n\n"
            "And these corrections were fitted against a model with a +10.3% hits "
            "error. That error is being fixed. Corrections fitted to compensate "
            "for a broken model will OVER-CORRECT a fixed one.\n\n"
            "Enable anyway?",
        )
        if not ok:
            self.corrections_var.set(False)

    def run_predictions(self) -> None:
        target_date = self.date_var.get()
        use_projected = self.projected_lineups_var.get()
        self.status_var.set(f"Running all categories for {target_date}...")
        self.root.update_idletasks()
        for tab in self.tabs.values():
            tab.clear()

        try:
            # Build (or rebuild) the predictor from the SELECTED config. Rebuilt
            # when the selection changes, because a cached engine would silently
            # serve the previous model's numbers.
            cfg_path = self._selected_config_path()
            if self.predictor is None:
                if not cfg_path.exists():
                    raise FileNotFoundError(f"{cfg_path} does not exist")
                self.predictor = DailyPredictor(config_path=cfg_path)
                self._refresh_model_label()

            # ONE call generates every category. Same model, same math as
            # running each category separately — just requested together so the
            # archive holds all of them for this date.
            result = self.predictor.predict(
                target_date,
                hitter_categories=HITTER_CATEGORIES,
                include_pitchers=INCLUDE_PITCHERS,
                apply_corrections=self.corrections_var.get(),
                use_projected_lineups=use_projected,
                # Manual GUI runs are still decision-time model runs. Persist
                # the exact consumed feature bundles and code/config snapshot
                # so they can be audited later; a shadow ledger will reject an
                # archive without these facts rather than recreate them after
                # the game. These flags never affect projections or ranking.
                persist_features=True,
                capture_prediction_provenance=True,
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
            corr = ", CORRECTIONS ON" if self.corrections_var.get() else ""
            self.status_var.set(
                f"Complete for {target_date} ({mode}{corr}) [{self.config_var.get()}] — "
                + ", ".join(counts)
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
            "input_health": display_health_flags(p.input_health_flags),
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
