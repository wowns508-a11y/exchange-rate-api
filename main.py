from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import re
from datetime import datetime, timedelta
import urllib3
import os
import bcrypt
import time
from supabase import create_client

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

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =====================
# 캐시
# =====================
_pnl_cache:   dict = {}
_rates_cache: dict = {}
PNL_CACHE_TTL   = 1800  # 30분
RATES_CACHE_TTL = 1800  # 30분

def get_cached_records() -> list:
    now = time.time()
    if "records" in _pnl_cache and now - _pnl_cache["timestamp"] < PNL_CACHE_TTL:
        print(f"[PNL CACHE HIT] {len(_pnl_cache['records'])}건")
        return _pnl_cache["records"]
    print("[PNL CACHE MISS] Supabase 재조회")
    records = fetch_supabase_all()
    _pnl_cache["records"]   = records
    _pnl_cache["timestamp"] = now
    print(f"[PNL CACHE SET] {len(records)}건")
    return records

def get_cached_rates() -> dict | None:
    now = time.time()
    if "data" in _rates_cache and now - _rates_cache["timestamp"] < RATES_CACHE_TTL:
        print("[RATES CACHE HIT]")
        return _rates_cache["data"]
    return None

def set_rates_cache(data: dict):
    _rates_cache["data"]      = data
    _rates_cache["timestamp"] = time.time()
    print("[RATES CACHE SET]")

# =====================
# Supabase PnL 조회 (Airtable 대체)
# =====================
def fetch_supabase_all() -> list:
    """
    pnl_monthly + branches 조인해서 Framer가 기대하는 한국어 필드명으로 변환
    """
    all_records = []
    page_size   = 1000
    offset      = 0

    while True:
        res = supabase.table("pnl_monthly").select(
            "year, month, revenue, material_cost, labor_cost, "
            "expenses, hq_allocated_cost, gross_profit, operating_profit, "
            "branches(branch_name, entity_name)"
        ).range(offset, offset + page_size - 1).execute()

        rows = res.data
        if not rows:
            break

        for row in rows:
            branch  = row.get("branches") or {}
            revenue = float(row.get("revenue", 0) or 0)
            mat     = float(row.get("material_cost", 0) or 0)
            labor   = float(row.get("labor_cost", 0) or 0)
            exp     = float(row.get("expenses", 0) or 0)
            corp    = float(row.get("hq_allocated_cost", 0) or 0)
            gross   = float(row.get("gross_profit", 0) or 0)
            op      = float(row.get("operating_profit", 0) or 0)
            m       = revenue if revenue != 0 else 1  # 분모 0 방지

            year  = int(row.get("year", 0) or 0)
            month = int(row.get("month", 0) or 0)

            all_records.append({
                "연도":      year,
                "월":        month,
                "연월":      f"{year}{month:02d}",
                "지역":      branch.get("entity_name", ""),
                "영업점":    branch.get("branch_name", ""),
                "매출":          revenue,
                "재료비":        mat,
                "재료비율":      mat / m,
                "인건비":        labor,
                "인건비율":      labor / m,
                "경비":          exp,
                "경비율":        exp / m,
                "매출총이익":    gross,
                "매출총이익율":  gross / m,
                "법인비용":      corp,
                "영업이익":      op,
                "영업이익율":    op / m,
            })

        if len(rows) < page_size:
            break
        offset += page_size

    return all_records


# =====================
# 서울외국환중개 XML
# =====================
def fetch_smbs_xml(endpoint, currency, start, end, referer=None):
    arr_value = f"{currency}_{start}_{end}"
    headers = {**SMBS_HEADERS}
    if referer:
        headers["Referer"] = referer
    try:
        res = requests.get(
            f"http://www.smbs.biz/ExRate/{endpoint}?arr_value={arr_value}",
            headers=headers, timeout=10, verify=False
        )
        content = res.content.decode("euc-kr").strip()
        pattern = re.compile(r"<set[^>]+label='([^']+)'[^>]+value='([^']+)'")
        data = {}
        for match in pattern.finditer(content):
            label = match.group(1).strip()
            value = match.group(2).strip()
            if len(label.split(".")[0]) == 2:
                parts = label.split(".")
                key = f"20{parts[0]}{parts[1]}{parts[2]}"
            else:
                key = label.replace(".", "")
            data[key] = value
        return data
    except Exception as e:
        print(f"SMBS XML 오류 ({currency}): {e}")
        return {}

def to_dash(date_str):
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

def get_latest_date():
    date = datetime.now()
    for _ in range(10):
        date_str  = date.strftime("%Y%m%d")
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
    formatted = to_dash(date_str)
    data = fetch_smbs_xml(
        "StdExRate_xml.jsp", currency, formatted, formatted,
        "http://www.smbs.biz/ExRate/StdExRate.jsp"
    )
    if date_str in data:
        return data[date_str]
    if data:
        return list(data.values())[-1]
    return ""

def fetch_smbs_monthly_avg(currency, year, month):
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

def fetch_er_open():
    try:
        res  = requests.get("https://open.er-api.com/v6/latest/KRW", timeout=10, verify=False)
        data = res.json().get("rates", {})
        result = {}
        for cur in ["IQD", "LBP"]:
            if cur in data and data[cur] != 0:
                result[cur] = str(round(1 / data[cur], 4))
        return result
    except Exception as e:
        print(f"ER Open 오류: {e}")
        return {}


# =====================
# 기본
# =====================
@app.get("/")
def root():
    return {"status": "ok", "message": "환율 API 서버 작동중"}

# =====================
# 환율
# =====================
@app.get("/rates")
def get_rates():
    try:
        cached = get_cached_rates()
        if cached:
            return cached

        today_str = get_latest_date()
        prev_date = datetime.strptime(today_str, "%Y%m%d") - timedelta(days=1)
        yesterday_str = ""
        for _ in range(10):
            ps = prev_date.strftime("%Y%m%d")
            if fetch_smbs_today("USD", ps):
                yesterday_str = ps
                break
            prev_date -= timedelta(days=1)

        rates = []
        for cur in ALL_TARGET:
            try:
                today_val     = fetch_smbs_today(cur, today_str)
                yesterday_val = fetch_smbs_today(cur, yesterday_str) if yesterday_str else ""
                if today_val:
                    decimal = 4 if cur == "KZT" else 2
                    change, change_val = calc_change(today_val, yesterday_val, decimal)
                    rates.append({
                        "currency": cur, "name": CUR_NAMES[cur],
                        "base": today_val, "buy": "-", "sell": "-",
                        "change": change, "change_val": change_val,
                    })
            except Exception as e:
                print(f"{cur} 오류: {e}")

        try:
            er_data = fetch_er_open()
            for cur in ["IQD", "LBP"]:
                if cur in er_data:
                    rates.append({
                        "currency": cur, "name": CUR_NAMES[cur],
                        "base": er_data[cur], "buy": "-", "sell": "-",
                        "change": "", "change_val": "",
                    })
        except Exception as e:
            print(f"IQD/LBP 오류: {e}")

        result = {
            "success": True,
            "date": today_str,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
        set_rates_cache(result)
        return result

    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/by-date")
def get_rates_by_date(date: str):
    try:
        prev_date = datetime.strptime(date, "%Y%m%d") - timedelta(days=1)
        prev_str  = ""
        for _ in range(10):
            ps = prev_date.strftime("%Y%m%d")
            if fetch_smbs_today("USD", ps):
                prev_str = ps
                break
            prev_date -= timedelta(days=1)

        rates = []
        for cur in ALL_TARGET:
            try:
                today_val     = fetch_smbs_today(cur, date)
                yesterday_val = fetch_smbs_today(cur, prev_str) if prev_str else ""
                if today_val:
                    decimal = 4 if cur == "KZT" else 2
                    change, change_val = calc_change(today_val, yesterday_val, decimal)
                    rates.append({
                        "currency": cur, "name": CUR_NAMES[cur],
                        "base": today_val, "buy": "-", "sell": "-",
                        "change": change, "change_val": change_val,
                    })
            except Exception as e:
                print(f"{cur} 오류: {e}")

        try:
            er_data = fetch_er_open()
            for cur in ["IQD", "LBP"]:
                if cur in er_data:
                    rates.append({
                        "currency": cur, "name": CUR_NAMES[cur],
                        "base": er_data[cur], "buy": "-", "sell": "-",
                        "change": "", "change_val": "",
                    })
        except:
            pass

        if not rates:
            return {"success": False, "error": "데이터 없음 (주말/공휴일)", "data": []}

        return {
            "success": True, "date": date,
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
                    result.append({"currency": cur, "name": CUR_NAMES[cur],
                                   "base": val, "buy": "-", "sell": "-"})
            except Exception as e:
                print(f"{cur} 월평균 오류: {e}")
        return {"success": True, "year": year, "month": month, "data": result}
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
                    result.append({"currency": cur, "name": CUR_NAMES[cur],
                                   "base": val, "buy": "-", "sell": "-"})
            except Exception as e:
                print(f"{cur} 월말 오류: {e}")
        return {"success": True, "year": year, "month": month, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/weekly")
def get_weekly(currency: str = "USD"):
    try:
        result = []
        date   = datetime.now()
        count  = 0
        while count < 15:
            date_str = date.strftime("%Y%m%d")
            val = fetch_smbs_today(currency, date_str)
            if val:
                result.append({
                    "date": f"{date.month}/{date.day}",
                    "value": float(val.replace(",", "")),
                    "full_date": date_str,
                })
                count += 1
            date -= timedelta(days=1)
        result.reverse()
        return {"success": True, "currency": currency, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/cache/status")
def rates_cache_status():
    if "timestamp" not in _rates_cache:
        return {"cached": False}
    age = int(time.time() - _rates_cache["timestamp"])
    return {"cached": True, "age_seconds": age, "expires_in": max(0, RATES_CACHE_TTL - age)}

@app.post("/rates/cache/clear")
def clear_rates_cache():
    _rates_cache.clear()
    return {"success": True, "message": "환율 캐시 초기화 완료"}

@app.get("/debug/today")
def debug_today():
    try:
        today_str = get_latest_date()
        result    = {cur: fetch_smbs_today(cur, today_str) for cur in ALL_TARGET}
        return {"today": today_str, "formatted": to_dash(today_str), "result": result}
    except Exception as e:
        return {"error": str(e)}

# =====================
# 인증
# =====================
@app.post("/auth/register")
def register(data: dict):
    try:
        email       = data.get("email", "").strip()
        name        = data.get("name", "").strip()
        employee_id = data.get("employee_id", "").strip()
        password    = data.get("password", "").strip()

        if not all([email, name, employee_id, password]):
            return {"success": False, "error": "모든 항목을 입력해주세요."}
        if supabase.table("users").select("id").eq("employee_id", employee_id).execute().data:
            return {"success": False, "error": "이미 등록된 사번입니다."}
        if supabase.table("users").select("id").eq("email", email).execute().data:
            return {"success": False, "error": "이미 등록된 이메일입니다."}

        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        supabase.table("users").insert({
            "email": email, "name": name,
            "employee_id": employee_id, "password": hashed, "approved": False,
        }).execute()
        return {"success": True, "message": "계정 신청이 완료됐습니다. 관리자 승인 후 로그인 가능합니다."}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/auth/login")
def login(data: dict):
    try:
        employee_id = data.get("employee_id", "").strip()
        password    = data.get("password", "").strip()

        if not employee_id or not password:
            return {"success": False, "error": "사번과 비밀번호를 입력해주세요."}

        result = supabase.table("users").select("*").eq("employee_id", employee_id).execute()
        if not result.data:
            return {"success": False, "error": "존재하지 않는 사번입니다."}

        user = result.data[0]
        if not user.get("approved"):
            return {"success": False, "error": "관리자 승인 대기 중입니다."}
        if not bcrypt.checkpw(password.encode("utf-8"), user["password"].encode("utf-8")):
            return {"success": False, "error": "비밀번호가 올바르지 않습니다."}

        return {
            "success": True,
            "user": {
                "employee_id": user["employee_id"],
                "name": user["name"],
                "email": user["email"],
                "role": user.get("role", "user"),
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/auth/pending")
def get_pending():
    try:
        result = supabase.table("users").select("*").eq("approved", False).execute()
        return {"success": True, "data": result.data}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/auth/approve")
def approve_user(data: dict):
    try:
        employee_id = data.get("employee_id")
        supabase.table("users").update({"approved": True}).eq("employee_id", employee_id).execute()
        return {"success": True, "message": f"{employee_id} 승인 완료"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/auth/reject")
def reject_user(data: dict):
    try:
        employee_id = data.get("employee_id")
        supabase.table("users").delete().eq("employee_id", employee_id).execute()
        return {"success": True, "message": f"{employee_id} 거절 완료"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# =====================
# PnL 캐시 관리
# =====================
@app.get("/pnl/cache/status")
def pnl_cache_status():
    if "timestamp" not in _pnl_cache:
        return {"cached": False}
    age = int(time.time() - _pnl_cache["timestamp"])
    return {
        "cached": True,
        "records": len(_pnl_cache.get("records", [])),
        "age_seconds": age,
        "expires_in": max(0, PNL_CACHE_TTL - age)
    }

@app.post("/pnl/cache/clear")
def clear_pnl_cache():
    _pnl_cache.clear()
    return {"success": True, "message": "PnL 캐시 초기화 완료"}

# =====================
# PnL 엔드포인트 (Framer가 호출하는 API — 필드명 동일 유지)
# =====================
@app.get("/pnl/raw")
def get_raw_data(year: int = None, month: int = None, region: str = None, store: str = None):
    try:
        records = get_cached_records()
        if year:    records = [r for r in records if r["연도"] == year]
        if month:   records = [r for r in records if r["월"] == month]
        if region:  records = [r for r in records if r["지역"] == region]
        if store:   records = [r for r in records if store in r["영업점"]]
        return {"success": True, "count": len(records), "data": records}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/pnl/monthly")
def get_monthly(year: int, month: int):
    try:
        records = get_cached_records()
        records = [r for r in records if r["연도"] == year and r["월"] == month]

        region_summary = {}
        for r in records:
            region = r["지역"]
            if region not in region_summary:
                region_summary[region] = {
                    "지역": region, "매출": 0, "재료비": 0, "인건비": 0,
                    "경비": 0, "매출총이익": 0, "법인비용": 0,
                    "영업이익": 0, "영업점_수": 0, "영업점목록": []
                }
            s = region_summary[region]
            for key in ["매출","재료비","인건비","경비","매출총이익","법인비용","영업이익"]:
                s[key] += r[key]
            s["영업점_수"] += 1
            s["영업점목록"].append(r)

        for region, s in region_summary.items():
            m = s["매출"]
            if m > 0:
                s["재료비율"]    = f"{s['재료비']/m*100:.2f}%"
                s["인건비율"]    = f"{s['인건비']/m*100:.2f}%"
                s["경비율"]      = f"{s['경비']/m*100:.2f}%"
                s["매출총이익율"] = f"{s['매출총이익']/m*100:.2f}%"
                s["영업이익율"]  = f"{s['영업이익']/m*100:.2f}%"
            else:
                s["재료비율"] = s["인건비율"] = s["경비율"] = s["매출총이익율"] = s["영업이익율"] = "0.00%"

        return {"success": True, "year": year, "month": month, "data": list(region_summary.values())}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/pnl/cumulative")
def get_cumulative(year: int, start_month: int = 1, end_month: int = 12):
    try:
        records = get_cached_records()
        records = [r for r in records if r["연도"]==year and start_month<=r["월"]<=end_month]

        total  = {"매출":0,"재료비":0,"인건비":0,"경비":0,"매출총이익":0,"법인비용":0,"영업이익":0}
        region_summary = {}

        for r in records:
            for key in total: total[key] += r[key]
            region = r["지역"]
            if region not in region_summary:
                region_summary[region] = {
                    "지역": region, "매출":0,"재료비":0,"인건비":0,
                    "경비":0,"매출총이익":0,"법인비용":0,"영업이익":0
                }
            for key in ["매출","재료비","인건비","경비","매출총이익","법인비용","영업이익"]:
                region_summary[region][key] += r[key]

        def add_rates(s):
            m = s["매출"]
            if m > 0:
                s["재료비율"]    = f"{s['재료비']/m*100:.2f}%"
                s["인건비율"]    = f"{s['인건비']/m*100:.2f}%"
                s["경비율"]      = f"{s['경비']/m*100:.2f}%"
                s["매출총이익율"] = f"{s['매출총이익']/m*100:.2f}%"
                s["영업이익율"]  = f"{s['영업이익']/m*100:.2f}%"
            else:
                s["재료비율"] = s["인건비율"] = s["경비율"] = s["매출총이익율"] = s["영업이익율"] = "0.00%"
            return s

        add_rates(total)
        for region in region_summary: add_rates(region_summary[region])

        return {
            "success": True, "year": year,
            "start_month": start_month, "end_month": end_month,
            "total": total, "by_region": list(region_summary.values())
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/pnl/regions")
def get_regions():
    try:
        records = get_cached_records()
        regions = sorted(list(set(r["지역"] for r in records if r["지역"])))
        return {"success": True, "data": regions}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/pnl/stores")
def get_stores(region: str = None):
    try:
        records = get_cached_records()
        if region:
            records = [r for r in records if r["지역"] == region]
        stores = sorted(list(set(r["영업점"] for r in records if r["영업점"])))
        return {"success": True, "data": stores}
    except Exception as e:
        return {"success": False, "error": str(e)}
