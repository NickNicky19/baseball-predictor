"""Freeze one game-level shared-PA candidate archive in a T-4 or T-1 lane."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_capture_plan import load_capture_plan
from src.evaluation.shared_pa_dual_horizon_lanes_v1 import (
    LANES,
    SharedPADualHorizonError,
    bind_prediction_archive_to_lane,
    publish_lane_archive_once,
)
from src.prediction.integrated_shared_pa_candidate import (
    CandidateEvidenceError,
    MODEL_ID,
    load_candidate_archive,
    unavailable_candidate_archive,
)


def _target(plan, game_pk: int):
    matches = [target for target in plan.targets if target.mlb_game_pk == game_pk]
    if len(matches) != 1:
        raise SharedPADualHorizonError(
            "requested game must appear exactly once in the immutable lane plan"
        )
    return matches[0]


def run_lane(
    *, lane: str, game_date: str, game_pk: int, plan_path: Path,
    candidate_evidence: Path | None, output_root: Path,
) -> dict:
    if lane not in LANES:
        raise SharedPADualHorizonError("unknown prediction lane")
    plan = load_capture_plan(plan_path)
    target = _target(plan, game_pk)
    if plan.official_game_date != game_date or plan.entry_hours != LANES[lane]:
        raise SharedPADualHorizonError("plan date or horizon differs from requested lane")
    if candidate_evidence is None:
        archive = unavailable_candidate_archive(
            game_date=game_date,
            reason_code=f"{lane.upper()}_QUALIFIED_EVIDENCE_UNAVAILABLE",
            detail=(
                "No qualified candidate side bundle was available at the immutable lane "
                "horizon; no frozen, cross-lane, guessed-lineup, or late fallback was used"
            ),
        )
    else:
        archive = load_candidate_archive(candidate_evidence, expected_date=game_date)
        observed_games = {
            int(prediction["mlb_game_pk"])
            for prediction in archive.get("predictions", [])
        }
        if observed_games != {game_pk}:
            raise SharedPADualHorizonError(
                "candidate evidence must contain predictions for exactly the requested game"
            )
        for prediction in archive["predictions"]:
            if prediction["decision_horizon_utc"] != target.entry_target_at_utc:
                raise SharedPADualHorizonError(
                    "candidate record does not match the immutable game horizon"
                )
    bound = bind_prediction_archive_to_lane(lane=lane, plan=plan, archive=archive)
    bound["target_game_pk"] = game_pk
    # target_game_pk is part of the immutable identity and therefore changes the
    # final digest after the generic lane binding has completed.
    unsigned = dict(bound)
    unsigned.pop("lane_archive_sha256")
    from src.evaluation.shared_pa_forward_evidence import sha256_value
    bound["lane_archive_sha256"] = sha256_value(unsigned)
    destination = (
        output_root / lane / game_date / str(game_pk) / f"{MODEL_ID}.json"
    )
    publish_lane_archive_once(bound, destination)
    return {
        "schema_version": "shared-pa-dual-horizon-run-result-v1",
        "lane_id": lane,
        "official_game_date": game_date,
        "mlb_game_pk": game_pk,
        "official_start_time_utc": target.official_start_time_utc,
        "decision_horizon_utc": target.entry_target_at_utc,
        "predicted_players": archive["coverage"]["predicted_players"],
        "abstained_players": archive["coverage"]["abstained_players"],
        "lane_archive_sha256": bound["lane_archive_sha256"],
        "output_path": str(destination.resolve()),
        "research_only": True,
        "betting_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=sorted(LANES), required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--game-pk", type=int, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--candidate-evidence", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_lane(
            lane=args.lane, game_date=args.date, game_pk=args.game_pk,
            plan_path=args.plan, candidate_evidence=args.candidate_evidence,
            output_root=args.output_root,
        )
    except (OSError, ValueError, CandidateEvidenceError, SharedPADualHorizonError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
