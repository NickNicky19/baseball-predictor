"""Receipt-bound pitcher identity and probability-consumption gate.

The frozen comparator may retain its historical pitcher path.  Any new strict
candidate must either present an immutable T-minus-4 authorization plus a
separately hash-bound point-in-time pitcher profile, or consume a completely
neutral pitcher block.  A missing receipt is exclusion, while a contradictory
receipt or profile is terminal.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

from src.evaluation.forward_pitcher_context import ForwardPitcherContext
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.models.dataclasses import (
    HitterGameContext,
    LeagueBaselines,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
)
from src.data.statcast_integrity import sha256_feature_value


AUTHORIZATION_SCHEMA = "pitcher-matchup-authorization-v1"
PROFILE_LINEAGE_SCHEMA = "pitcher-profile-lineage-v1"
STRICT_MODE = "receipt_required_or_exclude"
LEGACY_MODE = "legacy_frozen"
ALLOWED_MODES = frozenset({LEGACY_MODE, STRICT_MODE})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PitcherMatchupGateError(ValueError):
    """Pitcher context cannot be consumed truthfully."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _hash(value: object, label: str) -> str:
    output = str(value).strip().lower()
    if not _SHA256.fullmatch(output):
        raise PitcherMatchupGateError(f"{label} must be a SHA-256 digest")
    return output


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise PitcherMatchupGateError(f"{label} must be a positive integer")
    try:
        output = int(value)
    except (TypeError, ValueError) as exc:
        raise PitcherMatchupGateError(f"{label} must be a positive integer") from exc
    if output <= 0:
        raise PitcherMatchupGateError(f"{label} must be a positive integer")
    return output


def _utc(value: object, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PitcherMatchupGateError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PitcherMatchupGateError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_mode(value: object) -> str:
    mode = str(value).strip()
    if mode not in ALLOWED_MODES:
        raise PitcherMatchupGateError(
            f"pitcher_context_identity_mode must be one of {sorted(ALLOWED_MODES)}"
        )
    return mode


@dataclass(frozen=True)
class PitcherMatchupAuthorization:
    target_id: str
    plan_sha256: str
    context_sha256: str
    captured_at_utc: str
    entry_target_at_utc: str
    mlb_game_pk: int
    official_game_date: str
    home_team: str
    away_team: str
    hitter_team: str
    opponent_team: str
    hitter_is_home: bool
    expected_pitcher_id: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_id", _hash(self.target_id, "target_id"))
        object.__setattr__(self, "plan_sha256", _hash(self.plan_sha256, "plan_sha256"))
        object.__setattr__(self, "context_sha256", _hash(self.context_sha256, "context_sha256"))
        object.__setattr__(self, "mlb_game_pk", _positive_int(self.mlb_game_pk, "mlb_game_pk"))
        object.__setattr__(
            self, "expected_pitcher_id", _positive_int(self.expected_pitcher_id, "expected_pitcher_id")
        )
        object.__setattr__(self, "captured_at_utc", _utc(self.captured_at_utc, "captured_at_utc"))
        object.__setattr__(self, "entry_target_at_utc", _utc(self.entry_target_at_utc, "entry_target_at_utc"))
        if datetime.fromisoformat(self.captured_at_utc.replace("Z", "+00:00")) > datetime.fromisoformat(
            self.entry_target_at_utc.replace("Z", "+00:00")
        ):
            raise PitcherMatchupGateError("pitcher receipt was captured after T-minus-4")
        for name in (
            "official_game_date",
            "home_team",
            "away_team",
            "hitter_team",
            "opponent_team",
        ):
            value = str(getattr(self, name)).strip()
            if not value:
                raise PitcherMatchupGateError(f"{name} cannot be blank")
            object.__setattr__(self, name, value)
        try:
            target_date = date.fromisoformat(self.official_game_date)
        except ValueError as exc:
            raise PitcherMatchupGateError("official_game_date must be YYYY-MM-DD") from exc
        if target_date.year == 2026 and target_date.month == 5:
            raise PitcherMatchupGateError("May 2026 pitcher receipts are sealed")
        if not isinstance(self.hitter_is_home, bool):
            raise PitcherMatchupGateError("hitter_is_home must be boolean")
        if self.home_team == self.away_team:
            raise PitcherMatchupGateError("receipt home and away teams cannot match")

    @property
    def authorization_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict(include_hash=False))).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {"schema_version": AUTHORIZATION_SCHEMA, **asdict(self)}
        if include_hash:
            payload["authorization_sha256"] = self.authorization_sha256
        return payload

    @classmethod
    def from_context(
        cls,
        *,
        context: ForwardPitcherContext,
        plan: ShadowCapturePlan,
        target: CaptureTarget,
        hitter: HitterGameContext,
    ) -> "PitcherMatchupAuthorization":
        if target.target_id not in {item.target_id for item in plan.targets}:
            raise PitcherMatchupGateError("receipt target is not in its plan")
        try:
            context.bind_target(target)
        except ValueError as exc:
            raise PitcherMatchupGateError(str(exc)) from exc
        if context.plan_sha256 != plan.plan_sha256:
            raise PitcherMatchupGateError("receipt context and plan hashes differ")
        if not context.candidate_input_eligible:
            raise PitcherMatchupGateError("receipt lacks two resolved probable starters")
        if hitter.game.game_pk != target.mlb_game_pk or hitter.game.game_date != target.official_game_date:
            raise PitcherMatchupGateError("hitter game identity contradicts receipt target")
        # MLBStatsAPI uses full team names while player identity commonly uses
        # abbreviations.  Game PK plus home/away side is the canonical join;
        # comparing presentation labels would create false mismatches.
        expected = (
            context.away_probable_pitcher.player_id
            if hitter.game.is_home
            else context.home_probable_pitcher.player_id
        )
        return cls(
            target_id=target.target_id,
            plan_sha256=plan.plan_sha256,
            context_sha256=context.context_sha256,
            captured_at_utc=context.captured_at_utc,
            entry_target_at_utc=target.entry_target_at_utc,
            mlb_game_pk=target.mlb_game_pk,
            official_game_date=target.official_game_date,
            home_team=context.home_team,
            away_team=context.away_team,
            hitter_team=hitter.player.team,
            opponent_team=hitter.game.opponent,
            hitter_is_home=hitter.game.is_home,
            expected_pitcher_id=_positive_int(expected, "receipt probable pitcher"),
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PitcherMatchupAuthorization":
        required = {
            "schema_version",
            "authorization_sha256",
            *cls.__dataclass_fields__,
        }
        if set(raw) != required or raw.get("schema_version") != AUTHORIZATION_SCHEMA:
            raise PitcherMatchupGateError("pitcher authorization schema is incomplete or unknown")
        authorization = cls(
            **{name: raw[name] for name in cls.__dataclass_fields__}
        )
        if _hash(raw["authorization_sha256"], "authorization_sha256") != authorization.authorization_sha256:
            raise PitcherMatchupGateError("pitcher authorization hash mismatch")
        return authorization

    def validate_hitter(self, hitter: HitterGameContext) -> None:
        if (
            hitter.game.game_pk != self.mlb_game_pk
            or hitter.game.game_date != self.official_game_date
            or hitter.player.team != self.hitter_team
            or hitter.game.opponent != self.opponent_team
            or hitter.game.is_home != self.hitter_is_home
        ):
            raise PitcherMatchupGateError("pitcher authorization does not match hitter identity")


def validate_pitcher_profile_lineage(
    *,
    profile: PitcherStatcastProfile,
    lineage: Mapping[str, Any],
    authorization: PitcherMatchupAuthorization,
) -> None:
    required = {
        "schema_version",
        "player_id",
        "target_date",
        "source_cutoff_date",
        "source_max_game_date",
        "source_hash",
        "source_dates_sha256",
        "source_row_count",
        "source_months",
        "fields",
    }
    if set(lineage) != required or lineage.get("schema_version") != PROFILE_LINEAGE_SCHEMA:
        raise PitcherMatchupGateError("pitcher profile lineage schema is incomplete or unknown")
    if _positive_int(lineage.get("player_id"), "pitcher lineage player_id") != authorization.expected_pitcher_id:
        raise PitcherMatchupGateError("pitcher lineage player identity mismatch")
    if profile.player_id != authorization.expected_pitcher_id:
        raise PitcherMatchupGateError("pitcher profile identity differs from receipt")
    if str(lineage.get("target_date")) != authorization.official_game_date:
        raise PitcherMatchupGateError("pitcher lineage target date mismatch")
    target = date.fromisoformat(authorization.official_game_date)
    cutoff = date.fromisoformat(str(lineage.get("source_cutoff_date")))
    maximum = date.fromisoformat(str(lineage.get("source_max_game_date")))
    if cutoff >= target or maximum > cutoff:
        raise PitcherMatchupGateError("pitcher lineage is not strictly pregame")
    months = lineage.get("source_months")
    if not isinstance(months, list) or not months or any(not isinstance(item, str) for item in months):
        raise PitcherMatchupGateError("pitcher lineage source_months is invalid")
    if "2026-05" in months:
        raise PitcherMatchupGateError("pitcher lineage includes sealed May 2026")
    _hash(lineage.get("source_hash"), "pitcher source_hash")
    _hash(lineage.get("source_dates_sha256"), "pitcher source_dates_sha256")
    row_count = _positive_int(lineage.get("source_row_count"), "pitcher source_row_count")
    fields = lineage.get("fields")
    if not isinstance(fields, Mapping) or set(fields) != {"k_rate", "bb_rate", "hr_per_9"}:
        raise PitcherMatchupGateError("pitcher lineage fields are incomplete")
    values = {
        "k_rate": profile.k_rate,
        "bb_rate": profile.bb_rate,
        "hr_per_9": profile.hr_per_9,
    }
    for name, value in values.items():
        if value is None or isinstance(value, bool) or not math.isfinite(float(value)):
            raise PitcherMatchupGateError(f"pitcher {name} is missing or nonfinite")
        if name in {"k_rate", "bb_rate"} and not 0.0 <= float(value) <= 1.0:
            raise PitcherMatchupGateError(f"pitcher {name} is outside [0, 1]")
        if name == "hr_per_9" and float(value) < 0.0:
            raise PitcherMatchupGateError("pitcher hr_per_9 cannot be negative")
        field = fields[name]
        if not isinstance(field, Mapping) or set(field) != {
            "numerator", "denominator", "sample_count", "value_sha256"
        }:
            raise PitcherMatchupGateError(f"pitcher {name} lineage is incomplete")
        numerator = field.get("numerator")
        denominator = field.get("denominator")
        sample_count = field.get("sample_count")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in (numerator, denominator)):
            raise PitcherMatchupGateError(f"pitcher {name} counts must be numeric")
        if float(numerator) < 0.0 or float(denominator) <= 0.0:
            raise PitcherMatchupGateError(f"pitcher {name} counts are invalid")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count <= 0:
            raise PitcherMatchupGateError(f"pitcher {name} sample_count is invalid")
        expected = (
            float(numerator) * 9.0 / float(denominator)
            if name == "hr_per_9"
            else float(numerator) / float(denominator)
        )
        if not math.isclose(expected, float(value), rel_tol=0.0, abs_tol=1e-12):
            raise PitcherMatchupGateError(f"pitcher {name} contradicts its counts")
        if _hash(field.get("value_sha256"), f"pitcher {name} value_sha256") != sha256_feature_value(value):
            raise PitcherMatchupGateError(f"pitcher {name} value hash mismatch")
        # ``source_row_count`` describes the immutable source table.  Each
        # field's sample count has a different baseball denominator (BF or
        # innings/outs), so comparing it numerically with row count would be a
        # false integrity check.  The exact counts, value hashes, source hash,
        # and date-set hash are the binding contract instead.


@dataclass(frozen=True)
class ResolvedPitcherProbabilityInputs:
    pitcher_k_pct: float
    pitcher_bb_pct: float
    pitcher_hr_per_9: float | None
    handedness_advantage: float
    bvp_ops_factor: float
    bvp_hr_factor: float
    status: str


def resolve_pitcher_probability_inputs(
    bundle: PlayerFeatureBundle,
    *,
    mode: str,
    league: LeagueBaselines,
) -> ResolvedPitcherProbabilityInputs:
    selected = validate_mode(mode)
    if selected == LEGACY_MODE:
        profile = bundle.pitcher_statcast
        return ResolvedPitcherProbabilityInputs(
            pitcher_k_pct=(
                float(profile.k_rate) * 100.0
                if profile is not None and profile.k_rate is not None
                else league.k_pct
            ),
            pitcher_bb_pct=(
                float(profile.bb_rate) * 100.0
                if profile is not None and profile.bb_rate is not None
                else league.bb_pct
            ),
            pitcher_hr_per_9=(
                float(profile.hr_per_9)
                if profile is not None and profile.hr_per_9 is not None
                else None
            ),
            handedness_advantage=bundle.matchup.platoon_advantage,
            bvp_ops_factor=bundle.matchup.bvp_ops_factor,
            bvp_hr_factor=bundle.matchup.bvp_hr_factor,
            status="legacy_frozen",
        )

    raw_authorization = bundle.metadata.get("pitcher_matchup_authorization")
    if raw_authorization is None:
        return ResolvedPitcherProbabilityInputs(
            league.k_pct, league.bb_pct, None, 0.0, 1.0, 1.0,
            "excluded_missing_receipt",
        )
    if not isinstance(raw_authorization, Mapping):
        raise PitcherMatchupGateError("pitcher authorization metadata must be an object")
    authorization = PitcherMatchupAuthorization.from_mapping(raw_authorization)
    authorization.validate_hitter(bundle.hitter)
    if bundle.hitter.opposing_pitcher_id != authorization.expected_pitcher_id:
        raise PitcherMatchupGateError("bundle opposing pitcher differs from receipt identity")
    profile = bundle.pitcher_statcast
    lineage = bundle.metadata.get("pitcher_profile_lineage")
    if profile is None and lineage is None:
        return ResolvedPitcherProbabilityInputs(
            league.k_pct, league.bb_pct, None, 0.0, 1.0, 1.0,
            "excluded_profile_unavailable",
        )
    if profile is None or not isinstance(lineage, Mapping):
        raise PitcherMatchupGateError("pitcher profile and lineage must be present together")
    validate_pitcher_profile_lineage(
        profile=profile, lineage=lineage, authorization=authorization
    )
    return ResolvedPitcherProbabilityInputs(
        pitcher_k_pct=float(profile.k_rate) * 100.0,
        pitcher_bb_pct=float(profile.bb_rate) * 100.0,
        pitcher_hr_per_9=float(profile.hr_per_9),
        handedness_advantage=0.0,
        bvp_ops_factor=1.0,
        bvp_hr_factor=1.0,
        status="receipt_and_profile_verified",
    )
