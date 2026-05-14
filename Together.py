# -*- coding: utf-8 -*-
"""
多公司新聞抓取程式（台積電 + 鴻海 + 聯電）
版本：v8-clean-filter
------------------------------------------------
✔ 過濾不相干新聞
✔ title + content 雙重 keyword 檢查
✔ blacklist 過濾
✔ 36 小時內新聞
✔ HuggingFace embedding
✔ Firestore 儲存
"""

import os
import time
import json
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
import firebase_admin
from firebase_admin import credentials, firestore
import yfinance as yf

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ---------------------- 設定 ---------------------- #

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

HF_API_URL = (
    "https://api-inference.huggingface.co/pipeline/feature-extraction/"
    "sentence-transformers/all-MiniLM-L6-v2"
)

HF_TOKEN = os.environ.get("HF_TOKEN")

if not HF_TOKEN:
    raise ValueError("⚠️ 找不到 HF_TOKEN")

HF_HEADERS = {
    "Authorization": f"Bearer {HF_TOKEN}"
}

# ---------------------- Firestore ---------------------- #

key_dict = json.loads(os.environ["NEWS"])

if not firebase_admin._apps:
    cred = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ---------------------- 股票對照 ---------------------- #

ticker_map = {
    "台積電": "2330.TW",
    "鴻海": "2317.TW",
    "聯電": "2303.TW"
}

# ---------------------- 關鍵字 ---------------------- #

keyword_map = {
    "台積電": ["台積電", "TSMC"],
    "鴻海": ["鴻海", "Foxconn"],
    "聯電": ["聯電", "UMC"]
}

BLACKLIST = [
    "MLB",
    "NBA",
    "棒球",
    "籃球",
    "直播",
    "廣告",
    "Sponsored",
    "廣編",
    "Podcast"
]

# ---------------------- 時間判斷 ---------------------- #

def is_recent(published_time, hours=36):
    now = datetime.now().astimezone()
    return (now - published_time) <= timedelta(hours=hours)

# ---------------------- 關鍵字過濾 ---------------------- #

def is_related_news(title, content, keywords):

    text = f"{title} {content}".lower()

    # blacklist
    for b in BLACKLIST:
        if b.lower() in text:
            return False

    # keyword
    return any(k.lower() in text for k in keywords)

# ---------------------- 股價漲跌 ---------------------- #

def fetch_stock_change(stock_name):

    ticker = ticker_map.get(stock_name)

    if not ticker:
        return "無資料"

    try:
        df = yf.Ticker(ticker).history(period="2d")

        if len(df) < 2:
            return "無資料"

        last = df["Close"].iloc[-1]
        prev = df["Close"].iloc[-2]

        diff = last - prev
        pct = diff / prev * 100

        sign = "+" if diff >= 0 else ""

        return f"{sign}{diff:.2f} ({sign}{pct:.2f}%)"

    except:
        return "無資料"

def add_price_change(news_list, stock_name):

    change = fetch_stock_change(stock_name)

    for n in news_list:
        n["price_change"] = change

    return news_list

# ---------------------- Embedding ---------------------- #

def generate_embedding(text):

    if not text:
        return []

    try:
        res = requests.post(
            HF_API_URL,
            headers=HF_HEADERS,
            json={"inputs": text[:1000]},
            timeout=20
        )

        data = res.json()

        if isinstance(data, list):
            return data

    except Exception as e:
        print(f"⚠️ Embedding 失敗: {e}")

    return []

# ---------------------- 文章內容 ---------------------- #

def fetch_article_content(url, source):

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)

        soup = BeautifulSoup(r.text, "html.parser")

        if source in ["yahoo", "cnbc"]:
            paragraphs = soup.select("article p") or soup.select("p")

        else:
            paragraphs = soup.select(
                "div.entry-content p, div.entry-content h2"
            )

        text = "\n".join([
            p.get_text(strip=True)
            for p in paragraphs
            if len(p.get_text(strip=True)) > 40
        ])

        return text[:1500]

    except:
        return ""

# ---------------------- TechNews ---------------------- #

def fetch_technews(keyword="台積電", limit=20):

    print(f"\n📡 TechNews：{keyword}")

    keywords = keyword_map.get(keyword, [keyword])

    url = f"https://technews.tw/google-search/?googlekeyword={keyword}"

    news = []
    seen = set()

    try:
        r = requests.get(url, headers=HEADERS)

        soup = BeautifulSoup(r.text, "html.parser")

        links = []

        for a in soup.find_all("a", href=True):

            href = a["href"]

            if (
                href.startswith("https://technews.tw/")
                and "/tag/" not in href
            ):
                if href not in links:
                    links.append(href)

        for link in links[:limit]:

            try:
                r2 = requests.get(link, headers=HEADERS)

                s2 = BeautifulSoup(r2.text, "html.parser")

                title_tag = s2.find("h1")

                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)

                if title in seen:
                    continue

                time_tag = s2.find("time", class_="entry-date")

                if not time_tag:
                    continue

                published_str = time_tag.get_text(strip=True)

                published_dt = datetime.strptime(
                    published_str,
                    "%Y/%m/%d %H:%M"
                ).astimezone()

                if not is_recent(published_dt):
                    continue

                content = fetch_article_content(link, "technews")

                # 關鍵字過濾
                if not is_related_news(title, content, keywords):
                    continue

                seen.add(title)

                news.append({
                    "title": title,
                    "content": content,
                    "published_time": published_dt
                })

                time.sleep(0.5)

            except:
                continue

    except:
        return []

    return news

# ---------------------- Yahoo ---------------------- #

def fetch_yahoo_news(keyword="台積電", limit=30):

    print(f"\n📡 Yahoo：{keyword}")

    keywords = keyword_map.get(keyword, [keyword])

    base = "https://tw.news.yahoo.com"

    url = f"{base}/search?p={keyword}&sort=time"

    news = []
    seen = set()

    try:

        r = requests.get(url, headers=HEADERS)

        soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.select("h3 a")

        for a in articles:

            if len(news) >= limit:
                break

            title = a.get_text(strip=True)

            href = a.get("href")

            if not title or not href:
                continue

            if title in seen:
                continue

            if not href.startswith("http"):
                href = base + href

            content = fetch_article_content(href, "yahoo")

            # keyword 過濾
            if not is_related_news(title, content, keywords):
                continue

            try:

                r2 = requests.get(href, headers=HEADERS)

                s2 = BeautifulSoup(r2.text, "html.parser")

                time_tag = s2.find("time")

                if not time_tag:
                    continue

                dt_str = time_tag.get("datetime")

                if not dt_str:
                    continue

                published_dt = datetime.fromisoformat(
                    dt_str.replace("Z", "+00:00")
                ).astimezone()

                if not is_recent(published_dt):
                    continue

            except:
                continue

            seen.add(title)

            news.append({
                "title": title,
                "content": content,
                "published_time": published_dt
            })

    except:
        pass

    return news

# ---------------------- CNBC ---------------------- #

def fetch_cnbc_news(keyword_list=["TSMC"], limit=20):

    print(f"\n📡 CNBC：{'/'.join(keyword_list)}")

    url = (
        "https://www.cnbc.com/search/?query="
        + "+".join(keyword_list)
    )

    news = []
    seen = set()

    try:

        r = requests.get(url, headers=HEADERS)

        soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.select("a.Card-title")

        for a in articles:

            if len(news) >= limit:
                break

            title = a.get_text(strip=True)

            href = a.get("href")

            if not title or not href:
                continue

            if title in seen:
                continue

            if not any(
                k.lower() in title.lower()
                for k in keyword_list
            ):
                continue

            if not href.startswith("http"):
                href = "https://www.cnbc.com" + href

            content = fetch_article_content(href, "cnbc")

            # content keyword filter
            if not is_related_news(title, content, keyword_list):
                continue

            try:

                r2 = requests.get(href, headers=HEADERS)

                s2 = BeautifulSoup(r2.text, "html.parser")

                time_tag = s2.find("time")

                if not time_tag:
                    continue

                dt_str = time_tag.get("datetime")

                if not dt_str:
                    continue

                published_dt = datetime.fromisoformat(
                    dt_str.replace("Z", "+00:00")
                ).astimezone()

                if not is_recent(published_dt):
                    continue

            except:
                continue

            seen.add(title)

            news.append({
                "title": title,
                "content": content,
                "published_time": published_dt
            })

    except:
        pass

    return news

# ---------------------- Firestore ---------------------- #

def save_news(news_list, collection):

    doc_id = datetime.now().strftime("%Y%m%d")

    ref = db.collection(collection).document(doc_id)

    data = {}

    for i, n in enumerate(news_list, 1):

        emb = generate_embedding(
            n.get("content", "")
        )

        data[f"news_{i}"] = {
            "title": n.get("title", ""),
            "price_change": n.get("price_change", "無資料"),
            "content": n.get("content", ""),
            "embedding": emb,
            "published_time": n.get(
                "published_time"
            ).strftime("%Y-%m-%d %H:%M")
        }

    ref.set(data)

    print(f"✅ Firestore 儲存完成：{collection}/{doc_id}")

# ---------------------- 主程式 ---------------------- #

if __name__ == "__main__":

    # 台積電
    tsmc_news = (
        fetch_technews("台積電", 20)
        + fetch_yahoo_news("台積電", 20)
        + fetch_cnbc_news(["TSMC"], 10)
    )

    if tsmc_news:

        tsmc_news = add_price_change(
            tsmc_news,
            "台積電"
        )

        save_news(tsmc_news, "NEWS")

    # 鴻海
    fox_news = (
        fetch_yahoo_news("鴻海", 20)
    )

    if fox_news:

        fox_news = add_price_change(
            fox_news,
            "鴻海"
        )

        save_news(
            fox_news,
            "NEWS_Foxxcon"
        )

    # 聯電
    umc_news = (
        fetch_technews("聯電", 20)
        + fetch_yahoo_news("聯電", 20)
        + fetch_cnbc_news(["UMC"], 10)
    )

    if umc_news:

        umc_news = add_price_change(
            umc_news,
            "聯電"
        )

        save_news(
            umc_news,
            "NEWS_UMC"
        )

    print("\n🎉 全部新聞抓取完成！")
