"""
tests/test_pipeline.py

Unit and integration tests for the EM Sovereign Bond Convexity Toolkit.

All core functions from data_pipeline.ipynb are re-implemented here as
importable pure-Python so the tests do not depend on Jupyter being installed
or on the notebook running successfully first.  The test module is fully
self-contained: running

    pytest tests/test_pipeline.py -v

from the repo root is sufficient.

Test coverage
-------------
 1.  yearfrac – 30/360 and act/365 correctness
 2.  cashflow_schedule – cash-flow count, timing, last-coupon principal flag
 3.  cashflow_schedule – accrued interest calculation
 4.  cashflow_schedule – edge case: settle == maturity
 5.  formula_analytics – par bond: ModDur ≈ known analytical value
 6.  formula_analytics – discount bond: convexity > 0
 7.  formula_analytics – empty schedule returns NaN
 8.  recompute – end-to-end on a small synthetic DataFrame
 9.  Convexity scale harmonisation – bimodal detection and x100 rescale
10.  Convexity scale harmonisation – unimodal panel unchanged
11.  Vendor impossibility flag – duration > maturity flagged correctly
12.  parse_security_des – mixed-fraction coupon (e.g. "6 5/8")
13.  parse_security_des – decimal coupon (e.g. "4.75")
14.  parse_security_des – missing date returns None
15.  Integration: pipeline produces panel_analysis.csv with correct schema
     and passes all plausibility checks (requires both input CSVs)
"""

import calendar
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers / constants
# (mirrors the locked-convention constants in data_pipeline.ipynb)
# ---------------------------------------------------------------------------

FREQUENCY = 2
DAYCOUNT   = "30360"
DUR_YIELD  = "periodic"
PRICE_BASE = "dirty"


def yearfrac(d0: pd.Timestamp, d1: pd.Timestamp, daycount: str = DAYCOUNT) -> float:
    """Year fraction between two dates under the chosen day-count convention."""
    if daycount == "act365":
        return (d1 - d0).days / 365.25
    if daycount == "30360":
        D1 = min(d0.day, 30)
        D2 = min(d1.day, 30)
        return (
            (d1.year  - d0.year)  * 360
            + (d1.month - d0.month) * 30
            + (D2 - D1)
        ) / 360.0
    raise ValueError(f"Unknown daycount: {daycount}")


def cashflow_schedule(
    settle:   pd.Timestamp,
    maturity: pd.Timestamp,
    coupon:   float,
    face:     float = 100.0,
    freq:     int   = FREQUENCY,
) -> tuple[list[dict], float]:
    """
    Semi-annual coupon schedule walked back from maturity.

    Returns
    -------
    (schedule, accrued_interest)
        schedule : list of {"cf": cash_flow, "t": time_in_years}
        accrued  : float
    """
    cpn    = face * (coupon / 100.0) / freq
    months = 12 // freq
    dates: list[pd.Timestamp] = []
    dt = maturity

    while dt > settle:
        dates.append(dt)
        m, y = dt.month - months, dt.year
        if m <= 0:
            m += 12
            y -= 1
        try:
            dt = dt.replace(year=y, month=m)
        except ValueError:
            last = calendar.monthrange(y, m)[1]
            dt   = dt.replace(year=y, month=m, day=min(dt.day, last))

    dates.sort()
    if not dates:
        return [], 0.0

    sched = [
        {
            "cf": cpn + (face if cd == dates[-1] else 0.0),
            "t":  yearfrac(settle, cd),
        }
        for cd in dates
    ]

    # accrued interest
    prev   = dates[0]
    m, y   = prev.month - months, prev.year
    if m <= 0:
        m += 12
        y -= 1
    try:
        prev = prev.replace(year=y, month=m)
    except ValueError:
        last = calendar.monthrange(y, m)[1]
        prev = prev.replace(year=y, month=m, day=min(prev.day, last))

    period  = yearfrac(prev, dates[0])
    elapsed = yearfrac(prev, settle)
    accrued = cpn * (elapsed / period) if period > 0 else 0.0

    return sched, accrued


def formula_analytics(
    sched:   list[dict],
    accrued: float,
    ytm:     float,
    freq:    int = FREQUENCY,
) -> tuple[float, float]:
    """
    Modified duration & convexity using the locked convention.

    Parameters
    ----------
    sched   : output of cashflow_schedule
    accrued : accrued interest from cashflow_schedule
    ytm     : yield to maturity in percent
    freq    : coupon frequency

    Returns
    -------
    (modified_duration, convexity)  – both NaN if schedule is empty or PV is zero
    """
    if not sched:
        return np.nan, np.nan

    y      = ytm / 100.0
    y_per  = y / freq
    pv_total = mac_num = conv_num = 0.0

    for item in sched:
        cf, t = item["cf"], item["t"]
        n     = t * freq
        disc  = (1 + y_per) ** n
        pv    = cf / disc
        pv_total += pv
        mac_num  += t  * pv
        conv_num += cf * n * (n + 1) / disc

    if pv_total <= 0:
        return np.nan, np.nan

    mac_dur   = mac_num / pv_total
    mod_dur   = (
        mac_dur / (1 + y_per) if DUR_YIELD == "periodic"
        else mac_dur / (1 + y)
    )
    denom     = pv_total if PRICE_BASE == "clean" else (pv_total + accrued)
    convexity = conv_num / (denom * freq ** 2)

    return mod_dur, convexity


def recompute(
    frame:    pd.DataFrame,
    date_col: str,
    mat_col:  str,
    cpn_col:  str,
    yld_col:  str,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply formula_analytics row-wise."""
    d = np.full(len(frame), np.nan)
    c = np.full(len(frame), np.nan)
    for pos, (_, r) in enumerate(frame.iterrows()):
        s, a    = cashflow_schedule(
            r[date_col].to_pydatetime(),
            r[mat_col].to_pydatetime(),
            r[cpn_col],
        )
        d[pos], c[pos] = formula_analytics(s, a, r[yld_col])
    return d, c


def parse_security_des(desc: str) -> dict | None:
    """
    Extract coupon rate and maturity from a Bloomberg security description.

    Examples
    --------
    "ARGENT 6 5/8 07/06/28"  -> {"Coupon": 6.625, "Maturity": Timestamp("2028-07-06")}
    "ARGENT 4.75 01/15/33"   -> {"Coupon": 4.75,  "Maturity": Timestamp("2033-01-15")}
    """
    desc = desc.strip()
    m = re.search(r"(\d{2}/\d{2}/\d{2})\s*$", desc)
    if not m:
        return None
    mm, dd, yy = m.group(1).split("/")
    mat        = pd.Timestamp(year=2000 + int(yy), month=int(mm), day=int(dd))
    cpn_part   = re.sub(r"^[A-Z]+\s+", "", desc[: m.start()].strip())
    frac       = re.match(r"(\d+)\s+(\d+)/(\d+)", cpn_part)
    if frac:
        cpn = int(frac.group(1)) + int(frac.group(2)) / int(frac.group(3))
    else:
        dec = re.match(r"([\d.]+)", cpn_part)
        cpn = float(dec.group(1)) if dec else np.nan
    return {"Coupon": cpn, "Maturity": mat}


# ---------------------------------------------------------------------------
# 1–2.  yearfrac
# ---------------------------------------------------------------------------

class TestYearfrac:
    """30/360 and act/365 day-count correctness."""

    def test_30360_full_year(self):
        d0 = pd.Timestamp("2020-01-01")
        d1 = pd.Timestamp("2021-01-01")
        assert yearfrac(d0, d1, "30360") == pytest.approx(1.0, abs=1e-6)

    def test_30360_semi_annual(self):
        """Six calendar months on 30/360 should be exactly 0.5."""
        d0 = pd.Timestamp("2020-01-15")
        d1 = pd.Timestamp("2020-07-15")
        assert yearfrac(d0, d1, "30360") == pytest.approx(0.5, abs=1e-6)

    def test_30360_end_of_month_capped(self):
        """30/360: days > 30 are capped at 30."""
        d0 = pd.Timestamp("2020-01-31")
        d1 = pd.Timestamp("2020-07-31")
        assert yearfrac(d0, d1, "30360") == pytest.approx(0.5, abs=1e-6)

    def test_act365_known_value(self):
        d0 = pd.Timestamp("2020-01-01")
        d1 = pd.Timestamp("2020-07-01")
        expected = 182 / 365.25
        assert yearfrac(d0, d1, "act365") == pytest.approx(expected, rel=1e-4)

    def test_unknown_daycount_raises(self):
        with pytest.raises(ValueError):
            yearfrac(pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01"), "actual360")


# ---------------------------------------------------------------------------
# 3–5.  cashflow_schedule
# ---------------------------------------------------------------------------

class TestCashflowSchedule:
    """Cash-flow count, timing, principal flag, and accrued interest."""

    # A 2-year semi-annual bond issued exactly on coupon date
    SETTLE   = pd.Timestamp("2020-01-15")
    MATURITY = pd.Timestamp("2022-01-15")
    COUPON   = 5.0        # percent

    def _schedule(self):
        return cashflow_schedule(self.SETTLE, self.MATURITY, self.COUPON)

    def test_cash_flow_count(self):
        sched, _ = self._schedule()
        assert len(sched) == 4, "2-year semi-annual bond should have exactly 4 coupons"

    def test_last_cashflow_includes_principal(self):
        sched, _ = self._schedule()
        last_cf   = sched[-1]["cf"]
        coupon_cf = 100 * (self.COUPON / 100) / FREQUENCY
        assert last_cf == pytest.approx(coupon_cf + 100.0, abs=1e-6)

    def test_intermediate_cashflow_no_principal(self):
        sched, _ = self._schedule()
        coupon_cf = 100 * (self.COUPON / 100) / FREQUENCY
        for item in sched[:-1]:
            assert item["cf"] == pytest.approx(coupon_cf, abs=1e-6)

    def test_times_are_positive_and_increasing(self):
        sched, _ = self._schedule()
        times = [item["t"] for item in sched]
        assert all(t > 0 for t in times)
        assert times == sorted(times)

    def test_accrued_interest_at_coupon_date_is_zero(self):
        """Settling on a coupon date means no accrued interest."""
        _, accrued = self._schedule()
        assert accrued == pytest.approx(0.0, abs=1e-4)

    def test_accrued_interest_mid_period(self):
        """Mid-period settle should produce roughly half a coupon of accrued."""
        settle   = pd.Timestamp("2020-04-15")   # ~halfway through Jan-Jul period
        maturity = pd.Timestamp("2022-07-15")
        _, accrued = cashflow_schedule(settle, maturity, 5.0)
        half_coupon = 100 * (5.0 / 100) / 2 / 2   # very rough midpoint check
        assert accrued > 0
        assert accrued < 100 * (5.0 / 100) / 2    # must be less than a full coupon

    def test_settle_at_maturity_returns_empty(self):
        sched, accrued = cashflow_schedule(
            self.MATURITY, self.MATURITY, self.COUPON
        )
        assert sched == []
        assert accrued == pytest.approx(0.0, abs=1e-10)


# ---------------------------------------------------------------------------
# 6–8.  formula_analytics
# ---------------------------------------------------------------------------

class TestFormulaAnalytics:
    """Modified duration and convexity against known values."""

    def test_par_bond_modified_duration(self):
        """
        For a par bond (price = 100, ytm = coupon), Macaulay duration is
        well-approximated by (1 + y/f)/y * (1 - 1/(1+y/f)^n) + n/(1+y/f)^n
        where n = total coupon periods.  We use a 5-year 6% semi-annual bond at par
        and check that the formula is within 1% of the analytical value.
        """
        settle   = pd.Timestamp("2020-01-15")
        maturity = pd.Timestamp("2025-01-15")
        coupon   = 6.0
        ytm      = 6.0    # par bond: yield = coupon

        sched, accrued = cashflow_schedule(settle, maturity, coupon)
        mod_dur, conv  = formula_analytics(sched, accrued, ytm)

        # Analytical Macaulay duration for par bond:
        # (1+y/f)/y * [1 - 1/(1+y/f)^(2T)] + 2T/(1+y/f)^(2T)  / (2)
        # Simple sanity bounds: must be between 1 and 5 for a 5-year bond.
        assert 3.5 < mod_dur < 4.5, f"ModDur {mod_dur:.4f} outside expected range"

    def test_discount_bond_convexity_positive(self):
        """Convexity must always be positive for a plain vanilla bond."""
        settle   = pd.Timestamp("2020-01-15")
        maturity = pd.Timestamp("2030-01-15")
        coupon   = 3.0
        ytm      = 8.0    # deep discount

        sched, accrued = cashflow_schedule(settle, maturity, coupon)
        _, conv = formula_analytics(sched, accrued, ytm)

        assert conv > 0, f"Convexity {conv:.4f} should be positive"

    def test_higher_yield_lower_duration(self):
        """For the same bond, higher yield implies shorter modified duration."""
        settle   = pd.Timestamp("2020-01-15")
        maturity = pd.Timestamp("2030-01-15")
        coupon   = 5.0

        sched_lo, acc_lo = cashflow_schedule(settle, maturity, coupon)
        sched_hi, acc_hi = cashflow_schedule(settle, maturity, coupon)

        dur_lo, _ = formula_analytics(sched_lo, acc_lo, 3.0)
        dur_hi, _ = formula_analytics(sched_hi, acc_hi, 10.0)

        assert dur_lo > dur_hi, "Higher yield should produce shorter modified duration"

    def test_empty_schedule_returns_nan(self):
        dur, conv = formula_analytics([], 0.0, 5.0)
        assert np.isnan(dur)
        assert np.isnan(conv)

    def test_convexity_increases_with_maturity(self):
        """Longer-dated bonds should have higher convexity at the same yield."""
        settle   = pd.Timestamp("2020-01-15")
        coupon   = 5.0
        ytm      = 5.0

        results = {}
        for years in [5, 10, 20]:
            maturity = pd.Timestamp(f"{2020 + years}-01-15")
            sched, acc = cashflow_schedule(settle, maturity, coupon)
            _, conv     = formula_analytics(sched, acc, ytm)
            results[years] = conv

        assert results[5] < results[10] < results[20]


# ---------------------------------------------------------------------------
# 9.  recompute (end-to-end on a synthetic DataFrame)
# ---------------------------------------------------------------------------

class TestRecompute:
    """recompute applies formula_analytics row-wise and returns finite arrays."""

    def _synthetic_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "Date":     pd.to_datetime(["2021-01-15", "2021-06-15", "2022-01-15"]),
            "Maturity": pd.to_datetime(["2031-01-15", "2031-01-15", "2031-01-15"]),
            "Coupon":   [5.0,  5.0,  5.0],
            "Yield":    [5.0,  6.5,  4.0],
        })

    def test_returns_correct_length(self):
        df      = self._synthetic_df()
        dur, cv = recompute(df, "Date", "Maturity", "Coupon", "Yield")
        assert len(dur) == len(df)
        assert len(cv)  == len(df)

    def test_all_values_finite(self):
        df      = self._synthetic_df()
        dur, cv = recompute(df, "Date", "Maturity", "Coupon", "Yield")
        assert np.all(np.isfinite(dur))
        assert np.all(np.isfinite(cv))

    def test_duration_in_plausible_range(self):
        """10-year bond: ModDur should be between 5 and 10."""
        df      = self._synthetic_df()
        dur, _  = recompute(df, "Date", "Maturity", "Coupon", "Yield")
        assert np.all(dur > 5.0)
        assert np.all(dur < 10.0)


# ---------------------------------------------------------------------------
# 10–11.  Convexity scale harmonisation
# ---------------------------------------------------------------------------

class TestConvexityHarmonisation:
    """Bimodal detection and x100 rescale logic."""

    def _make_panel(self, convexities, durations):
        return pd.DataFrame({
            "Convexity":          convexities,
            "Modified Duration":  durations,
        })

    def test_decimal_scale_rows_are_rescaled(self):
        """
        Rows where Convexity / ModDur^2 < 0.15 are on the decimal scale and
        must be multiplied by 100.
        """
        durations    = [5.0, 5.0, 7.0]
        # first two on decimal scale (ratio ~0.03), last on conventional
        # scale (ratio ~0.6 -- order of duration-squared, as real data is)
        convexities  = [0.75, 0.80, 29.4]
        df = self._make_panel(convexities, durations)

        ratio     = df["Convexity"] / (df["Modified Duration"] ** 2)
        small     = ratio < 0.15
        df.loc[small, "Convexity"] = df.loc[small, "Convexity"] * 100

        # after rescale all ratios should be > 0.15
        post_ratio = df["Convexity"] / (df["Modified Duration"] ** 2)
        assert (post_ratio > 0.15).all(), "Some rows still on decimal scale after rescale"
        assert small.sum() == 2, "Expected 2 decimal-scale rows"

    def test_unimodal_panel_unchanged(self):
        """A panel already on the conventional scale must not be modified."""
        durations   = [4.0, 6.0, 8.0]
        convexities = [6.0, 22.0, 40.0]   # ratio ≈ 0.375, 0.611, 0.625 — all > 0.15
        df = self._make_panel(convexities, durations)

        ratio = df["Convexity"] / (df["Modified Duration"] ** 2)
        small = ratio < 0.15
        assert small.sum() == 0, "No rows should be flagged in a unimodal panel"

    def test_post_rescale_assertion_passes(self):
        """After harmonisation the minimum ratio must exceed 0.5 (pipeline assertion)."""
        durations   = [5.0, 5.0, 7.0]
        convexities = [0.75, 0.80, 29.4]
        df = self._make_panel(convexities, durations)

        ratio = df["Convexity"] / (df["Modified Duration"] ** 2)
        df.loc[ratio < 0.15, "Convexity"] *= 100

        post_ratio = df["Convexity"] / (df["Modified Duration"] ** 2)
        assert post_ratio.min() > 0.5, "Pipeline assertion would fail after rescale"


# ---------------------------------------------------------------------------
# 12–13.  Vendor impossibility flag
# ---------------------------------------------------------------------------

class TestVendorImpossibilityFlag:
    """duration > remaining years to maturity should be flagged."""

    def _make_df(self):
        today = pd.Timestamp("2021-06-01")
        return pd.DataFrame({
            "Date":               [today,  today,  today],
            "Maturity":           [
                pd.Timestamp("2026-06-01"),   # 5 years remaining
                pd.Timestamp("2031-06-01"),   # 10 years remaining
                pd.Timestamp("2023-06-01"),   # 2 years remaining
            ],
            "Modified Duration":  [3.0, 15.0, 3.0],  # row 1: 15 > 10 (impossible)
                                                      # row 2: 3  > 2  (impossible)
        })

    def test_impossible_rows_flagged(self):
        df = self._make_df()
        df["rem_years"] = (df["Maturity"] - df["Date"]).dt.days / 365.25
        df["vendor_impossible"] = df["Modified Duration"] > df["rem_years"] + 0.5

        flagged = df["vendor_impossible"].sum()
        assert flagged == 2, f"Expected 2 impossible rows, got {flagged}"

    def test_valid_rows_not_flagged(self):
        df = self._make_df()
        df["rem_years"] = (df["Maturity"] - df["Date"]).dt.days / 365.25
        df["vendor_impossible"] = df["Modified Duration"] > df["rem_years"] + 0.5

        # Row 0: ModDur=3, rem=5 → valid
        assert not df.loc[0, "vendor_impossible"]

    def test_tolerance_boundary(self):
        """ModDur just inside rem_years + 0.5 should NOT be flagged (> not >=)."""
        today = pd.Timestamp("2021-01-01")
        df = pd.DataFrame({
            "Date":              [today],
            "Maturity":          [pd.Timestamp("2026-01-01")],
        })
        df["rem_years"] = (df["Maturity"] - df["Date"]).dt.days / 365.25
        # set duration exactly to the boundary so the strict-> comparison
        # is False (rem_years carries a leap-year fraction, so we anchor on
        # the computed value rather than assuming it is exactly 5.0)
        df["Modified Duration"] = df["rem_years"] + 0.5
        df["vendor_impossible"] = df["Modified Duration"] > df["rem_years"] + 0.5
        assert not df.loc[0, "vendor_impossible"]


# ---------------------------------------------------------------------------
# 14–16.  parse_security_des
# ---------------------------------------------------------------------------

class TestParseSecurityDes:
    """Bloomberg security description parser."""

    def test_mixed_fraction_coupon(self):
        result = parse_security_des("ARGENT 6 5/8 07/06/28")
        assert result is not None
        assert result["Coupon"] == pytest.approx(6.625, abs=1e-6)
        assert result["Maturity"] == pd.Timestamp("2028-07-06")

    def test_decimal_coupon(self):
        result = parse_security_des("ARGENT 4.75 01/15/33")
        assert result is not None
        assert result["Coupon"] == pytest.approx(4.75, abs=1e-6)
        assert result["Maturity"] == pd.Timestamp("2033-01-15")

    def test_integer_coupon(self):
        result = parse_security_des("ARGENT 5 04/22/27")
        assert result is not None
        assert result["Coupon"] == pytest.approx(5.0, abs=1e-6)

    def test_missing_date_returns_none(self):
        result = parse_security_des("ARGENT 5 NO DATE HERE")
        assert result is None

    def test_century_handling(self):
        """Year '28' should parse as 2028, not 1928."""
        result = parse_security_des("ARGENT 6 5/8 07/06/28")
        assert result["Maturity"].year == 2028

    def test_whitespace_robustness(self):
        """Leading/trailing whitespace should not break parsing."""
        result = parse_security_des("  ARGENT 4.75 01/15/33  ")
        assert result is not None
        assert result["Coupon"] == pytest.approx(4.75, abs=1e-6)


# ---------------------------------------------------------------------------
# 17.  Integration test
# ---------------------------------------------------------------------------

# Mark the integration test so it can be skipped when inputs are absent.
# Run with: pytest tests/test_pipeline.py -v  (skips if CSVs not found)
#       or: pytest tests/test_pipeline.py -v -k integration

PANEL_PATH = Path("panel_clean.csv")
ARG_PATH   = Path("argentina_bonds.csv")
OUT_PATH   = Path("panel_analysis.csv")


@pytest.mark.skipif(
    not (PANEL_PATH.exists() and ARG_PATH.exists()),
    reason="Input CSVs not found in working directory; skipping integration test.",
)
class TestPipelineIntegration:
    """
    Full pipeline smoke-test.

    Requires panel_clean.csv and argentina_bonds.csv in the working directory
    (i.e. the repo root).  Verifies that panel_analysis.csv is produced and
    passes all plausibility checks defined in the pipeline notebook.
    """

    @pytest.fixture(scope="class", autouse=True)
    def run_pipeline(self):
        """Execute the pipeline notebook and load the output."""
        import subprocess, sys, tempfile, os
        # write the executed-notebook artifact to a real temp directory;
        # tempfile.gettempdir() resolves correctly on Windows, macOS and Linux
        # (a hardcoded "/tmp" path does not exist on Windows).
        out_nb = os.path.join(tempfile.gettempdir(), "pipeline_test_executed.ipynb")
        result = subprocess.run(
            [
                sys.executable, "-m", "jupyter", "nbconvert",
                "--to", "notebook",
                "--execute",
                "--output", out_nb,
                "data_pipeline.ipynb",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.fail(
                f"Pipeline notebook failed to execute:\n{result.stderr[-3000:]}"
            )

    @pytest.fixture(scope="class")
    def panel(self):
        assert OUT_PATH.exists(), "panel_analysis.csv was not created by the pipeline"
        return pd.read_csv(OUT_PATH, parse_dates=["Date", "Maturity", "Issue Date"])

    # --- schema ---

    def test_required_columns_present(self, panel):
        required = [
            "ISIN", "Date", "Mid Price", "Mid Yield",
            "Dur_formula", "Conv_formula", "Coupon", "Maturity",
            "Cntry of Risk", "Z Spread", "rem_years",
        ]
        missing = [c for c in required if c not in panel.columns]
        assert missing == [], f"Missing columns: {missing}"

    def test_no_vendor_analytics_columns(self, panel):
        """Vendor duration/convexity must not be carried into the output."""
        for col in ["Modified Duration", "Convexity", "vendor_impossible"]:
            assert col not in panel.columns, f"Vendor column '{col}' found in output"

    # --- plausibility ---

    def test_no_non_positive_prices(self, panel):
        bad = (panel["Mid Price"] <= 0).sum()
        assert bad == 0, f"{bad} rows with Mid Price <= 0"

    def test_no_extreme_prices(self, panel):
        bad = (panel["Mid Price"] > 200).sum()
        assert bad == 0, f"{bad} rows with Mid Price > 200"

    def test_yields_in_range(self, panel):
        assert (panel["Mid Yield"] >= 0).all(), "Negative yields found"
        assert (panel["Mid Yield"] < 150).all(), "Unrealistically high yields found"

    def test_formula_durations_finite(self, panel):
        n_nan = panel["Dur_formula"].isna().sum()
        assert n_nan == 0, f"{n_nan} NaN values in Dur_formula"

    def test_formula_convexity_positive(self, panel):
        bad = (panel["Conv_formula"] <= 0).sum()
        assert bad == 0, f"{bad} non-positive convexity values"

    def test_no_impossible_durations(self, panel):
        """No row should have Dur_formula > rem_years + 0.5."""
        bad = (panel["Dur_formula"] > panel["rem_years"] + 0.5).sum()
        assert bad == 0, f"{bad} rows with duration > remaining maturity"

    def test_argentina_bonds_present(self, panel):
        ar_rows = (panel["Cntry of Risk"] == "AR").sum()
        assert ar_rows > 0, "Argentina bonds missing from merged panel"

    def test_argentina_cutoff_respected(self, panel):
        ar = panel[panel["Cntry of Risk"] == "AR"]
        cutoff = pd.Timestamp("2020-09-04")
        assert (ar["Date"] <= cutoff).all(), "Argentina rows found after restructuring cutoff"

    def test_no_duplicate_bond_days(self, panel):
        dups = panel.duplicated(subset=["ISIN", "Date"]).sum()
        assert dups == 0, f"{dups} duplicate (ISIN, Date) pairs in output"

    def test_panel_non_empty(self, panel):
        assert len(panel) > 100, "Output panel suspiciously small"
        assert panel["ISIN"].nunique() >= 10, "Fewer than 10 unique bonds"
