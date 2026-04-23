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

        # ✅ color 속성 있는 경우도 처리
        pattern = re.compile(r"<set[^>]+label='([^']+)'[^>]+value='([^']+)'")
        data = {}
        for match in pattern.finditer(content):
            label = match.group(1).strip()   # "26.04.23" 또는 "2026.03"
            value = match.group(2).strip()

            # ✅ YY.MM.DD → YYYYMMDD
            if len(label.split(".")[0]) == 2:
                parts = label.split(".")
                key = f"20{parts[0]}{parts[1]}{parts[2]}"
            # ✅ YYYY.MM → YYYYMM
            else:
                key = label.replace(".", "")

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
        

@app.get("/rates/weekly")
def get_weekly(currency: str = "USD"):
    """최근 15영업일 환율 데이터"""
    try:
        result = []
        date = datetime.now()
        count = 0

        while count < 15:  # ✅ 7 → 15
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
        return {
            "success": True,
            "currency": currency,
            "data": result
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

import bcrypt
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =====================
# 계정 생성
# =====================
@app.post("/auth/register")
def register(data: dict):
    try:
        email = data.get("email", "").strip()
        name = data.get("name", "").strip()
        employee_id = data.get("employee_id", "").strip()
        password = data.get("password", "").strip()

        # 필수값 확인
        if not all([email, name, employee_id, password]):
            return {"success": False, "error": "모든 항목을 입력해주세요."}

        # 사번 중복 확인
        existing = supabase.table("users").select("id").eq("employee_id", employee_id).execute()
        if existing.data:
            return {"success": False, "error": "이미 등록된 사번입니다."}

        # 이메일 중복 확인
        existing_email = supabase.table("users").select("id").eq("email", email).execute()
        if existing_email.data:
            return {"success": False, "error": "이미 등록된 이메일입니다."}

        # 비밀번호 암호화
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # DB 저장
        supabase.table("users").insert({
            "email": email,
            "name": name,
            "employee_id": employee_id,
            "password": hashed,
            "approved": False,
        }).execute()

        return {"success": True, "message": "계정 신청이 완료됐습니다. 관리자 승인 후 로그인 가능합니다."}

    except Exception as e:
        return {"success": False, "error": str(e)}

# =====================
# 로그인
# =====================
@app.post("/auth/login")
def login(data: dict):
    try:
        employee_id = data.get("employee_id", "").strip()
        password = data.get("password", "").strip()

        if not employee_id or not password:
            return {"success": False, "error": "사번과 비밀번호를 입력해주세요."}

        # 사용자 조회
        result = supabase.table("users").select("*").eq("employee_id", employee_id).execute()

        if not result.data:
            return {"success": False, "error": "존재하지 않는 사번입니다."}

        user = result.data[0]

        # 승인 확인
        if not user.get("approved"):
            return {"success": False, "error": "관리자 승인 대기 중입니다."}

        # 비밀번호 확인
        if not bcrypt.checkpw(password.encode("utf-8"), user["password"].encode("utf-8")):
            return {"success": False, "error": "비밀번호가 올바르지 않습니다."}

        return {
            "success": True,
            "user": {
                "employee_id": user["employee_id"],
                "name": user["name"],
                "email": user["email"],
            }
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

# =====================
# 관리자: 승인 대기 목록
# =====================
@app.get("/auth/pending")
def get_pending():
    try:
        result = supabase.table("users").select("*").eq("approved", False).execute()
        return {"success": True, "data": result.data}
    except Exception as e:
        return {"success": False, "error": str(e)}

# =====================
# 관리자: 계정 승인
# =====================
@app.post("/auth/approve")
def approve_user(data: dict):
    try:
        employee_id = data.get("employee_id")
        supabase.table("users").update({"approved": True}).eq("employee_id", employee_id).execute()
        return {"success": True, "message": f"{employee_id} 승인 완료"}
    except Exception as e:
        return {"success": False, "error": str(e)}
