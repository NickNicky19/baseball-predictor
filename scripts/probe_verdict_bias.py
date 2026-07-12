"""
Is the `hits` TIE real, or an artifact of a biased subsample?

The verdict matched only 1012/3786 hits rows (27%). If the matched rows are
systematically different from the full set -- e.g. only star players carry
two-sided bet365 lines -- then the model was scored on its easy cases.

RED FLAG that prompted this: the matched base rate is 0.643, but we measured
actual P(hits>=1) at 0.563 on the gate rows and 0.612 on pa>=2. 0.643 is HIGHER
than both. Either June was a high-hit month, or the subsample skews good.

Also: bet365 VOIDS pa==1 (DK's rule; assume bet365 similar). Those rows are
being scored as losses here. That is the grading bug, now inside the market
comparison.
"""
import unicodedata
import pandas as pd


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    return " ".join(p for p in s.split()
                    if p not in ("jr", "sr", "ii", "iii", "iv", "v"))


sim = pd.read_csv(r"data\market\sim_probs_2026-06.csv")
cl = pd.read_parquet(r"data\market\closes_2026-06.parquet")
tr = pd.read_csv(r"data\training\training_hitters_2023_2026.csv.gz", low_memory=False)

bridge = (tr[["player_id", "game_date", "player_name"]]
          .dropna().drop_duplicates(subset=["player_id", "game_date"]))
pa = tr[["player_id", "game_date", "out_pa", "out_hits"]].dropna()

s = (sim[(sim.category == "hits") & (sim.line == 0.5)]
     .merge(bridge, on=["player_id", "game_date"], how="left"))
s["player_key"] = s.player_name.map(norm)
s = s.merge(pa, on=["player_id", "game_date"], how="left").dropna(subset=["out_pa"])

m = cl[(cl.category == "hits") & (cl.book == "bet365") & (cl.line == 0.5)]
j = s.merge(m, on=["game_date", "player_key", "category", "line"], how="inner")

print("MATCHED vs FULL -- same population?")
print(f"  FULL sim   n={len(s):5d}  mean out_pa={s.out_pa.mean():.3f}  "
      f"P(hits>=1)={(s.out_hits >= 1).mean():.4f}  mean model_p={s.sim_p_over.mean():.4f}")
print(f"  MATCHED    n={len(j):5d}  mean out_pa={j.out_pa.mean():.3f}  "
      f"P(hits>=1)={(j.out_hits >= 1).mean():.4f}  mean model_p={j.sim_p_over.mean():.4f}")
print()
print("VOID ROWS inside the matched set (books void pa==1):")
print(f"  pa==1 : {(j.out_pa == 1).sum()} of {len(j)} ({100*(j.out_pa == 1).mean():.1f}%)")
print(f"  pa<=2 : {(j.out_pa <= 2).sum()} of {len(j)} ({100*(j.out_pa <= 2).mean():.1f}%)")
print()
print("BRIER, all rows vs GRADEABLE only:")
for tag, d in (("ALL rows", j), ("pa>=2 (DK-gradeable)", j[j.out_pa >= 2])):
    y = (d.out_hits >= 1).astype(float)
    bm = ((d.sim_p_over - y) ** 2).mean()
    bk = ((d.p_over_devig - y) ** 2).mean()
    print(f"  {tag:22s} n={len(d):5d}  base={y.mean():.4f}  "
          f"B_model={bm:.5f}  B_market={bk:.5f}  dBrier={bm-bk:+.5f}")
