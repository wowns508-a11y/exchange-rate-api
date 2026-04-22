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
    soup = BeautifulSoup(res.text, "html.parser")
    
    # 테이블 구조 확인
    tables = soup.find_all("table")
    return {
        "table_count": len(tables),
        "html_preview": res.text[:2000]  # 앞부분 2000자 확인
    }
