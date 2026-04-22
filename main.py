from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import xml.etree.ElementTree as ET
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
    params = {
        "authkey": API_KEY,
        "searchdate": date_str,
        "data": "AP01"
    }
    try:
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

# =====================
# 서울외국환중개 XML API
# =====================
def fetch_smbs_xml(url, currency, start_year, start_month, end_year, end_month):
    arr_value = f"{currency}_{start_year}-{start_month:02d}_{end_year}-{end_month:02d}"
    try:
        res = requests.get(
            f"http://www.smbs.biz/ExRate/{url}?arr_value={arr_value}",
            headers=SMBS_HEADERS,
            timeout=10,
            verify=False
        )
        content = res.content.decode("euc-kr").strip()

        # ✅ ET 대신 정규식으로 파싱
        import re
        data = {}
        pattern = re.compile(r"<set\s+label='([^']+)'\s+value='([^']+)'")
        for match in pattern.finditer(content):
            label = match.group(1).strip()   # "2026.03"
            value = match.group(2).strip()   # "83.61"
            key = label.replace(".", "")     # "202603"
            data[key] = value

        return data
    except Exception as e:
        print(f"SMBS XML 오류: {e}")
        return {}

def fetch_smbs_monthly_avg(currency, year, month):
    """월평균 - 12개월치 요청"""
    start_year = year - 1
    start_month = month
    return fetch_smbs_xml(
        "MonAvgStdExRate_xml.jsp",
        currency, start_year, start_month, year, month
    )

def fetch_smbs_month_end(currency, year, month):
    """월말"""
    start_year = year if month > 6 else year - 1
    start_month = month - 6 if month > 6 else month + 6
    return fetch_smbs_xml(
        "MonLastStdExRate_xml.jsp",
        currency, start_year, start_month, year, month
    )

def fetch_smbs_today(currency, date_str):
    """기간별 - 오늘 데이터"""
    year = int(date_str[:4])
    month = int(date_str[4:6])
    day = int(date_str[6:8])
    date_key = f"{year}{month:02d}"
    
    # 당월 1일부터 오늘까지
    arr_value = f"{currency}_{year}-{month:02d}-{day:02d}_{year}-{month:02d}-{day:02d}"
    try:
        res = requests.get(
            f"http://www.smbs.biz/ExRate/StdExRate_xml.jsp?arr_value={arr_value}",
            headers=SMBS_HEADERS,
            timeout=10,
            verify=False
        )
        root = ET.fromstring(res.content.decode("euc-kr"))
        sets = root.findall("set")
        if sets:
            return sets[-1].get("value", "")
    except Exception as e:
        print(f"SMBS Today 오류: {e}")
    return ""

# =====================
# exchangerates.org.uk
# =====================
def fetch_er_history(currency, year):
    """연도별 환율 히스토리"""
    url = f"https://www.exchangerates.org.uk/{currency}-KRW-spot-exchange-rates-history-{year}.html"
    try:
        res = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, timeout=10, verify=False)
        
        from bs4 import BeautifulSoup
        import re
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
        print(f"ER 오류: {e}")
        return {}

# =====================
# 전일대비 계산
# =====================
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

        # KZT, MXN - 서울외국환중개
        for cur in SMBS_TARGET:
            try:
                data = fetch_smbs_monthly_avg(cur, year, month)
                key = f"{year}{month:02d}"
        
        # ✅ 해당 월 없으면 가장 최근 데이터 사용
            if key not in data and data:
                key = sorted(data.keys())[-1]
        
            if key in data:
                result.append({
                    "currency": cur,
                    "name": CUR_NAMES[cur],
                    "base": data[key],
                    "buy": "-", "sell": "-",
            })
        except Exception as e:
            print(f"{cur} 월평균 오류: {e}")

        # IQD, LBP - exchangerates.org.uk
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

        # KZT, MXN
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

        # IQD, LBP
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
        result = []

        # USD, CNH, KWD, AED, SAR - 수출입은행 날짜별 평균
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        totals = {}
        counts = {}

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

        # KZT, MXN - 서울외국환중개 월평균 XML
        for cur in SMBS_TARGET:
            try:
                data = fetch_smbs_monthly_avg(cur, year, month)
                key = f"{year}{month:02d}"
                if key in data:
                    result.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": data[key],
                        "buy": "-", "sell": "-",
                    })
            except Exception as e:
                print(f"{cur} 월평균 오류: {e}")

        # IQD, LBP - exchangerates.org.uk 평균 계산
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
        result = []
        import calendar
        last_day = calendar.monthrange(year, month)[1]

        # USD, CNH, KWD, AED, SAR - 수출입은행 월말
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

        # KZT, MXN - 서울외국환중개 월말 XML
        for cur in SMBS_TARGET:
            try:
                data = fetch_smbs_month_end(cur, year, month)
                key = f"{year}{month:02d}"
                if key in data:
                    result.append({
                        "currency": cur,
                        "name": CUR_NAMES[cur],
                        "base": data[key],
                        "buy": "-", "sell": "-",
                    })
            except Exception as e:
                print(f"{cur} 월말 오류: {e}")

        # IQD, LBP - exchangerates.org.uk 월말
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
        
        import re
        pattern = re.compile(r"<set\s+label='([^']+)'\s+value='([^']+)'")
        matches = pattern.findall(content)
        
        return {
            "status_code": res.status_code,
            "content_preview": content[:500],
            "matches": matches
        }
    except Exception as e:
        return {"error": str(e)}
