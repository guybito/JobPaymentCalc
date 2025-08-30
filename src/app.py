# app.py
# -*- coding: utf-8 -*-
import io, math, json, re
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta, time

# =========================
# Page config
# =========================
st.set_page_config(page_title="מחשבון שכר", page_icon="💸", layout="wide")

# =========================
# Hebrew helpers (PDF normalization)
# =========================
HEB_ONLY = re.compile(r'^[\u0590-\u05FF\s\.\-]+$')

EXPECTED_HEB_WORDS = {
    "ראשון","שני","שלישי","רביעי","חמישי","שישי","שבת",
    "אין","דיווח","נוכחות","מחלה","חג","ערב","עבודה","מנוחה","יום"
}

def hebrew_only(s: str) -> bool:
    return isinstance(s, str) and bool(HEB_ONLY.match(s.strip())) and any('\u0590' <= ch <= '\u05FF' for ch in s)

def reverse_hebrew_if_needed(s: str) -> str:
    """היפוך פשוט – יופעל רק עבור טקסט עברי "טהור" כשנחליט שצריך."""
    if hebrew_only(s):
        return s[::-1]
    return s

def detect_column_orientation(series: pd.Series) -> str:
    normal_hits = 0
    reversed_hits = 0
    sample = (x for x in series.dropna().astype(str).head(200))
    for s in sample:
        s_stripped = s.strip()
        if not s_stripped:
            continue
        s_rev = s_stripped[::-1]
        for w in EXPECTED_HEB_WORDS:
            if w in s_stripped:
                normal_hits += 1
            if w in s_rev:
                reversed_hits += 1
    return "reversed" if reversed_hits > normal_hits + 1 else "normal"

def apply_hebrew_correction(df: pd.DataFrame, mode: str = "auto") -> pd.DataFrame:
    df2 = df.copy()
    obj_cols = [c for c in df2.columns if df2[c].dtype == object]
    if mode == "off":
        return df2
    for c in obj_cols:
        if mode == "on":
            df2[c] = df2[c].apply(reverse_hebrew_if_needed)
        else:  # auto
            orient = detect_column_orientation(df2[c])
            if orient == "reversed":
                df2[c] = df2[c].apply(reverse_hebrew_if_needed)
    return df2

# =========================
# Theme (force dark)
# =========================
base_theme = "dark"
if base_theme == "dark":
    PRIMARY = "#A78BFA"
    ACCENT  = "#34D399"
    INK     = "#F9FAFB"
    INK_SOFT= "#E5E7EB"
    MUTED   = "#94A3B8"
    CARD_BG = "#111827"
    BORDER  = "#1F2937"
    TABLE_BORDER = "#26303F"
    LINK_HOVER = "#F472B6"
else:
    PRIMARY = "#4F46E5"
    ACCENT  = "#10B981"
    INK     = "#0F172A"
    INK_SOFT= "#334155"
    MUTED   = "#64748B"
    CARD_BG = "#FFFFFF"
    BORDER  = "#E2E8F0"
    TABLE_BORDER = "#EEF2F7"
    LINK_HOVER = "#7C3AED"

st.markdown(f"""
<style>
html, body, [class*="css"] {{
  direction: rtl;
  text-align: right;
  color: {INK};
  font-family: "Rubik","Assistant","Segoe UI",Tahoma,sans-serif;
}}
.block-container {{ padding-top: 1rem; }}
h1, h2, h3 {{ letter-spacing: .3px; }}
h1 {{ color: {INK}; }}
h2 {{ color: {INK_SOFT}; }}
.card {{
  background: {CARD_BG};
  border:1px solid {BORDER};
  border-radius:16px;
  padding: 18px 18px;
  box-shadow: 0 6px 16px rgba(2,6,23,.06);
}}
.kpi {{
  border-radius:16px; padding:18px;
  background: linear-gradient(135deg, rgba(255,255,255,.08) 0%, rgba(248,250,252,.04) 100%);
  border:1px solid {BORDER};
}}
.kpi .label {{ color:{MUTED}; font-size:.9rem; }}
.kpi .value {{ font-weight:800; font-size:1.45rem; color:{INK}; }}
.stDataFrame thead tr th, .stDataFrame tbody tr td,
.stDataEditor thead tr th, .stDataEditor tbody tr td {{
  border-color:{TABLE_BORDER} !important;
  color:{INK} !important;
}}
.stDataEditor table, .stDataFrame table {{ unicode-bidi: plaintext; }}
.stDataEditor [contenteditable="true"] {{
  unicode-bidi: plaintext;
  text-align: right;
  color:{INK} !important;
}}
.badge {{
  display:inline-block; padding:.25rem .6rem; border-radius:999px;
  font-size:.8rem; font-weight:600; background:rgba(79,70,229,.12); color:{PRIMARY};
}}
.step {{ display:flex; gap:.5rem; align-items:center; margin:.4rem 0 1rem 0; }}
.step .num {{
  width:28px; height:28px; border-radius:999px; display:flex; align-items:center; justify-content:center;
  background:{PRIMARY}; color:#fff; font-weight:700;
}}
.step .title {{ color:{INK}; font-weight:700; }}
.small {{ font-size:.9rem; color:{MUTED}; }}
hr {{ border-top:1px solid {BORDER}; }}
</style>
""", unsafe_allow_html=True)

# =========================
# Utilities
# =========================
def parse_hhmm(s):
    if s is None: return None
    if isinstance(s, float) and math.isnan(s): return None
    s = str(s).strip()
    if not s or s in {".","-"}: return None
    try: return datetime.strptime(s, "%H:%M").time()
    except: return None

def parse_date(s): return datetime.strptime(str(s), "%d/%m/%Y").date()
def overlap_minutes(a_start,a_end,b_start,b_end):
    start=max(a_start,b_start); end=min(a_end,b_end)
    return max(0,int((end-start).total_seconds()//60))
def daily_interval(date_obj,t_start,t_end):
    start=datetime.combine(date_obj,t_start); end=datetime.combine(date_obj,t_end)
    if t_end<=t_start: end+=timedelta(days=1)
    return start,end
def minutes_to_hours(m): return m/60.0
def money(v): return f"{v:,.2f} ₪".replace(",", ",")

# =========================
# Defaults / Params
# =========================
DEFAULT_PARAMS = {
    "HOURLY_WAGE": 65.0,
    "EVENING_BONUS": 0.20, "NIGHT_BONUS": 0.30, "WEEKEND_BONUS": 0.50, "HOLIDAY_BONUS": 0.50,
    "DAILY_TRAVEL": 22.0, "SIBUS_MONTHLY": 450.0,
    "SICK_KEYWORD": "מחלה", "NO_ATTENDANCE_KEYWORD": "אין דיווח נוכחות", "HOLIDAY_HINTS": ["חג","ערב חג"],
    "USE_AVG_HOURS_FOR_SICK": True, "DEFAULT_DAILY_SICK_HOURS": 8.0,
    "OVERTIME_T1_BONUS": 0.25, "OVERTIME_T2_BONUS": 0.50, "DAILY_REGULAR_HOURS": 8.0, "DAILY_T1_HOURS": 2.0,
    "CREDIT_POINTS": 2.25, "CREDIT_POINT_VALUE": 235.0,
    "TAX_BRACKETS": [(6790,0.10),(9720,0.14),(15760,0.20),(21700,0.31),(45180,0.35),(float("inf"),0.47)],
    "NI_THRESHOLD": 7570.0, "NI_LOW": 0.004, "NI_HIGH": 0.07, "HEALTH_LOW": 0.031, "HEALTH_HIGH": 0.05,
    "EMPLOYEE_PENSION_RATE": 0.07, "PENSION_BASE_MODE": "wage_only",
    "OT_BASIS": "Daily + Weekly (max)",
    "WEEK_START": "Sunday",
}

# =========================
# Payroll engine
# =========================
def build_processor(params):
    HOURLY_WAGE=params["HOURLY_WAGE"]; EVENING_BONUS=params["EVENING_BONUS"]; NIGHT_BONUS=params["NIGHT_BONUS"]
    WEEKEND_BONUS=params["WEEKEND_BONUS"]; HOLIDAY_BONUS=params["HOLIDAY_BONUS"]
    DAILY_TRAVEL=params["DAILY_TRAVEL"]; SIBUS_MONTHLY=params["SIBUS_MONTHLY"]
    SICK_KEYWORD=params["SICK_KEYWORD"]; NO_ATTENDANCE_KEYWORD=params["NO_ATTENDANCE_KEYWORD"]; HOLIDAY_HINTS=params["HOLIDAY_HINTS"]
    USE_AVG_HOURS_FOR_SICK=params["USE_AVG_HOURS_FOR_SICK"]; DEFAULT_DAILY_SICK_HOURS=params["DEFAULT_DAILY_SICK_HOURS"]
    OVERTIME_T1_BONUS=params["OVERTIME_T1_BONUS"]; OVERTIME_T2_BONUS=params["OVERTIME_T2_BONUS"]
    DAILY_REGULAR_HOURS=params["DAILY_REGULAR_HOURS"]; DAILY_T1_HOURS=params["DAILY_T1_HOURS"]
    CREDIT_POINTS=params["CREDIT_POINTS"]; CREDIT_POINT_VALUE=params["CREDIT_POINT_VALUE"]; TAX_BRACKETS=params["TAX_BRACKETS"]
    NI_THRESHOLD=params["NI_THRESHOLD"]; NI_LOW=params["NI_LOW"]; NI_HIGH=params["NI_HIGH"]; HEALTH_LOW=params["HEALTH_LOW"]; HEALTH_HIGH=params["HEALTH_HIGH"]
    EMPLOYEE_PENSION_RATE=params["EMPLOYEE_PENSION_RATE"]; PENSION_BASE_MODE=params["PENSION_BASE_MODE"]
    OT_BASIS=params.get("OT_BASIS","Daily + Weekly (max)")
    WEEK_START=params.get("WEEK_START","Sunday")

    def is_holiday(t): return isinstance(t,str) and any(h in t for h in HOLIDAY_HINTS)
    def is_sick(t):    return isinstance(t,str) and (SICK_KEYWORD in t)
    def is_no_att(t):  return isinstance(t,str) and (NO_ATTENDANCE_KEYWORD in t)

    # -------- FIX 1: forward-fill date/day for split shifts --------
    def load_attendance_from_csv(csv_df: pd.DataFrame) -> pd.DataFrame:
        required = ["סה\"כ נוכחות", "שעת יציאה", "שעת כניסה", "סטטוס/הערות", "יום בשבוע", "תאריך"]
        for c in required:
            if c not in csv_df.columns:
                raise ValueError(f"עמודה חסרה בקובץ: {c}")
        df = csv_df.copy()

        # ניקוי ערכים ריקים/מיותרים בעמודות התאריך והיום
        for col in ["תאריך", "יום בשבוע"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
                df[col] = df[col].replace({"None": pd.NA, "nan": pd.NA, "NaN": pd.NA, "": pd.NA, ".": pd.NA})

        # >>> כאן הקסם: מילוי קדימה כדי ששורה שנייה של אותה משמרת תקבל את התאריך/יום שלמעלה
        df[["תאריך", "יום בשבוע"]] = df[["תאריך", "יום בשבוע"]].ffill()

        return df.astype(str)

    # ---------------- time buckets ----------------
    def compute_evening_minutes(start_dt,end_dt):
        total=0; cur=start_dt
        while cur<end_dt:
            day=cur.date(); eve_start,eve_end=daily_interval(day,time(16,0),time(0,0))
            total+=overlap_minutes(start_dt,end_dt,eve_start,eve_end)
            cur=datetime.combine(day,time(0,0))+timedelta(days=1)
        return total

    def compute_night_minutes(start_dt,end_dt):
        total=0; cur=start_dt
        while cur<end_dt:
            day=cur.date(); night_start=datetime.combine(day,time(0,0)); night_end=datetime.combine(day,time(7,30))
            total+=overlap_minutes(start_dt,end_dt,night_start,night_end)
            cur=datetime.combine(day,time(0,0))+timedelta(days=1)
        return total

    def iter_weekend_windows(around_start,around_end):
        window_start=(around_start - timedelta(days=3)).date()
        window_end=(around_end + timedelta(days=3)).date()
        d=window_start
        while d<=window_end:
            off=(4 - d.weekday())%7  # Fri=4
            fri=d+timedelta(days=off)
            fri_16=datetime.combine(fri,time(16,0))
            sun=fri+timedelta(days=2); sun_0730=datetime.combine(sun,time(7,30))
            yield fri_16,sun_0730
            d+=timedelta(days=7)

    def compute_weekend_minutes(start_dt,end_dt):
        total=0
        for w_start,w_end in iter_weekend_windows(start_dt,end_dt):
            total+=overlap_minutes(start_dt,end_dt,w_start,w_end)
        return total

    def compute_daily_rows(selected_df: pd.DataFrame):
        rows=[]
        for _,r in selected_df.iterrows():
            status=str(r.get("סטטוס/הערות","") or "").strip()
            if is_no_att(status): continue
            try: date=parse_date(str(r["תאריך"]))
            except: continue
            t_in=parse_hhmm(r.get("שעת כניסה"))
            t_out=parse_hhmm(r.get("שעת יציאה"))
            holiday_flag=is_holiday(status); sick_flag=is_sick(status)
            if sick_flag:
                rows.append({"תאריך":date,"סטטוס/הערות":status,"is_sick":True,"holiday":holiday_flag,
                             "start":None,"end":None,"minutes_total":0,"minutes_evening":0,"minutes_night":0,
                             "minutes_weekend":0,"minutes_holiday":0,"worked_day":False})
                continue
            if not t_in or not t_out: continue
            start=datetime.combine(date,t_in); end=datetime.combine(date,t_out)
            if end<=start: end+=timedelta(days=1)
            minutes_total=int((end-start).total_seconds()//60)
            if minutes_total<=0: continue
            minutes_evening=compute_evening_minutes(start,end)
            minutes_night=compute_night_minutes(start,end)
            minutes_weekend=compute_weekend_minutes(start,end)
            minutes_holiday=minutes_total if holiday_flag else 0
            rows.append({"תאריך":date,"סטטוס/הערות":status,"is_sick":False,"holiday":holiday_flag,
                        "start":start,"end":end,"minutes_total":minutes_total,"minutes_evening":minutes_evening,
                        "minutes_night":minutes_night,"minutes_weekend":minutes_weekend,"minutes_holiday":minutes_holiday,
                        "worked_day":True})
        return pd.DataFrame(rows).sort_values("תאריך").reset_index(drop=True)

    # OT daily + pay
    def add_pay_columns(daily_df: pd.DataFrame):
        df=daily_df.copy(); base=HOURLY_WAGE
        for k in ["total","evening","night","weekend","holiday"]:
            df[f"hours_{k}"]=df[f"minutes_{k}"].apply(minutes_to_hours)

        # "בוקר" = כל מה שלא ערב/לילה
        df["hours_morning"] = (df["hours_total"] - df["hours_evening"] - df["hours_night"]).clip(lower=0)

        df["pay_base"]=df["hours_total"]*base
        df["pay_evening_bonus"]=df["hours_evening"]*base*EVENING_BONUS
        df["pay_night_bonus"]=df["hours_night"]*base*NIGHT_BONUS
        df["pay_weekend_bonus"]=df["hours_weekend"]*base*WEEKEND_BONUS
        df["pay_holiday_bonus"]=df["hours_holiday"]*base*HOLIDAY_BONUS

        overtime=(df["hours_total"]-params["DAILY_REGULAR_HOURS"]).clip(lower=0)
        df["hours_ot_t1"]=overtime.clip(upper=params["DAILY_T1_HOURS"])
        df["hours_ot_t2"]=(overtime-df["hours_ot_t1"]).clip(lower=0)
        df["pay_overtime_t1"]=df["hours_ot_t1"]*base*OVERTIME_T1_BONUS
        df["pay_overtime_t2"]=df["hours_ot_t2"]*base*OVERTIME_T2_BONUS

        # -------- FIX 2: נסיעות פעם אחת ליום --------
        df["travel_pay"]=0.0
        for d, g in df.groupby("תאריך", sort=False):
            idx = g.index[(g["worked_day"]) & (~g["is_sick"])].tolist()
            if idx:
                df.loc[idx[0], "travel_pay"] = DAILY_TRAVEL

        df["pay_total_day"]=(df["pay_base"]+df["pay_evening_bonus"]+df["pay_night_bonus"]+
                             df["pay_weekend_bonus"]+df["pay_holiday_bonus"]+
                             df["pay_overtime_t1"]+df["pay_overtime_t2"]+df["travel_pay"])
        return df

    # Weekly top-up (42h)
    def week_period_series(dates, week_start_str):
        freq = "W-SAT" if week_start_str == "Sunday" else "W-SUN"
        return pd.to_datetime(dates).dt.to_period(freq)

    def compute_weekly_overtime_topup(daily_df, weekly_threshold=42.0, week_start_str="Sunday"):
        df = daily_df.copy()
        df["__week"] = week_period_series(df["תאריך"], week_start_str)
        df["weekly_topup_125"] = 0.0
        df["weekly_topup_150"] = 0.0
        df["hours_regular_day"] = df["hours_total"].clip(upper=params["DAILY_REGULAR_HOURS"])

        for _, g in df.groupby("__week", sort=False):
            g_sorted = g.sort_values("תאריך")
            total_reg = float(g_sorted["hours_regular_day"].sum())
            excess = max(0.0, total_reg - weekly_threshold)
            if excess <= 0:
                continue
            remain = excess
            for idx in g_sorted.index:
                if remain <= 0: break
                take = min(float(df.loc[idx, "hours_regular_day"]), remain)
                df.loc[idx, "weekly_topup_125"] += take * HOURLY_WAGE * OVERTIME_T1_BONUS
                remain -= take

        return df.drop(columns="__week")

    # Sick pay
    def add_sick_pay(daily_df: pd.DataFrame, selected_df: pd.DataFrame):
        df=daily_df.copy()
        avg_hours=DEFAULT_DAILY_SICK_HOURS
        if USE_AVG_HOURS_FOR_SICK:
            wh=df.loc[df["worked_day"],"hours_total"]
            if len(wh)>0: avg_hours=float(wh.mean())
        sick_dates=[]
        for _,r in selected_df.iterrows():
            s=str(r.get("סטטוס/הערות","") or "").strip()
            if is_sick(s) and not is_no_att(s):
                try: sick_dates.append(parse_date(str(r["תאריך"])))
                except: pass
        if not sick_dates:
            df["pay_sick"]=0.0; return df
        sick_dates=sorted(set(sick_dates))
        pay_map={}; i=0
        while i<len(sick_dates):
            j=i+1
            while j<len(sick_dates) and (sick_dates[j]-sick_dates[j-1]).days==1: j+=1
            seq=sick_dates[i:j]
            for k,d in enumerate(seq, start=1):
                pct=0.0 if k==1 else (0.5 if k in (2,3) else 1.0)
                pay_map[d]=avg_hours*HOURLY_WAGE*pct
            i=j
        df["pay_sick"]=0.0
        for d,pay in pay_map.items():
            if d in df["תאריך"].values:
                df.loc[df["תאריך"]==d,[
                    "pay_base","pay_evening_bonus","pay_night_bonus","pay_weekend_bonus","pay_holiday_bonus",
                    "pay_overtime_t1","pay_overtime_t2","travel_pay","pay_total_day","weekly_topup_125","weekly_topup_150"
                ]]=0.0
                df.loc[df["תאריך"]==d,"pay_sick"]=pay
            else:
                df=pd.concat([df,pd.DataFrame([{
                    "תאריך":d,"סטטוס/הערות":"מחלה","is_sick":True,"holiday":False,"start":None,"end":None,
                    "minutes_total":0,"minutes_evening":0,"minutes_night":0,"minutes_weekend":0,"minutes_holiday":0,
                    "worked_day":False,
                    "hours_total":0.0,"hours_evening":0.0,"hours_night":0.0,"hours_weekend":0.0,"hours_holiday":0.0,
                    "hours_morning":0.0,
                    "hours_ot_t1":0.0,"hours_ot_t2":0.0,
                    "pay_base":0.0,"pay_evening_bonus":0.0,"pay_night_bonus":0.0,"pay_weekend_bonus":0.0,"pay_holiday_bonus":0.0,
                    "pay_overtime_t1":0.0,"pay_overtime_t2":0.0,"weekly_topup_125":0.0,"weekly_topup_150":0.0,
                    "travel_pay":0.0,"pay_total_day":0.0,"pay_sick":pay
                }])], ignore_index=True)
        return df.sort_values("תאריך").reset_index(drop=True)

    def summarize_hours(df_paid: pd.DataFrame):
        hours_cols=["hours_total","hours_morning","hours_evening","hours_night","hours_weekend","hours_holiday","hours_ot_t1","hours_ot_t2"]
        for c in hours_cols:
            if c not in df_paid.columns: df_paid[c]=0.0
        return df_paid[hours_cols].sum()

    # taxes
    def income_tax_before_credit(monthly_taxable: float)->float:
        tax=0.0; last=0.0
        for cap,rate in TAX_BRACKETS:
            if monthly_taxable>cap: tax+=(cap-last)*rate; last=cap
            else:
                tax+=(monthly_taxable-last)*rate
                return max(0.0,round(tax,2))
        return max(0.0,round(tax,2))
    def apply_credit_points(tax_before: float)->float:
        return max(0.0, round(tax_before - CREDIT_POINTS*CREDIT_POINT_VALUE, 2))
    def ni_health(monthly_gross: float):
        low=min(monthly_gross,NI_THRESHOLD); high=max(0.0,monthly_gross-NI_THRESHOLD)
        ni=low*NI_LOW + high*NI_HIGH; health=low*HEALTH_LOW + high*HEALTH_HIGH
        return round(ni,2), round(health,2)

    def process(csv_df: pd.DataFrame):
        selected=load_attendance_from_csv(csv_df)
        daily=compute_daily_rows(selected)
        paid=add_pay_columns(daily)

        if OT_BASIS in ("Weekly 42h only", "Daily + Weekly (max)"):
            paid_week = compute_weekly_overtime_topup(paid, weekly_threshold=42.0, week_start_str=WEEK_START)
            paid_week["pay_weekly_topup"] = paid_week["weekly_topup_125"] + paid_week["weekly_topup_150"]
        else:
            paid_week = paid.copy()
            paid_week["pay_weekly_topup"] = 0.0

        paid_week = add_sick_pay(paid_week, selected)

        if OT_BASIS == "Weekly 42h only":
            for c in ("pay_overtime_t1","pay_overtime_t2"):
                if c in paid_week.columns: paid_week[c]=0.0
            paid_week["pay_total_day"] = (
                paid_week["pay_base"] + paid_week["pay_evening_bonus"] + paid_week["pay_night_bonus"] +
                paid_week["pay_weekend_bonus"] + paid_week["pay_holiday_bonus"] +
                paid_week["travel_pay"] + paid_week["pay_weekly_topup"]
            )
            paid_final = paid_week
        elif OT_BASIS == "Daily + Weekly (max)":
            modelA = paid.copy(); modelA["pay_weekly_topup"]=0.0
            modelA = add_sick_pay(modelA, selected)
            modelA["pay_total_day_final"] = modelA["pay_total_day"]

            modelB = paid_week.copy()
            modelB_no_daily = paid.copy()
            for c in ("pay_overtime_t1","pay_overtime_t2"):
                if c in modelB_no_daily.columns: modelB_no_daily[c]=0.0
            modelB["pay_total_day_final"] = (
                modelB_no_daily["pay_base"] + modelB_no_daily["pay_evening_bonus"] + modelB_no_daily["pay_night_bonus"] +
                modelB_no_daily["pay_weekend_bonus"] + modelB_no_daily["pay_holiday_bonus"] +
                modelB_no_daily["travel_pay"] + modelB["pay_weekly_topup"]
            )

            paid_final = modelA.copy()
            choose = modelB["pay_total_day_final"] > modelA["pay_total_day_final"]
            paid_final["pay_total_day"] = modelA["pay_total_day_final"]
            paid_final.loc[choose,"pay_total_day"] = modelB.loc[choose,"pay_total_day_final"]
            paid_final["pay_weekly_topup"]=0.0
        else:
            paid_final = paid_week.copy()
            paid_final["pay_weekly_topup"]=0.0

        sums_hours = summarize_hours(paid_final)
        wage_components = (
            paid_final["pay_base"].sum()
            + paid_final["pay_evening_bonus"].sum()
            + paid_final["pay_night_bonus"].sum()
            + paid_final["pay_weekend_bonus"].sum()
            + paid_final["pay_holiday_bonus"].sum()
            + paid_final.get("pay_overtime_t1",0).sum()
            + paid_final.get("pay_overtime_t2",0).sum()
            + paid_final.get("pay_weekly_topup",0).sum()
            + paid_final["pay_sick"].sum()
        )
        travel_sum = paid_final["travel_pay"].sum()
        monthly_gross_taxable = wage_components + travel_sum + SIBUS_MONTHLY

        pension_base = monthly_gross_taxable if PENSION_BASE_MODE=="include_all" else wage_components
        employee_pension = round(pension_base*EMPLOYEE_PENSION_RATE,2)
        ni,health = ni_health(monthly_gross_taxable)
        tax_before = income_tax_before_credit(monthly_gross_taxable)
        tax_after  = apply_credit_points(tax_before)
        net = monthly_gross_taxable - (employee_pension + ni + health + tax_after)

        brk = pd.DataFrame([
            ["שכר בסיס", paid_final["pay_base"].sum()],
            ["תוספת ערב", paid_final["pay_evening_bonus"].sum()],
            ["תוספת לילה", paid_final["pay_night_bonus"].sum()],
            ["תוספת סופ\"ש", paid_final["pay_weekend_bonus"].sum()],
            ["תוספת חג", paid_final["pay_holiday_bonus"].sum()],
            ["שעות נוספות 125%", paid_final.get("pay_overtime_t1",0).sum()],
            ["שעות נוספות 150%", paid_final.get("pay_overtime_t2",0).sum()],
            ["טופ-אפ שבועי 42ש׳", paid_final.get("pay_weekly_topup",0).sum()],
            ["מחלה", paid_final["pay_sick"].sum()],
            ["נסיעות", travel_sum],
            ["סיבוס", SIBUS_MONTHLY],
            ["סה\"כ ברוטו חייב", monthly_gross_taxable],
        ], columns=["רכיב", "סכום"]).style.format({"סכום": money})

        deds = pd.DataFrame([
            ["פנסיה עובד", employee_pension],
            ["ביטוח לאומי", ni],
            ["בריאות", health],
            ["מס הכנסה לפני זיכוי", tax_before],
            [f"זיכוי מס (נק׳ × {CREDIT_POINT_VALUE:.0f})", tax_before - tax_after],
            ["מס הכנסה לתשלום", tax_after],
        ], columns=["ניכוי", "סכום"]).style.format({"סכום": money})

        charts_df = pd.DataFrame({
            "רכיב": ["שכר בסיס","ערב","לילה","סופ\"ש","חג","OT 125%","OT 150%","טופ-אפ שבועי","מחלה","נסיעות","סיבוס"],
            "סכום": [
                paid_final["pay_base"].sum(),
                paid_final["pay_evening_bonus"].sum(),
                paid_final["pay_night_bonus"].sum(),
                paid_final["pay_weekend_bonus"].sum(),
                paid_final["pay_holiday_bonus"].sum(),
                paid_final.get("pay_overtime_t1",0).sum(),
                paid_final.get("pay_overtime_t2",0).sum(),
                paid_final.get("pay_weekly_topup",0).sum(),
                paid_final["pay_sick"].sum(),
                travel_sum,
                SIBUS_MONTHLY,
            ]
        })

        # exports
        csv_buf=io.StringIO(); paid_final.to_csv(csv_buf,index=False,encoding="utf-8-sig"); csv_bytes=csv_buf.getvalue().encode("utf-8-sig")
        json_bytes=json.dumps({
            "gross": monthly_gross_taxable,
            "net": float(net),
            "deductions_total": float((employee_pension + ni + health + tax_after))
        }, ensure_ascii=False, indent=2).encode("utf-8")

        # sick days counters
        sick_df = paid_final.copy()
        if "is_sick" not in sick_df.columns:
            sick_df["is_sick"] = False
        if "pay_sick" not in sick_df.columns:
            sick_df["pay_sick"] = 0.0
        total_sick_days  = int((sick_df["is_sick"] == True).sum())
        paid_sick_days   = int(((sick_df["is_sick"] == True) & (sick_df["pay_sick"] > 0)).sum())
        unpaid_sick_days = total_sick_days - paid_sick_days

        return (
            paid_final, summarize_hours(paid_final), brk, deds, charts_df, net, monthly_gross_taxable,
            csv_bytes, json_bytes, total_sick_days, paid_sick_days, unpaid_sick_days
        )

    return process

# =========================
# Sidebar – settings
# =========================
with st.sidebar:
    st.markdown('<span class="badge">הגדרות</span>', unsafe_allow_html=True)
    st.header("⚙️ בסיס")
    hourly  = st.number_input("שכר לשעה (₪)", min_value=0.0, value=DEFAULT_PARAMS["HOURLY_WAGE"], step=0.5)
    evening = st.number_input("תוספת ערב (%)", min_value=0.0, value=DEFAULT_PARAMS["EVENING_BONUS"]*100, step=5.0)/100.0
    night   = st.number_input("תוספת לילה (%)", min_value=0.0, value=DEFAULT_PARAMS["NIGHT_BONUS"]*100, step=5.0)/100.0
    weekend = st.number_input("תוספת סופ״ש (%)", min_value=0.0, value=DEFAULT_PARAMS["WEEKEND_BONUS"]*100, step=5.0)/100.0
    holiday = st.number_input("תוספת חג (%)", min_value=0.0, value=DEFAULT_PARAMS["HOLIDAY_BONUS"]*100, step=5.0)/100.0
    travel  = st.number_input("נסיעות ליום (₪)", min_value=0.0, value=DEFAULT_PARAMS["DAILY_TRAVEL"], step=1.0)
    sibus   = st.number_input("סיבוס חודשי (₪)", min_value=0.0, value=DEFAULT_PARAMS["SIBUS_MONTHLY"], step=10.0)

    st.subheader("⏱️ שעות נוספות")
    ot_t1 = st.number_input("125% – תוספת (%)", min_value=0.0, value=DEFAULT_PARAMS["OVERTIME_T1_BONUS"]*100, step=5.0)/100.0
    ot_t2 = st.number_input("150% – תוספת (%)", min_value=0.0, value=DEFAULT_PARAMS["OVERTIME_T2_BONUS"]*100, step=5.0)/100.0
    base_hours = st.number_input("שעות רגילות ביום", min_value=0.0, value=DEFAULT_PARAMS["DAILY_REGULAR_HOURS"], step=0.5)
    t1_hours   = st.number_input("שעות בדרגת 125% (ביום)", min_value=0.0, value=DEFAULT_PARAMS["DAILY_T1_HOURS"], step=0.5)

    st.radio("בסיס שעות נוספות", ["Daily only", "Weekly 42h only", "Daily + Weekly (max)"], index=2, key="ot_basis")
    st.selectbox("תחילת שבוע", ["Sunday","Monday"], index=0, key="week_start")

    st.subheader("🏦 מסים ופנסיה")
    pension_rate = st.number_input("פנסיה עובד (%)", min_value=0.0, value=DEFAULT_PARAMS["EMPLOYEE_PENSION_RATE"]*100, step=0.5)/100.0
    pension_base_mode = st.radio("בסיס לפנסיה", ["wage_only","include_all"], index=0, horizontal=True)

    credit_pts = st.number_input("נק׳ זיכוי", min_value=0.0, value=DEFAULT_PARAMS["CREDIT_POINTS"], step=0.25)
    credit_val = st.number_input("שווי נק׳ (₪)", min_value=0.0, value=DEFAULT_PARAMS["CREDIT_POINT_VALUE"], step=5.0)
    default_brackets = [{"cap": 6790, "rate": 0.10},{"cap": 9720,"rate":0.14},{"cap":15760,"rate":0.20},
                        {"cap":21700,"rate":0.31},{"cap":45180,"rate":0.35},{"cap": None,"rate":0.47}]
    tax_json = st.text_area("מדרגות מס (JSON)", value=json.dumps(default_brackets, ensure_ascii=False, indent=2), height=160)

    c6,c7 = st.columns(2)
    with c6:
        ni_thr = st.number_input("סף ב״ל/בריאות (₪)", min_value=0.0, value=DEFAULT_PARAMS["NI_THRESHOLD"], step=50.0)
        ni_low = st.number_input("ব״ל – מדרגה נמוכה (%)", min_value=0.0, value=DEFAULT_PARAMS["NI_LOW"]*100, step=0.1)/100.0
        hl_low = st.number_input("בריאות – מדרגה נמוכה (%)", min_value=0.0, value=DEFAULT_PARAMS["HEALTH_LOW"]*100, step=0.1)/100.0
    with c7:
        ni_high= st.number_input("ב״ל – מדרגה גבוהה (%)", min_value=0.0, value=DEFAULT_PARAMS["NI_HIGH"]*100, step=0.5)/100.0
        hl_high= st.number_input("בריאות – מדרגה גבוהה (%)", min_value=0.0, value=DEFAULT_PARAMS["HEALTH_HIGH"]*100, step=0.5)/100.0

# parse tax brackets
def parse_tax_brackets(txt: str):
    try:
        arr=json.loads(txt)
        out=[]
        for item in arr:
            cap=item["cap"]; rate=float(item["rate"])
            out.append((float("inf") if cap in (None,"null") else float(cap), rate))
        return out
    except Exception:
        return DEFAULT_PARAMS["TAX_BRACKETS"]

# =========================
# Params dict
# =========================
params = {
    "HOURLY_WAGE": hourly, "EVENING_BONUS": evening, "NIGHT_BONUS": night,
    "WEEKEND_BONUS": weekend, "HOLIDAY_BONUS": holiday,
    "DAILY_TRAVEL": travel, "SIBUS_MONTHLY": sibus,
    "OVERTIME_T1_BONUS": ot_t1, "OVERTIME_T2_BONUS": ot_t2,
    "DAILY_REGULAR_HOURS": base_hours, "DAILY_T1_HOURS": t1_hours,
    "EMPLOYEE_PENSION_RATE": pension_rate, "PENSION_BASE_MODE": pension_base_mode,
    "OT_BASIS": st.session_state.ot_basis, "WEEK_START": st.session_state.week_start,
    "CREDIT_POINTS": credit_pts, "CREDIT_POINT_VALUE": credit_val,
    "TAX_BRACKETS": parse_tax_brackets(tax_json),
    "NI_THRESHOLD": ni_thr, "NI_LOW": ni_low, "NI_HIGH": ni_high,
    "HEALTH_LOW": hl_low, "HEALTH_HIGH": hl_high,
    "USE_AVG_HOURS_FOR_SICK": DEFAULT_PARAMS["USE_AVG_HOURS_FOR_SICK"],
    "DEFAULT_DAILY_SICK_HOURS": DEFAULT_PARAMS["DEFAULT_DAILY_SICK_HOURS"],
    "SICK_KEYWORD": DEFAULT_PARAMS["SICK_KEYWORD"],
    "NO_ATTENDANCE_KEYWORD": DEFAULT_PARAMS["NO_ATTENDANCE_KEYWORD"],
    "HOLIDAY_HINTS": DEFAULT_PARAMS["HOLIDAY_HINTS"],
}

# =========================
# FLOW state
# =========================
if "step" not in st.session_state: st.session_state.step = 1
if "input_df_raw" not in st.session_state: st.session_state.input_df_raw = None
if "input_df_edited" not in st.session_state: st.session_state.input_df_edited = None

st.title("💸 מחשבון שכר – אשף זרימה")
def step_header(num, title):
    st.markdown(f'<div class="step"><div class="num">{num}</div><div class="title">{title}</div></div>', unsafe_allow_html=True)

processor = build_processor(params)

# =========================
# Step 1: Upload
# =========================
if st.session_state.step == 1:
    step_header(1, "העלה קובץ (CSV או PDF)")
    mode = st.radio("בחר מקור:", ["CSV", "PDF"], horizontal=True)
    df_in = None

    if mode=="CSV":
        st.caption("דרוש CSV עם העמודות: סה\"כ נוכחות, שעת יציאה, שעת כניסה, סטטוס/הערות, יום בשבוע, תאריך")
        up = st.file_uploader("בחר קובץ CSV", type=["csv"], key="u_csv")
        if up is not None:
            try: df_in = pd.read_csv(up, encoding="utf-8-sig")
            except UnicodeDecodeError: df_in = pd.read_csv(up)
    else:
        st.caption("PDF של דוח נוכחות. נחלץ טבלה ותתבצע התאמת עברית חכמה.")
        heb_fix_mode = st.selectbox("מצב תיקון עברית מה-PDF", ["אוטומטי","לא להפוך","להפוך תמיד"], index=0)
        up = st.file_uploader("בחר קובץ PDF", type=["pdf"], key="u_pdf")
        if up is not None:
            try:
                import pdfplumber
                with pdfplumber.open(up) as pdf:
                    tables=[]
                    for page in pdf.pages:
                        for t in (page.extract_tables() or []):
                            df=pd.DataFrame(t)
                            if df.shape[1]>=6 and df.dropna(how="all").shape[0]>0:
                                tables.append(df)
                if not tables:
                    st.error("לא נמצאו טבלאות מתאימות ב־PDF")
                else:
                    df=tables[0].reset_index(drop=True).copy()
                    cols_map = {2:"סה\"כ נוכחות", 3:"שעת יציאה", 4:"שעת כניסה", 5:"סטטוס/הערות", 7:"יום בשבוע", 8:"תאריך"}
                    df_in = df.rename(columns=cols_map)
                    need = ["סה\"כ נוכחות","שעת יציאה","שעת כניסה","סטטוס/הערות","יום בשבוע","תאריך"]
                    df_in = df_in[[c for c in need if c in df_in.columns]].copy()
                    # תיקון תאריך/יום חסרים משורה שנייה של אותו יום
                    for col in ["תאריך", "יום בשבוע"]:
                        if col in df_in.columns:
                            df_in[col] = df_in[col].astype(str).str.strip()
                            df_in[col] = df_in[col].replace(
                                {"None": pd.NA, "nan": pd.NA, "NaN": pd.NA, "": pd.NA, ".": pd.NA})
                    df_in[["תאריך", "יום בשבוע"]] = df_in[["תאריך", "יום בשבוע"]].ffill()

                    mode_map = {"אוטומטי":"auto","לא להפוך":"off","להפוך תמיד":"on"}
                    df_in = apply_hebrew_correction(df_in, mode=mode_map[heb_fix_mode])
                    st.success("טבלה חולצה. ממשיכים לעריכה…")
            except ModuleNotFoundError:
                st.error("נדרש: pip install pdfplumber")
            except Exception as e:
                st.error(f"שגיאת חילוץ PDF: {e}")

    if df_in is not None:
        st.session_state.input_df_raw = df_in
        st.session_state.input_df_edited = df_in.copy()  # נשמור גם כבסיס לעריכה
        st.session_state.step = 2
        st.rerun()

# =========================
# Step 2: Edit & confirm
# =========================
elif st.session_state.step == 2:
    step_header(2, "ערוך את הטבלה ואשר")

    if st.session_state.input_df_raw is None:
        st.warning("אין נתונים לתצוגה. חזור לשלב 1.")
        if st.button("⬅️ חזור להעלאת קובץ"):
            st.session_state.step = 1
            st.rerun()
    else:
        st.caption("ודא כותרות: סה\"כ נוכחות, שעת יציאה, שעת כניסה, סטטוס/הערות, יום בשבוע, תאריך. ניתן להוסיף/למחוק/לערוך.")

        # ======= טופס עריכה — לא מבצע rerun על כל שינוי =======
        with st.form("edit_form", clear_on_submit=False):
            edited = st.data_editor(
                st.session_state.input_df_raw,
                use_container_width=True,
                num_rows="dynamic",
                hide_index=True,
                key="editor_table",
            )

            # ולידציה לפני כפתורי הפעולה
            cols_needed = ["סה\"כ נוכחות","שעת יציאה","שעת כניסה","סטטוס/הערות","יום בשבוע","תאריך"]
            missing = [c for c in cols_needed if c not in edited.columns]
            if missing:
                st.error("חסרות העמודות: " + ", ".join(missing))

            colA, colB = st.columns(2)
            back_btn   = colA.form_submit_button("⬅️ חזור")
            next_btn   = colB.form_submit_button("✅ אשר והמשך", disabled=bool(missing), type="primary")
        # ======= סוף טופס =======

        # חשוב: שמירה מפורשת רק לאחר לחיצה
        if back_btn:
            st.session_state.input_df_raw    = edited.copy()   # נשמרת העריכה
            st.session_state.input_df_edited = edited.copy()
            st.session_state.step = 1
            st.rerun()

        if next_btn:
            st.session_state.input_df_raw    = edited.copy()   # נשמרת העריכה
            st.session_state.input_df_edited = edited.copy()
            st.session_state.step = 3
            st.rerun()


# =========================
# Step 3: Results
# =========================
elif st.session_state.step == 3:
    step_header(3, "תוצאות וחישוב")
    if st.session_state.input_df_edited is None:
        st.warning("אין טבלה מאושרת. חזור לשלב העריכה.")
        if st.button("⬅️ חזור לעריכה"):
            st.session_state.step = 2
            st.rerun()
    else:
        try:
            (paid_df, sums_hours, brk_style, deds_style, charts_df, net, gross,
             csv_bytes, json_bytes, sick_all, sick_paid, sick_unpaid) = build_processor(params)(st.session_state.input_df_edited)

            # KPIs
            k1,k2,k3 = st.columns(3)
            with k1:
                st.markdown('<div class="kpi">', unsafe_allow_html=True)
                st.markdown('<div class="label">סה״כ ברוטו חייב</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="value">{money(gross)}</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
            with k2:
                st.markdown('<div class="kpi">', unsafe_allow_html=True)
                st.markdown('<div class="label">סה״כ ניכויים</div>', unsafe_allow_html=True)
                deductions_total = gross - net
                st.markdown(f'<div class="value" style="color:{ACCENT}">{money(deductions_total)}</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
            with k3:
                st.markdown('<div class="kpi">', unsafe_allow_html=True)
                st.markdown('<div class="label">נטו לתשלום</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="value" style="color:{PRIMARY}">{money(net)}</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown("#### פירוט רכיבי ברוטו")
            st.dataframe(brk_style, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

            cA, cB = st.columns([1.2, 1])
            with cA:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown("#### רכיבי שכר (גרף עמודות)")
                chart_df = charts_df.sort_values("סכום", ascending=False).head(8).set_index("רכיב")
                st.bar_chart(chart_df)
                st.markdown('</div>', unsafe_allow_html=True)
            with cB:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown("#### ניכויים")
                st.dataframe(deds_style, use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)

            # שעות + מחלה
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown("#### סיכום שעות")
            colH, colS = st.columns([1.2, 0.8])
            with colH:
                hours_df = pd.DataFrame([
                    ["סה\"כ שעות",  sums_hours.get("hours_total",  0.0)],
                    ["בוקר",        sums_hours.get("hours_morning",0.0)],
                    ["ערב",         sums_hours.get("hours_evening",0.0)],
                    ["לילה",        sums_hours.get("hours_night",  0.0)],
                    ["סופ\"ש",      sums_hours.get("hours_weekend",0.0)],
                    ["חג",          sums_hours.get("hours_holiday",0.0)],
                    ["נוספות 125%", sums_hours.get("hours_ot_t1",  0.0)],
                    ["נוספות 150%", sums_hours.get("hours_ot_t2",  0.0)],
                ], columns=["קטגוריה","שעות"])
                st.dataframe(hours_df.style.format({"שעות":"{:.2f}"}), use_container_width=True, hide_index=True)
            with colS:
                sick_tbl = pd.DataFrame([
                    ["סה\"כ ימי מחלה",      sick_all],
                    ["ימי מחלה בתשלום",     sick_paid],
                    ["ימי מחלה ללא תשלום",  sick_unpaid],
                ], columns=["קטגוריה", "כמות"])
                st.dataframe(sick_tbl, use_container_width=True, hide_index=True)
            st.markdown('</div>', unsafe_allow_html=True)

            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown("#### פירוט יומי (לאחר החישוב)")
            st.dataframe(paid_df, use_container_width=True, hide_index=True)
            st.markdown('</div>', unsafe_allow_html=True)

            c1,c2,c3 = st.columns(3)
            with c1:
                st.download_button("⬇️ פירוט יומי (CSV)", data=csv_bytes, file_name="daily_breakdown.csv", mime="text/csv", type="primary")
            with c2:
                st.download_button("⬇️ KPIs (JSON)", data=json_bytes, file_name="summary_kpis.json", mime="application/json")
            with c3:
                if st.button("⬅️ חזור לעריכה", use_container_width=True):
                    # נשמור את הטבלה שאושרה/נערכה קודם
                    st.session_state.input_df_raw = st.session_state.input_df_edited.copy()
                    st.session_state.step = 2
                    st.rerun()

        except ValueError as ve:
            st.error(f"שגיאת ולידציה: {ve}")
        except Exception as e:
            st.error(f"שגיאת עיבוד: {e}")
