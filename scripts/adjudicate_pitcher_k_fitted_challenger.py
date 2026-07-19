#!/usr/bin/env python3
"""Stage-gated adjudication of the sole predeclared pitcher-K challenger."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.pitcher_k_fitted_challenger import (  # noqa: E402
    KEY,
    choose_parameters,
    confirmation_pass,
    count_nll,
    empirical_pmf,
    fit_mean_model,
    load_open_2026,
    load_pre2026,
    load_protocol,
    market_frame,
    predict_mean,
    score_market_rows,
    sha256,
)


def atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        frame.to_csv(handle, index=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _by_year(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    return frame[pd.to_datetime(frame.game_date, errors="raise").dt.year.eq(year)].copy()


def _benchmark_rows(protocol: dict, evidence_root: Path) -> pd.DataFrame:
    record = protocol["inputs"]["open_benchmark_rows"]
    path = evidence_root / record["path"]
    if not path.is_file() or sha256(path) != record["sha256"]:
        raise ValueError("open benchmark rows binding changed")
    rows = pd.read_csv(path)
    if len(rows) != record["rows"]:
        raise ValueError("open benchmark row denominator changed")
    rows = rows[rows.terminal_state.eq("started")].copy()
    rows = rows.rename(columns={"mlb_game_pk": "game_pk"})
    if len(rows) != protocol["evaluation"]["required_open_market_rows"] or rows[KEY + ["line"]].duplicated().any():
        raise ValueError("open benchmark started market identity changed")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/pitcher_k_fitted_challenger_protocol.json")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    protocol = load_protocol(args.protocol, repo_root=ROOT, evidence_root=args.evidence_root)
    pre = load_pre2026(protocol, evidence_root=args.evidence_root)
    train_2023, select_2024, confirm_2025 = (_by_year(pre, year) for year in (2023, 2024, 2025))
    selection = choose_parameters(train_2023, select_2024, protocol)
    selected = selection["selected"]
    model_2023 = fit_mean_model(train_2023, alpha=selected["alpha"])
    mean_2025 = predict_mean(model_2023, confirm_2025)
    simple_2023 = empirical_pmf(train_2023)
    confirmation_rows = market_frame(confirm_2025, mean_2025, selected["dispersion"], baseline_pmf=simple_2023)
    confirmation_metrics, confirmation_calibration, confirmation_paired = score_market_rows(
        confirmation_rows, baseline="simple_p_over", protocol=protocol
    )
    selection["confirmation_count_nll_candidate"] = count_nll(confirm_2025.out_k.to_numpy(int), mean_2025, selected["dispersion"])
    # A constant-mean count distribution is the exact simple count comparator.
    simple_mean = float(confirm_2025.out_k.mean()) * 0 + float(train_2023.out_k.mean())
    selection["confirmation_count_nll_simple"] = count_nll(confirm_2025.out_k.to_numpy(int), simple_mean, 0.0)
    passed_confirmation, confirmation_gate = confirmation_pass(selection, confirmation_metrics, confirmation_paired)

    report = {
        "schema_version": "pitcher-k-fitted-challenger-report-v1",
        "status": "CONFIRMATION_REJECTED" if not passed_confirmation else "CONFIRMATION_PASSED_OPEN_EVALUATION_PENDING",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "economic_evidence_eligible": False,
        "protocol": {"path": args.protocol.resolve().relative_to(ROOT.resolve()).as_posix(), "sha256": sha256(args.protocol)},
        "implementation": {"module_path": "src/evaluation/pitcher_k_fitted_challenger.py", "module_sha256": sha256(ROOT / "src/evaluation/pitcher_k_fitted_challenger.py"), "builder_path": Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(), "builder_sha256": sha256(Path(__file__))},
        "selected_parameters": selection,
        "confirmation_gate": confirmation_gate,
        "confirmation": {"rows": int(len(confirmation_rows)), "metrics": confirmation_metrics.to_dict(orient="records"), "paired_uncertainty": confirmation_paired},
        "protected_invariants": protocol["protected_invariants"],
        "artifacts": {},
        "interpretation": "Research-only fitted count-probability adjudication. No historical price is claimed executable; no policy, payout, ROI, or betting conclusion is produced.",
    }
    atomic_csv(confirmation_rows, args.out_dir / "confirmation_2025_rows.csv")
    atomic_csv(confirmation_metrics, args.out_dir / "confirmation_2025_metrics.csv")
    atomic_csv(confirmation_calibration, args.out_dir / "confirmation_2025_calibration.csv")
    for name in ("confirmation_2025_rows.csv", "confirmation_2025_metrics.csv", "confirmation_2025_calibration.csv"):
        path = args.out_dir / name
        report["artifacts"][name] = {"path": path.resolve().relative_to(args.evidence_root.resolve()).as_posix(), "sha256": sha256(path), "rows": int(len(pd.read_csv(path)))}

    if passed_confirmation:
        # This block is unreachable unless the untouched 2025 confirmation gate passed.
        refit = fit_mean_model(pre, alpha=selected["alpha"])
        open_training = load_open_2026(protocol, evidence_root=args.evidence_root)
        benchmark = _benchmark_rows(protocol, args.evidence_root)
        game_keys = benchmark[KEY].drop_duplicates()
        features = game_keys.merge(open_training, on=KEY, how="left", validate="one_to_one")
        if len(features) != protocol["evaluation"]["required_open_pitcher_games"] or features.isna().any().any():
            raise ValueError("open candidate feature coverage changed")
        means = pd.DataFrame({**{name: features[name].to_numpy() for name in KEY}, "candidate_mean_k": predict_mean(refit, features)})
        open_rows = benchmark.merge(means, on=KEY, how="left", validate="many_to_one")
        if open_rows.candidate_mean_k.isna().any() or len(open_rows) != protocol["evaluation"]["required_open_market_rows"]:
            raise ValueError("open candidate probability coverage changed")
        pmf = empirical_pmf(pre)
        constructed = market_frame(
            open_rows.rename(columns={"actual_strikeouts": "out_k"}),
            open_rows.candidate_mean_k.to_numpy(float), selected["dispersion"], baseline_pmf=pmf,
        )
        # Preserve the exact official/production hard market identity from benchmark rows.
        constructed = constructed.merge(open_rows[KEY + ["line", "sim_p_over", "entry_reference_p_over"]], on=KEY + ["line"], how="left", validate="one_to_one")
        if constructed.sim_p_over.isna().any():
            raise ValueError("immutable production comparator coverage changed")
        production_metrics, production_calibration, production_paired = score_market_rows(constructed.assign(production_p_over=constructed.sim_p_over), baseline="production_p_over", protocol=protocol)
        simple_metrics, simple_calibration, simple_paired = score_market_rows(constructed, baseline="simple_p_over", protocol=protocol)
        report["status"] = "OPEN_EVALUATION_COMPLETE_REQUIRES_SEPARATE_GATE_ADJUDICATION"
        report["open_2026"] = {"rows": int(len(constructed)), "production_metrics": production_metrics.to_dict(orient="records"), "production_paired_uncertainty": production_paired, "simple_metrics": simple_metrics.to_dict(orient="records"), "simple_paired_uncertainty": simple_paired}
        for name, frame in (("open_2026_rows.csv", constructed), ("open_2026_vs_production_metrics.csv", production_metrics), ("open_2026_vs_simple_metrics.csv", simple_metrics), ("open_2026_production_calibration.csv", production_calibration), ("open_2026_simple_calibration.csv", simple_calibration)):
            path = args.out_dir / name
            atomic_csv(frame, path)
            report["artifacts"][name] = {"path": path.resolve().relative_to(args.evidence_root.resolve()).as_posix(), "sha256": sha256(path), "rows": int(len(frame))}
    atomic_json(report, args.out_dir / "report.json")
    print(json.dumps({"report": str(args.out_dir / "report.json"), "sha256": sha256(args.out_dir / "report.json"), "status": report["status"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
