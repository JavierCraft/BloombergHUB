"""The deadline model: P(YES) for a market some days before its deadline.

How it is judged, in this order — the Paper Test page shows the same order:

  1. **Against the market price, out of sample.** The price is itself a
     forecast, usually a good one. The model's Brier score is compared with the
     price's Brier score on markets the model never saw, and the gap gets a
     bootstrap interval resampled over *markets* (nine snapshots of one market
     are not nine independent observations). Only an interval that stays above
     zero counts as "beats the market".
  2. **Calibration** — when it says 70%, does it happen about 70% of the time.
  3. **Trading results** on the same held-out predictions: win rate next to the
     average entry price, which is the break-even win rate, and ROI after an
     explicit cost per trade.

Validation is walk-forward. Markets are ordered by deadline; the model trains on
earlier blocks and is tested on the next one, with a 30-day purge so no training
market closes inside a test snapshot's look-back.

Both estimators are **anchored to the market**: the price's own log-odds enter
as a fixed offset, and the model only learns corrections to it. This matters.
The first version fed the price in as an ordinary regularised feature; on 150
Manifold markets the penalty shrank that coefficient, the model drifted toward
the base rate, bet on longshots, and lost to the price it was trading against
(Brier 0.156 against 0.132). With an offset, strong regularisation means "trust
the market", which is the right default when data is thin.

Two anchored estimators compete — a logistic regression (every correction is a
readable coefficient) and a gradient-boosted tree ensemble starting from the
market prior. The lower out-of-sample log loss wins and is refitted on all rows.
Neither is trusted by default — the verdict decides, and it is printed next to
every EV the model produces.

Separately, `deadline_effect` answers the question that motivated this module
without any model at all: as the deadline gets close, do favourites win more
often than their price says? If yes, prices of near-deadline favourites tend to
rise toward 100%, and by how much is measured per time bucket.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable

from src.ml import features, history, store

MODEL_PATH = store.MODELS / "deadline.joblib"
REPORT_PATH = store.MODELS / "deadline.json"

MIN_ROWS = 300
MIN_MARKETS = 80
TRAIN_BAND = (0.02, 0.98)
EVAL_BAND = (0.05, 0.95)     # near-certain snapshots flatter every score; judge on contested ones
FOLDS = 5
PURGE_DAYS = 30
BOOTSTRAP_REPS = 500
BACKTEST_MIN_EV = 0.05
BACKTEST_COST_PP = 1.0

Progress = Callable[[str, int, int, str], None]


def _noop(stage: str, done: int, total: int, note: str = "") -> None:
    return None


NotEnoughData = history.NotEnoughData


def _in_band(p: float, band: tuple[float, float]) -> bool:
    return band[0] <= p <= band[1]


def _arrays(rows: list[dict]):
    import numpy as np

    X = np.array([[float(r[f]) for f in features.FEATURES] for r in rows], dtype=float)
    y = np.array([int(r["y"]) for r in rows], dtype=int)
    p = np.array([float(r["p"]) for r in rows], dtype=float)
    groups = np.array([f"{r['source']}:{r['market_id']}" for r in rows])
    counts = Counter(groups.tolist())
    # Equal say per market: a market sampled nine times must not outvote one sampled twice.
    w = np.array([1.0 / counts[g] for g in groups.tolist()], dtype=float)
    return X, y, p, groups, w


ANCHOR = features.FEATURES.index("logit_p")


def _sigmoid(z):
    import numpy as np

    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


try:
    from sklearn.base import BaseEstimator, ClassifierMixin
except ImportError:
    # Tanpa scikit-learn kelas ini tidak pernah dipakai, tetapi definisinya tetap
    # dieksekusi saat modul diimpor — dan `AnchoredLogit(object, object)` adalah
    # TypeError: duplicate base class. Dua kelas kosong yang berbeda menjaga
    # `import src.ml.model` tetap berhasil, sehingga daftar tenggat dan laporan
    # model masih bisa dilayani di pemasangan ramping (mis. Vercel).
    class BaseEstimator:  # type: ignore[no-redef]
        pass

    class ClassifierMixin:  # type: ignore[no-redef]
        pass


class AnchoredLogit(ClassifierMixin, BaseEstimator):
    """Logistic regression with the market's log-odds as a fixed offset.

    logit(q) = logit(price) + intercept + Σ coef·standardised feature

    L2 penalty on every learned term (not on the offset), scaled by the number of
    rows, fitted by Newton's method. With no signal the corrections shrink to
    zero and the model returns the market price unchanged.
    """

    def __init__(self, alpha: float = 0.01, anchor_index: int = ANCHOR, max_iter: int = 60):
        self.alpha = alpha
        self.anchor_index = anchor_index
        self.max_iter = max_iter

    def fit(self, X, y, sample_weight=None):
        import numpy as np

        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, d = X.shape
        w = np.ones(n) if sample_weight is None else np.asarray(sample_weight, dtype=float)
        w = w * (n / w.sum())
        self.mean_ = X.mean(axis=0)
        self.scale_ = X.std(axis=0)
        self.scale_[self.scale_ == 0] = 1.0
        Z = np.hstack([np.ones((n, 1)), (X - self.mean_) / self.scale_])
        offset = X[:, self.anchor_index]
        penalty = np.full(d + 1, self.alpha * n)
        penalty[0] = self.alpha * n * 0.1
        beta = np.zeros(d + 1)
        for _ in range(self.max_iter):
            mu = _sigmoid(offset + Z @ beta)
            grad = Z.T @ (w * (mu - y)) + penalty * beta
            hess = Z.T @ (Z * (w * mu * (1 - mu))[:, None]) + np.diag(penalty)
            step = np.linalg.solve(hess, grad)
            beta -= step
            if np.max(np.abs(step)) < 1e-8:
                break
        self.intercept_ = float(beta[0])
        self.coef_ = beta[1:]
        self.classes_ = np.array([0, 1])
        return self

    def decision_function(self, X):
        import numpy as np

        X = np.asarray(X, dtype=float)
        return X[:, self.anchor_index] + self.intercept_ + ((X - self.mean_) / self.scale_) @ self.coef_

    def predict_proba(self, X):
        import numpy as np

        p = _sigmoid(self.decision_function(X))
        return np.column_stack([1 - p, p])

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


class MarketPrior(ClassifierMixin, BaseEstimator):
    """The market price as a classifier — the starting point for boosting."""

    def __init__(self, anchor_index: int = ANCHOR):
        self.anchor_index = anchor_index

    def fit(self, X, y, sample_weight=None):
        import numpy as np

        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, X):
        import numpy as np

        p = _sigmoid(np.asarray(X, dtype=float)[:, self.anchor_index])
        return np.column_stack([1 - p, p])


def _estimators() -> dict[str, Any]:
    """Candidates, weakest to strongest penalty. With thin data the strongest one —
    effectively the market price — should win, and that is the honest outcome."""
    from sklearn.ensemble import GradientBoostingClassifier

    return {
        "anchored_logit_light": AnchoredLogit(alpha=0.03),
        "anchored_logit": AnchoredLogit(alpha=0.3),
        "anchored_logit_strong": AnchoredLogit(alpha=3.0),
        "anchored_boost": GradientBoostingClassifier(
            init=MarketPrior(), n_estimators=100, learning_rate=0.02, max_depth=2,
            subsample=0.7, min_samples_leaf=60, random_state=0),
    }


def _fit(estimator, X, y, w):
    estimator.fit(X, y, sample_weight=w)
    return estimator


def _folds(rows: list[dict], folds: int = FOLDS) -> list[tuple[int, list[int], list[int]]]:
    import numpy as np

    deadline: dict[tuple, int] = {}
    for r in rows:
        key = (r["source"], r["market_id"])
        deadline[key] = r["deadline_ms"]
    ordered = sorted(deadline, key=lambda k: deadline[k])
    blocks = np.array_split(np.arange(len(ordered)), folds + 1)
    purge = PURGE_DAYS * features.DAY_MS

    out = []
    for k in range(1, folds + 1):
        test_keys = {ordered[i] for i in blocks[k]}
        if not test_keys:
            continue
        first_test = min(deadline[m] for m in test_keys)
        train_keys = {ordered[i] for block in blocks[:k] for i in block
                      if deadline[ordered[i]] < first_test - purge}
        train_idx = [i for i, r in enumerate(rows) if (r["source"], r["market_id"]) in train_keys]
        test_idx = [i for i, r in enumerate(rows) if (r["source"], r["market_id"]) in test_keys]
        out.append((k, train_idx, test_idx))
    return out


def _metrics(y, q, p) -> dict:
    import numpy as np

    n = int(len(y))
    if n == 0:
        return {"n": 0}
    eps = 1e-6
    qc, pc = np.clip(q, eps, 1 - eps), np.clip(p, eps, 1 - eps)
    brier_model = float(np.mean((q - y) ** 2))
    brier_market = float(np.mean((p - y) ** 2))
    moved = np.abs(q - p) > 1e-9
    hits = ((q > p) & (y == 1)) | ((q < p) & (y == 0))
    out = {
        "n": n,
        "base_rate": round(float(np.mean(y)), 4),
        "brier_model": round(brier_model, 5),
        "brier_market": round(brier_market, 5),
        "brier_skill_vs_market": round(1 - brier_model / brier_market, 4) if brier_market > 0 else None,
        "log_loss_model": round(float(-np.mean(y * np.log(qc) + (1 - y) * np.log(1 - qc))), 5),
        "log_loss_market": round(float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))), 5),
        "accuracy_model": round(float(np.mean((q >= 0.5) == (y == 1))), 4),
        "accuracy_market": round(float(np.mean((p >= 0.5) == (y == 1))), 4),
        "direction_accuracy": round(float(hits[moved].mean()), 4) if moved.any() else None,
        "direction_n": int(moved.sum()),
    }
    if len(set(y.tolist())) == 2:
        from sklearn.metrics import roc_auc_score

        out["auc_model"] = round(float(roc_auc_score(y, q)), 4)
        out["auc_market"] = round(float(roc_auc_score(y, p)), 4)
    return out


def _bootstrap(y, q, p, groups, reps: int = BOOTSTRAP_REPS, seed: int = 11) -> dict:
    """Brier(market) − Brier(model), resampled over markets. Positive = model better."""
    import numpy as np

    if len(y) == 0:
        return {"mean": None, "low": None, "high": None, "markets": 0}
    uniq, inverse = np.unique(groups, return_inverse=True)
    diff = (p - y) ** 2 - (q - y) ** 2
    sums = np.bincount(inverse, weights=diff, minlength=len(uniq))
    counts = np.bincount(inverse, minlength=len(uniq))
    rng = np.random.default_rng(seed)
    stats = np.empty(reps)
    for i in range(reps):
        pick = rng.integers(0, len(uniq), len(uniq))
        stats[i] = sums[pick].sum() / max(1, counts[pick].sum())
    return {
        "mean": round(float(diff.mean()), 5),
        "low": round(float(np.percentile(stats, 5)), 5),
        "high": round(float(np.percentile(stats, 95)), 5),
        "markets": int(len(uniq)),
        "reps": reps,
    }


def summarise_trades(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "wins": 0, "win_rate": None, "avg_entry": None, "edge_pp": None,
                "roi_pct": None, "avg_ev_pct": None}
    wins = sum(1 for t in trades if t["win"])
    win_rate = wins / n
    avg_entry = sum(t["entry"] for t in trades) / n
    return {
        "n": n,
        "wins": wins,
        "win_rate": round(float(win_rate), 4),
        "avg_entry": round(float(avg_entry), 4),
        "edge_pp": round(float(win_rate - avg_entry) * 100, 2),
        "roi_pct": round(float(sum(t["pnl"] for t in trades)) / n * 100, 2),
        "avg_ev_pct": round(float(sum(t["ev"] for t in trades)) / n * 100, 2),
    }


def _backtest(rows: list[dict], q, min_ev: float = BACKTEST_MIN_EV,
              cost_pp: float = BACKTEST_COST_PP) -> dict:
    """Trade the held-out predictions: one position per market per deadline bucket."""
    trades: dict[tuple, dict] = {}
    for r, prob in zip(rows, q):
        p = r["p"]
        if not _in_band(p, EVAL_BAND):
            continue
        ask_yes = min(0.999, p + cost_pp / 100)
        ask_no = min(0.999, (1 - p) + cost_pp / 100)
        ev_yes, ev_no = prob / ask_yes - 1, (1 - prob) / ask_no - 1
        side, ev, entry = ("YES", ev_yes, ask_yes) if ev_yes >= ev_no else ("NO", ev_no, ask_no)
        if ev < min_ev:
            continue
        bucket = features.deadline_bucket(r["offset_days"])
        key = (r["source"], r["market_id"], bucket)
        if key in trades and trades[key]["offset_days"] <= r["offset_days"]:
            continue
        win = (side == "YES") == (r["y"] == 1)
        trades[key] = {"bucket": bucket, "offset_days": r["offset_days"], "side": side,
                       "entry": entry, "ev": ev, "win": win,
                       "pnl": (1 / entry - 1) if win else -1.0}

    items = list(trades.values())
    return {
        "min_ev_pct": min_ev * 100,
        "cost_pp": cost_pp,
        "overall": summarise_trades(items),
        "by_bucket": [{"bucket": b, **summarise_trades([t for t in items if t["bucket"] == b])}
                      for b in features.BUCKET_ORDER if any(t["bucket"] == b for t in items)],
        "by_side": [{"side": s, **summarise_trades([t for t in items if t["side"] == s])}
                    for s in ("YES", "NO") if any(t["side"] == s for t in items)],
        "note": (f"Dari prediksi out-of-sample: posisi dibuka bila EV model ≥ {min_ev * 100:.0f}% "
                 f"setelah biaya {cost_pp:g} poin per transaksi, satu posisi per pasar per rentang "
                 "tenggat, dipegang sampai selesai. Win rate harus melampaui rata-rata harga masuk "
                 "(break-even) — win rate tinggi dari membeli favorit mahal tidak berarti untung."),
    }


QUESTION_KINDS = (
    ("semua", "Semua pertanyaan", lambda r: True),
    ("bertenggat", "“…by/before <tanggal>?”", lambda r: r.get("q_by_deadline") == 1.0),
    ("lainnya", "Lainnya (peristiwa terjadwal, pertandingan, dll.)", lambda r: r.get("q_by_deadline") != 1.0),
)


def _deadline_effect(rows: list[dict], q=None) -> list[dict]:
    """Per question kind and time-to-deadline bucket, measured against outcomes.

    Two readings per row, because "the price rises near the deadline" can mean
    two different things:

      * favourite — does the side above 50% win more often than its price? If so,
        favourites' prices tend to climb toward 100% as the deadline arrives.
      * YES — is YES priced above or below how often it happens? For "will X
        happen by <date>" questions a YES that is too expensive means time decay:
        the NO price tends to rise as the days run out.
    """
    import math

    def se_pp(rate: float, n: int) -> float:
        return math.sqrt(max(rate * (1 - rate), 1e-9) / n) * 100

    out = []
    for kind, label, keep in QUESTION_KINDS:
        for bucket in features.BUCKET_ORDER:
            idx = [i for i, r in enumerate(rows)
                   if features.deadline_bucket(r["offset_days"]) == bucket
                   and _in_band(r["p"], EVAL_BAND) and keep(r)]
            if not idx:
                continue
            n = len(idx)
            avg_fav = sum(max(rows[i]["p"], 1 - rows[i]["p"]) for i in idx) / n
            fav_rate = sum(1 for i in idx if (rows[i]["p"] >= 0.5) == (rows[i]["y"] == 1)) / n
            avg_yes = sum(rows[i]["p"] for i in idx) / n
            yes_rate = sum(rows[i]["y"] for i in idx) / n
            fav_gap, fav_se = (fav_rate - avg_fav) * 100, se_pp(fav_rate, n)
            yes_gap, yes_se = (yes_rate - avg_yes) * 100, se_pp(yes_rate, n)
            row = {
                "kind": kind, "kind_label": label, "bucket": bucket, "n": n,
                "markets": len({(rows[i]["source"], rows[i]["market_id"]) for i in idx}),
                "avg_favorite_price": round(avg_fav, 4), "favorite_win_rate": round(fav_rate, 4),
                "gap_pp": round(fav_gap, 2), "se_pp": round(fav_se, 2),
                "avg_yes_price": round(avg_yes, 4), "yes_rate": round(yes_rate, 4),
                "yes_gap_pp": round(yes_gap, 2), "yes_se_pp": round(yes_se, 2),
                "brier_market": round(sum((rows[i]["p"] - rows[i]["y"]) ** 2 for i in idx) / n, 5),
            }
            if q is not None:
                row["brier_model"] = round(sum((float(q[i]) - rows[i]["y"]) ** 2 for i in idx) / n, 5)
            if n < 30:
                row["reading"] = row["yes_reading"] = "Sampel terlalu kecil untuk disimpulkan."
            else:
                if fav_gap > 2 * fav_se:
                    row["reading"] = "Favorit menang lebih sering dari harganya — harga favorit cenderung NAIK menuju 100%."
                elif fav_gap < -2 * fav_se:
                    row["reading"] = "Favorit menang lebih jarang dari harganya — favorit terlalu mahal."
                else:
                    row["reading"] = "Harga favorit kira-kira tepat (selisih dalam batas acak)."
                if yes_gap < -2 * yes_se:
                    row["yes_reading"] = "YES terlalu mahal — harga NO cenderung naik saat tenggat mendekat."
                elif yes_gap > 2 * yes_se:
                    row["yes_reading"] = "YES terlalu murah — harga YES cenderung naik saat tenggat mendekat."
                else:
                    row["yes_reading"] = "Harga YES kira-kira tepat (selisih dalam batas acak)."
            out.append(row)
    return out


def _coefficients(X, y, w) -> dict:
    """Corrections the anchored logistic model applies on top of the market's log-odds."""
    estimator = _fit(_estimators()["anchored_logit"], X, y, w)
    rows = sorted(zip(features.FEATURES, estimator.coef_), key=lambda kv: -abs(kv[1]))
    return {
        "intercept": round(estimator.intercept_, 4),
        "rows": [{"feature": f, "label": features.LABELS.get(f, f), "coef": round(float(c), 4),
                  "effect": "menaikkan peluang YES" if c > 0 else "menurunkan peluang YES"}
                 for f, c in rows],
        "note": ("Koefisien adalah koreksi (dalam log-odds per satu simpangan baku fitur) di atas "
                 "harga pasar. Nol berarti model mengikuti pasar. Koefisien positif pada harga pasar "
                 "sendiri berarti pasar terlalu ragu (harga perlu ditarik menjauhi 50%); negatif berarti "
                 "pasar terlalu yakin."),
    }


def _importance(estimator, X, y) -> list[dict]:
    from sklearn.inspection import permutation_importance

    try:
        result = permutation_importance(estimator, X, y, scoring="neg_log_loss",
                                        n_repeats=5, random_state=0)
    except Exception:  # noqa: BLE001 — importance is a nicety, never a reason to fail training
        return []
    pairs = sorted(zip(features.FEATURES, result.importances_mean), key=lambda kv: -kv[1])
    return [{"feature": f, "label": features.LABELS.get(f, f), "importance": round(float(v), 5)}
            for f, v in pairs]


def _verdict(tested_markets: int, boot: dict, metrics: dict) -> str:
    bm, bk = metrics.get("brier_model"), metrics.get("brier_market")
    if tested_markets < 60:
        return (f"Data uji baru {tested_markets} pasar — terlalu sedikit untuk menilai model. "
                "Kumpulkan lebih banyak pasar yang sudah selesai lalu latih ulang.")
    span = f"[{boot['low']:+.5f}, {boot['high']:+.5f}]"
    if boot["low"] is not None and boot["low"] > 0:
        return (f"Model lebih tepat dari harga pasar pada {tested_markets} pasar yang tidak pernah "
                f"dilihatnya: Brier {bm:.4f} lawan {bk:.4f}, rentang 90% selisihnya {span} tidak "
                "menyentuh nol. EV positif dari model ini layak diperiksa — tetap dengan ukuran kecil, "
                "dan tetap dibuktikan di paper test.")
    if boot["mean"] is not None and boot["mean"] > 0:
        return (f"Model sedikit lebih tepat (Brier {bm:.4f} lawan {bk:.4f}), tetapi rentang 90% "
                f"selisihnya {span} melewati nol — belum bisa dibedakan dari kebetulan. Pakai sebagai "
                "bahan periksa, bukan sinyal.")
    return (f"Model TIDAK lebih tepat dari harga pasar (Brier {bm:.4f} lawan {bk:.4f}, selisih {span}). "
            "Harga pasar sudah memuat informasi yang dipakai model. EV positif darinya jangan "
            "dipercaya; paper test tetap berguna untuk memastikannya dengan data baru.")


def train(rows: list[dict] | None = None, dataset_meta: dict | None = None,
          progress: Progress = _noop) -> dict:
    import numpy as np
    import joblib
    import sklearn

    if rows is None:
        rows, dataset_meta = history.load_dataset()
    unknown = [r for r in rows if not r.get("deadline_known", True)]
    rows = [r for r in rows if r.get("deadline_known", True) and _in_band(float(r["p"]), TRAIN_BAND)]
    excluded = {"rows": len(unknown), "markets": len({(r["source"], r["market_id"]) for r in unknown})}
    markets = {(r["source"], r["market_id"]) for r in rows}
    if len(rows) < MIN_ROWS or len(markets) < MIN_MARKETS:
        raise NotEnoughData(
            f"Butuh minimal {MIN_ROWS} baris dari {MIN_MARKETS} pasar; tersedia {len(rows)} baris "
            f"dari {len(markets)} pasar. Kumpulkan data dulu (tombol Latih model mengambilnya).")
    rows.sort(key=lambda r: (r["deadline_ms"], str(r["market_id"]), -r["offset_days"]))
    X, y, p, groups, w = _arrays(rows)

    names = list(_estimators())
    oos = {name: np.full(len(rows), np.nan) for name in names}
    last_fold: dict[str, tuple] = {}
    folds = _folds(rows)
    for k, train_idx, test_idx in folds:
        progress("validasi walk-forward", k, len(folds), f"blok uji {k} dari {len(folds)}")
        if len(train_idx) < 100 or len(set(y[train_idx].tolist())) < 2 or not test_idx:
            continue
        for name, estimator in _estimators().items():
            _fit(estimator, X[train_idx], y[train_idx], w[train_idx])
            oos[name][test_idx] = estimator.predict_proba(X[test_idx])[:, 1]
            last_fold[name] = (estimator, test_idx)

    tested = ~np.isnan(oos[names[0]])
    if tested.sum() < 100:
        raise NotEnoughData("Terlalu sedikit baris yang bisa diuji secara walk-forward. "
                            "Tambah jumlah pasar lalu latih ulang.")
    contested = tested & (p >= EVAL_BAND[0]) & (p <= EVAL_BAND[1])

    results = {name: {"all": _metrics(y[tested], oos[name][tested], p[tested]),
                      "contested": _metrics(y[contested], oos[name][contested], p[contested])}
               for name in names}
    chosen = min(names, key=lambda n: results[n]["contested"].get("log_loss_model", 9e9))
    q = oos[chosen]
    boot = _bootstrap(y[contested], q[contested], p[contested], groups[contested])
    tested_idx = np.where(tested)[0]
    tested_rows = [rows[i] for i in tested_idx]
    tested_markets = len({(r["source"], r["market_id"]) for r in tested_rows})

    from src.sports.betting import calibration_table

    progress("melatih model akhir", 0, 1, "semua data")
    final = _fit(_estimators()[chosen], X, y, w)
    estimator, idx = last_fold.get(chosen, (None, []))
    importance = _importance(estimator, X[idx], y[idx]) if estimator is not None and len(idx) else []

    trained_at = store.now_iso()
    model_id = f"deadline-{chosen}-{datetime.now(timezone.utc):%Y%m%d-%H%M}"
    metrics = results[chosen]
    beats = bool(boot["low"] is not None and boot["low"] > 0 and tested_markets >= 60)
    by_source = Counter(r["source"] for r in rows)

    report = {
        "model_id": model_id,
        "algorithm": chosen,
        "trained_at": trained_at,
        "sklearn": sklearn.__version__,
        "features": [{"name": f, "label": features.LABELS[f]} for f in features.FEATURES],
        "data": {
            "rows": len(rows), "markets": len(markets), "by_source": dict(by_source),
            "tested_rows": int(tested.sum()), "tested_markets": tested_markets,
            "contested_rows": int(contested.sum()), "folds": len(folds),
            "offsets_days": list(features.OFFSETS_DAYS),
            "first_deadline": store.ms_to_iso(rows[0]["deadline_ms"]),
            "last_deadline": store.ms_to_iso(rows[-1]["deadline_ms"]),
            "excluded_unknown_deadline": excluded,
            "dataset": dataset_meta or {},
        },
        "candidates": results,
        "metrics": metrics,
        "bootstrap": boot,
        "beats_market": beats,
        "verdict": _verdict(tested_markets, boot, metrics["contested"]),
        "calibration_model": calibration_table(q[contested].tolist(), y[contested].tolist(), bins=10),
        "calibration_market": calibration_table(p[contested].tolist(), y[contested].tolist(), bins=10),
        "backtest": _backtest(tested_rows, q[tested_idx]),
        "deadline_effect": _deadline_effect(tested_rows, q[tested_idx]),
        "coefficients": _coefficients(X, y, w),
        "importance": importance,
        "caveats": [
            f"Model dipilih dari {len(names)} kandidat berdasarkan data uji yang sama; skor kandidat "
            "terpilih karena itu sedikit optimistis.",
            "Data latih sebagian besar dari Manifold (uang main). Perilaku pasar uang sungguhan bisa "
            "berbeda; paper test di Polymarket adalah ujian yang sebenarnya.",
            f"{excluded['markets']} pasar Manifold yang diselesaikan sebelum jadwal tidak dipakai: Manifold "
            "menimpa tanggal tutupnya dengan waktu resolusi, jadi tenggat aslinya tidak diketahui. Memakainya "
            "mengajari model bahwa YES makin mungkin menjelang tenggat — kebalikan dari data yang jujur. "
            "Akibat sisanya: resolusi YES yang terjadi beberapa hari sebelum jadwal ikut terbuang, sehingga "
            "peluang YES menjelang tenggat bisa sedikit diremehkan.",
            "Skor dihitung pada snapshot dengan harga 5–95%; snapshot yang sudah nyaris pasti "
            "membuat model mana pun tampak hebat.",
        ],
    }

    store.MODELS.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "estimator": final, "features": list(features.FEATURES), "model_id": model_id,
        "algorithm": chosen, "trained_at": trained_at, "sklearn": sklearn.__version__,
        "beats_market": beats, "max_offset_days": features.MAX_OFFSET_DAYS,
    }, MODEL_PATH)
    store.write_json(REPORT_PATH, report)
    _CACHE["mtime"] = None
    return report


def train_from_sources(sources: tuple[str, ...] = ("manifold", "polymarket"), max_markets: int = 600,
                       progress: Progress = _noop, offline: bool = False) -> dict:
    built = history.build(sources=sources, max_markets=max_markets, progress=progress, offline=offline)
    return train(built["rows"], built["meta"], progress=progress)


# ---------------------------------------------------------------- prediction

_CACHE: dict[str, Any] = {"mtime": None, "bundle": None, "report": None}


def load() -> dict | None:
    if not MODEL_PATH.exists():
        return None
    mtime = MODEL_PATH.stat().st_mtime
    if _CACHE["mtime"] != mtime:
        import joblib

        try:
            bundle = joblib.load(MODEL_PATH)
        except Exception:  # noqa: BLE001 — a corrupt or incompatible file means "no model"
            return None
        _CACHE.update(mtime=mtime, bundle=bundle, report=store.read_json(REPORT_PATH, {}) or {})
    return _CACHE["bundle"]


def report() -> dict:
    load()
    return _CACHE.get("report") or store.read_json(REPORT_PATH, {}) or {}


def status() -> dict:
    bundle = load()
    rep = report() if bundle else {}
    meta = store.read_json(history.DATASET_META, {}) or {}
    contested = (rep.get("metrics") or {}).get("contested") or {}
    return {
        "trained": bool(bundle),
        "model_id": bundle.get("model_id") if bundle else None,
        "algorithm": bundle.get("algorithm") if bundle else None,
        "trained_at": bundle.get("trained_at") if bundle else None,
        "beats_market": bool(bundle.get("beats_market")) if bundle else False,
        "verdict": rep.get("verdict") or ("Model belum dilatih." if not bundle else None),
        "brier_model": contested.get("brier_model"),
        "brier_market": contested.get("brier_market"),
        "tested_markets": (rep.get("data") or {}).get("tested_markets"),
        "dataset": meta,
        "max_offset_days": features.MAX_OFFSET_DAYS,
    }


def _drivers(bundle: dict, feats: dict) -> list[dict]:
    """Largest corrections to the market's log-odds for this one prediction."""
    estimator = bundle.get("estimator")
    if not isinstance(estimator, AnchoredLogit):
        return []
    out = []
    for i, name in enumerate(bundle["features"]):
        contribution = float(estimator.coef_[i] * (feats[name] - estimator.mean_[i]) / estimator.scale_[i])
        out.append({"feature": name, "label": features.LABELS.get(name, name),
                    "contribution": round(contribution, 3)})
    out.sort(key=lambda d: -abs(d["contribution"]))
    return [d for d in out[:4] if abs(d["contribution"]) >= 0.005]


def predict_market(snapshot: dict, now_ms: int | None = None) -> dict:
    """Model probability for the first outcome of a live market snapshot."""
    # numpy diimpor setelah semua jalan keluar "tidak ada prediksi": daftar
    # tenggat tetap tampil (harga, arah, EV) di pemasangan tanpa numpy/sklearn,
    # dengan kolom model berisi alasannya.
    bundle = load()
    if not bundle:
        return {"available": False,
                "reason": "Model belum dilatih. Buka halaman Paper Test lalu tekan Latih model."}
    now = now_ms or store.now_ms()
    live = features.live_features(snapshot, now)
    if live is None:
        return {"available": False, "reason": "Harga atau tenggat pasar ini tidak tersedia."}
    limit = float(bundle.get("max_offset_days") or features.MAX_OFFSET_DAYS)
    if live["days_left"] < min(features.OFFSETS_DAYS) * 0.8:
        return {"available": False, "days_left": round(live["days_left"], 3),
                "reason": (f"Tinggal {live['days_left'] * 24:.1f} jam. Model dilatih dari snapshot paling dekat "
                           f"{min(features.OFFSETS_DAYS) * 24:.0f} jam sebelum tenggat; di bawah itu ia hanya menebak.")}
    if live["days_left"] > limit * 1.5:
        return {"available": False, "days_left": round(live["days_left"], 2),
                "reason": (f"Model dilatih untuk tenggat ≤ {limit:g} hari; pasar ini masih "
                           f"{live['days_left']:.0f} hari lagi. Di luar rentang itu model hanya menebak.")}

    import numpy as np

    X = np.array([[live["features"][f] for f in bundle["features"]]], dtype=float)
    prob = float(bundle["estimator"].predict_proba(X)[0, 1])
    price = float(snapshot["p_yes"])
    rep = _CACHE.get("report") or {}
    contested = (rep.get("metrics") or {}).get("contested") or {}
    caveats = []
    if live["imputed_age"]:
        caveats.append("Tanggal pembuatan pasar tidak tersedia; umur pasar diperkirakan.")
    if not live["momentum_known"]:
        caveats.append("Gerak harga 24 jam tidak tersedia; dianggap nol.")
    if prob > price + 0.005:
        direction = "naik"
    elif prob < price - 0.005:
        direction = "turun"
    else:
        direction = "datar"
    return {
        "available": True,
        "prob_yes": round(prob, 4),
        "market_prob_yes": round(price, 4),
        "edge_pp": round((prob - price) * 100, 2),
        "direction": direction,
        "days_left": round(live["days_left"], 3),
        "bucket": features.deadline_bucket(live["days_left"]),
        "model_id": bundle.get("model_id"),
        "algorithm": bundle.get("algorithm"),
        "trained_at": bundle.get("trained_at"),
        "beats_market": bool(bundle.get("beats_market")),
        "brier_model": contested.get("brier_model"),
        "brier_market": contested.get("brier_market"),
        "drivers": _drivers(bundle, live["features"]),
        "features": {k: round(v, 4) for k, v in live["features"].items()},
        "caveats": caveats,
    }
