from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import ssl
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

urllib3.disable_warnings()

class OldSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)

app = FastAPI()

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

    session = requests.Session()
    session.mount("https://", OldSSLAdapter())
    res = session.get(url, headers=headers, timeout=10, verify=False)
    res.encoding = "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")

    rates = []

    # ✅ 텍스트에서 직접 파싱
    # 페이지의 모든 텍스트 노드에서 통화코드 + 숫자 패턴 찾기
    import re

    # 방법 1: td 태그 전체 탐색
    all_tds = soup.find_all("td")
    for td in all_tds:
        text = td.get_text(strip=True)
        # "미국 USD 1,470.80" 같은 패턴 찾기
        match = re.search(r'([A-Z]{3})\s*([\d,]+\.?\d*)', text)
        if match:
            currency = match.group(1)
            value = match.group(2)
            rates.append({
                "currency": currency,
                "base": value,
                "buy": "-",
                "sell": "-",
                "name": text.replace(currency, "").replace(value, "").strip()
            })

    # 방법 2: td가 없으면 span/div 탐색
    if not rates:
        all_text = soup.get_text()
        pattern = re.finditer(r'([가-힣\s]+)\s([A-Z]{3})\s*([\d,]+\.?\d*)', all_text)
        for match in pattern:
            rates.append({
                "currency": match.group(2),
                "name": match.group(1).strip(),
                "base": match.group(3),
                "buy": "-",
                "sell": "-",
            })

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

@app.get("/debug")
def debug():
    url = "https://www.smbs.biz/ExchangeRate/StandardExchangeRate.jsp"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    session = requests.Session()
    session.mount("https://", OldSSLAdapter())
    res = session.get(url, headers=headers, timeout=10, verify=False)
    res.encoding = "utf-8"
    return {
        "html_preview": res.text[:3000]
    }
