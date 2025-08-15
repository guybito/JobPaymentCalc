import io
import re
import math
import pdfplumber
import pandas as pd
from datetime import datetime, timedelta, time
import streamlit as st

st.set_page_config(page_title="חישוב שכר אוטומטי", page_icon="💸", layout="wide")

# ---------- Utils ----------
HE_DAY_NAMES = ["ראשון","שני","שלישי","רביעי","חמישי","שישי","שבת"]
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")

def parse_hhmm(s: str):
    try:
        return datetime.strptime(s, "%H:%M").time()
    except Exception:
        return None

def parse_date_he(s: str):
    return datetime.strptime(s.strip(), "%d/%m/%Y").date()

def to_dt(date_obj, t):
    return datetime.combine(date_obj, t)

def extract_entries_from_pdf(pdf_bytes: bytes):
    """
    מפענח את ה-PDF (דו״ח מל״ם), מפריד לבלוקים יומיים.
    מוסיף flags לזיהוי 'חג' / 'ערב חג' אם מופיע במלל של אותו יום.
    """
    text = ""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            text += "\n" + t

    parts = DATE_RE.split(text)
    entries = []
    for i in range(1, len(parts), 2):
        date_str = parts[i]
        block = parts[i+1]
        try:
            date_obj = datetime.strptime(date_str, "%d/%m/%Y").date()
        except Exception:
            continue

        dow_he = ""
        for name in HE_DAY_NAMES:
            if name in block:
                dow_he = name
                break

        status = ""
        if re.search(r"מחלה", block):
            status = "מחלה"
        elif re.search(r"אין\s+דיווח\s+נוכחות", block):
            status = "אין דיווח"

        # זיהוי אזכורי חג/ערב חג בטקסט של אותו יום (אם הדו״ח מציין זאת)
        is_hag_text = bool(re.search(r"\bחג\b", block))
        is_erev_hag_text = bool(re.search(r"\bערב\s*חג\b", block))

        times = re.findall(r"\b(\d{2}:\d{2})\b", block)
        parsed = [parse_hhmm(t) for t in times if parse_hhmm(t)]

        first_in = min(parsed) if parsed else None
        last_out = max(parsed) if parsed else None

        entries.append(dict(
            date=date_obj, dow_he=dow_he, status=status,
            first_in=first_in, last_out=last_out,
            is_hag_text=is_hag_text, is_erev_hag_text=is_erev_hag_text
        ))
    return entries

def segment_hours(date_obj, start_t, end_t,
                  enable_weekend_holiday=False,
                  holiday_dates=None, erev_holiday_dates=None):
    """
    מחלק טווח עבודה לקטגוריות:
    - regular   (רגיל)
    - evening   (16:00–24:00) +20%
    - night     (00:00–07:30) +30%
    - weekend   (+50%) עבור שבת/חג בהתאם לכללים:
        * שישי מ-16:00 ועד שבת 24:00 + לילה שאחרי שבת עד 07:30 של ראשון
        * יום חג מלא +50%
        * ערב חג מ-16:00 ועד 07:30 שלמחרת +50%
    אם enable_weekend_holiday=False, לא מחילים את 50% אלא רק ערב/לילה.
    """
    if not start_t or not end_t:
        return dict(regular=0.0, evening=0.0, night=0.0, weekend=0.0)

    holiday_dates = holiday_dates or set()
    erev_holiday_dates = erev_holiday_dates or set()

    start = to_dt(date_obj, start_t)
    end = to_dt(date_obj, end_t)
    if end <= start:
        end += timedelta(days=1)

    step = timedelta(minutes=15)
    buckets = dict(regular=0.0, evening=0.0, night=0.0, weekend=0.0)

    t = start
    while t < end:
        dow = t.weekday()  # Mon=0 ... Sun=6
        tt = t.time()
        d = t.date()

        # חלונות קבועים:
        is_night = time(0,0) <= tt < time(7,30)
        is_evening = time(16,0) <= tt < time(23,59,59)

        # לוגיקת שבת/חג
        is_weekendish_50 = False
        if enable_weekend_holiday:
            # שבת וחלון סביב שבת
            if (dow == 4 and tt >= time(16,0)) or (dow == 5) or (dow == 6 and tt < time(7,30)):
                is_weekendish_50 = True
            # חג מלא
            if d in holiday_dates:
                is_weekendish_50 = True
            # ערב חג: מ-16:00 ועד 07:30 שלמחרת
            if d in erev_holiday_dates and (tt >= time(16,0) or tt < time(7,30)):
                is_weekendish_50 = True

        if is_weekendish_50:
            cat = "weekend"
        else:
            if is_night:
                cat = "night"
            elif is_evening:
                cat = "evening"
            else:
                cat = "regular"

        buckets[cat] += step.total_seconds()/3600.0
        t += step

    return buckets

def compute_pay(entries, hourly_rate=65.0, travel_per_day=22.6,
                sick_by_avg_day=True, fixed_sick_day_hours=8.5,
                enable_weekend_holiday=False,
                manual_holidays=None, manual_erev_holidays=None):
    """
    חישוב מלא, עם תמיכה באופציית שבת/חג 50%:
    - manual_holidays: set של תאריכי חג (date)
    - manual_erev_holidays: set של תאריכי ערב חג (date)
    בנוסף, אם ה-PDF עצמו מציין 'חג/ערב חג' ביום מסוים, נתייחס לכך אוטומטית.
    """
    manual_holidays = set(manual_holidays or [])
    manual_erev_holidays = set(manual_erev_holidays or [])

    # פירוק רשומות
    worked = []
    sick = []
    for e in entries:
        if e["status"] == "מחלה":
            sick.append(e)
            continue
        if e["status"] == "אין דיווח":
            continue

        # בנה סטים ליום הזה: חג/ערב חג ידניים או מזוהים מהדו״ח
        day_is_hag = e.get("is_hag_text", False) or (e["date"] in manual_holidays)
        day_is_erev = e.get("is_erev_hag_text", False) or (e["date"] in manual_erev_holidays)

        worked.append({
            **e,
            "day_is_hag": day_is_hag,
            "day_is_erev": day_is_erev
        })

    # שעות לפי קטגוריות
    totals = dict(regular=0.0, evening=0.0, night=0.0, weekend=0.0)
    per_day_rows = []
    for e in worked:
        b = segment_hours(
            e["date"], e["first_in"], e["last_out"],
            enable_weekend_holiday=enable_weekend_holiday,
            holiday_dates={e["date"]} if e["day_is_hag"] else set(),
            erev_holiday_dates={e["date"]} if e["day_is_erev"] else set()
        )
        for k in totals:
            totals[k] += b[k]
        per_day_rows.append({
            "תאריך": e["date"].strftime("%d/%m/%Y"),
            "יום": e["dow_he"],
            "כניסה": e["first_in"].strftime("%H:%M") if e["first_in"] else "",
            "יציאה": e["last_out"].strftime("%H:%M") if e["last_out"] else "",
            "זוהה חג?": "כן" if e["day_is_hag"] else "",
            "זוהה ערב חג?": "כן" if e["day_is_erev"] else "",
            "רגיל": round(b["regular"],2),
            "ערב (+20%)": round(b["evening"],2),
            "לילה (+30%)": round(b["night"],2),
            "שבת/חג (+50%)": round(b["weekend"],2),
            "סך שעות": round(sum(b.values()),2)
        })

    reg_h, eve_h, night_h, we_h = totals["regular"], totals["evening"], totals["night"], totals["weekend"]
    base_hours = reg_h + eve_h + night_h + we_h

    # שכר בסיס + תוספות
    base_pay = base_hours * hourly_rate
    premium_pay = eve_h*hourly_rate*0.20 + night_h*hourly_rate*0.30 + we_h*hourly_rate*0.50

    # נסיעות
    workdays_count = len([e for e in worked if e["first_in"] and e["last_out"]])
    travel_pay = workdays_count * travel_per_day

    # מחלה – לפי חוק
    if sick_by_avg_day:
        avg_daily_hours = (base_hours / workdays_count) if workdays_count else 0.0
        sick_day_hours = avg_daily_hours
    else:
        sick_day_hours = fixed_sick_day_hours

    sick_sorted = sorted(sick, key=lambda x: x["date"])
    sick_hours_total = 0.0
    for i, _ in enumerate(sick_sorted, start=1):
        if i == 1:
            sick_hours_total += 0.0
        elif i in (2,3):
            sick_hours_total += sick_day_hours * 0.5
        else:
            sick_hours_total += sick_day_hours * 1.0
    sick_pay = sick_hours_total * hourly_rate

    gross_total = base_pay + premium_pay + travel_pay + sick_pay

    # טבלאות להצגה/הורדה
    df_days = pd.DataFrame(per_day_rows)
    summary = pd.DataFrame({
        "קטגוריה":["רגיל","ערב (+20%)","לילה (+30%)","שבת/חג (+50%)","סה״כ"],
        "שעות":[round(reg_h,2), round(eve_h,2), round(night_h,2), round(we_h,2), round(base_hours,2)],
        "שכר בסיס (₪)":[round(reg_h*hourly_rate,2), round(eve_h*hourly_rate,2), round(night_h*hourly_rate,2), round(we_h*hourly_rate,2), round(base_hours*hourly_rate,2)],
        "תוספת (₪)":[0.0, round(eve_h*hourly_rate*0.20,2), round(night_h*hourly_rate*0.30,2), round(we_h*hourly_rate*0.50,2), round(premium_pay,2)],
        "תת-סכום (₪)":[round(reg_h*hourly_rate,2), round(eve_h*hourly_rate*1.20,2), round(night_h*hourly_rate*1.30,2), round(we_h*hourly_rate*1.50,2), round(base_pay+premium_pay,2)],
    })

    meta = {
        "ימי עבודה": workdays_count,
        "ימי מחלה": len(sick_sorted),
        "שעות/יום למחלה": round(sick_day_hours,2),
        "שכר בסיס": round(base_pay,2),
        "תוספות משמרת": round(premium_pay,2),
        "נסיעות": round(travel_pay,2),
        "שכר מחלה": round(sick_pay,2),
        "ברוטו משוער": round(gross_total,2),
    }

    return df_days, summary, meta

# ---------- UI ----------
st.title("💸 מחשבון שכר אוטומטי לדו״ח נוכחות (מל״ם)")
st.caption("מעלה דו״ח PDF חודשי, מקבל פירוט יומי, תוספות ערב/לילה/שבת/חגים, נסיעות ומחלה.")

row1 = st.columns(4)
with row1[0]:
    hourly_rate = st.number_input("שכר לשעה (₪)", min_value=0.0, step=0.5, value=65.0)
with row1[1]:
    travel_per_day = st.number_input("דמי נסיעות ליום (₪)", min_value=0.0, step=0.1, value=22.6)
with row1[2]:
    sick_mode = st.selectbox("חישוב בסיס שעות למחלה", ["ממוצע שעות יומיות בפועל", "ערך קבוע (למשל 8.5)"])
with row1[3]:
    enable_weekend_holiday = st.checkbox("חשב שבת/חג בתוספת 50% (כולל שישי מ־16:00, ערב חג מ־16:00)", value=True)

fixed_hours = None
if sick_mode == "ערך קבוע (למשל 8.5)":
    fixed_hours = st.number_input("שעות ליום מחלה (קבוע)", min_value=0.0, step=0.25, value=8.5)

st.markdown("**חגים ידניים (אופציונלי):** הזן תאריכי חג בפורמט `DD/MM/YYYY` מופרדים בפסיקים.\
 אפשר גם להזין תאריכי *ערב חג* בנפרד.")
col_h1, col_h2 = st.columns(2)
with col_h1:
    manual_holidays_str = st.text_input("תאריכי חג (למשל: 03/10/2025, 04/10/2025)", "")
with col_h2:
    manual_erev_holidays_str = st.text_input("תאריכי ערב חג (למשל: 02/10/2025)", "")

def parse_dates_list(s):
    if not s.strip():
        return set()
    items = [x for x in s.split(",") if x.strip()]
    return {parse_date_he(x) for x in items}

manual_holidays = parse_dates_list(manual_holidays_str)
manual_erev_holidays = parse_dates_list(manual_erev_holidays_str)

uploaded = st.file_uploader("העלה כאן את דו״ח ה-PDF ממלם", type=["pdf"])

if uploaded:
    try:
        pdf_bytes = uploaded.read()
        entries = extract_entries_from_pdf(pdf_bytes)

        st.subheader("תצוגה מקדימה של הימים שזוהו")
        preview = pd.DataFrame([{
            "תאריך": e["date"].strftime("%d/%m/%Y"),
            "יום": e["dow_he"],
            "סטטוס": e["status"] or "עבודה",
            "זוהה חג בטקסט?": "כן" if e["is_hag_text"] else "",
            "זוהה ערב חג בטקסט?": "כן" if e["is_erev_hag_text"] else "",
            "כניסה": e["first_in"].strftime("%H:%M") if e["first_in"] else "",
            "יציאה": e["last_out"].strftime("%H:%M") if e["last_out"] else "",
        } for e in entries])
        st.dataframe(preview, use_container_width=True, hide_index=True)

        # חישוב
        df_days, summary, meta = compute_pay(
            entries,
            hourly_rate=hourly_rate,
            travel_per_day=travel_per_day,
            sick_by_avg_day=(sick_mode == "ממוצע שעות יומיות בפועל"),
            fixed_sick_day_hours=(fixed_hours if fixed_hours is not None else 8.5),
            enable_weekend_holiday=enable_weekend_holiday,
            manual_holidays=manual_holidays,
            manual_erev_holidays=manual_erev_holidays
        )

        st.subheader("🔢 תוצאות")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ימי עבודה", meta["ימי עבודה"])
        c2.metric("ימי מחלה", meta["ימי מחלה"])
        c3.metric("שעות/יום למחלה", meta["שעות/יום למחלה"])
        c4.metric("ברוטו משוער (₪)", f'{meta["ברוטו משוער"]:,}')

        st.markdown("### פירוט יומי")
        st.dataframe(df_days, use_container_width=True, hide_index=True)

        st.markdown("### סיכום שעות ותוספות")
        st.dataframe(summary, use_container_width=True, hide_index=True)

        # הורדות
        days_csv = df_days.to_csv(index=False).encode("utf-8-sig")
        summ_csv = summary.to_csv(index=False).encode("utf-8-sig")
        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button("⬇️ הורד CSV: פירוט יומי", data=days_csv, file_name="timesheet_per_day.csv", mime="text/csv")
        with col_b:
            st.download_button("⬇️ הורד CSV: סיכום שעות", data=summ_csv, file_name="shift_summary.csv", mime="text/csv")

        st.success("החישוב הושלם. ניתן לכוונן חגים/ערב חג דרך השדות למעלה.")
    except Exception as e:
        st.error(f"נכשלו פענוח או חישוב: {e}")
else:
    st.info("העלה דו״ח PDF לקבלת חישוב.")
