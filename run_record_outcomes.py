#!/usr/bin/env python3
"""
Read data/predictions/preds_<date>.csv and write prediction outcomes to
data/predictions/prediction_outcomes_<date>.csv and data/predictions/prediction_outcomes.csv.

Usage:
  python run_record_outcomes.py --date 2026-07-06
"""
from pathlib import Path
import argparse
import sys

try:
    import pandas as pd
except Exception as e:
    print("ERROR: pandas is required. Install with `pip install pandas`.")
    raise

def load_preds(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)

def compute_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    # If the CSV already contains 'actual' and 'predicted', compute correctness.
    if {"actual", "predicted"}.issubset(df.columns):
        df = df.copy()
        df["outcome_correct"] = df["predicted"] == df["actual"]
        return df

    # If there is a probability and a label column, derive predicted label.
    if "pred_prob" in df.columns and "label" in df.columns:
        df = df.copy()
        df["predicted_label"] = (df["pred_prob"] > 0.5).astype(int)
        df["outcome_correct"] = df["predicted_label"] == df["label"]
        return df

    # Generic fallback: add a marker column and return unchanged data.
    df = df.copy()
    df["outcome_computed"] = False
    return df

def write_outcomes(df: pd.DataFrame, outdir: Path, basename: str, date_str: str):
    outdir.mkdir(parents=True, exist_ok=True)
    dated = outdir / f"{basename}_{date_str}.csv"
    latest = outdir / f"{basename}.csv"
    df.to_csv(dated, index=False)
    df.to_csv(latest, index=False)
    return dated, latest

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Date to grade (YYYY-MM-DD)")
    parser.add_argument("--input-dir", default="data/predictions", help="Directory with preds_<date>.csv")
    parser.add_argument("--out-name", default="prediction_outcomes", help="Output basename (without extension)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args(argv)

    date = args.date
    input_dir = Path(args.input_dir)
    infile = input_dir / f"preds_{date}.csv"

    if args.verbose:
        print(f"Looking for predictions file: {infile}")

    if not infile.exists():
        print(f"No predictions file found at {infile}. Nothing to grade. Exiting normally.")
        # Exit success so CI/workflow doesn't fail when there are no preds to grade.
        return 0

    try:
        if args.verbose:
            print(f"Loading predictions from {infile}...")
        df = load_preds(infile)
    except Exception as e:
        print(f"Error reading predictions file {infile}: {e}")
        return 2

    if args.verbose:
        print("Computing outcomes...")
    df_out = compute_outcomes(df)

    if args.verbose:
        print("Writing outcomes...")
    try:
        dated, latest = write_outcomes(df_out, input_dir, args.out_name, date)
    except Exception as e:
        print(f"Error writing outcomes: {e}")
        return 3

    print(f"Wrote outcomes: {dated} and {latest}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
