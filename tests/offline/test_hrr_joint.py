"""
Offline harness: HRR joint-distribution invariants.

Guards the Step 2 fix (persistent BaseState + lambda re-mix + conditioned run
coin) against the four ways it can silently fail to be a fix.

RULE 4 COMPLIANCE: every invariant below FAILS on the broken code (or on a
specific wrong design). A harness that passes on both guards nothing. Run
    python test_hrr_joint.py --mutate <name>
to prove it. Mutations: broken | lambda1 | gamma0 | staterun | cumrbi

Thresholds are CALIBRATED against measured broken-code behaviour, not chosen
by sign. The broken code already has Corr(hits,rbi) = +0.43 -- a "correlation
is positive" test would pass on it and prove nothing.
"""
from __future__ import annotations
import argparse, random, statistics, sys
import sim_ref as S

N_SIMS = 200_000
SEED = 20260711
PA_MIX = ([3, 4, 5], [0.25, 0.55, 0.20])

# ---- calibrated reference points (measured, Step 2 sweep, n=300k) ----
BROKEN_C_HITS_RBI = 0.433    # broken code's shared-`kind` correlation channel
GAMMA0_C_HITS_RUNS = 0.5176  # max over 8 seeds @ gamma=0 (measured, not guessed)
TOL = 0.012                  # >> MC sd (0.0018 @ n=200k, measured over 8 seeds)


def corr(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys)) / n
    sx = (sum((x-mx)**2 for x in xs)/n) ** 0.5
    sy = (sum((y-my)**2 for y in ys)/n) ** 0.5
    return cov / (sx*sy) if sx and sy else 0.0


def collect(simfn, n=N_SIMS, seed=SEED, **kw):
    rng = random.Random(seed)
    pa = S.PAStub(random.Random(seed + 1))
    H, R, RB = [], [], []
    for _ in range(n):
        n_pa = rng.choices(*PA_MIX, k=1)[0]
        h, r, rb = simfn(rng, pa, n_pa, **kw)
        H.append(h); R.append(r); RB.append(rb)
    HRR = [h + r + rb for h, r, rb in zip(H, R, RB)]
    return dict(
        H=H, R=R, RB=RB, HRR=HRR,
        c_hits_rbi=corr(H, RB), c_hits_runs=corr(H, R), c_runs_rbi=corr(R, RB),
        mean_hrr=statistics.mean(HRR), sd_hrr=statistics.pstdev(HRR),
        mean_hits=statistics.mean(H), mean_runs=statistics.mean(R),
        mean_rbi=statistics.mean(RB),
        p_ge2=sum(1 for x in HRR if x >= 2)/len(HRR),
        p_ge3=sum(1 for x in HRR if x >= 3)/len(HRR),
    )


class Failure(Exception):
    pass


def check(name, ok, msg):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}")
    if not ok:
        print(f"         -> {msg}")
        raise Failure(name)
    return True


# =========================================================================
# INVARIANTS
# =========================================================================
def inv1_runs_rbi_not_degenerate(m):
    """Corr(runs,rbi) must be materially BELOW 1.0.
       Catches the naive 'read state.runs' design: BaseState._score_runs()
       increments runs and rbi by the same amount on EVERY call, so reading
       state.runs forces runs == rbi identically (Corr = 1.000)."""
    c = m["c_runs_rbi"]
    return check(
        "INV1 runs/rbi not degenerate (Corr < 0.95)",
        c < 0.95,
        f"Corr(runs,rbi) = {c:.4f}. state.runs counts TEAM runs during the PA, "
        f"not the BATTER's run. Reading it makes runs == rbi identically. "
        f"Use the conditioned run coin, not state.runs.",
    )


def inv2_hits_rbi_causally_coupled(m):
    """Corr(hits,rbi) must EXCEED the broken code's baseline.
       The broken code already scores +0.433 via the shared-PA-outcome channel
       (a HR increments hits and forces rbi>=1). A 'positive correlation' test
       would PASS on the broken code. The fix must add a CAUSAL channel on top:
       rbi on PA k now depends on the state left by PA k-1."""
    c = m["c_hits_rbi"]
    floor = BROKEN_C_HITS_RBI + TOL
    return check(
        f"INV2 hits/rbi causally coupled (Corr > {floor:.3f} = broken+tol)",
        c > floor,
        f"Corr(hits,rbi) = {c:.4f}, not materially above the BROKEN baseline "
        f"of {BROKEN_C_HITS_RBI:.3f}. The persistent base state is not carrying "
        f"information across PAs -- check lambda < 1.0 and that BaseState is "
        f"allocated ONCE per game, not per PA.",
    )


def inv3_mixing_knob_is_live(m_fixed):
    """lambda MUST be a live knob: lambda=1.0 + gamma=0 must reproduce the
       BROKEN distribution, and the shipped lambda must differ from it.
       This is the degeneracy guard. A 'persistent BaseState' that re-mixes
       fully every PA is MATHEMATICALLY IDENTICAL to the per-PA resample it
       replaced -- the fix would be a no-op wearing a state machine costume.
       'No drift' in the gate would then mean 'the fix did not happen',
       not 'the fix is neutral'."""
    m_deg = collect(S.simulate_game_fixed, lam=1.0, gamma=0.0)
    m_brk = collect(S.simulate_game_broken)

    same = (abs(m_deg["c_hits_rbi"] - m_brk["c_hits_rbi"]) < TOL
            and abs(m_deg["mean_hrr"] - m_brk["mean_hrr"]) < 0.03
            and abs(m_deg["sd_hrr"] - m_brk["sd_hrr"]) < 0.05)
    check(
        "INV3a lambda=1,gamma=0 degenerates to BROKEN",
        same,
        f"lambda=1/gamma=0 gave hrr={m_deg['mean_hrr']:.3f} sd={m_deg['sd_hrr']:.3f} "
        f"C(h,rbi)={m_deg['c_hits_rbi']:.3f}; BROKEN gave hrr={m_brk['mean_hrr']:.3f} "
        f"sd={m_brk['sd_hrr']:.3f} C(h,rbi)={m_brk['c_hits_rbi']:.3f}. These MUST "
        f"match -- if they don't, the fixed path differs from broken for some "
        f"reason OTHER than lambda/gamma, and the knob is not the fix.",
    )
    moved = abs(m_fixed["c_hits_rbi"] - m_deg["c_hits_rbi"]) > TOL
    return check(
        "INV3b shipped lambda moves off the degenerate point",
        moved,
        f"shipped C(h,rbi)={m_fixed['c_hits_rbi']:.4f} vs degenerate "
        f"{m_deg['c_hits_rbi']:.4f}. lambda is not doing anything -- the fix is "
        f"a no-op. Check lambda is actually plumbed through from config.",
    )


def inv4_rbi_read_as_delta(simfn, kw):
    """RBI must be read as a PER-PA DELTA off the persistent state.

       EXACT structural identity -- no hand-picked threshold:
       per-PA deltas TELESCOPE, so sum(delta_k) == final state.rbi. The
       cumulative read `rbi += state.rbi` sums RUNNING TOTALS (1 + 3 + 6 ...)
       and strictly exceeds it whenever the batter has 2+ RBI-scoring PAs.

       RULE 5: this must test the SIMULATOR UNDER TEST against a delta-read
       reference on IDENTICAL draws -- not the reference against itself.
       (First version of this invariant did exactly that and passed on the
       cumulative-RBI mutation.)"""
    over = 0; n = 20000
    seq = random.Random(SEED)
    for _ in range(n):
        n_pa = seq.choices(*PA_MIX, k=1)[0]
        s = seq.randrange(2**31)
        # simulator under test
        r_t = random.Random(s); p_t = S.PAStub(random.Random(s + 1))
        _, _, rbi_test = simfn(r_t, p_t, n_pa, **kw)
        # delta-read reference, identical draws
        r_r = random.Random(s); p_r = S.PAStub(random.Random(s + 1))
        _, _, rbi_ref = S.simulate_game_fixed(r_r, p_r, n_pa,
                                              lam=kw.get("lam", S.LAMBDA_DEFAULT),
                                              gamma=kw.get("gamma", S.GAMMA_DEFAULT))
        if rbi_test > rbi_ref:
            over += 1
    frac = over / n
    return check(
        "INV4 rbi read as per-PA delta (telescoping identity)",
        frac < 0.005,
        f"simulator under test reported MORE rbi than the delta-read reference "
        f"on {100*frac:.2f}% of identically-seeded games. This is the signature "
        f"of `rbi += state.rbi` (cumulative running total) instead of "
        f"`rbi += state.rbi - rbi_before` (per-PA delta) on a persistent "
        f"BaseState -- it double-counts every multi-RBI game.",
    )


def inv6_run_coin_is_conditioned(m):
    """gamma MUST be live: the run coin must be CONDITIONED on traffic behind
       the batter, not an independent flip.
       Corr(hits,runs) at gamma=0 measures [0.5130, 0.5176] over 8 seeds
       (MC sd 0.0018). A conditioned coin must clear that band -- otherwise
       gamma has been silently zeroed and _run_for_batter is still the
       independent coin the fix was supposed to remove."""
    c = m["c_hits_runs"]
    floor = GAMMA0_C_HITS_RUNS + 0.003
    return check(
        f"INV6 run coin conditioned on traffic (C(h,run) > {floor:.4f} = gamma0+tol)",
        c > floor,
        f"Corr(hits,runs) = {c:.4f}, inside the gamma=0 band "
        f"[0.5130, 0.5176]. The run coin is NOT conditioned on the base state: "
        f"_run_for_batter is still flipping an independent coin against "
        f"p_score_from_base. Check gamma is plumbed through and that `behind` "
        f"is computed from the POST-advance state.",
    )


def inv5_means_preserved(m):
    """The fix must not blow up the marginals it was not supposed to touch.
       Regression tripwire only -- hits is untouched by this change."""
    return check(
        "INV5 hits marginal unchanged (regression tripwire)",
        0.85 < m["mean_hits"] < 0.96,
        f"mean hits = {m['mean_hits']:.3f}, outside [0.85, 0.96]. The base-state "
        f"fix must not touch the hits marginal -- something else changed.",
    )


MUTATIONS = {
    "broken":   ("current broken code",              S.simulate_game_broken, {}),
    "lambda1":  ("lambda=1.0 (full re-mix; no-op)",  S.simulate_game_fixed, dict(lam=1.0)),
    "gamma0":   ("gamma=0 (unconditioned run coin)", S.simulate_game_fixed, dict(gamma=0.0)),
    "staterun": ("naive read of state.runs",         S.simulate_game_naive_staterun, {}),
    "cumrbi":   ("cumulative rbi (not delta)",       S.simulate_game_fixed, dict(_cumulative_rbi_bug=True)),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutate", choices=list(MUTATIONS), default=None)
    a = ap.parse_args()

    if a.mutate:
        desc, fn, kw = MUTATIONS[a.mutate]
        print(f"\n=== MUTATION: {a.mutate} -- {desc} ===")
        print("    (harness MUST fail; if it passes, it guards nothing)\n")
    else:
        desc, fn, kw = "Design B (lambda=0.7, gamma=0.15)", S.simulate_game_fixed, {}
        print(f"\n=== FIXED: {desc} ===\n")

    m = collect(fn, **kw)
    print(f"  n={N_SIMS}  hits={m['mean_hits']:.3f} runs={m['mean_runs']:.3f} "
          f"rbi={m['mean_rbi']:.3f} hrr={m['mean_hrr']:.3f} sd={m['sd_hrr']:.3f}")
    print(f"  C(h,rbi)={m['c_hits_rbi']:.4f} C(h,run)={m['c_hits_runs']:.4f} "
          f"C(r,rbi)={m['c_runs_rbi']:.4f}  P>=2={m['p_ge2']:.3f} P>=3={m['p_ge3']:.3f}\n")

    try:
        inv1_runs_rbi_not_degenerate(m)
        inv2_hits_rbi_causally_coupled(m)
        inv4_rbi_read_as_delta(fn, kw)
        inv5_means_preserved(m)
        inv6_run_coin_is_conditioned(m)
        if not a.mutate:
            inv3_mixing_knob_is_live(m)
        else:
            # INV3 compares against broken; only meaningful on the real fix.
            # For lambda1 the INV2 failure is the one that names the bug.
            pass
    except Failure as f:
        print(f"\nHARNESS FAILED at {f}")
        return 1 if not a.mutate else 0   # mutation SHOULD fail
    print("\nALL INVARIANTS PASS")
    return 0 if not a.mutate else 1        # mutation passing == harness is useless


if __name__ == "__main__":
    sys.exit(main())
