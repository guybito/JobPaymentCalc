#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
from datetime import datetime, timedelta, time
from pathlib import Path
import math
import json

# ===== פרמטרים כלליים =====
HOURLY_WAGE = 65.0          # ש"ח לשעה
EVENING_BONUS = 0.20        # 20% בין 16:00–24:00
NIGHT_BONUS   = 0.30        # 30% בין 24:00–07:30
WEEKEND_BONUS = 0.50        # 50% משישי 16:00 עד א׳ 07:30
HOLIDAY_BONUS = 0.50        # 50% חג/ערב חג (אם מזוהה)
DAILY_TRAVEL  = 22.0        # ש"ח ליום עבודה (נכלל בברוטו החייב)

# סיבוס חודשי (הטבה חייבת במס). אם אין – קבע 0.
SIBUS_MONTHLY = 450.0

# ===== שעות נוספות =====
# לפי חוק: השעתיים הראשונות 125%, מעבר לכך 150%
OVERTIME_T1_BONUS = 0.25      # +25% (כלומר 125% סה"כ)
OVERTIME_T2_BONUS = 0.50      # +50% (כלומר 150% סה"כ)
DAILY_REGULAR_HOURS = 8.0     # מעל 8 שעות מתחילות שעות נוספות
DAILY_T1_HOURS      = 2.0     # שעתיים ראשונות בדרגה 1 (125%)

# ===== ימי מחלה =====
USE_AVG_HOURS_FOR_SICK   = True
DEFAULT_DAILY_SICK_HOURS = 8.0

# ===== מילות מפתח לסטטוסים =====
SICK_KEYWORD          = "מחלה"
NO_ATTENDANCE_KEYWORD = "אין דיווח נוכחות"
HOLIDAY_HINTS         = ["חג", "ערב חג"]

# ===== ניכויים לנטו =====
CREDIT_POINTS        = 2.25            # נקודות זיכוי למס הכנסה
CREDIT_POINT_VALUE   = 235.0           # ₪ לנק׳ (ניתן לעדכון)
# מדרגות מס (חודשיות, בקירוב; עדכן לשנה הרלוונטית אם צריך)
TAX_BRACKETS = [
    (6790,   0.10),
    (9720,   0.14),
    (15760,  0.20),
    (21700,  0.31),
    (45180,  0.35),
    (float("inf"), 0.47),
]
# ביטוח לאומי/בריאות (בקירוב; ניתן לעדכון)
NI_THRESHOLD = 7570.0
NI_LOW   = 0.004
NI_HIGH  = 0.07
HEALTH_LOW  = 0.031
HEALTH_HIGH = 0.05

# פנסיה עובד
EMPLOYEE_PENSION_RATE = 0.07
# בסיס לפנסיה: רק שכר עבודה (בסיס+בונוסים) ללא נסיעות/סיבוס, או כולל הכל
PENSION_BASE_MODE = "wage_only"   # "wage_only" / "include_all"


# ===== כלים לזמן =====
def parse_hhmm(s):
    """ממיר 'HH:MM' ל-time או None."""
    if s is None:
        return None
    if isinstance(s, float) and math.isnan(s):
        return None
    s = str(s).strip()
    if not s or s in {".", "-"}:
        return None
    try:
        return datetime.strptime(s, "%H:%M").time()
    except Exception:
        return None

def parse_date(s):
    return datetime.strptime(s, "%d/%m/%Y").date()

def overlap_minutes(a_start, a_end, b_start, b_end):
    start = max(a_start, b_start)
    end   = min(a_end, b_end)
    return max(0, int((end - start).total_seconds() // 60))

def daily_interval(date_obj, t_start, t_end):
    start = datetime.combine(date_obj, t_start)
    end   = datetime.combine(date_obj, t_end)
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


# ===== חלונות זמן =====
def compute_evening_minutes(start_dt, end_dt):
    total = 0
    cur = start_dt
    while cur < end_dt:
        day = cur.date()
        eve_start, eve_end = daily_interval(day, time(16, 0), time(0, 0))
        total += overlap_minutes(start_dt, end_dt, eve_start, eve_end)
        cur = datetime.combine(day, time(0, 0)) + timedelta(days=1)
    return total

def compute_night_minutes(start_dt, end_dt):
    total = 0
    cur = start_dt
    while cur < end_dt:
        day = cur.date()
        night_start = datetime.combine(day, time(0, 0))
        night_end   = datetime.combine(day, time(7, 30))
        total += overlap_minutes(start_dt, end_dt, night_start, night_end)
        cur = datetime.combine(day, time(0, 0)) + timedelta(days=1)
    return total

def iter_weekend_windows(around_start, around_end):
    window_start = (around_start - timedelta(days=3)).date()
    window_end   = (around_end   + timedelta(days=3)).date()
    d = window_start
    while d <= window_end:
        offset_to_fri = (4 - d.weekday()) % 7  # Fri=4
        fri = d + timedelta(days=offset_to_fri)
        fri_16 = datetime.combine(fri, time(16, 0))
        sun = fri + timedelta(days=2)
        sun_0730 = datetime.combine(sun, time(7, 30))
        yield fri_16, sun_0730
        d += timedelta(days=7)

def compute_weekend_minutes(start_dt, end_dt):
    total = 0
    for w_start, w_end in iter_weekend_windows(start_dt, end_dt):
        total += overlap_minutes(start_dt, end_dt, w_start, w_end)
    return total


# ===== קריאת הקלט וחישובי ברוטו =====
def load_attendance(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str, keep_default_na=False, na_values=[])
    for col in ["שעת כניסה", "שעת יציאה", "סה\"כ נוכחות", "סטטוס/הערות", "יום בשבוע", "תאריך"]:
        if col not in df.columns:
            raise ValueError(f"עמודה חסרה בקובץ: {col}")
    return df

def compute_daily_rows(df):
    rows = []
    for _, r in df.iterrows():
        status = str(r["סטטוס/הערות"] or "").strip()
        if is_no_attendance(status):
            continue

        date = parse_date(str(r["תאריך"]))
        t_in  = parse_hhmm(r["שעת כניסה"])
        t_out = parse_hhmm(r["שעת יציאה"])
        holiday_flag = is_holiday(status)
        sick_flag    = is_sick(status)

        if sick_flag:
            rows.append({
                "תאריך": date,
                "סטטוס/הערות": status,
                "is_sick": True,
                "holiday": holiday_flag,
                "start": None, "end": None,
                "minutes_total": 0, "minutes_evening": 0, "minutes_night": 0,
                "minutes_weekend": 0, "minutes_holiday": 0,
                "worked_day": False
            })
            continue

        if not t_in or not t_out:
            continue

        start = datetime.combine(date, t_in)
        end   = datetime.combine(date, t_out)
        if end <= start:
            end += timedelta(days=1)  # חציית חצות

        minutes_total = int((end - start).total_seconds() // 60)
        if minutes_total <= 0:
            continue

        minutes_evening = compute_evening_minutes(start, end)
        minutes_night   = compute_night_minutes(start, end)
        minutes_weekend = compute_weekend_minutes(start, end)
        minutes_holiday = minutes_total if holiday_flag else 0

        rows.append({
            "תאריך": date,
            "סטטוס/הערות": status,
            "is_sick": False,
            "holiday": holiday_flag,
            "start": start, "end": end,
            "minutes_total": minutes_total,
            "minutes_evening": minutes_evening,
            "minutes_night": minutes_night,
            "minutes_weekend": minutes_weekend,
            "minutes_holiday": minutes_holiday,
            "worked_day": True
        })

    out = pd.DataFrame(rows).sort_values("תאריך").reset_index(drop=True)
    return out

def add_pay_columns(daily_df: pd.DataFrame):
    """חישוב תשלומים ליום: בסיס + תוספות + שעות נוספות + נסיעות."""
    df = daily_df.copy()
    base_rate = HOURLY_WAGE

    # המרת דקות לשעות
    df["hours_total"]   = df["minutes_total"].apply(lambda m: m / 60.0)
    df["hours_evening"] = df["minutes_evening"].apply(lambda m: m / 60.0)
    df["hours_night"]   = df["minutes_night"].apply(lambda m: m / 60.0)
    df["hours_weekend"] = df["minutes_weekend"].apply(lambda m: m / 60.0)
    df["hours_holiday"] = df["minutes_holiday"].apply(lambda m: m / 60.0)

    # בסיס 100% לכל השעות
    df["pay_base"] = df["hours_total"] * base_rate

    # תוספות (מצטבר)
    df["pay_evening_bonus"] = df["hours_evening"] * base_rate * EVENING_BONUS
    df["pay_night_bonus"]   = df["hours_night"]   * base_rate * NIGHT_BONUS
    df["pay_weekend_bonus"] = df["hours_weekend"] * base_rate * WEEKEND_BONUS
    df["pay_holiday_bonus"] = df["hours_holiday"] * base_rate * HOLIDAY_BONUS

    # ===== שעות נוספות (מצטבר בנוסף לתוספות למעלה) =====
    overtime = (df["hours_total"] - DAILY_REGULAR_HOURS).clip(lower=0)
    df["hours_ot_t1"] = overtime.clip(upper=DAILY_T1_HOURS)                    # עד 2 שעות
    df["hours_ot_t2"] = (overtime - df["hours_ot_t1"]).clip(lower=0)           # מעבר לכך

    df["pay_overtime_t1"] = df["hours_ot_t1"] * base_rate * OVERTIME_T1_BONUS  # +25%
    df["pay_overtime_t2"] = df["hours_ot_t2"] * base_rate * OVERTIME_T2_BONUS  # +50%

    # נסיעות
    df["travel_pay"] = df["worked_day"].apply(lambda w: DAILY_TRAVEL if w else 0.0)
    df.loc[df["hours_total"] <= 0, "travel_pay"] = 0.0

    # סיכום יומי כולל הכל (ללא ימי מחלה – נטפל בנפרד)
    df["pay_total_day"] = (
        df["pay_base"]
        + df["pay_evening_bonus"]
        + df["pay_night_bonus"]
        + df["pay_weekend_bonus"]
        + df["pay_holiday_bonus"]
        + df["pay_overtime_t1"]
        + df["pay_overtime_t2"]
        + df["travel_pay"]
    )
    return df

def add_sick_pay(daily_df: pd.DataFrame, original_selected_csv: pd.DataFrame):
    """ימי מחלה לפי החוק: יום 1=0%, ימים 2–3=50%, מהיום 4=100%."""
    df = daily_df.copy()

    avg_hours = DEFAULT_DAILY_SICK_HOURS
    if USE_AVG_HOURS_FOR_SICK:
        worked_hours = df.loc[df["worked_day"], "hours_total"]
        if len(worked_hours) > 0:
            avg_hours = worked_hours.mean()

    # זיהוי תאריכי מחלה מתוך קובץ המקור
    sick_dates = []
    for _, r in original_selected_csv.iterrows():
        status = str(r["סטטוס/הערות"] or "").strip()
        if is_sick(status) and not is_no_attendance(status):
            sick_dates.append(parse_date(str(r["תאריך"])))

    if not sick_dates:
        df["pay_sick"] = 0.0
        return df

    sick_dates = sorted(set(sick_dates))

    # פריסה לרצפים עוקבים
    pay_sick_map = {}
    i = 0
    while i < len(sick_dates):
        j = i + 1
        while j < len(sick_dates) and (sick_dates[j] - sick_dates[j-1]).days == 1:
            j += 1
        seq = sick_dates[i:j]
        # חישוב לפי חוק
        for k, d in enumerate(seq, start=1):
            if k == 1:
                pct = 0.0
            elif k in (2, 3):
                pct = 0.5
            else:
                pct = 1.0
            pay_sick_map[d] = avg_hours * HOURLY_WAGE * pct
        i = j

    df["pay_sick"] = 0.0
    for d, pay in pay_sick_map.items():
        if d in df["תאריך"].values:
            # לא לצבור עבודה+מחלה באותו יום: מבטלים רכיבי עבודה ומשאירים מחלה
            df.loc[df["תאריך"] == d, [
                "pay_base","pay_evening_bonus","pay_night_bonus","pay_weekend_bonus","pay_holiday_bonus",
                "pay_overtime_t1","pay_overtime_t2","travel_pay","pay_total_day"
            ]] = 0.0
            df.loc[df["תאריך"] == d, "pay_sick"] = pay
        else:
            # יום מחלה שאינו מופיע בטבלה – הוסף שורה חדשה
            df = pd.concat([df, pd.DataFrame([{
                "תאריך": d, "סטטוס/הערות": "מחלה", "is_sick": True, "holiday": False,
                "start": None, "end": None,
                "minutes_total": 0, "minutes_evening": 0, "minutes_night": 0, "minutes_weekend": 0, "minutes_holiday": 0,
                "worked_day": False,
                "hours_total": 0.0, "hours_evening": 0.0, "hours_night": 0.0, "hours_weekend": 0.0, "hours_holiday": 0.0,
                "hours_ot_t1": 0.0, "hours_ot_t2": 0.0,
                "pay_base": 0.0, "pay_evening_bonus": 0.0, "pay_night_bonus": 0.0,
                "pay_weekend_bonus": 0.0, "pay_holiday_bonus": 0.0,
                "pay_overtime_t1": 0.0, "pay_overtime_t2": 0.0,
                "travel_pay": 0.0, "pay_total_day": 0.0,
                "pay_sick": pay
            }])], ignore_index=True)

    df = df.sort_values("תאריך").reset_index(drop=True)
    return df

def summarize(df_paid: pd.DataFrame):
    hours_cols = ["hours_total","hours_evening","hours_night","hours_weekend","hours_holiday","hours_ot_t1","hours_ot_t2"]
    for c in hours_cols:
        if c not in df_paid.columns:
            df_paid[c] = 0.0

    sums_hours = df_paid[hours_cols].sum()

    money_cols = [
        "pay_base","pay_evening_bonus","pay_night_bonus",
        "pay_weekend_bonus","pay_holiday_bonus",
        "pay_overtime_t1","pay_overtime_t2",
        "travel_pay","pay_sick"
    ]
    for c in money_cols:
        if c not in df_paid.columns:
            df_paid[c] = 0.0

    sums_money = df_paid[money_cols].sum()
    total_wage_only = (df_paid["pay_total_day"].sum()) + df_paid["pay_sick"].sum()
    return sums_hours, sums_money, total_wage_only


# ===== מיסים וניכויים =====
def income_tax_before_credit(monthly_taxable: float) -> float:
    tax = 0.0
    last = 0.0
    for cap, rate in TAX_BRACKETS:
        if monthly_taxable > cap:
            tax += (cap - last) * rate
            last = cap
        else:
            tax += (monthly_taxable - last) * rate
            return max(0.0, round(tax, 2))
    return max(0.0, round(tax, 2))

def apply_credit_points(tax_before: float) -> float:
    relief = CREDIT_POINTS * CREDIT_POINT_VALUE
    return max(0.0, round(tax_before - relief, 2))

def ni_health(monthly_gross: float):
    low  = min(monthly_gross, NI_THRESHOLD)
    high = max(0.0, monthly_gross - NI_THRESHOLD)
    ni     = low * NI_LOW   + high * NI_HIGH
    health = low * HEALTH_LOW + high * HEALTH_HIGH
    return round(ni, 2), round(health, 2)


def main():
    csv_path = Path("attendance_selected_columns.csv")
    if not csv_path.exists():
        raise SystemExit("לא נמצא attendance_selected_columns.csv בתיקייה הנוכחית.")

    selected_df = load_attendance(csv_path)
    daily = compute_daily_rows(selected_df)
    paid  = add_pay_columns(daily)
    paid  = add_sick_pay(paid, selected_df)

    # סיכומים
    sums_hours, sums_money, total_wage_only = summarize(paid)

    # פירוק רכיבי שכר לעבודה (בסיס+בונוסים+שעות נוספות) + מחלה
    wage_components = (
        paid["pay_base"].sum()
        + paid["pay_evening_bonus"].sum()
        + paid["pay_night_bonus"].sum()
        + paid["pay_weekend_bonus"].sum()
        + paid["pay_holiday_bonus"].sum()
        + paid["pay_overtime_t1"].sum()
        + paid["pay_overtime_t2"].sum()
        + paid["pay_sick"].sum()
    )
    travel_sum = paid["travel_pay"].sum()

    # ברוטו חייב
    monthly_gross_taxable = wage_components + travel_sum + SIBUS_MONTHLY

    # בסיס לפנסיה
    if PENSION_BASE_MODE == "include_all":
        pension_base = monthly_gross_taxable
    else:
        pension_base = wage_components  # ללא נסיעות/סיבוס

    employee_pension = round(pension_base * EMPLOYEE_PENSION_RATE, 2)

    # ביטוח לאומי/בריאות
    ni, health = ni_health(monthly_gross_taxable)

    # מס הכנסה
    tax_before = income_tax_before_credit(monthly_gross_taxable)
    tax_after  = apply_credit_points(tax_before)

    # נטו
    net = monthly_gross_taxable - (employee_pension + ni + health + tax_after)

    # פלטים
    out_dir = Path("./payroll_out")
    out_dir.mkdir(exist_ok=True)
    paid.to_csv(out_dir / "daily_breakdown.csv", index=False, encoding="utf-8-sig")

    # === בניית טקסט מסכם קריא ===
    def fmt_money(v):
        return f"{v:,.2f} ₪".replace(",", ",")
    def fmt_hours(v):
        return f"{v:.2f} שעות"
    def pad(s, w=22):
        return s.ljust(w)

    sep = "-" * 60
    summary_lines = []

    summary_lines.append("== סיכום שעות ==")
    summary_lines.append(sep)
    summary_lines.append(pad("שעות סה\"כ")         + " : " + fmt_hours(sums_hours["hours_total"]))
    summary_lines.append(pad("שעות ערב")           + " : " + fmt_hours(sums_hours["hours_evening"]))
    summary_lines.append(pad("שעות לילה")          + " : " + fmt_hours(sums_hours["hours_night"]))
    summary_lines.append(pad("שעות סופ\"ש")        + " : " + fmt_hours(sums_hours["hours_weekend"]))
    summary_lines.append(pad("שעות חג")            + " : " + fmt_hours(sums_hours["hours_holiday"]))
    summary_lines.append(pad("שעות נוספות 125%")   + " : " + fmt_hours(sums_hours["hours_ot_t1"]))
    summary_lines.append(pad("שעות נוספות 150%")   + " : " + fmt_hours(sums_hours["hours_ot_t2"]))
    summary_lines.append("")

    summary_lines.append("== סיכום רכיבי שכר (ברוטו) ==")
    summary_lines.append(sep)
    summary_lines.append(pad("שכר בסיס")           + " : " + fmt_money(paid["pay_base"].sum()))
    summary_lines.append(pad("תוספת ערב")          + " : " + fmt_money(paid["pay_evening_bonus"].sum()))
    summary_lines.append(pad("תוספת לילה")         + " : " + fmt_money(paid["pay_night_bonus"].sum()))
    summary_lines.append(pad("תוספת סופ\"ש")       + " : " + fmt_money(paid["pay_weekend_bonus"].sum()))
    summary_lines.append(pad("תוספת חג")           + " : " + fmt_money(paid["pay_holiday_bonus"].sum()))
    summary_lines.append(pad("שעות נוספות 125%")    + " : " + fmt_money(paid["pay_overtime_t1"].sum()))
    summary_lines.append(pad("שעות נוספות 150%")    + " : " + fmt_money(paid["pay_overtime_t2"].sum()))
    summary_lines.append(pad("נסיעות")              + " : " + fmt_money(travel_sum))
    summary_lines.append(pad("מחלה")                + " : " + fmt_money(paid["pay_sick"].sum()))
    summary_lines.append(pad("סיבוס")               + " : " + fmt_money(SIBUS_MONTHLY))
    summary_lines.append(pad("סה\"כ ברוטו חייב")     + " : " + fmt_money(monthly_gross_taxable))
    summary_lines.append("")

    summary_lines.append("== ניכויים ==")
    summary_lines.append(sep)
    summary_lines.append(pad("פנסיה עובד")          + " : " + fmt_money(employee_pension) + f"  ({EMPLOYEE_PENSION_RATE*100:.1f}% | בסיס: {PENSION_BASE_MODE})")
    summary_lines.append(pad("ביטוח לאומי")         + " : " + fmt_money(ni))
    summary_lines.append(pad("בריאות")              + " : " + fmt_money(health))
    summary_lines.append(pad("מס לפני זיכוי")       + " : " + fmt_money(tax_before))
    summary_lines.append(pad("זיכוי (נק׳)")         + " : " + fmt_money(tax_before - tax_after) + f"  ({CREDIT_POINTS} × {CREDIT_POINT_VALUE:.0f})")
    summary_lines.append(pad("מס לתשלום")           + " : " + fmt_money(tax_after))
    summary_lines.append("")

    summary_lines.append("== נטו ==")
    summary_lines.append(sep)
    summary_lines.append(pad("נטו לתשלום")          + " : " + fmt_money(net))

    (out_dir / "summary.txt").write_text("\n".join(summary_lines), encoding="utf-8")

    # JSON מסכם
    result = {
        "params": {
            "HOURLY_WAGE": HOURLY_WAGE,
            "EVENING_BONUS": EVENING_BONUS,
            "NIGHT_BONUS": NIGHT_BONUS,
            "WEEKEND_BONUS": WEEKEND_BONUS,
            "HOLIDAY_BONUS": HOLIDAY_BONUS,
            "DAILY_TRAVEL": DAILY_TRAVEL,
            "SIBUS_MONTHLY": SIBUS_MONTHLY,
            "OVERTIME_T1_BONUS": OVERTIME_T1_BONUS,
            "OVERTIME_T2_BONUS": OVERTIME_T2_BONUS,
            "DAILY_REGULAR_HOURS": DAILY_REGULAR_HOURS,
            "DAILY_T1_HOURS": DAILY_T1_HOURS,
            "CREDIT_POINTS": CREDIT_POINTS,
            "CREDIT_POINT_VALUE": CREDIT_POINT_VALUE,
            "TAX_BRACKETS": TAX_BRACKETS,
            "NI_THRESHOLD": NI_THRESHOLD,
            "NI_LOW": NI_LOW, "NI_HIGH": NI_HIGH,
            "HEALTH_LOW": HEALTH_LOW, "HEALTH_HIGH": HEALTH_HIGH,
            "EMPLOYEE_PENSION_RATE": EMPLOYEE_PENSION_RATE,
            "PENSION_BASE_MODE": PENSION_BASE_MODE,
        },
        "gross_components": {
            "wage_components": round(wage_components, 2),
            "travel_sum": round(travel_sum, 2),
            "sibus_monthly": round(SIBUS_MONTHLY, 2),
            "monthly_gross_taxable": round(monthly_gross_taxable, 2),
            "pension_base": round(pension_base, 2),
        },
        "deductions": {
            "employee_pension": employee_pension,
            "ni": ni,
            "health": health,
            "income_tax_before": round(tax_before, 2),
            "income_tax_after_credit": round(tax_after, 2),
            "credit_relief": round(tax_before - tax_after, 2),
        },
        "net": round(net, 2),
        "hours_summary": {k: float(v) for k, v in sums_hours.items()}
    }
    (out_dir / "net_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("נוצרו קבצים בתיקייה payroll_out:")
    print("- daily_breakdown.csv (פירוט יומי מלא)")
    print("- summary.txt (ברוטו, ניכויים ונטו)")
    print("- net_summary.json (כל הנתונים במבנה JSON)")
    print(f"\nנטו לתשלום: {result['net']:.2f} ₪")


if __name__ == "__main__":
    main()
