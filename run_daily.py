#!/usr/bin/env python3
"""
Production CLI for daily MLB prop predictions.

Examples:
    python run_daily.py --date 2026-07-01 --category hrr
    python run_daily.py --apply-corrections --format all --output data/predictions.csv
    python run_daily.py --odds-file data/odds/lines.csv --min-edge 5
    python run_daily.py --verbose --pitchers-only
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

from src.models.dataclasses import DailyPrediction, PropCategory, PropProjection
from src.prediction import DailyPredictor
from src.utils.errors import ConfigError, DataFetchError, OddsLoadError, PredictorError
from src.utils.logging import setup_logging

CATEGORY_CHOICES = ["hits", "hrr", "hr", "fantasy", "strikeouts"]
FORMAT_CHOICES = ["console", "csv", "json", "all"]
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_DATA = 2
EXIT_CONFIG = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run daily MLB prop predictions with optional corrections and edge analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        metavar="YYYY-MM-DD",
        help="Slate date (default: today)",
    )
    parser.add_argument(
        "--category",
        default="hrr",
        choices=CATEGORY_CHOICES,
        help="Prop category to rank and export (default: hrr)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=25,
        metavar="N",
        help="Number of players to include in ranked output (default: 25)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config.json (default: config/config.json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        help="Output file path (format inferred from extension: .csv or .json)",
    )
    parser.add_argument(
        "--format",
        choices=FORMAT_CHOICES,
        default="console",
        help="Output format: console, csv, json, or all (default: console)",
    )
    parser.add_argument(
        "--pitchers-only",
        action="store_true",
        help="Project pitcher strikeouts only",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass MLB API day cache for this run",
    )
    parser.add_argument(
        "--include-projected-lineups",
        action="store_true",
        help="Include projected/predicted lineups when confirmed orders are unavailable",
    )

    corrections = parser.add_mutually_exclusive_group()
    corrections.add_argument(
        "--apply-corrections",
        action="store_true",
        help="Apply learned bias corrections from CorrectionManager",
    )
    corrections.add_argument(
        "--no-corrections",
        action="store_true",
        help="Explicitly disable corrections even if enabled in config",
    )

    edges = parser.add_mutually_exclusive_group()
    edges.add_argument(
        "--with-edges",
        action="store_true",
        help="Compute +EV value plays when odds data is available",
    )
    edges.add_argument(
        "--no-edges",
        action="store_true",
        help="Skip edge calculation even if odds.enabled in config",
    )
    parser.add_argument(
        "--odds-file",
        metavar="PATH",
        help="Override odds CSV/JSON path for this run",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        metavar="PCT",
        help="Minimum edge %% to include in value plays output",
    )

    logging_group = parser.add_mutually_exclusive_group()
    logging_group.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    logging_group.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress non-error console output",
    )

    return parser.parse_args(argv)


def category_key(category: str) -> PropCategory:
    mapping = {"hr": "home_runs", "pitcher_k": "strikeouts", "pitcher_ks": "strikeouts"}
    return mapping.get(category, category)  # type: ignore[return-value]


def resolve_corrections(args: argparse.Namespace) -> bool | None:
    if args.apply_corrections:
        return True
    if args.no_corrections:
        return False
    return None


def resolve_edges(args: argparse.Namespace) -> bool | None:
    if args.with_edges:
        return True
    if args.no_edges:
        return False
    return None


def build_predictor(args: argparse.Namespace) -> DailyPredictor:
    config_path = Path(args.config) if args.config else None
    predictor = DailyPredictor(config_path=config_path)

    if args.odds_file:
        from src.data.odds import CompositeOddsProvider, FileOddsSettings, OddsSettings

        settings = OddsSettings.from_config(predictor.config)
        path = Path(args.odds_file)
        if path.suffix.lower() == ".json":
            settings.file.json_path = str(path)
            settings.file.csv_path = ""
        else:
            settings.file.csv_path = str(path)
        settings.enabled = True
        settings.file.require_file = True
        settings.require_any_source = True
        predictor.odds_loader = CompositeOddsProvider(settings=settings)

    return predictor


def run_prediction(args: argparse.Namespace, predictor: DailyPredictor) -> DailyPrediction:
    if args.refresh:
        predictor.mlb_api.clear_cache(args.date)

    if args.pitchers_only or args.category == "strikeouts":
        return predictor.predict(
            args.date,
            include_pitchers=True,
            hitter_categories=(),
            apply_corrections=resolve_corrections(args),
            include_edges=resolve_edges(args),
            min_edge_pct=args.min_edge,
            use_projected_lineups=args.include_projected_lineups,
        )

    cat = category_key(args.category)
    return predictor.predict(
        args.date,
        hitter_categories=(cat,),
        include_pitchers=False,
        apply_corrections=resolve_corrections(args),
        include_edges=resolve_edges(args),
        min_edge_pct=args.min_edge,
        use_projected_lineups=args.include_projected_lineups,
    )


def get_ranked(
    prediction: DailyPrediction,
    args: argparse.Namespace,
    predictor: DailyPredictor,
) -> tuple[list[PropProjection], str]:
    if args.pitchers_only or args.category == "strikeouts":
        ranked = predictor.rank_pitcher_projections(prediction.pitcher_projections, args.top)
        return ranked, "pitcher strikeouts"
    cat = category_key(args.category)
    ranked = predictor.rank_hitter_projections(prediction.hitter_projections, cat, args.top)
    return ranked, f"top {args.category}"


def projection_rows(ranked: list[PropProjection], game_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in ranked:
        sim = p.simulation
        rows.append(
            {
                "date": game_date,
                "player": p.player_name,
                "team": p.team,
                "opponent": p.opponent,
                "opposing_pitcher": p.opposing_pitcher,
                "lineup_status": p.lineup_status,
                "category": p.category,
                "projected": p.projected_value,
                "confidence": p.confidence,
                "mc_mean": round(sim.mean, 3) if sim else "",
                "mc_p10": round(sim.p10, 3) if sim else "",
                "mc_p90": round(sim.p90, 3) if sim else "",
            }
        )
    return rows


def value_play_rows(prediction: DailyPrediction) -> list[dict[str, Any]]:
    return [
        {
            "player": edge.player_name,
            "category": edge.category,
            "line": edge.line,
            "projected": edge.projected_value,
            "edge_pct": edge.edge_pct,
            "recommendation": edge.recommendation.value,
            "model_prob_over": edge.model_prob_over,
            "implied_prob_over": edge.implied_prob_over,
            "confidence": edge.confidence,
        }
        for edge in prediction.value_plays
    ]


def write_csv(rows: list[dict[str, Any]], path: str) -> None:
    if not rows:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def write_json(prediction: DailyPrediction, path: str, ranked: list[PropProjection]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = prediction.to_dict()
    payload["ranked"] = [
        {
            "player": p.player_name,
            "category": p.category,
            "projected": p.projected_value,
            "confidence": p.confidence,
        }
        for p in ranked
    ]
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def print_console(
    ranked: list[PropProjection],
    prediction: DailyPrediction,
    label: str,
    game_date: str,
    quiet: bool,
) -> None:
    if quiet:
        return

    print(f"\n{label.upper()} — {game_date} ({len(ranked)} players)")
    print("-" * 72)
    for i, p in enumerate(ranked[:10], 1):
        print(
            f"{i:2}. {p.player_name:<24} {p.category:<12} "
            f"proj {p.projected_value:.2f}  conf {p.confidence:.2f}"
        )

    if prediction.value_plays:
        print(f"\nVALUE PLAYS ({len(prediction.value_plays)})")
        print("-" * 72)
        for i, edge in enumerate(prediction.value_plays[:10], 1):
            print(
                f"{i:2}. {edge.player_name:<24} {edge.category:<10} "
                f"line {edge.line}  edge {edge.edge_pct:+.1f}%  "
                f"{edge.recommendation.value}"
            )


def resolve_output_paths(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if not args.output:
        return None, None
    path = Path(args.output)
    if path.suffix.lower() == ".json":
        return None, str(path)
    if path.suffix.lower() == ".csv":
        return str(path), None
    return str(path.with_suffix(".csv")), str(path.with_suffix(".json"))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    try:
        predictor = build_predictor(args)
        prediction = run_prediction(args, predictor)
        ranked, label = get_ranked(prediction, args, predictor)

        if not ranked and not prediction.value_plays:
            print(
                f"No projections for {args.date}. "
                "Lineups may not be confirmed yet — try again closer to game time.",
                file=sys.stderr,
            )
            return EXIT_NO_DATA

        rows = projection_rows(ranked, args.date)
        csv_path, json_path = resolve_output_paths(args)

        fmt = args.format
        if args.output and fmt == "console":
            fmt = "all"

        if fmt in ("console", "all"):
            print_console(ranked, prediction, label, args.date, args.quiet)

        if fmt in ("csv", "all"):
            target = csv_path or args.output or "data/daily_predictions.csv"
            write_csv(rows, target)
            if not args.quiet:
                print(f"Wrote {len(rows)} rows to {target}")

        if fmt in ("json", "all"):
            target = json_path or (args.output if args.output and args.output.endswith(".json") else "data/daily_predictions.json")
            write_json(prediction, target, ranked)
            if not args.quiet:
                print(f"Wrote JSON to {target}")

        if prediction.value_plays and fmt in ("csv", "all"):
            edge_path = str(Path(csv_path or "data/daily_predictions.csv").with_name("value_plays.csv"))
            write_csv(value_play_rows(prediction), edge_path)
            if not args.quiet:
                print(f"Wrote {len(prediction.value_plays)} value plays to {edge_path}")

        return EXIT_OK

    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except (DataFetchError, OddsLoadError) as exc:
        print(f"Data error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except PredictorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())