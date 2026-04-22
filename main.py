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

API_KEY = os.environ.get("KEY", "9U8uB1WTiCDYQx6g7UsRMi05Jhi8upFr")
EXIM_URL = "https://www.koreaexim.go.kr/site/program/financial/exchangeJSON"
EXIM_TARGET = ["USD", "CNH", "KWD", "AED", "SAR"]
SMBS_TARGET = ["KZT", "MXN"]
ER_TARGET = ["IQD", "LBP"]

SMBS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "http://www.smbs.biz/ExRate/MonAvgStdExRate.jsp"
}

CUR_NAMES = {
    "USD": "미국 달러", "CNH": "중국 위안화",
    "KWD": "쿠웨이트 디나르", "AED": "UAE 디르함",
    "SAR": "사우디 리얄", "KZT": "카자흐스탄 텡게",
    "MXN": "멕시코 페소", "IQD": "이라크 디나르",
    "LBP": "레바논 파운드",
}

# =====================
# 한국수출입은행 API
# =====================
def fetch_exim_by_date(date_str):
    try:
        params = {
            "authkey": API_KEY,
            "searchdate": date_str,
            "data": "AP01"
        }
        res = requests.get(EXIM_URL, params=params, timeout=10, verify=False)
        data = res.json()
        if not isinstance(data, list):
            return {}
        return {item["cur_unit"]: item for item in data}
    except:
        return {}

def get_workday(offset=0):
    date = datetime.now()
    count = 0
    while count > offset:
        date -= timedelta(days=1)
        if date.weekday() < 5:
            count -= 1
    return date.strftime("%Y%m%d")

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
# 서울외국환중개 XML
# =====================
def fetch_smbs_xml(endpoint, currency, start_year, start_month, end_year, end_month):
    arr_value = f"{currency}_{start_year}-{start_month:02d}_{end_year}-{end_month:02d}"
    try:
        res = requests.get(
            f"http://www.smbs.biz/ExRate/{endpoint}?arr_value={arr_value}",
            headers=SMBS_HEADERS,
            timeout=10,
            verify=False
        )
        content = res.content.decode("euc-kr").strip()
        pattern = re.compile(r"<set\s+label='([^']+)'\s+value='([^']+)'")
        data = {}
        for match in pattern.finditer(content):
            label = match.group(1).strip()
            value = match.group(2).strip()
            key = label.replace(".", "")  # "2026.03" → "202603"
            data[key] = value
        return data
    except Exception as e:
        print(f"SMBS XML 오류 ({currency}): {e}")
        return {}

def fetch_smbs_monthly_avg(currency, year, month):
    return fetch_smbs_xml(
        "MonAvgStdExRate_xml.jsp",
        currency,
        year - 1, month,  # 12개월치
        year, month
    )

def fetch_smbs_month_end(currency, year, month):
    return fetch_smbs_xml(
        "MonLastStdExRate_xml.jsp",
        currency,
        year - 1, month,
        year, month
    )

def fetch_smbs_today(currency, date_str):
    """기간별 - 특정일 환율"""
    year = int(date_str[:4])
    month = int(date_str[4:6])
    day = int(date_str[6:8])
    arr_value = f"{currency}_{year}-{month:02d}-{day:02d}_{year}-{month:02d}-{day:02d}"
    try:
        res = requests.get(
            f"http://www.smbs.biz/ExRate/StdExRate_xml.jsp?arr_value={arr_value}",
            headers={**SMBS_HEADERS, "Referer": "http://www.smbs.biz/ExRate/StdExRate.jsp"},
            timeout=10,
            verify=False
        )
        content = res.content.decode("euc-kr").strip()
        pattern = re.compile(r"<set\s+label='([^']+)'\s+value='([^']+)'")
        matches = pattern.findall(content)
        if matches:
            return matches[-1][1]
        return ""
    except Exception as e:
        print(f"SMBS Today 오류 ({currency}): {e}")
        return ""

# =====================
# exchangerates.org.uk
# =====================
def fetch_er_history(currency, year):
    url = f"https://www.exchangerates.org.uk/{currency}-KRW-spot-exchange-rates-history-{year}.html"
    try:
        from bs4 import BeautifulSoup
        res = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, timeout=10, verify=False)
        soup = BeautifulSoup(res.text, "html.parser")
        data = {}
        rows = soup.find_all("tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 2:
                date_text = cols[0].get_text(strip=True)
                rate_text = cols[1].get_text(strip=True)
                try:
                    date_obj = datetime.strptime(date_text, "%d %b %Y")
                    date_key = date_obj.strftime("%Y%m%d")
                    rate_val = re.search(r'[\d.]+', rate_text)
                    if rate_val:
                        data[date_key] = rate_val.group()
                except:
                    continue
        return data
    except Exception as e:
        print(f"ER 오류 ({currency}): {e}")
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
        today_str = get_workday(0)
        yesterday_str = get_workday(-1)
        today_exim = fetch_exim_by_date(today_str)
        yesterday_exim = fetch_exim_by_date(yesterday_str)
        rates = []

        # USD, CNH, KWD, AED, SAR
        for cur in EXIM_TARGET:
            today = today_exim.get(cur)
            yesterday = yesterday_exim.get(cur)
            if not today:
                continue
            change, change_val = calc_change(
                today["deal_bas_r"].replace(",", ""),
                yesterday["deal_bas_r"].replace(",", "") if yesterday else None
            )
            rates.append({
                "currency": cur,
                "name": today["cur_nm"],
                "base": today["deal_bas_r"],
                "buy": today["ttb"],
                "sell": today["tts"],
                "change": change,
                "change_val": change_val,
            })

        # KZT, MXN
        for cur in SMBS_TARGET:
            try:
                today_val = fetch_smbs_today(cur, today_str)
                yesterday_val = fetch_smbs_today(cur, yesterday_str)
                if today_val:
                    change, change_val = calc_change(today_val, yesterday_val, 4)
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
        year = datetime.now().year
        for cur in ER_TARGET:
            try:
                data = fetch_er_history(cur, year)
                today_val = data.get(today_str)
                yesterday_val = data.get(yesterday_str)
                if today_val:
                    change, change_val = calc_change(today_val, yesterday_val, 6)
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

        return {
            "success": True,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/by-date")
def get_rates_by_date(date: str):
    try:
        prev_date = datetime.strptime(date, "%Y%m%d") - timedelta(days=1)
        while prev_date.weekday() >= 5:
            prev_date -= timedelta(days=1)
        prev_str = prev_date.strftime("%Y%m%d")

        today_exim = fetch_exim_by_date(date)
        yesterday_exim = fetch_exim_by_date(prev_str)

        if not today_exim:
            return {"success": False, "error": "데이터 없음 (주말/공휴일)", "data": []}

        rates = []

        for cur in EXIM_TARGET:
            today = today_exim.get(cur)
            yesterday = yesterday_exim.get(cur)
            if not today:
                continue
            change, change_val = calc_change(
                today["deal_bas_r"].replace(",", ""),
                yesterday["deal_bas_r"].replace(",", "") if yesterday else None
            )
            rates.append({
                "currency": cur,
                "name": today["cur_nm"],
                "base": today["deal_bas_r"],
                "buy": today["ttb"],
                "sell": today["tts"],
                "change": change,
                "change_val": change_val,
            })

        for cur in SMBS_TARGET:
            try:
                today_val = fetch_smbs_today(cur, date)
                yesterday_val = fetch_smbs_today(cur, prev_str)
                if today_val:
                    change, change_val = calc_change(today_val, yesterday_val, 4)
                    rates.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": today_val,
                        "buy": "-", "sell": "-",
                        "change": change,
                        "change_val": change_val,
                    })
            except:
                pass

        year = int(date[:4])
        for cur in ER_TARGET:
            try:
                data = fetch_er_history(cur, year)
                today_val = data.get(date)
                yesterday_val = data.get(prev_str)
                if today_val:
                    change, change_val = calc_change(today_val, yesterday_val, 6)
                    rates.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": today_val,
                        "buy": "-", "sell": "-",
                        "change": change,
                        "change_val": change_val,
                    })
            except:
                pass

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
        last_day = calendar.monthrange(year, month)[1]
        totals = {}
        counts = {}
        result = []

        # USD, CNH, KWD, AED, SAR
        for day in range(1, last_day + 1):
            date_obj = datetime(year, month, day)
            if date_obj.weekday() >= 5:
                continue
            date_str = date_obj.strftime("%Y%m%d")
            data = fetch_exim_by_date(date_str)
            for cur in EXIM_TARGET:
                if cur in data:
                    val = float(data[cur]["deal_bas_r"].replace(",", ""))
                    totals[cur] = totals.get(cur, 0) + val
                    counts[cur] = counts.get(cur, 0) + 1

        for cur in EXIM_TARGET:
            if cur in totals and counts[cur] > 0:
                avg = totals[cur] / counts[cur]
                result.append({
                    "currency": cur,
                    "name": CUR_NAMES[cur],
                    "base": f"{avg:,.2f}",
                    "buy": "-", "sell": "-",
                })

        # KZT, MXN
        for cur in SMBS_TARGET:
            try:
                data = fetch_smbs_monthly_avg(cur, year, month)
                target_key = f"{year}{month:02d}"

                # 해당 월 없으면 가장 최근 데이터 사용
                if target_key not in data and data:
                    target_key = sorted(data.keys())[-1]

                if target_key in data:
                    result.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": data[target_key],
                        "buy": "-", "sell": "-",
                    })
            except Exception as e:
                print(f"{cur} 월평균 오류: {e}")

        # IQD, LBP
        for cur in ER_TARGET:
            try:
                data = fetch_er_history(cur, year)
                vals = []
                for day in range(1, last_day + 1):
                    date_obj = datetime(year, month, day)
                    if date_obj.weekday() >= 5:
                        continue
                    date_key = date_obj.strftime("%Y%m%d")
                    if date_key in data:
                        vals.append(float(data[date_key]))
                if vals:
                    avg = sum(vals) / len(vals)
                    result.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": f"{avg:.6f}",
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
        last_day = calendar.monthrange(year, month)[1]
        result = []

        # USD, CNH, KWD, AED, SAR
        for day in range(last_day, 0, -1):
            date_obj = datetime(year, month, day)
            if date_obj.weekday() >= 5:
                continue
            date_str = date_obj.strftime("%Y%m%d")
            data = fetch_exim_by_date(date_str)
            if data:
                for cur in EXIM_TARGET:
                    if cur in data:
                        result.append({
                            "currency": cur,
                            "name": data[cur]["cur_nm"],
                            "base": data[cur]["deal_bas_r"],
                            "buy": data[cur]["ttb"],
                            "sell": data[cur]["tts"],
                        })
                break

        # KZT, MXN
        for cur in SMBS_TARGET:
            try:
                data = fetch_smbs_month_end(cur, year, month)
                target_key = f"{year}{month:02d}"

                # 해당 월 없으면 가장 최근 데이터 사용
                if target_key not in data and data:
                    target_key = sorted(data.keys())[-1]

                if target_key in data:
                    result.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": data[target_key],
                        "buy": "-", "sell": "-",
                    })
            except Exception as e:
                print(f"{cur} 월말 오류: {e}")

        # IQD, LBP
        for cur in ER_TARGET:
            try:
                data = fetch_er_history(cur, year)
                for day in range(last_day, 0, -1):
                    date_obj = datetime(year, month, day)
                    if date_obj.weekday() >= 5:
                        continue
                    date_key = date_obj.strftime("%Y%m%d")
                    if date_key in data:
                        result.append({
                            "currency": cur,
                            "name": CUR_NAMES[cur],
                            "base": data[date_key],
                            "buy": "-", "sell": "-",
                        })
                        break
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

@app.get("/debug/smbs")
def debug_smbs():
    try:
        arr_value = "MXN_2025-10_2026-04"
        res = requests.get(
            f"http://www.smbs.biz/ExRate/MonAvgStdExRate_xml.jsp?arr_value={arr_value}",
            headers=SMBS_HEADERS,
            timeout=10,
            verify=False
        )
        content = res.content.decode("euc-kr").strip()
        pattern = re.compile(r"<set\s+label='([^']+)'\s+value='([^']+)'")
        matches = pattern.findall(content)
        return {
            "status_code": res.status_code,
            "content_preview": content[:500],
            "matches": matches
        }
    except Exception as e:
        return {"error": str(e)}
