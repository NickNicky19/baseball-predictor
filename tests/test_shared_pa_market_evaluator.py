from __future__ import annotations

import copy
import json
import math
import re
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import check_shared_pa_market_evaluator_offline as offline_checker
from src.evaluation import shared_pa_market_evaluator as evaluator


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/shared_pa_market_evaluation_contract_v1.json"


class _SchemaFailure(AssertionError):
    pass


def _json_equal(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _stdlib_schema_validate(instance, schema: dict, root_schema: dict) -> None:
    """Tiny Draft-2020-12 subset used only to test these shipped schemas.

    jsonschema is present on the developer machine but is not declared by this
    release, so the component must not silently depend on it.
    """
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/"):
            raise _SchemaFailure("only local schema references are permitted")
        target = root_schema
        for token in reference[2:].split("/"):
            target = target[token.replace("~1", "/").replace("~0", "~")]
        _stdlib_schema_validate(instance, target, root_schema)
    for child in schema.get("allOf", []):
        _stdlib_schema_validate(instance, child, root_schema)
    if "anyOf" in schema:
        if not any(_schema_branch_accepts(instance, child, root_schema) for child in schema["anyOf"]):
            raise _SchemaFailure("anyOf accepted no branches")
    if "oneOf" in schema:
        accepted = 0
        for child in schema["oneOf"]:
            try:
                _stdlib_schema_validate(instance, child, root_schema)
            except _SchemaFailure:
                continue
            accepted += 1
        if accepted != 1:
            raise _SchemaFailure(f"oneOf accepted {accepted} branches")
    if "if" in schema:
        try:
            _stdlib_schema_validate(instance, schema["if"], root_schema)
        except _SchemaFailure:
            branch = schema.get("else")
        else:
            branch = schema.get("then")
        if branch is not None:
            _stdlib_schema_validate(instance, branch, root_schema)
    if "const" in schema and not _json_equal(instance, schema["const"]):
        raise _SchemaFailure("const differs")
    if "enum" in schema and not any(_json_equal(instance, value) for value in schema["enum"]):
        raise _SchemaFailure("enum differs")
    expected_type = schema.get("type")
    if expected_type is not None:
        names = expected_type if isinstance(expected_type, list) else [expected_type]
        checks = {
            "null": instance is None,
            "boolean": isinstance(instance, bool),
            "integer": isinstance(instance, int) and not isinstance(instance, bool),
            "number": isinstance(instance, (int, float)) and not isinstance(instance, bool) and math.isfinite(float(instance)),
            "string": isinstance(instance, str),
            "array": isinstance(instance, list),
            "object": isinstance(instance, dict),
        }
        if not any(checks[name] for name in names):
            raise _SchemaFailure("type differs")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise _SchemaFailure("below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise _SchemaFailure("above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise _SchemaFailure("below exclusive minimum")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            raise _SchemaFailure("above exclusive maximum")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise _SchemaFailure("string is too short")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise _SchemaFailure("pattern differs")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0) or len(instance) > schema.get("maxItems", len(instance)):
            raise _SchemaFailure("array length differs")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in instance}) != len(instance):
            raise _SchemaFailure("array items are not unique")
        if isinstance(schema.get("items"), dict):
            for item in instance:
                _stdlib_schema_validate(item, schema["items"], root_schema)
    if isinstance(instance, dict):
        required = schema.get("required", [])
        if any(key not in instance for key in required):
            raise _SchemaFailure("required property is missing")
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        for key, value in instance.items():
            matched = False
            if key in properties:
                _stdlib_schema_validate(value, properties[key], root_schema)
                matched = True
            for pattern, child in patterns.items():
                if re.search(pattern, key):
                    _stdlib_schema_validate(value, child, root_schema)
                    matched = True
            additional = schema.get("additionalProperties", True)
            if not matched and additional is False:
                raise _SchemaFailure("additional property is forbidden")
            if not matched and isinstance(additional, dict):
                _stdlib_schema_validate(value, additional, root_schema)
        if len(instance) < schema.get("minProperties", 0):
            raise _SchemaFailure("object has too few properties")
        if "propertyNames" in schema:
            for key in instance:
                _stdlib_schema_validate(key, schema["propertyNames"], root_schema)


def _schema_branch_accepts(instance, schema: dict, root_schema: dict) -> bool:
    try:
        _stdlib_schema_validate(instance, schema, root_schema)
    except _SchemaFailure:
        return False
    return True


def _count_distribution(probability: float, line: str) -> dict[str, float]:
    over_count = int(Decimal(line)) + 1
    return {str(count): (1.0 - probability if count == 0 else probability if count == over_count else 0.0)
            for count in range(over_count + 1)}


def _baseline(probability: float, market: str, line: str) -> dict:
    return {
        "available": True,
        "probability": probability,
        "artifact_sha256": "a" * 64,
        "probability_scale": "total_bases_count_distribution_v1" if market == "total_bases" else "binary_market_tail_v1",
        "distribution": _count_distribution(probability, line) if market == "total_bases" else None,
    }


def _market(probability: float | None = 0.22) -> dict:
    identity_fields = {
        "probability_scale": None,
        "official_game_pk": None,
        "player_id": None,
        "market": None,
        "source_id": "synthetic-market-source",
        "product_contract_id": "synthetic-product-contract",
        "availability_observed_at_utc": None,
        "availability_receipt_sha256": None,
    }
    if probability is None:
        return {
            "available": False,
            "probability": None,
            "unavailable_reason": "TERMINAL_SOURCE_UNAVAILABLE",
            "sportsbook": "verified-book",
            "source_market_id": None,
            "receipt_sha256": None,
            "quote_observed_at_utc": None,
            "line": None,
            "over_decimal_price": None,
            "under_decimal_price": None,
            "no_vig_method": None,
            **identity_fields,
        }
    value = {
        "available": True,
        "probability": probability,
        "unavailable_reason": None,
        "sportsbook": "verified-book",
        "source_market_id": "source-market-1",
        "receipt_sha256": "b" * 64,
        "quote_observed_at_utc": "2026-07-28T11:00:00Z",
        "line": "0.5",
        "over_decimal_price": 1.0 / probability,
        "under_decimal_price": 1.0 / (1.0 - probability),
        "no_vig_method": "normalized_inverse_decimal_v1",
        "probability_scale": "binary_market_tail_v1",
        "official_game_pk": 0,
        "player_id": 0,
        "market": "",
        "source_id": "synthetic-market-source",
        "product_contract_id": "synthetic-product-contract",
        "availability_observed_at_utc": None,
        "availability_receipt_sha256": None,
    }
    return value


def _row(
    *,
    market: str = "hr_over_0_5",
    date: str = "2026-07-28",
    game: int = 100,
    player: int = 200,
    outcome: int = 0,
    settlement_status: str = "GRADED",
    outcome_value: int | None = None,
    candidate: float | None = 0.20,
    market_probability: float | None = 0.22,
    line: str = "0.5",
) -> dict:
    market_block = _market(market_probability)
    market_block["product_contract_id"] = f"synthetic-{market}-over-contract"
    market_block["line"] = line
    market_block["official_game_pk"] = game
    market_block["player_id"] = player
    market_block["market"] = market
    market_block["probability_scale"] = "binary_market_tail_v1"
    if market_probability is not None:
        market_block["quote_observed_at_utc"] = f"{date}T11:00:00Z"
    else:
        market_block["availability_observed_at_utc"] = f"{date}T11:00:00Z"
        market_block["availability_receipt_sha256"] = "d" * 64
    return {
        "official_game_date": date,
        "official_game_pk": game,
        "player_id": player,
        "market": market,
        "line": line,
        "settlement_status": settlement_status,
        "outcome_value": outcome_value if outcome_value is not None else (int(Decimal(line)) + 1 if outcome else max(0, int(Decimal(line)))),
        "prediction_created_at_utc": f"{date}T10:00:00Z",
        "decision_horizon_utc": f"{date}T12:00:00Z",
        "scheduled_start_utc": f"{date}T16:00:00Z",
        "outcome_observed_at_utc": f"{date}T23:00:00Z",
        "evidence_window_id": "synthetic_test_only",
        "candidate_available": candidate is not None,
        "candidate_probability": candidate,
        "candidate_distribution": _count_distribution(candidate, line) if market == "total_bases" and candidate is not None else None,
        "abstention_reason": None if candidate is not None else "MISSING_POINT_IN_TIME_INPUT",
        "baselines": {
            "league_rate": _baseline(0.18, market, line),
            "player_time_safe_eb": _baseline(0.19, market, line),
            "frozen_simulator": _baseline(0.21, market, line),
            "valid_market_implied": market_block,
        },
    }


def _rows() -> list[dict]:
    rows = []
    for date_index, official in enumerate(("2026-07-27", "2026-07-28", "2026-07-29")):
        for game_offset in (0, 1):
            game = 100 + date_index * 10 + game_offset
            rows.extend([
                _row(date=official, game=game, player=game * 10 + 1, market="hits", line="0.5", candidate=0.68, outcome=(game + 1) % 2, market_probability=0.64),
                _row(date=official, game=game, player=game * 10 + 2, market="hr_over_0_5", candidate=0.12 + 0.05 * game_offset, outcome=game_offset, market_probability=0.14),
                _row(date=official, game=game, player=game * 10 + 3, market="total_bases", line="1.5", candidate=0.44, outcome=date_index % 2, market_probability=None if game_offset else 0.46),
            ])
    return rows


def _contract() -> dict:
    return evaluator.load_contract(CONTRACT_PATH)


def _product(report: dict, market: str, line: str = "0.5") -> dict:
    return report["markets"][market]["products"][f"over_{line}"]


def _receipt_payload(row: dict) -> tuple[str, dict]:
    market = row["baselines"]["valid_market_implied"]
    common = {
        "protocol_id": evaluator.SOURCE_RECEIPT_PROTOCOL,
        "receipt_kind": "AVAILABLE_QUOTE_PAIR" if market["available"] else "TERMINAL_UNAVAILABLE_QUERY",
        "source_id": market["source_id"],
        "sportsbook": market["sportsbook"],
        "product_contract_id": market["product_contract_id"],
        "official_game_date": row["official_game_date"],
        "official_game_pk": market["official_game_pk"],
        "player_id": market["player_id"],
        "market": market["market"],
        "line": market["line"],
    }
    if market["available"]:
        observed = market["quote_observed_at_utc"]
        return "AVAILABLE_QUOTE_PAIR", {
            "schema_version": evaluator.AVAILABLE_RECEIPT_SCHEMA,
            **common,
            "source_market_id": market["source_market_id"],
            "quote_observed_at_utc": observed,
            "quotes": [
                {"side": "over", "decimal_price": market["over_decimal_price"], "observed_at_utc": observed},
                {"side": "under", "decimal_price": market["under_decimal_price"], "observed_at_utc": observed},
            ],
        }
    return "TERMINAL_UNAVAILABLE_QUERY", {
        "schema_version": evaluator.UNAVAILABLE_RECEIPT_SCHEMA,
        **common,
        "availability_observed_at_utc": market["availability_observed_at_utc"],
        "unavailable_reason": market["unavailable_reason"],
    }


def _source_authority(rows: list[dict], root: Path) -> evaluator.VerifiedSourceAuthority:
    receipt_dir = root / "receipts"
    receipt_dir.mkdir()
    manifest_rows = []
    for index, row in enumerate(rows):
        kind, payload = _receipt_payload(row)
        receipt_bytes = evaluator.canonical_bytes(payload)
        digest = evaluator.sha256_bytes(receipt_bytes)
        relative = f"receipts/{index:04d}.json"
        (root / relative).write_bytes(receipt_bytes)
        field = "receipt_sha256" if kind == "AVAILABLE_QUOTE_PAIR" else "availability_receipt_sha256"
        row["baselines"]["valid_market_implied"][field] = digest
        manifest_rows.append({"receipt_sha256": digest, "relative_path": relative, "receipt_kind": kind})
    manifest = {
        "schema_version": evaluator.SOURCE_AUTHORITY_SCHEMA,
        "authority_id": "synthetic-structural-authority-v1",
        "authority_state": evaluator.SYNTHETIC_SOURCE_AUTHORITY,
        "protocol_id": evaluator.SOURCE_RECEIPT_PROTOCOL,
        "evidence_window_id": "synthetic_test_only",
        "receipts": manifest_rows,
    }
    manifest_path = root / "source_authority.json"
    manifest_path.write_bytes(evaluator.canonical_bytes(manifest))
    return evaluator.load_source_authority(
        root=root,
        manifest_relative_path="source_authority.json",
        expected_manifest_sha256=evaluator.sha256_file(manifest_path),
    )


def _evaluate(rows: list[dict], contract: dict | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="shared-pa-evaluator-") as temp:
        authority = _source_authority(rows, Path(temp))
        return evaluator.evaluate_market_separated(
            rows,
            contract or _contract(),
            source_authority=authority,
            allow_unbound_synthetic_test_only=True,
        )


def _evaluate_against(rows: list[dict], authority_rows: list[dict]) -> dict:
    with tempfile.TemporaryDirectory(prefix="shared-pa-evaluator-") as temp:
        authority = _source_authority(authority_rows, Path(temp))
        for row, authority_row in zip(rows, authority_rows, strict=True):
            market = row["baselines"]["valid_market_implied"]
            authority_market = authority_row["baselines"]["valid_market_implied"]
            field = "receipt_sha256" if market["available"] else "availability_receipt_sha256"
            if market.get(field) in {"b" * 64, "d" * 64}:
                market[field] = authority_market[field]
        return evaluator.evaluate_market_separated(
            rows,
            _contract(),
            source_authority=authority,
            allow_unbound_synthetic_test_only=True,
        )


def test_contract_is_unbound_and_preserves_capture_lower_bound() -> None:
    contract = _contract()
    assert contract["status"] == evaluator.UNBOUND
    assert contract["economic_boundary"]["capture_lower_bound_minimum"] == 0.10
    assert contract["protected_boundaries"]["betting_authorized"] is False


def test_authoritative_evaluation_is_refused_without_external_authorities() -> None:
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="UNBOUND"):
        evaluator.evaluate_market_separated(_rows(), _contract())
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="not implemented"):
        evaluator.evaluate_market_separated(_rows(), _contract(), external_authority={"pretend": True})


def test_market_reports_are_separate_deterministic_and_never_pooled() -> None:
    first = _evaluate(_rows())
    second = _evaluate(_rows())
    assert first == second
    assert first["market_order"] == list(evaluator.MARKETS)
    assert first["pooled_summary"] is None
    assert first["cross_market_rescue"] is False
    assert first["capture_lower_bound_minimum"] == 0.10
    assert set(first["markets"]) == set(evaluator.MARKETS)
    for market in evaluator.MARKETS:
        assert first["markets"][market]["market"] == market
        assert first["markets"][market]["qualification"].startswith("REFUSED")
        assert first["markets"][market]["pooled_across_products"] is None


def test_metrics_calibration_discrimination_and_hr_tail_are_reported() -> None:
    report = _evaluate(_rows())
    hr = _product(report, "hr_over_0_5")
    assert hr["candidate_metrics"]["count"] == 6
    assert hr["candidate_metrics"]["brier"] > 0
    assert hr["candidate_metrics"]["log_loss"] > 0
    assert len(hr["candidate_metrics"]["calibration"]["bins"]) == 10
    assert hr["candidate_metrics"]["calibration"]["intercept_slope"]["status"] in {
        "OK", "UNIDENTIFIABLE_OR_SEPARATED", "NONCONVERGED",
    }
    assert hr["candidate_metrics"]["discrimination"]["status"] == "OK"
    assert [row["threshold"] for row in hr["hr_upper_tail"]["candidate"]] == [0.1, 0.2, 0.3]
    assert set(hr["hr_upper_tail"]["baselines"]) == set(evaluator.BASELINES)
    assert _product(report, "hits")["hr_upper_tail"] is None


def test_date_and_game_cluster_intervals_keep_correlated_rows_together() -> None:
    report = _evaluate(_rows())
    comparison = _product(report, "hits")["comparisons"]["frozen_simulator"]
    assert comparison["date_cluster"]["brier"]["cluster_count"] == 3
    assert comparison["game_cluster"]["brier"]["cluster_count"] == 6
    assert comparison["date_cluster"]["brier"]["draws"] == 2000
    assert len(comparison["date_cluster"]["brier"]["interval"]) == 2


def test_unequal_coverage_and_abstentions_are_explicit() -> None:
    rows = _rows()
    rows[0]["candidate_available"] = False
    rows[0]["candidate_probability"] = None
    rows[0]["abstention_reason"] = "LINEUP_OPPORTUNITY_UNAVAILABLE"
    replacement = _row(
        market=rows[2]["market"], date=rows[2]["official_game_date"],
        game=rows[2]["official_game_pk"], player=rows[2]["player_id"],
        line=rows[2]["line"], market_probability=None,
    )
    rows[2]["baselines"]["valid_market_implied"] = replacement["baselines"]["valid_market_implied"]
    report = _evaluate(rows)
    hits = _product(report, "hits")
    assert hits["coverage"]["candidate_available"] == 5
    assert hits["coverage"]["candidate_abstained"] == 1
    assert hits["coverage"]["reason_coded_abstentions"] == {"LINEUP_OPPORTUNITY_UNAVAILABLE": 1}
    tb = _product(report, "total_bases", "1.5")
    assert tb["coverage"]["baseline_available"]["valid_market_implied"] < tb["coverage"]["eligible_universe"]
    assert tb["comparisons"]["valid_market_implied"]["common_support"] == tb["coverage"]["baseline_available"]["valid_market_implied"]


def test_duplicate_identity_fails_closed() -> None:
    row = _row()
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="duplicate"):
        _evaluate([row, copy.deepcopy(row)])


@pytest.mark.parametrize("field", ["official_game_pk", "player_id"])
def test_invalid_identity_fails_closed(field: str) -> None:
    row = _row()
    row[field] = 0
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="positive integer"):
        _evaluate([row])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prediction_created_at_utc", "2026-07-28T12:00:01Z"),
        ("decision_horizon_utc", "2026-07-28T16:00:00Z"),
        ("outcome_observed_at_utc", "2026-07-28T15:59:59Z"),
    ],
)
def test_chronology_mutations_fail_closed(field: str, value: str) -> None:
    row = _row()
    row[field] = value
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="chronology"):
        _evaluate([row])


@pytest.mark.parametrize("date_value", ["2026-05-01", "2026-05-31"])
def test_may_is_rejected_before_evaluation(date_value: str) -> None:
    row = _row(date=date_value)
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="May 2026"):
        _evaluate([row])


@pytest.mark.parametrize("window", ["2024_regular_season", "confirmation_2025", "Synthetic_Test_Only", "synthetic-test-only"])
def test_spent_or_alias_evidence_window_is_not_allowlisted(window: str) -> None:
    row = _row()
    row["evidence_window_id"] = window
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="exact allowlisted"):
        _evaluate([row])


def test_candidate_abstention_cannot_carry_probability_or_unknown_reason() -> None:
    row = _row(candidate=None)
    row["candidate_probability"] = 0.2
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="fabricated probability"):
        _evaluate([row])
    row = _row(candidate=None)
    row["abstention_reason"] = "OTHER"
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="canonical reason"):
        _evaluate([row])


def test_probability_clipping_is_forbidden() -> None:
    for probability in (0.0, 1.0, -0.1, 1.1):
        row = _row(candidate=probability)
        with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="clipping is forbidden"):
            _evaluate([row])


def test_required_baseline_missing_or_unbound_fails() -> None:
    row = _row()
    row["baselines"]["frozen_simulator"]["available"] = False
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="frozen_simulator is unavailable"):
        _evaluate([row])
    row = _row()
    row["baselines"]["league_rate"]["artifact_sha256"] = None
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="lowercase SHA"):
        _evaluate([row])


def test_missing_market_requires_terminal_reason_and_no_fabricated_fields() -> None:
    row = _row(market_probability=None)
    row["baselines"]["valid_market_implied"]["unavailable_reason"] = None
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="terminal unavailability reason"):
        _evaluate([row])
    row = _row(market_probability=None)
    row["baselines"]["valid_market_implied"]["source_market_id"] = "invented-quote"
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="fabricated metadata"):
        _evaluate([row])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("availability_receipt_sha256", None, "lowercase SHA"),
        ("availability_observed_at_utc", "2026-07-28T12:00:01Z", "after decision horizon"),
        ("official_game_pk", 999, "event/player/market identity"),
        ("player_id", 999, "event/player/market identity"),
        ("market", "hits", "event/player/market identity"),
        ("line", "1.5", "line identity"),
        ("source_id", "other-source", "receipt semantics"),
        ("sportsbook", "other-book", "receipt semantics"),
        ("product_contract_id", "other-product", "receipt semantics"),
        ("unavailable_reason", "NO_MATCHING_MARKET", "receipt semantics"),
    ],
)
def test_missing_market_requires_bound_prehorizon_availability_query(field: str, value, message: str) -> None:
    authority_row = _row(market_probability=None)
    row = copy.deepcopy(authority_row)
    row["baselines"]["valid_market_implied"][field] = value
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match=message):
        _evaluate_against([row], [authority_row])


def test_market_quote_line_and_chronology_are_bound() -> None:
    authority_row = _row()
    row = copy.deepcopy(authority_row)
    row["baselines"]["valid_market_implied"]["line"] = "1.5"
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="line identity"):
        _evaluate_against([row], [authority_row])
    authority_row = _row()
    row = copy.deepcopy(authority_row)
    row["baselines"]["valid_market_implied"]["quote_observed_at_utc"] = "2026-07-28T12:00:01Z"
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="after decision"):
        _evaluate_against([row], [authority_row])


def test_market_no_vig_probability_is_recomputed() -> None:
    row = _row()
    row["baselines"]["valid_market_implied"]["probability"] = 0.30
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="differs from paired no-vig"):
        _evaluate([row])


def test_hr_line_is_fixed_but_hits_products_are_kept_separate() -> None:
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="exactly over 0.5"):
        _evaluate([_row(market="hr_over_0_5", line="1.5")])
    rows = [_row(market="hits", line="0.5"), _row(market="hits", line="1.5")]
    report = _evaluate(rows)
    assert set(report["markets"]["hits"]["products"]) == {"over_0.5", "over_1.5"}
    assert report["markets"]["hits"]["pooled_across_products"] is None


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("pooling_forbidden",), False, "market separation"),
        (("cross_market_rescue_forbidden",), False, "market separation"),
        (("economic_boundary", "capture_lower_bound_minimum"), 0.09, "capture lower-bound"),
        (("protected_boundaries", "spent_2025_hr_confirmation_reusable"), True, "protected boundary"),
    ],
)
def test_policy_mutations_cannot_enable_pooling_rescue_or_lower_capture_gate(path, value, message) -> None:
    contract = _contract()
    target = contract
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match=message):
        evaluator.validate_contract(contract)


@pytest.mark.parametrize(
    "edges",
    [
        [0.0, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0],
        [0.0, 0.05, 0.1, 0.2, 0.15, 0.3, 0.4, 0.5, 0.65, 0.8, 1.0],
        [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 0.9, 1.0],
    ],
)
def test_calibration_bins_are_the_exact_locked_list(edges: list[float]) -> None:
    contract = _contract()
    contract["calibration_bin_edges"] = edges
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="exact locked list"):
        evaluator.validate_contract(contract)


def test_single_class_discrimination_is_explicit_not_fabricated() -> None:
    rows = [_row(game=100+i, player=200+i, outcome=0) for i in range(3)]
    report = _evaluate(rows)
    assert _product(report, "hr_over_0_5")["candidate_metrics"]["discrimination"] == {
        "status": "INSUFFICIENT_CLASSES", "auc": None, "positive_count": 0, "negative_count": 3,
    }
    assert _product(report, "hr_over_0_5")["candidate_metrics"]["calibration"]["intercept_slope"]["status"] == "INSUFFICIENT_CLASSES"


def test_unknown_fields_and_market_rescue_surface_fail_closed() -> None:
    row = _row()
    row["helpful_fallback"] = 0.5
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="surface changed"):
        _evaluate([row])


def test_total_bases_is_scored_as_full_distribution_and_tail() -> None:
    report = _evaluate([_row(market="total_bases", line="1.5", outcome=1)])
    product = _product(report, "total_bases", "1.5")
    full = product["candidate_metrics"]["full_distribution"]
    assert full["count"] == 1
    assert full["multiclass_brier"] >= 0
    assert full["ranked_probability_score"] >= 0
    assert full["log_loss_status"] == "OK"
    assert "full_distribution" in product["comparisons"]["frozen_simulator"]
    assert "full_distribution" not in product["comparisons"]["valid_market_implied"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"0": 0.56, "2": 0.44}, "contiguous"),
        ({"0": 0.56, "1": 0.01, "2": 0.44}, "sum exactly"),
        ({"0": 0.60, "1": 0.0, "2": 0.40}, "structural Total Bases tail"),
    ],
)
def test_total_bases_candidate_distribution_mutations_fail_closed(mutation: dict, message: str) -> None:
    row = _row(market="total_bases", line="1.5", candidate=0.44)
    row["candidate_distribution"] = mutation
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match=message):
        _evaluate([row])


def test_total_bases_baseline_scale_and_tail_mutations_fail_closed() -> None:
    row = _row(market="total_bases", line="1.5")
    row["baselines"]["frozen_simulator"]["probability_scale"] = "binary_market_tail_v1"
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="Total Bases scale"):
        _evaluate([row])
    row = _row(market="total_bases", line="1.5")
    row["baselines"]["frozen_simulator"]["distribution"] = {"0": 0.8, "1": 0.0, "2": 0.2}
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="structural Total Bases tail"):
        _evaluate([row])


@pytest.mark.parametrize("status", ["PUSH", "VOID", "UNMATCHED"])
def test_push_void_and_unmatched_are_counted_but_never_graded(status: str) -> None:
    row = _row(market="hits", line="1")
    row["settlement_status"] = status
    row["outcome_value"] = 1 if status == "PUSH" else None
    report = _evaluate([row])
    product = _product(report, "hits", "1")
    assert product["coverage"]["settlement_status"][status] == 1
    assert product["coverage"]["ungradeable"] == 1
    assert product["candidate_metrics"] is None
    assert product["comparisons"]["frozen_simulator"]["status"] == "NO_GRADED_COMMON_SUPPORT"


def test_contradictory_settlement_fails_closed() -> None:
    row = _row(market="hits", line="1")
    row["settlement_status"] = "GRADED"
    row["outcome_value"] = 1
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="ungradeable push"):
        _evaluate([row])
    row = _row()
    row["settlement_status"] = "VOID"
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="must not contain"):
        _evaluate([row])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("official_game_pk", 999, "event/player/market identity"),
        ("market", "hits", "event/player/market identity"),
        ("source_market_id", "different-market", "receipt semantics"),
        ("source_id", "different-source", "receipt semantics"),
    ],
)
def test_market_pair_identity_is_checked_against_receipt_bytes(field: str, value, message: str) -> None:
    authority_row = _row()
    row = copy.deepcopy(authority_row)
    row["baselines"]["valid_market_implied"][field] = value
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match=message):
        _evaluate_against([row], [authority_row])


def test_coverage_table_exposes_candidate_only_baseline_only_and_ungradeable() -> None:
    rows = [
        _row(game=100, player=200, candidate=0.2, market_probability=None),
        _row(game=101, player=201, candidate=None, market_probability=0.22),
        _row(game=102, player=202, candidate=0.2, market_probability=0.22),
    ]
    rows[2]["settlement_status"] = "VOID"
    rows[2]["outcome_value"] = None
    report = _evaluate(rows)
    coverage = _product(report, "hr_over_0_5")["comparisons"]["valid_market_implied"]["coverage"]
    assert coverage == {
        "eligible_universe": 3,
        "candidate_available": 2,
        "baseline_available": 2,
        "common_support_all_settlements": 1,
        "common_support_graded": 0,
        "candidate_only": 1,
        "baseline_only": 1,
        "neither": 0,
        "ungradeable_common_support": 1,
    }


def test_component_manifest_hashes_and_json_artifacts_are_exact() -> None:
    manifest = json.loads((ROOT / "config/shared_pa_market_evaluator_v1_file_manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == evaluator.UNBOUND
    assert manifest["research_only"] is True
    assert manifest["betting_authorized"] is False
    for item in manifest["files"]:
        path = ROOT / item["path"]
        assert path.stat().st_size == item["size"]
        assert evaluator.sha256_file(path) == item["sha256"]
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))


def test_float_lines_fail_and_nearby_decimal_products_cannot_collide() -> None:
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="canonical nonnegative decimal string"):
        _evaluate([_row(market="hits", line=0.5)])
    rows = [
        _row(market="hits", game=301, player=401, line="0.5"),
        _row(market="hits", game=301, player=401, line="0.5000000000000001"),
    ]
    report = _evaluate(rows)
    assert set(report["markets"]["hits"]["products"]) == {"over_0.5", "over_0.5000000000000001"}


def test_terminal_unavailability_fake_digest_cannot_substitute_for_receipt_bytes() -> None:
    authority_row = _row(market_probability=None)
    row = copy.deepcopy(authority_row)
    row["baselines"]["valid_market_implied"]["availability_receipt_sha256"] = "e" * 64
    with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="absent or has the wrong typed kind"):
        _evaluate_against([row], [authority_row])


def test_source_authority_rehashes_referenced_receipt_bytes() -> None:
    with tempfile.TemporaryDirectory(prefix="shared-pa-authority-") as temp:
        root = Path(temp)
        row = _row()
        authority = _source_authority([row], root)
        receipt = root / "receipts/0000.json"
        receipt.write_bytes(receipt.read_bytes() + b" ")
        with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="bytes differ"):
            evaluator.load_source_authority(
                root=root,
                manifest_relative_path="source_authority.json",
                expected_manifest_sha256=authority.manifest_sha256,
            )


def test_source_authority_rejects_semantically_malformed_quote_pair() -> None:
    with tempfile.TemporaryDirectory(prefix="shared-pa-authority-") as temp:
        root = Path(temp)
        row = _row()
        _source_authority([row], root)
        receipt_path = root / "receipts/0000.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["quotes"][1]["observed_at_utc"] = "2026-07-28T11:00:01Z"
        receipt_path.write_bytes(evaluator.canonical_bytes(receipt))
        digest = evaluator.sha256_file(receipt_path)
        manifest_path = root / "source_authority.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["receipts"][0]["receipt_sha256"] = digest
        manifest_path.write_bytes(evaluator.canonical_bytes(manifest))
        with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="sides or timestamps"):
            evaluator.load_source_authority(
                root=root,
                manifest_relative_path="source_authority.json",
                expected_manifest_sha256=evaluator.sha256_file(manifest_path),
            )


def test_source_receipt_may_date_is_rejected_before_synthetic_evaluation() -> None:
    with tempfile.TemporaryDirectory(prefix="shared-pa-authority-") as temp:
        with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="May 2026"):
            _source_authority([_row(date="2026-05-14")], Path(temp))


def test_source_authority_rejects_parent_traversal() -> None:
    with tempfile.TemporaryDirectory(prefix="shared-pa-authority-") as temp:
        root = Path(temp)
        outside = root.parent / f"{root.name}-outside.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            manifest = {
                "schema_version": evaluator.SOURCE_AUTHORITY_SCHEMA,
                "authority_id": "synthetic-structural-authority-v1",
                "authority_state": evaluator.SYNTHETIC_SOURCE_AUTHORITY,
                "protocol_id": evaluator.SOURCE_RECEIPT_PROTOCOL,
                "evidence_window_id": "synthetic_test_only",
                "receipts": [{
                    "receipt_sha256": evaluator.sha256_file(outside),
                    "relative_path": f"../{outside.name}",
                    "receipt_kind": "AVAILABLE_QUOTE_PAIR",
                }],
            }
            manifest_path = root / "source_authority.json"
            manifest_path.write_bytes(evaluator.canonical_bytes(manifest))
            with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="unsafe"):
                evaluator.load_source_authority(
                    root=root,
                    manifest_relative_path="source_authority.json",
                    expected_manifest_sha256=evaluator.sha256_file(manifest_path),
                )
        finally:
            outside.unlink(missing_ok=True)


def test_source_authority_rejects_reparse_ancestor_mutation(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(prefix="shared-pa-authority-") as temp:
        root = Path(temp)
        row = _row()
        authority = _source_authority([row], root)
        original = evaluator._is_link_or_reparse

        def injected_reparse(path: Path) -> bool:
            return path == root.absolute() / "receipts" or original(path)

        monkeypatch.setattr(evaluator, "_is_link_or_reparse", injected_reparse)
        with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="symlink, junction, or reparse"):
            evaluator.load_source_authority(
                root=root,
                manifest_relative_path="source_authority.json",
                expected_manifest_sha256=authority.manifest_sha256,
            )


def test_offline_manifest_rejects_path_escape_before_hash_acceptance() -> None:
    with tempfile.TemporaryDirectory(prefix="shared-pa-manifest-") as temp:
        root = Path(temp)
        (root / "config").mkdir()
        outside = root.parent / f"{root.name}-outside-component.txt"
        outside.write_text("not a component", encoding="utf-8")
        manifest = {
            "schema_version": "shared-pa-market-evaluator-file-manifest-v1",
            "component_id": "shared_pa_market_evaluator_v1",
            "state": evaluator.UNBOUND,
            "source_base_commit": "9f86f6a34fc71151b76debf48059829aa4fa91da",
            "research_only": True,
            "betting_authorized": False,
            "files": [{"path": f"../{outside.name}", "size": outside.stat().st_size, "sha256": evaluator.sha256_file(outside), "purpose": "escape"}],
        }
        (root / "config/shared_pa_market_evaluator_v1_file_manifest.json").write_bytes(evaluator.canonical_bytes(manifest))
        try:
            with pytest.raises(evaluator.SharedPAMarketEvaluationError, match="unsafe"):
                offline_checker.validate_component_manifest(root)
        finally:
            outside.unlink(missing_ok=True)


def test_nested_schemas_fail_closed_on_changed_surfaces() -> None:
    contract_schema = json.loads((ROOT / "config/schemas/shared_pa_market_evaluation_contract_v1.schema.json").read_text(encoding="utf-8"))
    row_schema = json.loads((ROOT / "config/schemas/shared_pa_market_evaluation_row_v1.schema.json").read_text(encoding="utf-8"))
    report_schema = json.loads((ROOT / "config/schemas/shared_pa_market_evaluation_report_v1.schema.json").read_text(encoding="utf-8"))
    assert contract_schema["properties"]["protected_boundaries"]["additionalProperties"] is False
    assert contract_schema["properties"]["uncertainty"]["additionalProperties"] is False
    assert row_schema["$defs"]["binaryBaselines"]["additionalProperties"] is False
    assert row_schema["$defs"]["totalBasesBaselines"]["additionalProperties"] is False
    assert row_schema["$defs"]["marketAvailable"]["additionalProperties"] is False
    assert report_schema["properties"]["markets"]["additionalProperties"] is False
    assert report_schema["$defs"]["product"]["additionalProperties"] is False


def _schema(name: str) -> dict:
    return json.loads((ROOT / f"config/schemas/{name}").read_text(encoding="utf-8"))


def _assert_schema_accepts(instance: object, schema: dict) -> None:
    _stdlib_schema_validate(instance, schema, schema)


def _assert_schema_rejects(instance: object, schema: dict) -> None:
    with pytest.raises(_SchemaFailure):
        _stdlib_schema_validate(instance, schema, schema)


def test_row_schema_accepts_runtime_valid_market_specific_rows_and_exact_epsilon_bounds() -> None:
    schema = _schema("shared_pa_market_evaluation_row_v1.schema.json")
    for market, line in (("hits", "0.5"), ("hr_over_0_5", "0.5"), ("total_bases", "1.5")):
        _assert_schema_accepts(_row(market=market, line=line), schema)
        _assert_schema_accepts(_row(market=market, line=line, candidate=None, market_probability=None), schema)
    for boundary in (1e-12, 1.0 - 1e-12):
        row = _row(candidate=boundary)
        _assert_schema_accepts(row, schema)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(candidate_probability=-1.0),
        lambda row: row.update(candidate_probability=0.0),
        lambda row: row.update(candidate_probability=0.5e-12),
        lambda row: row.update(candidate_probability=1.0 - 0.5e-12),
        lambda row: row.update(candidate_probability=1.0),
        lambda row: row.update(abstention_reason="MISSING_POINT_IN_TIME_INPUT"),
        lambda row: row["baselines"]["league_rate"].update(probability=-1.0),
        lambda row: row["baselines"]["valid_market_implied"].update(probability=0.0),
        lambda row: row["baselines"].update(unknown_baseline=copy.deepcopy(row["baselines"]["league_rate"])),
    ],
)
def test_row_schema_rejects_probability_availability_and_baseline_mutations(mutation) -> None:
    schema = _schema("shared_pa_market_evaluation_row_v1.schema.json")
    row = _row()
    mutation(row)
    _assert_schema_rejects(row, schema)


def test_row_schema_rejects_market_distribution_and_settlement_condition_mutations() -> None:
    schema = _schema("shared_pa_market_evaluation_row_v1.schema.json")
    mutations: list[dict] = []

    unavailable = _row(candidate=None, market_probability=None)
    unavailable["candidate_probability"] = 0.2
    mutations.append(unavailable)
    unavailable_distribution = _row(candidate=None, market_probability=None)
    unavailable_distribution["candidate_distribution"] = {"0": 1.0}
    mutations.append(unavailable_distribution)
    unavailable_reason = _row(candidate=None, market_probability=None)
    unavailable_reason["abstention_reason"] = None
    mutations.append(unavailable_reason)

    binary_distribution = _row(market="hits")
    binary_distribution["candidate_distribution"] = {"0": 0.5, "1": 0.5}
    mutations.append(binary_distribution)
    total_bases_missing_distribution = _row(market="total_bases", line="1.5")
    total_bases_missing_distribution["candidate_distribution"] = None
    mutations.append(total_bases_missing_distribution)
    binary_with_tb_baseline = _row(market="hits")
    binary_with_tb_baseline["baselines"]["league_rate"] = _baseline(0.2, "total_bases", "1.5")
    mutations.append(binary_with_tb_baseline)
    tb_with_binary_baseline = _row(market="total_bases", line="1.5")
    tb_with_binary_baseline["baselines"]["league_rate"] = _baseline(0.2, "hits", "0.5")
    mutations.append(tb_with_binary_baseline)

    wrong_hr_line = _row(line="1.5")
    mutations.append(wrong_hr_line)
    void_with_outcome = _row()
    void_with_outcome["settlement_status"] = "VOID"
    mutations.append(void_with_outcome)
    graded_without_outcome = _row()
    graded_without_outcome["outcome_value"] = None
    mutations.append(graded_without_outcome)

    for mutation in mutations:
        _assert_schema_rejects(mutation, schema)


def test_report_schema_accepts_runtime_report_and_rejects_authority_surface_mutations() -> None:
    schema = _schema("shared_pa_market_evaluation_report_v1.schema.json")
    report = _evaluate(_rows())
    _assert_schema_accepts(report, schema)

    mutations = []
    wrong_metric = copy.deepcopy(report)
    wrong_metric["markets"]["hits"]["products"]["over_0.5"]["candidate_metrics"]["brier"] = "0.1"
    mutations.append(wrong_metric)
    extra_metric = copy.deepcopy(report)
    extra_metric["markets"]["hits"]["products"]["over_0.5"]["candidate_metrics"]["invented"] = 1
    mutations.append(extra_metric)
    extra_comparator = copy.deepcopy(report)
    extra_comparator["markets"]["hits"]["products"]["over_0.5"]["comparisons"]["invented"] = {}
    mutations.append(extra_comparator)
    bad_reason = copy.deepcopy(report)
    bad_reason["markets"]["hits"]["products"]["over_0.5"]["coverage"]["reason_coded_abstentions"]["UNKNOWN"] = 1
    mutations.append(bad_reason)
    bad_interval = copy.deepcopy(report)
    bad_interval["markets"]["hits"]["products"]["over_0.5"]["comparisons"]["league_rate"]["date_cluster"]["brier"]["draws"] = 0
    mutations.append(bad_interval)
    bad_calibration = copy.deepcopy(report)
    bad_calibration["markets"]["hits"]["products"]["over_0.5"]["candidate_metrics"]["calibration"]["bins"][0]["observed_rate"] = 2.0
    mutations.append(bad_calibration)
    bad_auc = copy.deepcopy(report)
    bad_auc["markets"]["hits"]["products"]["over_0.5"]["candidate_metrics"]["discrimination"]["auc"] = 2.0
    mutations.append(bad_auc)
    missing_tb_distribution_metric = copy.deepcopy(report)
    del missing_tb_distribution_metric["markets"]["total_bases"]["products"]["over_1.5"]["candidate_metrics"]["full_distribution"]["ranked_probability_score"]
    mutations.append(missing_tb_distribution_metric)

    for mutation in mutations:
        _assert_schema_rejects(mutation, schema)
