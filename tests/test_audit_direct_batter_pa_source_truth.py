from __future__ import annotations

import pandas as pd

from scripts.audit_direct_batter_pa_source_truth import reconcile
from src.features.direct_batter_pa_history import OUTCOMES


def official()->pd.DataFrame:
    row={"game_pk":1,"player_id":7,**{f"target_{name}":0 for name in OUTCOMES}}
    row["target_single"]=1; row["target_bip_out"]=2
    return pd.DataFrame([row])


def test_exact_source_truth_reconciliation()->None:
    raw={"game_pk":1,"player_id":7,**{f"raw_{name}":0 for name in OUTCOMES}}
    raw["raw_single"]=1; raw["raw_bip_out"]=2
    _,mismatch=reconcile(official(),pd.DataFrame([raw])); assert mismatch.empty


def test_mutation_wrong_denominator_is_detected()->None:
    raw={"game_pk":1,"player_id":7,**{f"raw_{name}":0 for name in OUTCOMES}}
    raw["raw_single"]=1; raw["raw_bip_out"]=1
    _,mismatch=reconcile(official(),pd.DataFrame([raw])); assert len(mismatch)==1
