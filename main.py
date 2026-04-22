from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re

app = FastAPI()

# ✅ Framer에서 접근 가능하도록 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def crawl_exchange_rates():
    url = "https://www.smbs.biz/ExchangeRate/StandardExchangeRate.jsp"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    res = requests.get(url, headers=headers, timeout=10)
    res.encoding = "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")
    
    rates = []
    table = soup.find("table", {"class": re.compile("table")})
    
    if table:
        rows = table.find_all("tr")[1:]  # 헤더 제외
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 4:
                try:
                    rates.append({
                        "currency": cols[0].get_text(strip=True),
                        "name": cols[1].get_text(strip=True),
                        "buy": cols[2].get_text(strip=True),   # 전신환 매입
                        "sell": cols[3].get_text(strip=True),  # 전신환 매도
                        "base": cols[4].get_text(strip=True) if len(cols) > 4 else "-",  # 매매기준율
                    })
                except:
                    continue
    
    return rates

@app.get("/")
def root():
    return {"status": "ok", "message": "환율 API 서버 작동중"}

@app.get("/rates")
def get_rates():
    try:
        rates = crawl_exchange_rates()
        return {
            "success": True,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": rates
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "data": []
        }

# 주요 법인 통화만 필터링
@app.get("/rates/main")
def get_main_rates():
    target = ["USD", "CNY", "KWD", "KZT", "AED", "IQD", "LBP", "MXN"]
    try:
        rates = crawl_exchange_rates()
        filtered = [r for r in rates if r["currency"] in target]
        return {
            "success": True,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            "data": filtered
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}
