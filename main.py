from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import re
from datetime import datetime, timedelta
import calendar
import urllib3
import os

urllib3.disable_warnings()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SMBS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "http://www.smbs.biz/ExRate/StdExRate.jsp"
}

ALL_TARGET = ["USD", "CNH", "KWD", "AED", "SAR", "KZT", "MXN"]

CUR_NAMES = {
    "USD": "미국 달러", "CNH": "중국 위안화",
    "KWD": "쿠웨이트 디나르", "AED": "UAE 디르함",
    "SAR": "사우디 리얄", "KZT": "카자흐스탄 텡게",
    "MXN": "멕시코 페소", "IQD": "이라크 디나르",
    "LBP": "레바논 파운드",
}

# =====================
# 서울외국환중개 XML
# =====================
def fetch_smbs_xml(endpoint, currency, start, end, referer=None):
    """start, end: YYYY-MM-DD 형식"""
    arr_value = f"{currency}_{start}_{end}"
    headers = {**SMBS_HEADERS}
    if referer:
        headers["Referer"] = referer
    try:
        res = requests.get(
            f"http://www.smbs.biz/ExRate/{endpoint}?arr_value={arr_value}",
            headers=headers,
            timeout=10,
            verify=False
        )
        content = res.content.decode("euc-kr").strip()
        pattern = re.compile(r"<set\s+label='([^']+)'\s+value='([^']+)'")
        data = {}
        for match in pattern.finditer(content):
            label = match.group(1).strip()
            value = match.group(2).strip()
            key = label.replace(".", "").replace("-", "")
            data[key] = value
        return data
    except Exception as e:
        print(f"SMBS XML 오류 ({currency}): {e}")
        return {}

def to_dash(date_str):
    """20260423 → 2026-04-23"""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

def get_latest_date():
    """오늘부터 최대 10일 전까지 데이터 있는 날짜 찾기"""
    date = datetime.now()
    for _ in range(10):
        date_str = date.strftime("%Y%m%d")
        formatted = to_dash(date_str)
        data = fetch_smbs_xml(
            "StdExRate_xml.jsp", "USD", formatted, formatted,
            "http://www.smbs.biz/ExRate/StdExRate.jsp"
        )
        if data:
            return date_str
        date -= timedelta(days=1)
    return datetime.now().strftime("%Y%m%d")

def fetch_smbs_today(currency, date_str):
    """특정일 환율"""
    formatted = to_dash(date_str)
    data = fetch_smbs_xml(
        "StdExRate_xml.jsp", currency, formatted, formatted,
        "http://www.smbs.biz/ExRate/StdExRate.jsp"
    )
    key = date_str
    if key in data:
        return data[key]
    if data:
        return list(data.values())[-1]
    return ""

def fetch_smbs_monthly_avg(currency, year, month):
    """월평균"""
    data = fetch_smbs_xml(
        "MonAvgStdExRate_xml.jsp", currency,
        f"{year-1}-{month:02d}", f"{year}-{month:02d}",
        "http://www.smbs.biz/ExRate/MonAvgStdExRate.jsp"
    )
    target_key = f"{year}{month:02d}"
    if target_key not in data and data:
        target_key = sorted(data.keys())[-1]
    return data.get(target_key, "")

def fetch_smbs_month_end(currency, year, month):
    """월말"""
    data = fetch_smbs_xml(
        "MonLastStdExRate_xml.jsp", currency,
        f"{year-1}-{month:02d}", f"{year}-{month:02d}",
        "http://www.smbs.biz/ExRate/MonLastStdExRate.jsp"
    )
    target_key = f"{year}{month:02d}"
    if target_key not in data and data:
        target_key = sorted(data.keys())[-1]
    return data.get(target_key, "")

def calc_change(today_val, yesterday_val, decimal=2):
    try:
        t = float(str(today_val).replace(",", ""))
        y = float(str(yesterday_val).replace(",", ""))
        diff = t - y
        if diff > 0:
            return "RISE", f"{diff:+.{decimal}f}"
        elif diff < 0:
            return "FALL", f"{diff:+.{decimal}f}"
        else:
            return "EVEN", "0.00"
    except:
        return "", ""

# =====================
# open.er-api.com - IQD, LBP
# =====================
def fetch_er_open():
    try:
        res = requests.get(
            "https://open.er-api.com/v6/latest/KRW",
            timeout=10,
            verify=False
        )
        data = res.json().get("rates", {})
        result = {}
        for cur in ["IQD", "LBP"]:
            if cur in data and data[cur] != 0:
                rate = round(1 / data[cur], 4)
                result[cur] = str(rate)
        return result
    except Exception as e:
        print(f"ER Open 오류: {e}")
        return {}

# =====================
# API 엔드포인트
# =====================
@app.get("/")
def root():
    return {"status": "ok", "message": "환율 API 서버 작동중"}

@app.get("/rates")
def get_rates():
    try:
        today_str = get_latest_date()
        
        # 전일 찾기
        prev_date = datetime.strptime(today_str, "%Y%m%d") - timedelta(days=1)
        yesterday_str = ""
        for _ in range(10):
            ps = prev_date.strftime("%Y%m%d")
            test = fetch_smbs_today("USD", ps)
            if test:
                yesterday_str = ps
                break
            prev_date -= timedelta(days=1)

        rates = []

        # USD, CNH, KWD, AED, SAR, KZT, MXN
        for cur in ALL_TARGET:
            try:
                today_val = fetch_smbs_today(cur, today_str)
                yesterday_val = fetch_smbs_today(cur, yesterday_str) if yesterday_str else ""
                if today_val:
                    decimal = 4 if cur in ["KZT"] else 2
                    change, change_val = calc_change(today_val, yesterday_val, decimal)
                    rates.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": today_val,
                        "buy": "-", "sell": "-",
                        "change": change,
                        "change_val": change_val,
                    })
            except Exception as e:
                print(f"{cur} 오류: {e}")

        # IQD, LBP - open.er-api.com
        try:
            er_data = fetch_er_open()
            for cur in ["IQD", "LBP"]:
                if cur in er_data:
                    rates.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": er_data[cur],
                        "buy": "-", "sell": "-",
                        "change": "", "change_val": "",
                    })
        except Exception as e:
            print(f"IQD/LBP 오류: {e}")

        return {
            "success": True,
            "date": today_str,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/by-date")
def get_rates_by_date(date: str):
    try:
        prev_date = datetime.strptime(date, "%Y%m%d") - timedelta(days=1)
        prev_str = ""
        for _ in range(10):
            ps = prev_date.strftime("%Y%m%d")
            test = fetch_smbs_today("USD", ps)
            if test:
                prev_str = ps
                break
            prev_date -= timedelta(days=1)

        rates = []

        for cur in ALL_TARGET:
            try:
                today_val = fetch_smbs_today(cur, date)
                yesterday_val = fetch_smbs_today(cur, prev_str) if prev_str else ""
                if today_val:
                    decimal = 4 if cur in ["KZT"] else 2
                    change, change_val = calc_change(today_val, yesterday_val, decimal)
                    rates.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": today_val,
                        "buy": "-", "sell": "-",
                        "change": change,
                        "change_val": change_val,
                    })
            except Exception as e:
                print(f"{cur} 오류: {e}")

        # IQD, LBP
        try:
            er_data = fetch_er_open()
            for cur in ["IQD", "LBP"]:
                if cur in er_data:
                    rates.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": er_data[cur],
                        "buy": "-", "sell": "-",
                        "change": "", "change_val": "",
                    })
        except:
            pass

        if not rates:
            return {"success": False, "error": "데이터 없음 (주말/공휴일)", "data": []}

        return {
            "success": True,
            "date": date,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/monthly-avg")
def get_monthly_avg(year: int, month: int):
    try:
        result = []

        for cur in ALL_TARGET:
            try:
                val = fetch_smbs_monthly_avg(cur, year, month)
                if val:
                    result.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": val,
                        "buy": "-", "sell": "-",
                    })
            except Exception as e:
                print(f"{cur} 월평균 오류: {e}")

        return {
            "success": True,
            "year": year,
            "month": month,
            "data": result
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/month-end")
def get_month_end(year: int, month: int):
    try:
        result = []

        for cur in ALL_TARGET:
            try:
                val = fetch_smbs_month_end(cur, year, month)
                if val:
                    result.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": val,
                        "buy": "-", "sell": "-",
                    })
            except Exception as e:
                print(f"{cur} 월말 오류: {e}")

        return {
            "success": True,
            "year": year,
            "month": month,
            "data": result
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/debug/today")
def debug_today():
    try:
        today_str = get_latest_date()
        formatted = to_dash(today_str)
        result = {}
        for cur in ALL_TARGET:
            val = fetch_smbs_today(cur, today_str)
            result[cur] = val
        return {"today": today_str, "formatted": formatted, "result": result}
    except Exception as e:
        return {"error": str(e)}
