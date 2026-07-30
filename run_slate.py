#!/usr/bin/env python3
"""
Full-slate daily prediction for automation (GitHub Actions).

ONE predict() call generates EVERY graded category at once — hits, hrr,
home_runs (hitters) + strikeouts (pitchers) — exactly like the GUI's
"Run all categories" button. Because config has archive_on_predict=True,
predict() automatically writes the rich JSON archive to
data/learning/predictions/predictions_<date>.json, including the per-row
`simulation` block (n_sims, mean, p10/p90, p_ge_threshold) that the flat
CSV path throws away.

Why this exists: run_daily.py takes a single --category (default hrr) and
its --format flag means output FILE FORMAT, not "all categories". Wiring
Actions to run_daily.py collected hrr-only, no simulation distributions.
This entry point is the automation-safe equivalent of the GUI.

The live model is untouched: same predict(), same math. apply_corrections
defaults to config (learning.apply_corrections=False) — frozen during the
collection window (discipline #1). Do not pass --apply-corrections until
the promotion gate (discipline #4) is cleared.

Usage:
    python run_slate.py --date 2026-07-08
    python run_slate.py                      # today (US slate date)

The default is the current K/BB research baseline.  This does not authorize
betting; the market-output policy remains fail closed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.models.dataclasses import PropCategory
from src.learning.prediction_archive import PredictionArchive
from src.prediction import DailyPredictor
from src.prediction.integrated_shared_pa_candidate import (
    FROZEN_MODEL_ID,
    MODEL_ID as SHARED_PA_MODEL_ID,
    CandidateEvidenceError,
    atomic_write_json,
    load_candidate_archive,
    unavailable_candidate_archive,
)
from src.utils.errors import ConfigError, DataFetchError, PredictorError
from src.utils.logging import setup_logging

# The categories graded by this project, matching the GUI's TABS. Fantasy is
# intentionally excluded (not a prop being graded). If gui.py's TABS changes,
# change this to match so automation and manual runs never drift.
HITTER_CATEGORIES: tuple[PropCategory, ...] = ("hits", "hrr", "home_runs")  # type: ignore[assignment]
INCLUDE_PITCHERS: bool = True  # strikeouts
DEFAULT_RESEARCH_CONFIG = Path("config/config.kbb.json")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_DATA = 2
EXIT_CONFIG = 3


class PregameArchiveBoundaryError(ValueError):
    """A live archive would be created after at least one game has started."""


def utc_now() -> datetime:
    """Clock boundary kept injectable by the pregame archive tests."""
    return datetime.now(timezone.utc)


def _parse_schedule_start(raw_game: dict[str, Any]) -> datetime:
    """Return a schedule game's exact UTC start or fail closed.

    ``run_slate.py`` archives a whole-slate prediction object.  Filtering its
    projections after the model has fetched live inputs is not sufficient: an
    in-progress game's season/recent inputs may already have changed.  The
    complete slate must therefore be proven pregame *before* prediction starts.
    """
    raw_start = raw_game.get("gameDate")
    game_pk = raw_game.get("gamePk", "unknown")
    if not isinstance(raw_start, str) or not raw_start:
        raise PregameArchiveBoundaryError(
            f"Refusing live archive: schedule game {game_pk} lacks an exact gameDate"
        )
    try:
        start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PregameArchiveBoundaryError(
            f"Refusing live archive: schedule game {game_pk} has an invalid gameDate"
        ) from exc
    if start.tzinfo is None:
        raise PregameArchiveBoundaryError(
            f"Refusing live archive: schedule game {game_pk} gameDate lacks a timezone"
        )
    return start.astimezone(timezone.utc)


def assert_full_slate_is_pregame(*, mlb_api: Any, game_date: str, assessed_at: datetime | None = None) -> None:
    """Fail closed unless every scheduled game starts strictly after now.

    This boundary applies before feature construction, simulation, or archive
    publication.  It deliberately rejects a partial slate: a partial archive
    would look like a full-day denominator later and could hide post-start input
    contamination.  Per-game T-4 collectors use their own receipt-bound entry
    points and are intentionally not routed through this whole-slate runner.
    """
    current = assessed_at or utc_now()
    if current.tzinfo is None:
        raise PregameArchiveBoundaryError("Refusing live archive: assessment clock lacks a timezone")
    current = current.astimezone(timezone.utc)
    try:
        games = mlb_api.get_schedule(game_date, include_lineups=False)
    except Exception as exc:
        raise PregameArchiveBoundaryError(
            "Refusing live archive: cannot obtain the authoritative slate schedule"
        ) from exc
    if not isinstance(games, list):
        raise PregameArchiveBoundaryError("Refusing live archive: schedule response is not a game list")

    started: list[str] = []
    for raw_game in games:
        if not isinstance(raw_game, dict):
            raise PregameArchiveBoundaryError("Refusing live archive: schedule contains a malformed game")
        start = _parse_schedule_start(raw_game)
        if start <= current:
            started.append(f"{raw_game.get('gamePk', 'unknown')}@{start.isoformat()}")
    if started:
        raise PregameArchiveBoundaryError(
            "Refusing live archive: one or more slate games have started or reached first pitch: "
            + ", ".join(started)
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ALL graded prop categories for a slate in one pass "
        "(automation equivalent of the GUI 'Run all categories').",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--model",
        choices=(FROZEN_MODEL_ID, SHARED_PA_MODEL_ID),
        help=(
            "Select one explicit research arm. Omit to preserve the legacy frozen "
            "runner interface. The candidate requires --candidate-evidence."
        ),
    )
    parser.add_argument(
        "--compare-models",
        action="store_true",
        help="Run the frozen arm and compare it with the retained candidate evidence.",
    )
    parser.add_argument(
        "--candidate-evidence",
        metavar="PATH",
        help="Hash-bound projected-opportunity side bundle or directory of bundles.",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        metavar="YYYY-MM-DD",
        help="Slate date (default: today)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to model config (default: config/config.kbb.json, current research baseline)",
    )
    parser.add_argument(
        "--archive-dir",
        metavar="PATH",
        help=(
            "Write the rich prediction archive to this directory without changing "
            "model configuration or prediction provenance. The forward collector "
            "uses a directory outside its clean release checkout."
        ),
    )
    parser.add_argument(
        "--include-projected-lineups",
        action="store_true",
        help=(
            "Reserved and fail-closed: unreceipted schedule projections cannot "
            "be used until a contract-bound projected-lineup source is certified"
        ),
    )
    parser.add_argument(
        "--apply-corrections",
        action="store_true",
        help="Apply learned corrections. LEAVE OFF during the collection "
        "window (discipline #1: model frozen). Off by default.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass MLB API day cache for this run",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return parser.parse_args(argv)


def _is_may_2026(value: str) -> bool:
    parsed = date.fromisoformat(value)
    return parsed.year == 2026 and parsed.month == 5


def _candidate_output_path(args: argparse.Namespace) -> Path:
    root = Path(args.archive_dir).resolve() if args.archive_dir else Path("data/learning/predictions")
    return root / SHARED_PA_MODEL_ID / f"predictions_{args.date}.json"


def _frozen_output_path(args: argparse.Namespace) -> Path:
    root = Path(args.archive_dir).resolve() if args.archive_dir else Path("data/learning/predictions")
    return root / FROZEN_MODEL_ID / f"predictions_{args.date}.json"


def _load_candidate(args: argparse.Namespace) -> dict[str, Any]:
    if not args.candidate_evidence:
        return unavailable_candidate_archive(
            game_date=args.date,
            reason_code="QUALIFIED_OPPORTUNITY_EVIDENCE_UNAVAILABLE",
            detail=(
                "No --candidate-evidence bundle was supplied; frozen artifacts and guessed "
                "lineups are prohibited fallbacks"
            ),
        )
    return load_candidate_archive(Path(args.candidate_evidence), expected_date=args.date)


def _run_frozen(args: argparse.Namespace, *, archive_predictions: bool | None) -> Any:
    config_path = Path(args.config) if args.config else DEFAULT_RESEARCH_CONFIG
    predictor = DailyPredictor(config_path=config_path)
    assert_full_slate_is_pregame(mlb_api=predictor.mlb_api, game_date=args.date)
    if args.refresh:
        predictor.mlb_api.clear_cache(args.date)
    return predictor.predict(
        args.date,
        hitter_categories=HITTER_CATEGORIES,
        include_pitchers=INCLUDE_PITCHERS,
        persist_features=True,
        archive_predictions=archive_predictions,
        capture_prediction_provenance=True,
        apply_corrections=True if args.apply_corrections else None,
        use_projected_lineups=args.include_projected_lineups,
    )


def _frozen_envelope(result: Any) -> dict[str, Any]:
    payload = result.to_dict()
    payload["model_id"] = FROZEN_MODEL_ID
    provenance = dict(payload.get("prediction_provenance") or {})
    provenance["model_id"] = FROZEN_MODEL_ID
    payload["prediction_provenance"] = provenance
    payload["research_only"] = True
    payload["betting_authorized"] = False
    return payload


def _comparison_rows(frozen: Any, candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    frozen_by_key: dict[tuple[int, int, str], Any] = {}
    player_display: dict[tuple[int, int], Any] = {}
    for projection in [*frozen.hitter_projections, *frozen.pitcher_projections]:
        if projection.mlb_game_pk is None:
            continue
        key = (int(projection.mlb_game_pk), int(projection.player_id), str(projection.category))
        if key in frozen_by_key:
            raise CandidateEvidenceError("frozen arm contains a duplicate hard market identity")
        frozen_by_key[key] = projection
        player_display[(key[0], key[1])] = projection
    rows: list[dict[str, Any]] = []
    for prediction in candidate.get("predictions", []):
        game_pk = int(prediction["mlb_game_pk"])
        player_id = int(prediction["player_id"])
        display = player_display.get((game_pk, player_id))
        for market, candidate_market in prediction["markets"].items():
            frozen_projection = frozen_by_key.get((game_pk, player_id, market))
            frozen_mean = None
            frozen_thresholds = None
            if frozen_projection is not None:
                frozen_mean = (
                    float(frozen_projection.simulation.mean)
                    if frozen_projection.simulation is not None
                    else float(frozen_projection.projected_value)
                )
                frozen_thresholds = (
                    {str(k): float(v) for k, v in frozen_projection.simulation.p_ge_threshold.items()}
                    if frozen_projection.simulation is not None
                    else None
                )
            candidate_mean = float(candidate_market["mean"])
            rows.append({
                "mlb_game_pk": game_pk,
                "team_id": prediction["team_id"],
                "side": prediction["side"],
                "player_id": player_id,
                "player_name": getattr(display, "player_name", None),
                "team": getattr(display, "team", None),
                "opponent": getattr(display, "opponent", None),
                "lineup_state": getattr(display, "lineup_status", None),
                "probable_starter_evidence_state": "excluded_batter_only",
                "market": market,
                "frozen_mean": frozen_mean,
                "candidate_mean": candidate_mean,
                "absolute_change": None if frozen_mean is None else candidate_mean - frozen_mean,
                "frozen_threshold_probabilities": frozen_thresholds,
                "candidate_threshold_probabilities": candidate_market["threshold_probabilities"],
                "opportunity_model_change": "receipt-bound start/slot/PA mixture replaces scalar PA",
                "major_change_reason": "batter-only EB outcomes; mutable Savant and unreceipted pitcher effects excluded",
                "candidate_input_health": prediction["input_health"],
                "comparison_status": "paired" if frozen_projection is not None else "candidate_only_market",
            })
    return rows


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    log = logging.getLogger("run_slate")

    try:
        if _is_may_2026(args.date):
            raise CandidateEvidenceError("May 2026 is sealed and cannot be run or inspected")
        if args.compare_models and args.model is not None:
            raise CandidateEvidenceError("use either --model or --compare-models, not both")
        if args.model == SHARED_PA_MODEL_ID:
            candidate = _load_candidate(args)
            destination = atomic_write_json(candidate, _candidate_output_path(args))
            print(
                f"Candidate slate {args.date}: {candidate['coverage']['predicted_players']} predicted + "
                f"{candidate['coverage']['abstained_players']} abstained players."
            )
            print(f"Archive: {destination}")
            return EXIT_OK if candidate["predictions"] else EXIT_NO_DATA
        if args.compare_models:
            candidate = _load_candidate(args)
            frozen = _run_frozen(args, archive_predictions=False)
            frozen_path = atomic_write_json(_frozen_envelope(frozen), _frozen_output_path(args))
            candidate_path = atomic_write_json(candidate, _candidate_output_path(args))
            comparison = {
                "schema_version": "frozen-versus-shared-pa-candidate-v1",
                "game_date": args.date,
                "research_only": True,
                "betting_authorized": False,
                "model_ids": [FROZEN_MODEL_ID, SHARED_PA_MODEL_ID],
                "rows": _comparison_rows(frozen, candidate),
                "candidate_abstentions": candidate["abstentions"],
                "warning": "Changed probabilities are not evidence of improvement.",
            }
            compare_root = Path(args.archive_dir).resolve() if args.archive_dir else Path("data/learning/predictions")
            comparison_path = atomic_write_json(
                comparison, compare_root / "comparisons" / f"comparison_{args.date}.json"
            )
            print(f"Comparison {args.date}: {len(comparison['rows'])} market rows.")
            print(f"Frozen archive: {frozen_path}")
            print(f"Candidate archive: {candidate_path}")
            print(f"Comparison archive: {comparison_path}")
            return EXIT_OK

        config_path = Path(args.config) if args.config else DEFAULT_RESEARCH_CONFIG
        predictor = DailyPredictor(config_path=config_path)
        # Do this before the first feature or stats request.  A post-start
        # archive cannot be repaired by dropping projections after the fact.
        assert_full_slate_is_pregame(
            mlb_api=predictor.mlb_api,
            game_date=args.date,
        )
        archive_dir = (
            Path(args.archive_dir).resolve()
            if args.archive_dir
            else Path("data/learning/predictions")
        )
        if args.archive_dir:
            predictor.outcome_recorder.archive = PredictionArchive(
                archive_dir=str(archive_dir),
                project_root=Path.cwd(),
            )

        if args.refresh:
            predictor.mlb_api.clear_cache(args.date)

        # ONE call, every category. archive_predictions left as None so it
        # follows config (archive_on_predict=True) — this is what writes the
        # rich JSON archive the outcome recorder later grades.
        result = predictor.predict(
            args.date,
            hitter_categories=HITTER_CATEGORIES,
            include_pitchers=INCLUDE_PITCHERS,
            # Persist the exact feature bundles as well as the prediction
            # archive. This makes factual fallback rates auditable per slate;
            # it does not change any feature, probability, or simulation seed.
            persist_features=True,
            # The automation archive is the candidate source for future
            # hard-keyed shadow entries, so record decision-time code/config
            # provenance now. Archives without it are deliberately ineligible
            # for forward-shadow evidence.
            capture_prediction_provenance=True,
            apply_corrections=True if args.apply_corrections else None,
            use_projected_lineups=args.include_projected_lineups,
            archive_predictions=False if args.model == FROZEN_MODEL_ID else None,
        )

        # Explicit named-arm runs use a model-separated envelope.  The legacy
        # no-flag command above retains its historical archive behavior.
        if args.model == FROZEN_MODEL_ID:
            atomic_write_json(_frozen_envelope(result), _frozen_output_path(args))

        n_hit = len(result.hitter_projections)
        n_pit = len(result.pitcher_projections)
        cats = sorted({p.category for p in result.hitter_projections}
                      | {p.category for p in result.pitcher_projections})

        if n_hit == 0 and n_pit == 0:
            log.warning("No projections generated for %s (no slate / no data).", args.date)
            print(f"No projections for {args.date}.", file=sys.stderr)
            return EXIT_NO_DATA

        print(
            f"Slate {args.date}: {n_hit} hitter + {n_pit} pitcher projections "
            f"across {cats}."
        )
        reported_archive = (
            _frozen_output_path(args)
            if args.model == FROZEN_MODEL_ID
            else archive_dir / f"predictions_{args.date}.json"
        )
        print(f"Archive: {reported_archive} (includes simulation blocks).")
        print(
            f"Feature snapshot: data/features/{args.date}/ "
            "(manifest-verified input-health evidence)."
        )
        return EXIT_OK

    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except PregameArchiveBoundaryError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_NO_DATA
    except (CandidateEvidenceError, ValueError, json.JSONDecodeError) as exc:
        print(f"Candidate input error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except (DataFetchError, PredictorError) as exc:
        print(f"Prediction error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
