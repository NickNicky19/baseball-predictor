"""
Rule 1/9: BEFORE designing a per-slot PA fit, verify there are enough rows.
A 'fitted' distribution on 40 rows is noise wearing a fit's clothes.

Also checks a thing I have NOT verified and would otherwise assume: that
lineup_slot in the training set is populated at all, and that out_pa varies
by slot the way the 2025-06-27 crosstab suggested (n=261, one date -- far too
thin to design against).
"""
import pandas as pd

tr = pd.read_csv(r"data\training\training_hitters_2023_2026.csv.gz", low_memory=False)
print(f"training rows: {len(tr):,}")
print(f"  with out_pa      : {tr.out_pa.notna().sum():,}")
print(f"  with lineup_slot : {tr.lineup_slot.notna().sum():,}")

d = tr[(tr.out_pa.notna()) & (tr.out_pa > 0) & (tr.lineup_slot.between(1, 9))].copy()
print(f"  usable (slot 1-9, out_pa>0): {len(d):,}")

print("\nrows per lineup_slot:")
print(d.lineup_slot.value_counts().sort_index().to_string())

print("\nout_pa distribution BY SLOT (row-normalized share):")
ct = pd.crosstab(d.lineup_slot, d.out_pa, normalize="index")
print(ct.round(4).to_string())

print("\nmean / sd / n of out_pa by slot:")
print(d.groupby("lineup_slot").out_pa.agg(["mean", "std", "count"]).round(3).to_string())

print("\nPOOLED out_pa distribution (all slots):")
print((d.out_pa.value_counts(normalize=True).sort_index()).round(4).to_string())

print("\nSANITY (rule 7 -- stated before reading):")
print("  expect ~5,000-15,000 rows per slot across 2023-2026")
print("  expect mean out_pa to DECREASE monotonically from slot 1 to slot 9")
print("  expect slot 1 mean ~4.5, slot 9 mean ~3.9")
print("  if any slot has < 500 rows, do NOT fit that slot separately -- pool it")
