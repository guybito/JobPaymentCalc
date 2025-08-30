#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
from datetime import datetime, timedelta, time
from pathlib import Path
import math
import re

# ===== פרמטרים לפי ההגדרות שלך =====
HOURLY_WAGE = 65.0  # ש"ח לשעה
EVENING_BONUS = 0.20  # 20% בין 16:00–24:00
NIGHT_BONUS = 0.30    # 30% בין 24:00–07:30
WEEKEND_BONUS = 0.50  # 50% משישי 16:00 עד סוף לילה של שבת (א׳ 07:30)
HOLIDAY_BONUS = 0.50  # 50% ערב חג/חג (אם מצוין בסטטוס/הערות)
DAILY_TRAVEL = 22.0   # ש"ח ליום עבודה

# אם לא רוצים ממוצע שעות בפועל לימי מחלה, אפשר לקבע:
USE_AVG_HOURS_FOR_SICK = True
DEFAULT_DAILY_SICK_HOURS = 8.0  # ישומש רק אם USE_AVG_HOURS_FOR_SICK=False

# מילות מפתח לזיהוי סטטוסים
SICK_KEYWORD = "מחלה"
NO_ATTENDANCE_KEYWORD = "אין דיווח נוכחות"
HOLIDAY_HINTS = ["חג", "ערב חג"]  # אם תוסיף טקסט מתאים בעמודה – יופעל בונוס חג


# ===== כלים לעבודה עם זמנים וחישוב חיתוכי טווחים =====
def parse_hhmm(s):
    """ממיר 'HH:MM' ל-time או None אם ריק/NaN."""
    import math
    from datetime import datetime

    # NaN או None
    if s is None:
        return None
    if isinstance(s, float) and math.isnan(s):
        return None

    # הפוך למחרוזת ובצע ניקוי
    s = str(s).strip()
    if not s:
        return None
    if s in {".", "-"}:
        return None

    try:
        return datetime.strptime(s, "%H:%M").time()
    except Exception:
        return None


def parse_date(s):
    """ממיר 'DD/MM/YYYY' ל- date."""
    return datetime.strptime(s, "%d/%m/%Y").date()

def overlap_minutes(a_start, a_end, b_start, b_end):
    """החזרת דקות החפיפה בין שני טווחי datetime."""
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return max(0, int((end - start).total_seconds() // 60))

def daily_interval(date_obj, t_start, t_end):
    """בונה טווח datetime יומי בין שתי שעות לאותה 'תאריך' (אם t_end < t_start חוצים חצות)."""
    start = datetime.combine(date_obj, t_start)
    end = datetime.combine(date_obj, t_end)
    if t_end <= t_start:
        end += timedelta(days=1)
    return start, end

def is_holiday(status_text):
    if not isinstance(status_text, str):
        return False
    return any(h in status_text for h in HOLIDAY_HINTS)

def is_sick(status_text):
    if not isinstance(status_text, str):
        return False
    return SICK_KEYWORD in status_text

def is_no_attendance(status_text):
    if not isinstance(status_text, str):
        return False
    return NO_ATTENDANCE_KEYWORD in status_text


# ===== חישובי תוספות לפי חלונות זמן =====
def compute_evening_minutes(start_dt, end_dt):
    """
    דקות בערב (16:00–24:00) – מסתכל גם אם חצינו חצות.
    נחלק לפי ימים קלנדריים ונחשב חפיפה מול חלון הערב בכל יום.
    """
    total = 0
    cur = start_dt
    while cur < end_dt:
        day = cur.date()
        eve_start, eve_end = daily_interval(day, time(16, 0), time(0, 0))  # עד חצות
        total += overlap_minutes(start_dt, end_dt, eve_start, eve_end)
        cur = datetime.combine(day, time(0, 0)) + timedelta(days=1)
    return total

def compute_night_minutes(start_dt, end_dt):
    """
    דקות בלילה (24:00–07:30). נעשה שני חלונות לכל יום: 00:00–07:30 של אותו יום.
    שים לב: "24:00–07:30" זהה ל- 00:00–07:30 של היום הבא.
    """
    total = 0
    cur = start_dt
    while cur < end_dt:
        day = cur.date()
        night_start = datetime.combine(day, time(0, 0))
        night_end = datetime.combine(day, time(7, 30))
        total += overlap_minutes(start_dt, end_dt, night_start, night_end)
        cur = datetime.combine(day, time(0, 0)) + timedelta(days=1)
    return total

def iter_weekend_windows(around_start, around_end):
    """
    יוצר חלונות סוף שבוע: שישי 16:00 → ראשון 07:30 עבור כל שבוע שעשוי לחפוף.
    נזוז קצת אחורה וקדימה כדי לכסות חציית שבוע.
    """
    # נתחיל משני ימים לפני תחילת הטווח ועד שני ימים אחרי סופו
    window_start = (around_start - timedelta(days=3)).date()
    window_end = (around_end + timedelta(days=3)).date()

    d = window_start
    while d <= window_end:
        # מצא את שישי של אותו שבוע (weekday(): Mon=0..Sun=6, Fri=4)
        offset_to_fri = (4 - d.weekday()) % 7
        fri = d + timedelta(days=offset_to_fri)
        fri_16 = datetime.combine(fri, time(16, 0))
        # "סוף משמרת לילה של שבת" = 24:00 שבת עד 07:30 ראשון (כלומר ראשון 07:30)
        sun = fri + timedelta(days=2)  # שישי+2=ראשון
        sun_0730 = datetime.combine(sun, time(7, 30))
        yield fri_16, sun_0730
        d += timedelta(days=7)

def compute_weekend_minutes(start_dt, end_dt):
    total = 0
    for w_start, w_end in iter_weekend_windows(start_dt, end_dt):
        total += overlap_minutes(start_dt, end_dt, w_start, w_end)
    return total


# ===== קריאת הקלט וחישוב השכר =====
def load_attendance(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        csv_path,
        encoding="utf-8-sig",
        dtype=str,                 # חשוב: הכל כמחרוזות
        keep_default_na=False,     # לא להפוך מחרוזות ריקות ל-NaN
        na_values=[]               # לא לסמן ערכים כ-NaN אוטומטית
    )
    # וידוא עמודות
    for col in ["שעת כניסה", "שעת יציאה", "סה\"כ נוכחות", "סטטוס/הערות", "יום בשבוע", "תאריך"]:
        if col not in df.columns:
            raise ValueError(f"עמודה חסרה בקובץ: {col}")
    return df


def compute_daily_rows(df):
    """
    מחזיר DataFrame מפורט ליום: זמנים, דקות לפי סוג (רגיל/ערב/לילה/סופ"ש/חג), ותשלומים.
    """
    rows = []
    for _, r in df.iterrows():
        status = str(r["סטטוס/הערות"] or "").strip()
        if is_no_attendance(status):
            # מתעלמים מיום ללא עבודה
            continue

        date = parse_date(str(r["תאריך"]))
        t_in = parse_hhmm(r["שעת כניסה"])
        t_out = parse_hhmm(r["שעת יציאה"])

        # זיהוי חג
        holiday_flag = is_holiday(status)
        sick_flag = is_sick(status)

        if sick_flag:
            # נחשב בפונקציה נפרדת, אבל נרשום שורה ריקה לשעות עבודה
            rows.append({
                "תאריך": date,
                "סטטוס/הערות": status,
                "is_sick": True,
                "holiday": holiday_flag,
                "start": None,
                "end": None,
                "minutes_total": 0,
                "minutes_evening": 0,
                "minutes_night": 0,
                "minutes_weekend": 0,
                "minutes_holiday": 0,
                "worked_day": False
            })
            continue

        # אם אין זמנים, אין עבודה ואין נסיעות
        if not t_in or not t_out:
            continue

        start = datetime.combine(date, t_in)
        end = datetime.combine(date, t_out)
        if end <= start:
            end += timedelta(days=1)  # חציית חצות

        minutes_total = int((end - start).total_seconds() // 60)
        if minutes_total <= 0:
            continue

        minutes_evening = compute_evening_minutes(start, end)
        minutes_night = compute_night_minutes(start, end)
        minutes_weekend = compute_weekend_minutes(start, end)
        minutes_holiday = 0
        if holiday_flag:
            minutes_holiday = minutes_total  # תוספת 50% לכל הדקות ביום חג/ערב חג (פשטני, אפשר לשכלל)

        rows.append({
            "תאריך": date,
            "סטטוס/הערות": status,
            "is_sick": False,
            "holiday": holiday_flag,
            "start": start,
            "end": end,
            "minutes_total": minutes_total,
            "minutes_evening": minutes_evening,
            "minutes_night": minutes_night,
            "minutes_weekend": minutes_weekend,
            "minutes_holiday": minutes_holiday,
            "worked_day": True
        })
    out = pd.DataFrame(rows).sort_values("תאריך").reset_index(drop=True)
    return out

def minutes_to_hours(mins):
    return mins / 60.0

def add_pay_columns(daily_df: pd.DataFrame):
    """
    מחשב תשלום לכל יום (שכר בסיס + תוספות), כולל נסיעות.
    תוספות נערמות (מצטברות) כאשר שעות חופפות לכמה חלונות (למשל שישי 18:00 = ערב+סופ"ש).
    """
    df = daily_df.copy()

    # בסיס לשעה:
    base_rate = HOURLY_WAGE

    # שעות (שעות = דקות/60)
    df["hours_total"]   = df["minutes_total"].apply(minutes_to_hours)
    df["hours_evening"] = df["minutes_evening"].apply(minutes_to_hours)
    df["hours_night"]   = df["minutes_night"].apply(minutes_to_hours)
    df["hours_weekend"] = df["minutes_weekend"].apply(minutes_to_hours)
    df["hours_holiday"] = df["minutes_holiday"].apply(minutes_to_hours)

    # שכר בסיס (ללא תוספות): כל הדקות עולות שכר בסיס
    df["pay_base"] = df["hours_total"] * base_rate

    # תוספות מצטברות לפי חפיפות:
    # הערה: אם שעה נמצאת גם בערב וגם בסופ"ש וגם בחג, יקבל 20% + 50% + 50% על אותה שעה.
    df["pay_evening_bonus"] = df["hours_evening"] * base_rate * EVENING_BONUS
    df["pay_night_bonus"]   = df["hours_night"]   * base_rate * NIGHT_BONUS
    df["pay_weekend_bonus"] = df["hours_weekend"] * base_rate * WEEKEND_BONUS
    df["pay_holiday_bonus"] = df["hours_holiday"] * base_rate * HOLIDAY_BONUS

    # נסיעות: רק אם יום עבודה (לא מחלה), ויש שעות בפועל
    df["travel_pay"] = df["worked_day"].apply(lambda w: DAILY_TRAVEL if w else 0.0)
    df.loc[df["hours_total"] <= 0, "travel_pay"] = 0.0

    # סיכום ליום
    df["pay_total_day"] = (
        df["pay_base"]
        + df["pay_evening_bonus"]
        + df["pay_night_bonus"]
        + df["pay_weekend_bonus"]
        + df["pay_holiday_bonus"]
        + df["travel_pay"]
    )

    return df

def add_sick_pay(daily_df: pd.DataFrame, original_selected_csv: pd.DataFrame):
    """
    מחשב שכר לימי מחלה לפי החוק, על בסיס רצפים של "מחלה".
    הנחה: שעות יומיות למחלה = ממוצע שעות עבודה אמיתי באותו חודש (אם USE_AVG_HOURS_FOR_SICK)
    אחרת, משתמשים ב-DEFAULT_DAILY_SICK_HOURS.
    """
    df = daily_df.copy()

    # ממוצע שעות עבודה יומי אמיתי (רק בימים שעבדת)
    avg_hours = DEFAULT_DAILY_SICK_HOURS
    if USE_AVG_HOURS_FOR_SICK:
        worked_hours = df.loc[df["worked_day"], "hours_total"]
        if len(worked_hours) > 0:
            avg_hours = worked_hours.mean()

    # נאתר את כל התאריכים שהוגדר בהם "מחלה" בקובץ המקורי (גם אם סוננו קודם)
    sick_dates = []
    for _, r in original_selected_csv.iterrows():
        status = str(r["סטטוס/הערות"] or "").strip()
        if is_sick(status) and not is_no_attendance(status):
            sick_dates.append(parse_date(str(r["תאריך"])))

    if not sick_dates:
        df["pay_sick"] = 0.0
        return df

    sick_dates = sorted(set(sick_dates))

    # נעבור בתאריכים עוקבים, ונחשב רצפים
    pay_sick_map = {}  # date -> sick pay
    i = 0
    while i < len(sick_dates):
        # התחל רצף חדש
        start_idx = i
        cur = sick_dates[i]
        j = i + 1
        while j < len(sick_dates) and (sick_dates[j] - sick_dates[j-1]).days == 1:
            j += 1
        # רצף = sick_dates[start_idx:j]
        seq = sick_dates[start_idx:j]

        # חישוב לפי חוק:
        # יום 1: 0%
        # יום 2-3: 50%
        # יום 4+: 100%
        for k, d in enumerate(seq, start=1):
            if k == 1:
                pct = 0.0
            elif k in (2, 3):
                pct = 0.5
            else:
                pct = 1.0
            pay_sick_map[d] = avg_hours * HOURLY_WAGE * pct

        i = j

    # הוספה לטבלת הימים:
    df["pay_sick"] = 0.0
    # נוודא שלא נספר ימי מחלה שכבר יש בהם עבודה בפועל (בדרך כלל לא צריך לשלב)
    for d, pay in pay_sick_map.items():
        if d in df["תאריך"].values:
            # אם קיים יום כזה כבר (עבודה/אחר), נעדכן עמודה נפרדת
            df.loc[df["תאריך"] == d, "pay_sick"] = pay
            # אם באותו יום יש גם עבודה בפועל, לפי מדיניות אפשר לבחור אחד.
            # כאן נשאיר את שניהם, ובסיכום נחליט. ברירת מחדל: לא מצטבר.
            # כדי לא לצבור, נוכל לאפס עבודה באותו יום:
            if df.loc[df["תאריך"] == d, "worked_day"].any():
                # לא לצבור: נבטל עבודה ונשאיר מחלה
                df.loc[df["תאריך"] == d, ["pay_base","pay_evening_bonus","pay_night_bonus",
                                           "pay_weekend_bonus","pay_holiday_bonus","travel_pay","pay_total_day"]] = 0.0
        else:
            # הוסף שורה חדשה עבור יום מחלה שלא הופיע ברשימת ימי עבודה
            df = pd.concat([df, pd.DataFrame([{
                "תאריך": d,
                "סטטוס/הערות": "מחלה",
                "is_sick": True,
                "holiday": False,
                "start": None,
                "end": None,
                "minutes_total": 0,
                "minutes_evening": 0,
                "minutes_night": 0,
                "minutes_weekend": 0,
                "minutes_holiday": 0,
                "worked_day": False,
                "hours_total": 0.0,
                "hours_evening": 0.0,
                "hours_night": 0.0,
                "hours_weekend": 0.0,
                "hours_holiday": 0.0,
                "pay_base": 0.0,
                "pay_evening_bonus": 0.0,
                "pay_night_bonus": 0.0,
                "pay_weekend_bonus": 0.0,
                "pay_holiday_bonus": 0.0,
                "travel_pay": 0.0,
                "pay_total_day": 0.0,
                "pay_sick": pay
            }])], ignore_index=True)

    df = df.sort_values("תאריך").reset_index(drop=True)
    return df

def summarize(df_paid: pd.DataFrame):
    # סכומי שעות
    hours_cols = ["hours_total","hours_evening","hours_night","hours_weekend","hours_holiday"]
    sums_hours = df_paid[hours_cols].sum()

    # סכומי כסף
    money_cols = ["pay_base","pay_evening_bonus","pay_night_bonus",
                  "pay_weekend_bonus","pay_holiday_bonus","travel_pay","pay_sick"]
    sums_money = df_paid[money_cols].sum()

    # סה"כ שכר (לא צוברים מחלה + עבודה באותו יום כבר טיפלנו למעלה)
    total_pay = (df_paid["pay_total_day"].sum()) + df_paid["pay_sick"].sum()

    return sums_hours, sums_money, total_pay

def main():
    # קרא את הקובץ שסיננת: attendance_selected_columns.csv
    csv_path = Path("attendance_selected_columns.csv")
    if not csv_path.exists():
        raise SystemExit("לא נמצא attendance_selected_columns.csv בתיקייה הנוכחית.")

    selected_df = load_attendance(csv_path)

    # צריבת טבלת ימים מפורטת + חישוב שעות לפי חלונות
    daily = compute_daily_rows(selected_df)

    # חישוב תשלום לשעות עבודה (בסיס + תוספות + נסיעות)
    paid = add_pay_columns(daily)

    # הוספת תשלום ימי מחלה לפי החוק (מבוסס על ממוצע שעות יומיות בפועל)
    paid = add_sick_pay(paid, selected_df)

    # סיכומים
    sums_hours, sums_money, total_pay = summarize(paid)

    # שמירה לפלטים
    out_dir = Path("./payroll_out")
    out_dir.mkdir(exist_ok=True)
    paid.to_csv(out_dir / "daily_breakdown.csv", index=False, encoding="utf-8-sig")

    # דו"ח טקסט קצר
    with open(out_dir / "summary.txt", "w", encoding="utf-8") as f:
        f.write("== סיכום שעות ==\n")
        for k, v in sums_hours.items():
            f.write(f"{k}: {v:.2f} שעות\n")
        f.write("\n== סיכום רכיבי שכר ==\n")
        for k, v in sums_money.items():
            f.write(f"{k}: {v:.2f} ₪\n")
        f.write(f"\nסה\"כ לתשלום: {total_pay:.2f} ₪\n")

    print("נוצרו קבצים בתיקייה payroll_out:")
    print("- daily_breakdown.csv (פירוט יומי מלא)")
    print("- summary.txt (סיכומים וכמה לתשלום)")
    print(f"\nסה\"כ לתשלום: {total_pay:.2f} ₪")

if __name__ == "__main__":
    main()
