from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from datetime import datetime
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.environ.get("KEY")  # ✅ 환경변수에서 안전하게 불러오기
API_URL = "https://www.koreaexim.go.kr/site/program/financial/exchangeJSON"

def fetch_rates():
    params = {
        "authkey": API_KEY,
        "searchdate": datetime.now().strftime("%Y%m%d"),
        "data": "AP01"
    }
    res = requests.get(API_URL, params=params, timeout=10)
    return res.json()

@app.get("/")
def root():
    return {"status": "ok", "message": "환율 API 서버 작동중"}

@app.get("/rates")
def get_rates():
    try:
        data = fetch_rates()
        rates = []
        for item in data:
            rates.append({
                "currency": item.get("cur_unit", ""),
                "name": item.get("cur_nm", ""),
                "buy": item.get("ttb", "-"),   # 전신환 매입
                "sell": item.get("tts", "-"),  # 전신환 매도
                "base": item.get("deal_bas_r", "-"),  # 매매기준율
                "change": item.get("change", ""),
            })
        return {
            "success": True,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/rates/main")
def get_main_rates():
    target = ["USD", "CNY", "KWD", "KZT", "AED", "IQD", "LBP", "MXN"]
    try:
        data = fetch_rates()
        rates = []
        for item in data:
            cur = item.get("cur_unit", "")
            if cur in target:
                rates.append({
                    "currency": cur,
                    "name": item.get("cur_nm", ""),
                    "buy": item.get("ttb", "-"),
                    "sell": item.get("tts", "-"),
                    "base": item.get("deal_bas_r", "-"),
                })
        return {
            "success": True,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}
