# EM Sovereign Bond Convexity Toolkit

A self-contained Python toolkit for computing, cleaning, and analysing **convexity and modified duration** in Emerging Market (EM) sovereign bond panels. The toolkit was developed for a thesis on convexity mispricing under credit stress but is designed for independent reuse by any fixed-income data analyst working with EM sovereign panel data from Refinitiv or Bloomberg.

---

## Repository layout

```
.
├── data_pipeline.ipynb          # Step 1 – data cleaning & harmonisation
├── convexity_analysis_clean.ipynb  # Step 2 – analysis & figures
├── tests/
│   └── test_pipeline.py         # Unit & integration test suite
├── panel_clean.csv              # Example input: Refinitiv panel export
├── argentina_bonds.csv          # Example input: Bloomberg Argentina export
└── README.md
```

The two notebooks are deliberately separated: `data_pipeline.ipynb` is a **pure data-preparation notebook** that produces a single clean output file; `convexity_analysis_clean.ipynb` is a **pure analysis notebook** that consumes that file and performs no cleaning of its own. You can swap in any conforming dataset and re-run either notebook independently.

---

## What the toolkit does

### `data_pipeline.ipynb` – Data Pipeline: EM Sovereign Bond Panel

Takes two raw vendor exports and produces a single, analysis-ready CSV.

| Step | What it does |
|------|--------------|
| 1 | Load panel and standardize column types |
| 2 | **Convexity scale harmonization** – detects and corrects the ~100× field-convention discrepancy that appears in some Refinitiv exports |
| 3 | **Restructuring cutoffs** – drops Ecuador rows after 2020-08-31 and Argentina rows after 2020-09-04, where vendor analytics reference stale pre-restructuring cash-flow schedules |
| 4 | **Locked-convention formula analytics** – recomputes modified duration and convexity from cash flows using 30/360 day-count, periodic-yield modified duration, and dirty-price convexity normalization |
| 5 | **Vendor data-quality flag** – flags rows where `Modified Duration > remaining years to maturity`, which is mathematically impossible and indicates a corrupted vendor record |
| 6 | **Validation** – cross-checks formula values against vendor-reported analytics by yield bucket; confirms implementation |
| 7 | **Argentina adapter & merge** – renames Bloomberg raw field names to the panel schema, parses coupon and maturity from security descriptions, recomputes formula analytics on the same locked convention, then merges into the main panel |
| 8 | Save `panel_analysis.csv` |

### `convexity_analysis_clean.ipynb` – Convexity Mispricing Under Stress

Consumes `panel_analysis.csv` and produces a full set of figures and statistics examining the thesis that the closed-form convexity formula systematically **overstates** true convexity for distressed credit.

| Section | Content |
|---------|---------|
| 1.5 | Exploratory data analysis: schema, missingness, panel balance, univariate distributions, crisis window verification |
| 2 | Price-yield scatter, visual evidence of curvature compression |
| 3 | Level-fit convexity, measuring the overstatement |
| 4 | Overstatement vs. credit quality – the main result |
| 5 | Negative-convexity edge cases |
| 6 | Cash-flow argument: why the formula overstates, by construction |
| 7 | Save outputs to `output_convexity/` |

---

## Use cases outside the thesis

The toolkit is immediately usable for a range of fixed-income data-analysis tasks:

1. **Cross-vendor data harmonization** – the convexity scale-detection and Argentina adapter logic handle the real-world problem of merging Refinitiv and Bloomberg exports that use incompatible field-name schemas and numeric conventions.

2. **Recomputing analytics from scratch** – the `formula_analytics` / `cashflow_schedule` / `recompute` functions (pipeline step 4) implement a fully self-contained, convention-locked duration and convexity calculator. Drop in any bond with a settlement date, maturity, coupon, and yield, and get auditable formula values independent of any vendor.

3. **Auditing vendor-reported analytics** – the impossibility flag (duration > maturity) and the yield-bucket validation table are reusable quality checks for any sovereign or corporate bond panel.

4. **Convexity-under-stress research** – the analysis notebook's pre/crisis/post regime framework, EDA structure, and yield-bucketed comparison methodology apply directly to any study of bond analytics around a market-stress event (e.g. a default, sanctions, central-bank shock).

5. **Panel EDA template** – the six-check EDA block (schema, missingness, panel balance, univariate distributions, crisis window, bivariate structure) is a reusable template for any time-series panel of financial instruments.

---

## Requirements

### Python version

Python 3.9 or later.

### Dependencies

Install all dependencies with:

```bash
pip install -r requirements.txt
```

`requirements.txt`:

```
numpy>=1.24
pandas>=2.0
matplotlib>=3.7
jupyter>=1.0
pytest>=7.4
```

No other libraries are required. The pipeline and analysis notebooks use only the Python standard library plus the packages above.

---

## Installation

```bash
git clone https://github.com/<your-username>/em-convexity-toolkit.git
cd em-convexity-toolkit
pip install -r requirements.txt
```

---

## Input requirements

### `panel_clean.csv` — Refinitiv panel export

One row per bond per observation date. The following columns are required (names must match exactly, including capitalisation and spacing):

| Column | Type | Description |
|--------|------|-------------|
| `ISIN` | string | Bond identifier |
| `Date` | date | Observation date (`YYYY-MM-DD` or any pandas-parseable format) |
| `Maturity` | date | Bond maturity date |
| `Issue Date` | date | Bond issue date |
| `Coupon` | float | Annual coupon rate in percent (e.g. `6.625`) |
| `Mid Yield` | float | Mid yield to maturity in percent |
| `Modified Duration` | float | Vendor-reported modified duration (years) |
| `Convexity` | float | Vendor-reported convexity (any scale; the pipeline auto-detects and corrects) |
| `Mid Price` | float | Mid clean price |
| `Z Spread` | float | Z-spread in basis points |
| `Cntry of Risk` | string | Two-letter ISO country code |
| `Issuer Name` | string | Issuer name |
| `Ticker` | string | Ticker |
| `BBG Composite` | string | Bloomberg composite rating (optional but retained) |

Additional columns are passed through without modification.

### `argentina_bonds.csv` — Bloomberg raw export

One row per bond per observation date, using Bloomberg's raw field-name schema. Required columns:

| Column | Type | Description |
|--------|------|-------------|
| `date` | date | Observation date |
| `isin` | string | Bond ISIN |
| `security_des` | string | Bloomberg security description, e.g. `ARGENT 6 5/8 07/06/28` — coupon and maturity are parsed from this field |
| `PX_MID` | float | Mid price |
| `YLD_YTM_MID` | float | Mid yield to maturity in percent |
| `Z_SPRD_MID` | float | Z-spread |
| `country` | string | Country code |

Vendor analytics columns (`DUR_ADJ_MID`, `CONVEXITY`, etc.) are accepted but not used — the pipeline recomputes all analytics from the locked convention.

---

## Expected outputs

### From `data_pipeline.ipynb`

**`panel_analysis.csv`** — the harmonised, analysis-ready panel. Columns:

| Column | Description |
|--------|-------------|
| `ISIN` | Bond identifier |
| `Date` | Observation date |
| `Mid Price` | Mid clean price |
| `Mid Yield` | Mid yield to maturity (percent) |
| `Dur_formula` | Formula-computed modified duration (years), locked convention |
| `Conv_formula` | Formula-computed convexity, locked convention |
| `Coupon` | Annual coupon rate (percent) |
| `Maturity` | Bond maturity date |
| `Issue Date` | Issue date |
| `Cntry of Risk` | Country of risk (ISO-2) |
| `Issuer Name` | Issuer name |
| `Ticker` | Ticker |
| `BBG Composite` | Bloomberg composite rating |
| `Z Spread` | Z-spread (basis points) |
| `rem_years` | Remaining years to maturity at observation date |

Rows with `vendor_impossible == True` (corrupted vendor records) are dropped before saving. No vendor duration or convexity fields are carried into the output.

### From `convexity_analysis_clean.ipynb`

All figures are saved to `output_convexity/`:

| File | Description |
|------|-------------|
| `eda_missingness.png` | Missingness by column and through time |
| `eda_univariate.png` | Univariate distributions of all analysis variables |
| *(additional figures)* | Price-yield scatter, convexity overstatement plots, regime comparisons |

---

## Running the notebooks

```bash
# Step 1: build the clean panel
jupyter nbconvert --to notebook --execute data_pipeline.ipynb --output data_pipeline_executed.ipynb

# Step 2: run the analysis
jupyter nbconvert --to notebook --execute convexity_analysis_clean.ipynb --output convexity_analysis_executed.ipynb
```

Or open them interactively:

```bash
jupyter notebook
```

Run `data_pipeline.ipynb` first. `convexity_analysis_clean.ipynb` depends on its output (`panel_analysis.csv`).

---

## Running the tests

```bash
pytest tests/test_pipeline.py -v
```

The test suite covers:

- `yearfrac` correctness under 30/360 and act/365 conventions
- `cashflow_schedule` cash-flow count, timing, and accrued-interest calculation
- `formula_analytics` against known analytical values for a par bond and a discount bond
- `recompute` end-to-end on a small synthetic DataFrame
- Convexity scale-harmonisation logic (bimodal detection and rescaling)
- Vendor impossibility flag (duration > maturity)
- Argentina `security_des` coupon/maturity parser
- Pipeline integration: given both input CSVs, `panel_analysis.csv` is produced and passes plausibility checks (no negative prices, yields in range, formula values finite)

---

## Conventions locked by the pipeline

All formula analytics use:

| Convention | Value |
|------------|-------|
| Day count | 30/360 (Eurobond) |
| Modified duration | Periodic: `MacDur / (1 + y/f)` |
| Convexity denominator | Dirty price |
| Coupon frequency | Semi-annual (`f = 2`) |

These were identified by a calm-investment-grade sweep in the pipeline validation step (step 6). To change them, edit the four constants at the top of `data_pipeline.ipynb`:

```python
frequency = 2
daycount, dur_yield, price_base = "30360", "periodic", "dirty"
```

---

## Notes on vendor data quirks

**Convexity scale ambiguity.** Some Refinitiv exports report convexity on a "decimal" scale roughly 100× smaller than the conventional scale. The pipeline detects this automatically by inspecting the `Convexity / ModDur²` ratio and rescales affected rows. If your export is already on the conventional scale, no rows will be modified.

**Restructuring cutoffs.** Ecuador (cutoff: 2020-08-31) and Argentina (cutoff: 2020-09-04) both restructured in 2020. Vendor analytics past these dates may reference the pre-restructuring cash-flow schedule and produce impossible values. The pipeline drops these rows. To add cutoffs for other sovereigns, extend the `restructure_cutoffs` dictionary:

```python
restructure_cutoffs = {
    "EC": pd.Timestamp("2020-08-31"),
    "AR": pd.Timestamp("2020-09-04"),
    "XX": pd.Timestamp("YYYY-MM-DD"),   # add here
}
```

