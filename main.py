from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
import requests
from datetime import datetime, timedelta
import urllib3
import asyncio
import re
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
CRAWLER_TARGET = ["KZT", "MXN", "IQD", "LBP"]
ALL_TARGET = EXIM_TARGET + CRAWLER_TARGET

# =====================
# 한국수출입은행 API
# =====================
def fetch_exim_by_date(date_str):
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

def get_workday(offset=0):
    date = datetime.now()
    count = 0
    while count > offset:
        date -= timedelta(days=1)
        if date.weekday() < 5:
            count -= 1
    return date.strftime("%Y%m%d")

# =====================
# Playwright 브라우저
# =====================
async def get_page():
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )
    page = await browser.new_page(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    return playwright, browser, page

# =====================
# 서울외국환중개 - KZT, MXN
# =====================
async def crawl_smbs(date_str=None):
    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")

    url = "https://www.smbs.biz/ExchangeRate/StandardExchangeRate.jsp"
    playwright, browser, page = await get_page()

    try:
        await page.goto(url, timeout=30000)
        await page.wait_for_load_state("networkidle")

        if date_str != datetime.now().strftime("%Y%m%d"):
            try:
                await page.fill("input[name='searchDate']", date_str)
                await page.click("text=조회하기")
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)
            except:
                pass

        content = await page.content()
    finally:
        await browser.close()
        await playwright.stop()

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content, "html.parser")
    text = soup.get_text()

    results = {}
    for cur in ["KZT", "MXN"]:
        pattern = re.compile(rf'{cur}\s*([\d,]+\.?\d*)')
        match = pattern.search(text)
        if match:
            results[cur] = match.group(1)

    return results

# =====================
# exchangerates.org.uk - IQD, LBP
# =====================
async def crawl_exchangerates(currency: str, year: int):
    url = f"https://www.exchangerates.org.uk/{currency}-KRW-spot-exchange-rates-history-{year}.html"
    playwright, browser, page = await get_page()

    try:
        await page.goto(url, timeout=30000)
        await page.wait_for_load_state("networkidle")
        content = await page.content()
    finally:
        await browser.close()
        await playwright.stop()

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content, "html.parser")

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

# =====================
# API 엔드포인트
# =====================
@app.get("/")
def root():
    return {"status": "ok", "message": "환율 API 서버 작동중"}

@app.get("/rates")
async def get_rates():
    try:
        today_str = get_workday(0)
        yesterday_str = get_workday(-1)

        # 수출입은행 데이터
        today_exim = fetch_exim_by_date(today_str)
        yesterday_exim = fetch_exim_by_date(yesterday_str)

        rates = []

        # USD, CNH, KWD, AED, SAR
        for cur in EXIM_TARGET:
            today = today_exim.get(cur)
            yesterday = yesterday_exim.get(cur)
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

        # KZT, MXN - 서울외국환중개
        cur_names = {"KZT": "카자흐스탄 텡게", "MXN": "멕시코 페소"}
        try:
            smbs = await crawl_smbs(today_str)
            smbs_prev = await crawl_smbs(yesterday_str)
            for cur, val in smbs.items():
                prev_val = smbs_prev.get(cur)
                change = ""
                change_val = ""
                if prev_val:
                    diff = float(val.replace(",", "")) - float(prev_val.replace(",", ""))
                    if diff > 0:
                        change = "RISE"
                        change_val = f"{diff:+.4f}"
                    elif diff < 0:
                        change = "FALL"
                        change_val = f"{diff:+.4f}"
                    else:
                        change = "EVEN"
                        change_val = "0.00"
                rates.append({
                    "currency": cur,
                    "name": cur_names.get(cur, cur),
                    "base": val,
                    "buy": "-",
                    "sell": "-",
                    "change": change,
                    "change_val": change_val,
                })
        except Exception as e:
            print(f"SMBS 오류: {e}")

        # IQD, LBP - exchangerates.org.uk
        cur_names2 = {"IQD": "이라크 디나르", "LBP": "레바논 파운드"}
        year = datetime.now().year
        for cur in ["IQD", "LBP"]:
            try:
                data = await crawl_exchangerates(cur, year)
                if today_str in data:
                    prev_key = yesterday_str
                    change = ""
                    change_val = ""
                    if prev_key in data:
                        diff = float(data[today_str]) - float(data[prev_key])
                        if diff > 0:
                            change = "RISE"
                            change_val = f"{diff:+.6f}"
                        elif diff < 0:
                            change = "FALL"
                            change_val = f"{diff:+.6f}"
                        else:
                            change = "EVEN"
                            change_val = "0.00"
                    rates.append({
                        "currency": cur,
                        "name": cur_names2[cur],
                        "base": data[today_str],
                        "buy": "-",
                        "sell": "-",
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
async def get_rates_by_date(date: str):
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

        # KZT, MXN
        cur_names = {"KZT": "카자흐스탄 텡게", "MXN": "멕시코 페소"}
        try:
            smbs = await crawl_smbs(date)
            for cur, val in smbs.items():
                rates.append({
                    "currency": cur,
                    "name": cur_names.get(cur, cur),
                    "base": val,
                    "buy": "-", "sell": "-",
                    "change": "", "change_val": "",
                })
        except:
            pass

        # IQD, LBP
        cur_names2 = {"IQD": "이라크 디나르", "LBP": "레바논 파운드"}
        year = int(date[:4])
        for cur in ["IQD", "LBP"]:
            try:
                data = await crawl_exchangerates(cur, year)
                if date in data:
                    rates.append({
                        "currency": cur,
                        "name": cur_names2[cur],
                        "base": data[date],
                        "buy": "-", "sell": "-",
                        "change": "", "change_val": "",
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
async def get_monthly_avg(year: int, month: int):
    try:
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        exim_totals = {}
        exim_counts = {}
        smbs_totals = {"KZT": 0, "MXN": 0}
        smbs_counts = {"KZT": 0, "MXN": 0}

        # 수출입은행 월평균
        for day in range(1, last_day + 1):
            date_obj = datetime(year, month, day)
            if date_obj.weekday() >= 5:
                continue
            date_str = date_obj.strftime("%Y%m%d")
            data = fetch_exim_by_date(date_str)
            for cur in EXIM_TARGET:
                if cur in data:
                    val = float(data[cur]["deal_bas_r"].replace(",", ""))
                    exim_totals[cur] = exim_totals.get(cur, 0) + val
                    exim_counts[cur] = exim_counts.get(cur, 0) + 1

        # KZT, MXN 월평균
        for day in range(1, last_day + 1):
            date_obj = datetime(year, month, day)
            if date_obj.weekday() >= 5:
                continue
            date_str = date_obj.strftime("%Y%m%d")
            try:
                smbs = await crawl_smbs(date_str)
                for cur in ["KZT", "MXN"]:
                    if cur in smbs:
                        smbs_totals[cur] += float(smbs[cur].replace(",", ""))
                        smbs_counts[cur] += 1
            except:
                continue

        # IQD, LBP 월평균
        iqd_lbp_data = {}
        for cur in ["IQD", "LBP"]:
            try:
                iqd_lbp_data[cur] = await crawl_exchangerates(cur, year)
            except:
                iqd_lbp_data[cur] = {}

        result = []
        cur_name_map = {
            "USD": "미국 달러", "CNH": "중국 위안화",
            "KWD": "쿠웨이트 디나르", "AED": "UAE 디르함",
            "SAR": "사우디 리얄", "KZT": "카자흐스탄 텡게",
            "MXN": "멕시코 페소", "IQD": "이라크 디나르",
            "LBP": "레바논 파운드",
        }

        for cur in EXIM_TARGET:
            if cur in exim_totals and exim_counts[cur] > 0:
                avg = exim_totals[cur] / exim_counts[cur]
                result.append({
                    "currency": cur,
                    "name": cur_name_map[cur],
                    "base": f"{avg:,.2f}",
                    "buy": "-", "sell": "-",
                })

        for cur in ["KZT", "MXN"]:
            if smbs_counts[cur] > 0:
                avg = smbs_totals[cur] / smbs_counts[cur]
                result.append({
                    "currency": cur,
                    "name": cur_name_map[cur],
                    "base": f"{avg:,.4f}",
                    "buy": "-", "sell": "-",
                })

        for cur in ["IQD", "LBP"]:
            data = iqd_lbp_data.get(cur, {})
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
                    "name": cur_name_map[cur],
                    "base": f"{avg:.6f}",
                    "buy": "-", "sell": "-",
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
async def get_month_end(year: int, month: int):
    try:
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        result = []
        cur_name_map = {
            "USD": "미국 달러", "CNH": "중국 위안화",
            "KWD": "쿠웨이트 디나르", "AED": "UAE 디르함",
            "SAR": "사우디 리얄", "KZT": "카자흐스탄 텡게",
            "MXN": "멕시코 페소", "IQD": "이라크 디나르",
            "LBP": "레바논 파운드",
        }

        # 수출입은행 월말
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

        # KZT, MXN 월말
        for day in range(last_day, 0, -1):
            date_obj = datetime(year, month, day)
            if date_obj.weekday() >= 5:
                continue
            date_str = date_obj.strftime("%Y%m%d")
            try:
                smbs = await crawl_smbs(date_str)
                if smbs:
                    for cur, val in smbs.items():
                        result.append({
                            "currency": cur,
                            "name": cur_name_map[cur],
                            "base": val,
                            "buy": "-", "sell": "-",
                        })
                    break
            except:
                continue

        # IQD, LBP 월말
        for cur in ["IQD", "LBP"]:
            try:
                data = await crawl_exchangerates(cur, year)
                for day in range(last_day, 0, -1):
                    date_obj = datetime(year, month, day)
                    if date_obj.weekday() >= 5:
                        continue
                    date_key = date_obj.strftime("%Y%m%d")
                    if date_key in data:
                        result.append({
                            "currency": cur,
                            "name": cur_name_map[cur],
                            "base": data[date_key],
                            "buy": "-", "sell": "-",
                        })
                        break
            except:
                continue

        return {
            "success": True,
            "year": year,
            "month": month,
            "data": result
        }
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}
