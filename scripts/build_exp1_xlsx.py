from pathlib import Path
from urllib.parse import quote

import openpyxl
import pandas as pd
from openpyxl.drawing.image import Image
from openpyxl.styles import Font, PatternFill

PROJECT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT / "Results"
OUTPUTS = PROJECT / "outputs"

DST = RESULTS_DIR / "Markify Experiment 1 - Distance Score Calibration.xlsx"
RESULTS_CSV = OUTPUTS / "exp1_results.csv"
SUMMARY_CSV = OUTPUTS / "exp1_per_name_summary.csv"
INPUT_XLSX = PROJECT / "data" / "Markify tests.xlsx"
GLOBAL_HIST = OUTPUTS / "exp1_global_histogram.png"
MAX_HIST = OUTPUTS / "exp1_max_score_per_name.png"
SAMPLE_DIR = OUTPUTS / "exp1_sample_hists"

SINGLE_LIST_COLS = {
    "input_name": "Candidate Name",
    "related_trademark": "Related Trademark",
    "score": "Similarity Score",
    "request_url": "Request URL",
}


def build_url(mark: str, classes: str, database: str) -> str:
    base = "https://www.markify.com/api/search.json"
    params = (
        f"mark={quote(str(mark))}"
        f"&class={quote(str(classes))}"
        f"&database={quote(str(database))}"
        f"&mode=knockout&gas=1&minScore=0&pageLimit=100"
    )
    return f"{base}?{params}"


def name_to_classes_map() -> dict:
    sheet1 = pd.read_excel(INPUT_XLSX, sheet_name="Distance_Score_Tests")
    out = {}
    for _, row in sheet1.iterrows():
        v = row["Nice Classes"]
        out[row["Name"]] = str(int(v)) if isinstance(v, (int, float)) else str(v).strip()
    return out

PER_NAME_COLS = {
    "rank": "Rank",
    "input_name": "Candidate Name",
    "max_score": "Highest Similarity",
    "n_results": "Number of Similar Trademarks Found",
}

YELLOW = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")
TITLE = Font(bold=True, size=14)
BOLD = Font(bold=True, size=11)


def write_lines(ws, lines, start_row=1):
    for i, (text, kind) in enumerate(lines):
        cell = ws.cell(start_row + i, 1, text)
        if kind == "title":
            cell.font = TITLE
        elif kind == "bold":
            cell.font = BOLD
    return start_row + len(lines)


def write_table(ws, df, start_row):
    for col_idx, col_name in enumerate(df.columns, 1):
        cell = ws.cell(start_row, col_idx, col_name)
        cell.font = BOLD
    for r_off, row in enumerate(df.itertuples(index=False), 1):
        for c_off, val in enumerate(row, 1):
            ws.cell(start_row + r_off, c_off, val)
    return start_row + len(df)


def write_single_list(wb):
    ws = wb.create_sheet("Summary")

    guide = [
        ("EXPERIMENT 1 - DISTANCE SCORE CALIBRATION", "title"),
        ("", None),
        ("PURPOSE", "bold"),
        ("Pick a similarity threshold. Names with score >= threshold = risky.", None),
        ("", None),
        ("DECISION FLOW (work through tabs in this order):", "bold"),
        ("  STEP 1   Tab 2 [Overall Histogram]    look at distribution, form a threshold opinion", None),
        ("  STEP 2   Tab 3 [Match Data]           filter Candidate Name to drill into specific names", None),
        ("  STEP 3   Tab 4 [Per Name Histograms]  spot-check threshold against specific names", None),
        ("", None),
        ("WHAT EACH TAB CONTAINS:", "bold"),
        ("  Tab 1   Summary              master guide (this tab)", None),
        ("  Tab 2   Overall Histogram    2 charts: global distribution + bimodal pattern", None),
        ("  Tab 3   Match Data           all matches with Request URLs", None),
        ("  Tab 4   Per Name Histograms  10 sample charts (count is parametric in code)", None),
        ("", None),
    ]
    write_lines(ws, guide)
    ws.column_dimensions["A"].width = 100


def write_per_name_list(wb):
    ws = wb.create_sheet("Match Data")

    intro = [
        ("ALL MATCHES - one row per (candidate, related trademark)", "title"),
        ("", None),
        ("HOW TO READ:", "bold"),
        ("  Each row = one trademark match Markify returned.", None),
        ("  Filter Candidate Name to drill into one specific name's full match list.", None),
        ("  Request URL = the API call that produced this row (paste into browser to verify).", None),
        ("", None),
    ]
    next_row = write_lines(ws, intro)

    df = pd.read_csv(RESULTS_CSV, encoding="latin-1")
    classes_by_name = name_to_classes_map()
    df["request_url"] = [
        build_url(name, classes_by_name.get(name, ""), db)
        for name, db in zip(df["input_name"], df["db_called"])
    ]
    df = df[list(SINGLE_LIST_COLS.keys())].rename(columns=SINGLE_LIST_COLS)

    header_row = next_row
    for col_idx, col_name in enumerate(df.columns, 1):
        cell = ws.cell(header_row, col_idx, col_name)
        cell.font = BOLD
    for r_off, row in enumerate(df.itertuples(index=False), 1):
        for c_off, val in enumerate(row, 1):
            ws.cell(header_row + r_off, c_off, val)

    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 50
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 80
    ws.freeze_panes = ws.cell(header_row + 1, 1).coordinate


def write_overall_histogram(wb):
    ws = wb.create_sheet("Overall Histogram")

    intro = [
        ("OVERALL HISTOGRAM - global view of similarity scores.", "title"),
        ("", None),
        ("CHART 1: Distribution of all match scores (~4,000 matches mixed)", "bold"),
        ("  X = score 0-1.   Y = number of trademark matches in that range.", None),
        ("  Red dashed lines = candidate thresholds (0.7 / 0.8 / 0.9).", None),
        ("  USE: where the curve drops off is a natural cut-point.", None),
        ("", None),
    ]
    next_row = write_lines(ws, intro)
    ws.add_image(Image(str(GLOBAL_HIST)), ws.cell(next_row, 1).coordinate)

    chart2_row = next_row + 30
    chart2_intro = [
        ("CHART 2: Highest similarity per candidate (70 candidates)", "bold"),
        ("  X = each candidate's max score.   Y = number of candidates.", None),
        ("  Shows BIMODAL pattern: candidates cluster near 0 or near 1.", None),
        ("  IMPLICATION: threshold in 0.5-0.9 gives same verdict for ~89% of names.", None),
        ("", None),
    ]
    next_row = write_lines(ws, chart2_intro, start_row=chart2_row)
    ws.add_image(Image(str(MAX_HIST)), ws.cell(next_row, 1).coordinate)

    ws.column_dimensions["A"].width = 100


def write_per_name_histograms(wb):
    ws = wb.create_sheet("Per Name Histograms")

    intro = [
        ("SAMPLE PER NAME HISTOGRAMS - 10 candidates, one chart each.", "title"),
        ("", None),
        ("HOW TO READ:", "bold"),
        ("  Each chart = ONE candidate's score distribution.", None),
        ("  Bars right (>= 0.7) = high conflict; bars left (<= 0.4) = safe.", None),
        ("  Title shows: name + total matches + max score.", None),
        ("", None),
        ("USE:", "bold"),
        ("  Spot-check the threshold from Tab 3 against specific cases.", None),
        ("  Sample size is parametric (--sample-hists N in code).", None),
        ("", None),
    ]
    next_row = write_lines(ws, intro)

    row = next_row
    for png in sorted(SAMPLE_DIR.glob("*.png")):
        ws.cell(row, 1, png.stem).font = BOLD
        ws.add_image(Image(str(png)), ws.cell(row + 1, 1).coordinate)
        row += 25

    ws.column_dimensions["A"].width = 80


def main():
    for path in (RESULTS_CSV, SUMMARY_CSV, GLOBAL_HIST, MAX_HIST):
        if not path.exists():
            raise FileNotFoundError(f"Missing: {path}")
    if not SAMPLE_DIR.exists() or not list(SAMPLE_DIR.glob("*.png")):
        raise FileNotFoundError(f"Missing or empty: {SAMPLE_DIR}")

    RESULTS_DIR.mkdir(exist_ok=True)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    write_single_list(wb)
    write_overall_histogram(wb)
    write_per_name_list(wb)
    write_per_name_histograms(wb)

    wb.save(DST)
    print(f"Built: {DST}")
    print(f"Tabs: {wb.sheetnames}")


if __name__ == "__main__":
    main()
