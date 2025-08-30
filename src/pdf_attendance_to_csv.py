#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
סקריפט: פירוק טבלת נוכחות מקובץ PDF, תיקון עברית, ושמירת קבצי CSV
מחייב התקנה של: pdfplumber, pandas

דוגמת שימוש (שורת פקודה):
python pdf_attendance_to_csv.py --pdf "TimesheetAnalysisReport_202508.pdf_1755963802915.pdf" --outdir "./out"

הסקריפט יפיק שלושה קבצים:
1) attendance_full_table.csv - הטבלה הגולמית כפי שחולצה מה-PDF
2) attendance_full_table_readable.csv - אותה טבלה עם כותרות קריאות בעברית
3) attendance_selected_columns.csv - רק העמודות: סה"כ נוכחות, שעת יציאה, שעת כניסה, סטטוס/הערות, יום בשבוע, תאריך
"""

import argparse
from pathlib import Path
import re
import pandas as pd

try:
    import pdfplumber
except Exception as e:
    raise SystemExit("יש להתקין pdfplumber: pip install pdfplumber\nשגיאה: %s" % e)


def extract_tables_from_pdf(pdf_path: Path) -> list[pd.DataFrame]:
    """חילוץ כל הטבלאות מכל העמודים ב-PDF (כרשימת DataFrame-ים)."""
    tables: list[pd.DataFrame] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            try:
                page_tables = page.extract_tables() or []
            except Exception:
                page_tables = []
            for t in page_tables:
                df = pd.DataFrame(t)
                # התעלמות מטבלאות ריקות/זעירות
                if df.dropna(how="all").shape[0] == 0 or df.shape[1] < 2:
                    continue
                # הוספת אינדיקציה לעמוד
                df["__page"] = i + 1
                tables.append(df)
    return tables


def rename_columns_hebrew(df: pd.DataFrame) -> pd.DataFrame:
    """
    מיפוי כותרות אינדקס ספרתיות לכותרות קריאות בעברית בהתבסס על מבנה הדוח הספציפי.
    אם מבנה הדוח שונה, יש לעדכן את המיפוי.
    """
    column_names = {
        0: 'שעות חוסר פעילות לשכר',
        1: 'שעות עודף לשכר',
        2: 'סה"כ נוכחות',
        3: 'שעת יציאה',
        4: 'שעת כניסה',
        5: 'סטטוס/הערות',
        6: 'עמודה נוספת',
        7: 'יום בשבוע',
        8: 'תאריך',
        '__page': 'עמוד PDF'
    }
    # שינוי שם לעמודת ה-page אם קיימת
    if "__page" in df.columns:
        df = df.rename(columns={"__page": "עמוד PDF"})
    # שינוי שמות שאר העמודות לפי אינדקסים קיימים
    df = df.rename(columns={k: v for k, v in column_names.items() if k in df.columns})
    return df


HEB_ONLY = re.compile(r'^[\u0590-\u05FF\s]+$')


def fix_hebrew_text(text):
    """
    תיקון טקסטים בעברית שהודפסו הפוך, על ידי היפוך המחרוזת
    (מופעל רק אם המחרוזת מכילה עברית/רווחים בלבד).
    """
    if not isinstance(text, str):
        return text
    s = text.strip()
    if not s:
        return text
    if HEB_ONLY.match(s):
        return s[::-1]
    return text


def fix_hebrew_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    הפיכת כל התאים בעברית (כולל שמות הימים וסטטוסים) כך שיופיעו בכיוון קריאה תקין.
    """
    fixed = df.copy()
    for col in fixed.columns:
        fixed[col] = fixed[col].apply(fix_hebrew_text)
    return fixed


def select_requested_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    בחירת עמודות סופיות לפי בקשת המשתמש:
    סה"כ נוכחות, שעת יציאה, שעת כניסה, סטטוס/הערות, יום בשבוע, תאריך
    """
    cols = ['סה"כ נוכחות', 'שעת יציאה', 'שעת כניסה', 'סטטוס/הערות', 'יום בשבוע', 'תאריך']
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"עמודות חסרות בטבלה לאחר שינוי שמות: {missing}")
    return df[cols].copy()


def save_csv(df: pd.DataFrame, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding='utf-8-sig')


def main():
    ap = argparse.ArgumentParser(description="חילוץ טבלת נוכחות מ-PDF ושמירה ל-CSV")
    ap.add_argument("--pdf", required=True, help="נתיב לקובץ ה-PDF של הדוח")
    ap.add_argument("--outdir", default=".", help="ספריית פלט ל-CSV")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    outdir = Path(args.outdir)

    if not pdf_path.exists():
        raise SystemExit(f"לא נמצא קובץ PDF בנתיב: {pdf_path}")

    # 1) חילוץ טבלאות
    tables = extract_tables_from_pdf(pdf_path)
    if not tables:
        raise SystemExit("לא נמצאו טבלאות ב-PDF.")

    # בדוח שלך יש טבלה מרכזית אחת, נבחר בראשונה
    df_raw = tables[0].copy()

    # 2) שמירת הטבלה הגולמית בדיוק כפי שחולצה
    save_csv(df_raw, outdir / "attendance_full_table.csv")

    # 3) כותרות קריאות בעברית
    df_readable = rename_columns_hebrew(df_raw)

    # 4) תיקון טקסטים בעברית (ימים, סטטוסים וכו')
    df_hebrew_fixed = fix_hebrew_columns(df_readable)

    # 5) שמירת הגרסה הקריאה
    save_csv(df_hebrew_fixed, outdir / "attendance_full_table_readable.csv")

    # 6) בחירת עמודות סופיות לבקשת המשתמש
    df_selected = select_requested_columns(df_hebrew_fixed)

    # 7) שמירה לקובץ הסופי
    save_csv(df_selected, outdir / "attendance_selected_columns.csv")

    print("נשמרו קבצים:")
    print(f"- {outdir / 'attendance_full_table.csv'}")
    print(f"- {outdir / 'attendance_full_table_readable.csv'}")
    print(f"- {outdir / 'attendance_selected_columns.csv'}")


if __name__ == "__main__":
    main()
