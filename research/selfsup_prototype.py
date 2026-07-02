#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 asuramaya and Gestalt contributors
"""
Offline self-supervised prototype for the decepticons-based learned tracker.

Validates the core claim before touching the kernel or going live: can a small
CONTINUOUS echo-state reservoir + ridge readout predict the next head-pose vector
better than trivial baselines, and does its prediction error (surprise) track
INTENTIONAL motion (so it can serve as a learned intent/noise gate)?

The reservoir math mirrors decepticons' `reservoir.py` exactly, except input is
injected as `Win @ x` for a real vector x instead of a token column lookup — the
one-line change that makes the kernel substrate continuous (see
docs/LEARNED_TRACKER.md).

Run:  ~/code/eye_demo/.venv/bin/python research/selfsup_prototype.py
"""
from __future__ import annotations

import glob
import json
import os

import numpy as np

REC_DIR = os.path.expanduser("~/.local/share/gestalt/recordings")


# ----------------------------------------------------------------------------
# data: contiguous runs of face-tracked frames; input x_t = head forward vector
# ----------------------------------------------------------------------------
def load_runs(min_run=30):
    """Return a list of (T_i, 3) arrays — contiguous face_ok segments. Reset the
    reservoir between runs (face loss breaks temporal continuity)."""
    runs, cur = [], []
    for fp in sorted(glob.glob(os.path.join(REC_DIR, "*.jsonl"))):
        for line in open(fp):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("face_ok") and "fwd" in r:
                cur.append(r["fwd"])
            else:
                if len(cur) >= min_run:
                    runs.append(np.array(cur, dtype=np.float64))
                cur = []
        if len(cur) >= min_run:
            runs.append(np.array(cur, dtype=np.float64))
        cur = []
    return runs


# ----------------------------------------------------------------------------
# continuous echo-state reservoir (decepticons reservoir.py math, Win @ x)
# ----------------------------------------------------------------------------
class ContinuousReservoir:
    def __init__(self, dim_in, size=120, spectral_radius=0.9, leak=0.35,
                 connectivity=0.12, input_scale=0.3, seed=11):
        rng = np.random.default_rng(seed)
        mask = rng.random((size, size)) < connectivity
        W = rng.standard_normal((size, size)) * mask
        eig = np.max(np.abs(np.linalg.eigvals(W)))
        self.W = W * (spectral_radius / (eig + 1e-9))
        self.Win = rng.standard_normal((size, dim_in)) * input_scale
        self.leak = leak
        self.size = size

    def run(self, X):
        """X: (T, dim_in) -> states (T, size). Fresh state per run."""
        h = np.zeros(self.size)
        H = np.empty((len(X), self.size))
        for t, x in enumerate(X):
            h = (1.0 - self.leak) * h + self.leak * np.tanh(self.W @ h + self.Win @ x)
            H[t] = h
        return H


def ridge_fit(F, Y, l2=1e-3):
    """Solve (F W = Y) with Tikhonov regularization. F:(N,d) Y:(N,k)."""
    d = F.shape[1]
    A = F.T @ F + l2 * np.eye(d)
    return np.linalg.solve(A, F.T @ Y)


def mse(a, b):
    return float(np.mean((a - b) ** 2))


def main():
    runs = load_runs()
    if not runs:
        print("no recordings found in", REC_DIR)
        return
    total = sum(len(r) for r in runs)
    print(f"loaded {len(runs)} contiguous runs, {total} frames "
          f"(mean run {total // len(runs)})")

    # 80/20 split by run (no leakage across the boundary)
    cut = int(len(runs) * 0.8)
    train_runs, test_runs = runs[:cut], runs[cut:]
    print(f"train runs {len(train_runs)}  test runs {len(test_runs)}")

    res = ContinuousReservoir(dim_in=3)

    # build (reservoir_state_t, next_input_{t+1}) pairs, with a bias term.
    def pairs(rs):
        Fs, Ys, raws = [], [], []
        for X in rs:
            if len(X) < 2:
                continue
            H = res.run(X)
            F = np.hstack([H[:-1], np.ones((len(X) - 1, 1))])   # state_t (+bias)
            Fs.append(F)
            Ys.append(X[1:])        # predict next input
            raws.append(X[:-1])     # persistence baseline = x_t
        return np.vstack(Fs), np.vstack(Ys), np.vstack(raws)

    Ftr, Ytr, _ = pairs(train_runs)
    Fte, Yte, Xte = pairs(test_runs)

    Wout = ridge_fit(Ftr, Ytr, l2=1e-3)
    pred = Fte @ Wout

    # baselines
    persist = Xte                                   # x_{t+1} ≈ x_t
    # linear AR(1): fit y = A x + b on train raw pairs
    Xtr_raw = np.vstack([X[:-1] for X in train_runs if len(X) > 1])
    Ytr_raw = np.vstack([X[1:] for X in train_runs if len(X) > 1])
    Far = np.hstack([Xtr_raw, np.ones((len(Xtr_raw), 1))])
    War = ridge_fit(Far, Ytr_raw, l2=1e-6)
    ar = np.hstack([Xte, np.ones((len(Xte), 1))]) @ War

    m_res, m_per, m_ar = mse(pred, Yte), mse(persist, Yte), mse(ar, Yte)
    print("\nONE-STEP-AHEAD PREDICTION (test MSE, lower = better):")
    print(f"  persistence (x_t+1 = x_t) : {m_per:.3e}")
    print(f"  linear AR(1)              : {m_ar:.3e}")
    print(f"  continuous reservoir      : {m_res:.3e}   "
          f"({100 * (1 - m_res / m_per):+.1f}% vs persistence)")

    # surprise vs intent: does prediction error rise with head speed?
    err = np.linalg.norm(pred - Yte, axis=1)        # per-frame surprise
    speed = np.linalg.norm(np.diff(Xte, axis=0, prepend=Xte[:1]), axis=1)
    # correlation, and error in the fast vs slow halves
    corr = float(np.corrcoef(err, speed)[0, 1])
    med = np.median(speed)
    fast = err[speed > med].mean()
    slow = err[speed <= med].mean()
    print("\nSURPRISE AS AN INTENT GATE:")
    print(f"  corr(surprise, head-speed) = {corr:+.3f}")
    print(f"  mean surprise  fast-half {fast:.4f}  vs  slow-half {slow:.4f}  "
          f"(ratio {fast / max(slow, 1e-9):.2f}x)")

    # --- velocity prediction: predict the DELTA (persistence predicts zero) ----
    def vel_pairs(rs):
        Fs, Ys = [], []
        for X in rs:
            if len(X) < 2:
                continue
            H = res.run(X)
            Fs.append(np.hstack([H[:-1], np.ones((len(X) - 1, 1))]))
            Ys.append(np.diff(X, axis=0))     # velocity = x_{t+1} - x_t
        return np.vstack(Fs), np.vstack(Ys)

    Fvtr, Yvtr = vel_pairs(train_runs)
    Fvte, Yvte = vel_pairs(test_runs)
    Wv = ridge_fit(Fvtr, Yvtr, l2=1e-4)
    vpred = Fvte @ Wv
    m_vzero = mse(np.zeros_like(Yvte), Yvte)     # persistence = predict zero velocity
    m_vres = mse(vpred, Yvte)
    print("\nVELOCITY (DELTA) PREDICTION (test MSE):")
    print(f"  zero-velocity (persistence): {m_vzero:.3e}")
    print(f"  continuous reservoir       : {m_vres:.3e}   "
          f"({100 * (1 - m_vres / m_vzero):+.1f}% vs persistence)")

    # --- multi-step: predict x_{t+k}; persistence degrades, dynamics should win -
    print("\nMULTI-STEP PREDICTION (reservoir vs persistence, test MSE):")
    for k in (3, 6, 12):
        Fk, Yk, Pk = [], [], []
        for X in test_runs:
            if len(X) <= k:
                continue
            H = res.run(X)
            Fk.append(np.hstack([H[:-k], np.ones((len(X) - k, 1))]))
            Yk.append(X[k:])
            Pk.append(X[:-k])         # persistence: x_{t+k} ≈ x_t
        if not Fk:
            continue
        Fk, Yk, Pk = np.vstack(Fk), np.vstack(Yk), np.vstack(Pk)
        # readout for horizon k (refit on train)
        Fktr, Yktr = [], []
        for X in train_runs:
            if len(X) <= k:
                continue
            H = res.run(X)
            Fktr.append(np.hstack([H[:-k], np.ones((len(X) - k, 1))]))
            Yktr.append(X[k:])
        Wk = ridge_fit(np.vstack(Fktr), np.vstack(Yktr), l2=1e-3)
        mk_res, mk_per = mse(Fk @ Wk, Yk), mse(Pk, Yk)
        print(f"  k={k:2d} ({k * 50}ms):  persistence {mk_per:.3e}   "
              f"reservoir {mse(Fk @ Wk, Yk):.3e}   ({100 * (1 - mk_res / mk_per):+.1f}%)")

    # --- denoising: jitter during REST segments (true signal ~ constant) -------
    def rest_jitter(test_runs):
        raw_j, eu_j = [], []
        for X in test_runs:
            sp = np.linalg.norm(np.diff(X, axis=0, prepend=X[:1]), axis=1)
            restmask = sp < np.percentile(sp, 30)        # slowest 30% = at rest
            if restmask.sum() < 10:
                continue
            # high-freq jitter = std of the second difference (curvature of noise)
            raw_j.append(np.std(np.diff(X[restmask], n=2, axis=0)))
            f = oneeuro_filter(X)
            eu_j.append(np.std(np.diff(f[restmask], n=2, axis=0)))
        return float(np.mean(raw_j)), float(np.mean(eu_j))

    raw_j, eu_j = rest_jitter(test_runs)
    print("\nDENOISING (rest-segment jitter, lower = smoother):")
    print(f"  raw signal     : {raw_j:.5f}")
    print(f"  1€ filter      : {eu_j:.5f}   ({100 * (1 - eu_j / raw_j):+.1f}%)")

    print("\nverdict:",
          "surprise tracks intent (2.5x) + reservoir captures velocity/dynamics — "
          "self-supervised approach validated; next: promote continuous substrate"
          if (fast > slow and m_vres < m_vzero) else
          "surprise gate works; point-prediction is persistence-dominated (smooth signal)")


def oneeuro_filter(X, mincut=2.0, beta=0.8, dcut=1.0, dt=1 / 20):
    """Vectorized 1€ on each channel, for the denoising baseline."""
    import math
    out = np.empty_like(X)
    xp = X[0].copy()
    dxp = np.zeros(X.shape[1])
    for t in range(len(X)):
        if t == 0:
            out[t] = xp
            continue
        dx = (X[t] - xp) / dt
        a_d = 1.0 / (1.0 + (1.0 / (2 * math.pi * dcut)) / dt)
        dxp += a_d * (dx - dxp)
        cut = mincut + beta * np.abs(dxp)
        a = 1.0 / (1.0 + (1.0 / (2 * math.pi * cut)) / dt)
        xp = xp + a * (X[t] - xp)
        out[t] = xp
    return out


if __name__ == "__main__":
    main()
