"""Market conditions + playbook matching."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.lab.education.conditions import (
    TAG_CHOP,
    TAG_ELEVATED_VOL,
    TAG_RISK_OFF,
    TAG_RISK_ON,
    classify_spy,
)
from src.lab.education.playbook import build_education_payload, match_modules
from src.lab.education.report import build_brief


def _spy_trend(n=250, up=True, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-02", periods=n)
    # smooth trend + noise
    t = np.linspace(0, 1, n)
    base = 400 + (80 if up else -80) * t
    noise = rng.normal(0, 0.5, n)
    close = base + np.cumsum(noise) * 0.1
    high = close + 1.0
    low = close - 1.0
    open_ = close.copy()
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1e8},
        index=idx,
    )


def test_classify_risk_on_uptrend():
    df = _spy_trend(up=True)
    c = classify_spy(df)
    assert TAG_RISK_ON in c.tags
    assert TAG_RISK_OFF not in c.tags
    assert c.evidence["close"] > 0


def test_classify_risk_off_downtrend():
    df = _spy_trend(up=False)
    c = classify_spy(df)
    assert TAG_RISK_OFF in c.tags


def test_chop_when_flat_compressed():
    n = 120
    idx = pd.bdate_range("2024-01-02", periods=n)
    close = np.full(n, 450.0)
    # tiny wiggle last 10 days (near SMA20)
    close[-10:] = 450 + np.linspace(-0.5, 0.5, 10)
    high = close + 0.3
    low = close - 0.3
    # wide range in the portion still inside the last-60 window (bars -60..-11)
    high[-60:-10] = 470
    low[-60:-10] = 430
    df = pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1e8},
        index=idx,
    )
    c = classify_spy(df, as_of=idx[-1].date())
    assert TAG_CHOP in c.tags


def test_playbook_match_risk_off():
    mods = match_modules([TAG_RISK_OFF])
    assert mods
    assert mods[0]["id"] == "risk_off_prep"
    assert len(mods[0]["plays"]) == 3


def test_playbook_specificity_risk_on_chop():
    mods = match_modules([TAG_RISK_ON, TAG_CHOP])
    ids = [m["id"] for m in mods]
    assert "risk_on_chop" in ids
    # more specific should be first
    assert mods[0]["id"] == "risk_on_chop"


def test_build_brief_persists(tmp_path):
    df = _spy_trend(up=True)
    report = build_brief(df, state_dir=tmp_path, persist=True)
    assert "condition" in report and "education" in report
    as_of = report["condition"]["as_of"]
    path = tmp_path / "lab" / "conditions" / f"{as_of}.json"
    assert path.exists()
    edu = build_education_payload(report["condition"]["tags"])
    assert "always" in edu
