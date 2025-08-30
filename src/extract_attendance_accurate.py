# -*- coding: utf-8 -*-
"""
Extractor for bordered Hebrew attendance tables.

Strategy (in order):
1) Try Camelot (flavor="lattice") which uses the cell borders.
2) Fallback to Camelot (flavor="stream").
3) Fallback to Tabula (lattice, then stream).
4) Merge all tables and standardize headers to the expected 9 columns seen in the screenshot:
   ['תאריך כניסה','יום בשבוע','סוג יום','פעילות','שעת כניסה','שעת יציאה','סה"כ נוכחות','שעות חוסר לשכר','שעות עודף לשכר']

Output:
- attendance_full_raw.xlsx / .csv : Full table as extracted
- attendance_processed.xlsx / .csv : Only ['תאריך כניסה','יום בשבוע','יום מחלה','סה"כ נוכחות']
  with rows containing 'אין דיווח נוכחות' removed and 'יום מחלה' marked True where 'פעילות' contains 'מחלה'.
"""
import re
import sys
import pandas as pd

EXPECTED = ['תאריך כניסה','יום בשבוע','סוג יום','פעילות','שעת כניסה','שעת יציאה','סה"כ נוכחות','שעות חוסר לשכר','שעות עודף לשכר']

HEADER_CANON = {
    # canonical : possible variants (add as needed)
    'תאריך כניסה': ['תאריך כניסה', 'תאריך'],
    'יום בשבוע': ['יום בשבוע', 'יום'],
    'סוג יום': ['סוג יום', 'סוג יום לשכר', 'סוג'],
    'פעילות': ['פעילות', 'סטטוס'],
    'שעת כניסה': ['שעת כניסה', 'כניסה'],
    'שעת יציאה': ['שעת יציאה', 'יציאה'],
    'סה"כ נוכחות': ['סה"כ נוכחות', 'סה״כ נוכחות', 'סהכ נוכחות', 'סה"כ', 'נוכחות'],
    'שעות חוסר לשכר': ['שעות חוסר לשכר', 'חוסר לשכר'],
    'שעות עודף לשכר': ['שעות עודף לשכר', 'עודף לשכר'],
}

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r"[\\u200e\\u200f]", "", s)  # bidi
    s = re.sub(r"\\s+", " ", s).strip()
    return s

def canonicalize_headers(cols):
    out = []
    for c in cols:
        c_norm = normalize_text(c)
        mapped = None
        for canon, variants in HEADER_CANON.items():
            if c_norm in variants:
                mapped = canon; break
        if mapped is None:
            # Try fuzzy contains (handles multi-line split headers like 'סה"כ' + 'נוכחות')
            for canon, variants in HEADER_CANON.items():
                for v in variants:
                    v2 = normalize_text(v)
                    c2 = normalize_text(c_norm)
                    if v2 and (v2 in c2 or c2 in v2):
                        mapped = canon; break
                if mapped: break
        out.append(mapped or c_norm)
    return out

def tidy_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # Drop fully empty columns and rows
    df = df.copy()
    df.columns = [normalize_text(c) for c in df.columns]
    # collapse multiindex-like duplicate header rows: keep first non-empty header row
    if df.shape[0] > 0 and any(isinstance(x, str) and re.search(r"[א-ת]", x) for x in df.iloc[0].tolist()):
        # If first row looks like another header, merge it into columns when necessary
        first_row = [normalize_text(x) for x in df.iloc[0].tolist()]
        if sum(1 for x in first_row if x) >= max(3, int(len(first_row)*0.3)):
            # promote combined header when current headers are numeric or empty
            for i, h in enumerate(df.columns):
                if not normalize_text(h) and i < len(first_row) and first_row[i]:
                    df.columns = [first_row[j] if j==i else df.columns[j] for j in range(len(df.columns))]
            df = df.iloc[1:].reset_index(drop=True)

    df.columns = canonicalize_headers(df.columns)

    # If more columns than expected, try to select the closest mapping to EXPECTED
    # and drop obvious junk columns.
    # Also, if fewer, we keep what's available.
    # Normalize cells
    for c in df.columns:
        df[c] = df[c].apply(lambda x: normalize_text(x))

    # Keep only rows that look like data (date in 'תאריך כניסה' or day name in 'יום בשבוע')
    def looks_like_date(s):
        return bool(re.match(r"\\d{2}[./-]\\d{2}[./-]\\d{4}$", s or ""))

    if 'תאריך כניסה' in df.columns:
        df = df[df['תאריך כניסה'].apply(looks_like_date) | df['תאריך כניסה'].astype(str).str.contains(r"\\d{2}[./-]\\d{2}[./-]\\d{4}", regex=True)]
    df = df.reset_index(drop=True)

    # If 'סה"כ נוכחות' was split into two columns (e.g., 'סה"כ' and 'נוכחות'), try to stitch
    if "סה\"כ" in df.columns and "נוכחות" in df.columns and "סה\"כ נוכחות" not in df.columns:
        df['סה"כ נוכחות'] = df["סה\"כ"].str.cat(df["נוכחות"], sep=" ").str.strip()

    # Ensure preferred order (only those present)
    present = [c for c in EXPECTED if c in df.columns]
    df = df[present]
    return df

def try_camelot(pdf_path, flavor):
    try:
        import camelot
        tables = camelot.read_pdf(pdf_path, pages="all", flavor=flavor, strip_text="\\n")
        dfs = [t.df for t in tables]
        # Convert header row to columns
        out = []
        for d in dfs:
            # Promote first non-empty row as header if headers are empty
            d = d.copy()
            # Drop completely empty columns
            d = d.dropna(axis=1, how="all")
            # If header row is in first row:
            if d.shape[0] > 0:
                d.columns = d.iloc[0].tolist()
                d = d.iloc[1:].reset_index(drop=True)
            out.append(tidy_dataframe(d))
        if out:
            return pd.concat(out, ignore_index=True)
    except Exception as e:
        print(f"[camelot-{flavor}] {e}")
    return pd.DataFrame()

def try_tabula(pdf_path, lattice=True):
    try:
        import tabula
        dfs = tabula.read_pdf(pdf_path, pages="all", multiple_tables=True, lattice=lattice, stream=not lattice, guess=False)
        out = [tidy_dataframe(d) for d in dfs if isinstance(d, pd.DataFrame)]
        if out:
            return pd.concat(out, ignore_index=True)
    except Exception as e:
        print(f"[tabula-{'lattice' if lattice else 'stream'}] {e}")
    return pd.DataFrame()

def main(pdf_path: str, out_csv: str="attendance_full_raw.csv", out_xlsx: str="attendance_full_raw.xlsx",
         proc_csv: str="attendance_processed.csv", proc_xlsx: str="attendance_processed.xlsx"):
    # Try extractors
    df = try_camelot(pdf_path, "lattice")
    if df.empty:
        df = try_camelot(pdf_path, "stream")
    if df.empty:
        df = try_tabula(pdf_path, lattice=True)
    if df.empty:
        df = try_tabula(pdf_path, lattice=False)
    if df.empty:
        raise SystemExit("לא הצלחתי לחלץ טבלה בעזרת Camelot/Tabula. ודא שהמותקנים Java (לטאבולה) ו-Ghostscript (לקמלוט).")

    # Save full
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(out_xlsx, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Raw")

    # Build processed view per requirement
    df_proc = df.copy()
    if 'פעילות' in df_proc.columns:
        df_proc = df_proc[~df_proc['פעילות'].str.contains("אין דיווח נוכחות", na=False)]
    df_proc['יום מחלה'] = False
    if 'פעילות' in df_proc.columns:
        df_proc.loc[df_proc['פעילות'].str.contains("מחלה", na=False), 'יום מחלה'] = True

    keep = [c for c in ['תאריך כניסה','יום בשבוע','יום מחלה','סה"כ נוכחות'] if c in df_proc.columns or c == 'יום מחלה']
    df_proc = df_proc[keep]

    df_proc.to_csv(proc_csv, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(proc_xlsx, engine="xlsxwriter") as w:
        df_proc.to_excel(w, index=False, sheet_name="Processed")

    print(f"נשמרו קבצים:\n- {out_csv}\n- {out_xlsx}\n- {proc_csv}\n- {proc_xlsx}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("שימוש: python extract_attendance_accurate.py path/to/content.pdf")
        sys.exit(1)
    pdf_path = sys.argv[1]
    out_csv = sys.argv[2] if len(sys.argv) > 2 else "attendance_full_raw.csv"
    out_xlsx = sys.argv[3] if len(sys.argv) > 3 else "attendance_full_raw.xlsx"
    proc_csv = sys.argv[4] if len(sys.argv) > 4 else "attendance_processed.csv"
    proc_xlsx = sys.argv[5] if len(sys.argv) > 5 else "attendance_processed.xlsx"
    main(pdf_path, out_csv, out_xlsx, proc_csv, proc_xlsx)
