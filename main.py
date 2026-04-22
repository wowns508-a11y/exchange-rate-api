from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from datetime import datetime, timedelta
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

API_KEY = os.environ.get("KEY", "9U8uB1WTiCDYQx6g7UsRMi05Jhi8upFr")
API_URL = "https://www.koreaexim.go.kr/site/program/financial/exchangeJSON"
TARGET = ["USD", "CNH", "KWD", "AED", "SAR"]

def fetch_by_date(date_str):
    params = {
        "authkey": API_KEY,
        "searchdate": date_str,
        "data": "AP01"
    }
    res = requests.get(API_URL, params=params, timeout=10, verify=False)
    data = res.json()
    if not isinstance(data, list):
        return {}
    return {item["cur_unit"]: item for item in data}

def get_workday(offset=0):
    """영업일 계산 (주말 제외)"""
    date = datetime.now()
    count = 0
    while count > offset:
        date -= timedelta(days=1)
        if date.weekday() < 5:  # 월~금
            count -= 1
    return date.strftime("%Y%m%d")

@app.get("/")
def root():
    return {"status": "ok", "message": "환율 API 서버 작동중"}

@app.get("/rates")
def get_rates():
    try:
        today_str = get_workday(0)
        yesterday_str = get_workday(-1)

        today_data = fetch_by_date(today_str)
        yesterday_data = fetch_by_date(yesterday_str)

        rates = []
        for cur in TARGET:
            today = today_data.get(cur)
            yesterday = yesterday_data.get(cur)

            if not today:
                continue

            base_today = float(today["deal_bas_r"].replace(",", ""))
            
            # 전일대비 계산
            change = ""
            change_val = ""
            if yesterday:
                base_yesterday = float(yesterday["deal_bas_r"].replace(",", ""))
                diff = base_today - base_yesterday
                if diff > 0:
                    change = "RISE"
                    change_val = f"{diff:+.2f}"
                elif diff < 0:
                    change = "FALL"
                    change_val = f"{diff:+.2f}"
                else:
                    change = "EVEN"
                    change_val = "0.00"

            rates.append({
                "currency": cur,
                "name": today["cur_nm"],
                "base": today["deal_bas_r"],
                "buy": today["ttb"],
                "sell": today["tts"],
                "change": change,
                "change_val": change_val,
            })

        # IQD, LBP, MXN, KZT - open.er-api에서 가져오기
        try:
            er_res = requests.get(
                "https://open.er-api.com/v6/latest/KRW",
                timeout=10,
                verify=False
            )
            er_data = er_res.json().get("rates", {})
            extra_currencies = {
                "IQD": "이라크 디나르",
                "LBP": "레바논 파운드",
                "MXN": "멕시코 페소",
                "KZT": "카자흐스탄 텡게",
            }
            for cur, name in extra_currencies.items():
                if cur in er_data:
                    rate = round(1 / er_data[cur], 4)
                    rates.append({
                        "currency": cur,
                        "name": name,
                        "base": f"{rate:,.4f}",
                        "buy": "-",
                        "sell": "-",
                        "change": "",
                        "change_val": "",
                    })
        except:
            pass

        return {
            "success": True,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/monthly-avg")
def get_monthly_avg(year: int, month: int):
    """월평균 환율"""
    try:
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        all_rates = {}
        counts = {}

        for day in range(1, last_day + 1):
            date = datetime(year, month, day)
            if date.weekday() >= 5:  # 주말 제외
                continue
            date_str = date.strftime("%Y%m%d")
            data = fetch_by_date(date_str)
            for cur in TARGET:
                if cur in data:
                    val = float(data[cur]["deal_bas_r"].replace(",", ""))
                    if cur not in all_rates:
                        all_rates[cur] = 0
                        counts[cur] = 0
                    all_rates[cur] += val
                    counts[cur] += 1

        result = []
        for cur in TARGET:
            if cur in all_rates and counts[cur] > 0:
                avg = all_rates[cur] / counts[cur]
                result.append({
                    "currency": cur,
                    "base": f"{avg:,.2f}",
                    "buy": "-",
                    "sell": "-",
                    "change": "",
                    "change_val": "",
                })

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
    """월말 환율"""
    try:
        import calendar
        last_day = calendar.monthrange(year, month)[1]

        for day in range(last_day, 0, -1):
            date = datetime(year, month, day)
            if date.weekday() >= 5:
                continue
            date_str = date.strftime("%Y%m%d")
            data = fetch_by_date(date_str)
            if data:
                result = []
                for cur in TARGET:
                    if cur in data:
                        result.append({
                            "currency": cur,
                            "name": data[cur]["cur_nm"],
                            "base": data[cur]["deal_bas_r"],
                            "buy": data[cur]["ttb"],
                            "sell": data[cur]["tts"],
                            "change": "",
                            "change_val": "",
                        })
                return {
                    "success": True,
                    "year": year,
                    "month": month,
                    "date": date_str,
                    "data": result
                }

        return {"success": False, "error": "데이터 없음", "data": []}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}
        
@app.get("/rates/by-date")
def get_rates_by_date(date: str):
    """특정 날짜 환율 조회 (YYYYMMDD)"""
    try:
        today_data = fetch_by_date(date)
        
        # 전일 계산
        prev_date = datetime.strptime(date, "%Y%m%d") - timedelta(days=1)
        # 주말이면 금요일로
        while prev_date.weekday() >= 5:
            prev_date -= timedelta(days=1)
        yesterday_data = fetch_by_date(prev_date.strftime("%Y%m%d"))

        if not today_data:
            return {"success": False, "error": "데이터 없음", "data": []}

        rates = []
        for cur in TARGET:
            today = today_data.get(cur)
            yesterday = yesterday_data.get(cur)
            if not today:
                continue

            base_today = float(today["deal_bas_r"].replace(",", ""))
            change = ""
            change_val = ""
            if yesterday:
                base_yesterday = float(yesterday["deal_bas_r"].replace(",", ""))
                diff = base_today - base_yesterday
                if diff > 0:
                    change = "RISE"
                    change_val = f"{diff:+.2f}"
                elif diff < 0:
                    change = "FALL"
                    change_val = f"{diff:+.2f}"
                else:
                    change = "EVEN"
                    change_val = "0.00"

            rates.append({
                "currency": cur,
                "name": today["cur_nm"],
                "base": today["deal_bas_r"],
                "buy": today["ttb"],
                "sell": today["tts"],
                "change": change,
                "change_val": change_val,
            })

        return {
            "success": True,
            "date": date,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}
