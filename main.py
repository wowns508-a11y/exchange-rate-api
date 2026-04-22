from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import ssl
import urllib3

urllib3.disable_warnings()

# ✅ 구형 SSL 허용하는 어댑터
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

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
    table = soup.find("table")

    if table:
        rows = table.find_all("tr")[1:]
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 4:
                try:
                    rates.append({
                        "currency": cols[0].get_text(strip=True),
                        "name": cols[1].get_text(strip=True),
                        "buy": cols[2].get_text(strip=True),
                        "sell": cols[3].get_text(strip=True),
                        "base": cols[4].get_text(strip=True) if len(cols) > 4 else "-",
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
