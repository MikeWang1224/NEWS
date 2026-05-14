# -*- coding: utf-8 -*-
"""
多公司新聞抓取程式（台積電 + 鴻海 + 聯電）
版本：v8-debug-stable
------------------------------------------------
✔ Firestore 一篇新聞一個 document（避免 document 過大）
✔ 儲存新聞 title + content + 漲跌 + embedding
✔ Hugging Face 免費 Embedding API
✔ embedding 失敗自動 fallback []
✔ 只抓最近 36 小時新聞
✔ 加入完整 debug log
✔ 所有 exception 顯示錯誤
✔ 避免 GitHub Actions 完全沒 log
"""

import os
import time
import json
import requests
import warnings

from datetime import datetime, timedelta
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

import firebase_admin
from firebase_admin import credentials, firestore

import yfinance as yf

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

print("🚀 Program started")

# =========================================================
# 基本設定
# =========================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}

HF_API_URL = (
    "https://api-inference.huggingface.co/pipeline/feature-extraction/"
    "sentence-transformers/all-MiniLM-L6-v2"
)

HF_TOKEN = os.environ.get("HF_TOKEN")

if not HF_TOKEN:
    raise ValueError("❌ 找不到 HF_TOKEN")

print("✅ HF_TOKEN OK")

HF_HEADERS = {
    "Authorization": f"Bearer {HF_TOKEN}"
}

# =========================================================
# Firebase 初始化
# =========================================================

try:
    key_dict = json.loads(os.environ["NEWS"])

    cred = credentials.Certificate(key_dict)

    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)

    db = firestore.client()

    print("✅ Firebase OK")

except Exception as e:
    print(f"❌ Firebase 初始化失敗: {e}")
    raise

# =========================================================
# 股票 mapping
# =========================================================

ticker_map = {
    "台積電": "2330.TW",
    "鴻海": "2317.TW",
    "聯電": "2303.TW"
}

# =========================================================
# 工具
# =========================================================

def is_recent(published_time, hours=36):
    now = datetime.now().astimezone()
    return (now - published_time) <= timedelta(hours=hours)


# =========================================================
# 股價漲跌
# =========================================================

def fetch_stock_change(stock_name):

    ticker = ticker_map.get(stock_name)

    if not ticker:
        return "無資料"

    try:
        print(f"📈 Fetch stock: {ticker}")

        df = yf.Ticker(ticker).history(period="2d")

        if len(df) < 2:
            return "無資料"

        last = df["Close"].iloc[-1]
        prev = df["Close"].iloc[-2]

        diff = last - prev
        pct = diff / prev * 100

        sign = "+" if diff >= 0 else ""

        return f"{sign}{diff:.2f} ({sign}{pct:.2f}%)"

    except Exception as e:
        print(f"❌ 股價取得失敗: {e}")
        return "無資料"


def add_price_change(news_list, stock_name):

    change = fetch_stock_change(stock_name)

    for n in news_list:
        n["price_change"] = change

    return news_list


# =========================================================
# HuggingFace Embedding
# =========================================================

def generate_embedding(text):

    if not text:
        return []

    try:

        print("🧠 Generating embedding...")

        res = requests.post(
            HF_API_URL,
            headers=HF_HEADERS,
            json={"inputs": text[:1000]},
            timeout=60
        )

        print(f"HF Status: {res.status_code}")

        if res.status_code != 200:
            print(res.text)
            return []

        data = res.json()

        if isinstance(data, list):
            return data

    except Exception as e:
        print(f"❌ Embedding 失敗: {e}")

    return []


# =========================================================
# 文章內文抓取
# =========================================================

def fetch_article_content(url, source):

    try:

        print(f"📄 Fetch article: {url}")

        r = requests.get(url, headers=HEADERS, timeout=15)

        soup = BeautifulSoup(r.text, "html.parser")

        if source == "yahoo":
            paragraphs = soup.select("article p") or soup.select("p")

        elif source == "cnbc":
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

        if not text:
            return "無內容"

        return text[:1500]

    except Exception as e:
        print(f"❌ 文章抓取失敗: {e}")
        return "無法取得新聞內容"


# =========================================================
# TechNews
# =========================================================

def fetch_technews(keyword="台積電", limit=30):

    print(f"\n📡 TechNews：{keyword}")

    links = []
    news = []

    url = f"https://technews.tw/google-search/?googlekeyword={keyword}"

    try:

        res = requests.get(url, headers=HEADERS, timeout=15)

        soup = BeautifulSoup(res.text, "html.parser")

        for a in soup.find_all("a", href=True):

            href = a["href"]

            if (
                href.startswith("https://technews.tw/")
                and "/tag/" not in href
            ):

                if href not in links:
                    links.append(href)

        print(f"✅ TechNews links: {len(links)}")

        links = links[:limit]

    except Exception as e:
        print(f"❌ TechNews 搜尋失敗: {e}")
        return []

    for link in links:

        try:

            r = requests.get(link, headers=HEADERS, timeout=15)

            s = BeautifulSoup(r.text, "html.parser")

            title_tag = s.find("h1")

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)

            time_tag = s.find("time", class_="entry-date")

            if not time_tag:
                continue

            published_str = time_tag.get_text(strip=True)

            try:
                published_dt = datetime.strptime(
                    published_str,
                    "%Y/%m/%d %H:%M"
                ).astimezone()

            except:
                print(f"⚠️ 時間解析失敗: {published_str}")
                continue

            if not is_recent(published_dt, 36):
                continue

            content = fetch_article_content(link, "technews")

            news.append({
                "title": title,
                "content": content,
                "published_time": published_dt
            })

            print(f"📰 TechNews: {title}")

            time.sleep(0.5)

        except Exception as e:
            print(f"❌ TechNews 文章錯誤: {e}")

    return news


# =========================================================
# Yahoo 新聞
# =========================================================

def fetch_yahoo_news(keyword="台積電", limit=30):

    print(f"\n📡 Yahoo：{keyword}")

    base = "https://tw.news.yahoo.com"

    url = f"{base}/search?p={keyword}&sort=time"

    news_list = []
    seen = set()

    try:

        r = requests.get(url, headers=HEADERS, timeout=15)

        soup = BeautifulSoup(r.text, "html.parser")

        links = (
            soup.select("a.js-content-viewer")
            or soup.select("h3 a")
            or soup.find_all("a")
        )

        print(f"✅ Yahoo links found: {len(links)}")

        for a in links:

            if len(news_list) >= limit:
                break

            try:

                title = a.get_text(strip=True)

                if not title:
                    continue

                if title in seen:
                    continue

                seen.add(title)

                href = a.get("href")

                if not href:
                    continue

                if not href.startswith("http"):
                    href = base + href

                content = fetch_article_content(href, "yahoo")

                r2 = requests.get(
                    href,
                    headers=HEADERS,
                    timeout=15
                )

                s2 = BeautifulSoup(r2.text, "html.parser")

                time_tag = s2.find("time")

                if not time_tag:
                    continue

                if not time_tag.has_attr("datetime"):
                    continue

                published_dt = datetime.fromisoformat(
                    time_tag["datetime"].replace("Z", "+00:00")
                ).astimezone()

                if not is_recent(published_dt, 36):
                    continue

                news_list.append({
                    "title": title,
                    "content": content,
                    "published_time": published_dt
                })

                print(f"📰 Yahoo: {title}")

            except Exception as e:
                print(f"❌ Yahoo article error: {e}")

    except Exception as e:
        print(f"❌ Yahoo 搜尋失敗: {e}")

    return news_list


# =========================================================
# CNBC
# =========================================================

def fetch_cnbc_news(keyword_list=["TSMC"], limit=20):

    print(f"\n📡 CNBC：{'/'.join(keyword_list)}")

    news = []
    seen = set()

    try:

        url = (
            "https://www.cnbc.com/search/?query="
            + "+".join(keyword_list)
        )

        r = requests.get(url, headers=HEADERS, timeout=15)

        soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.select("article a")

        print(f"✅ CNBC articles: {len(articles)}")

        for a in articles:

            if len(news) >= limit:
                break

            try:

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

                r2 = requests.get(
                    href,
                    headers=HEADERS,
                    timeout=15
                )

                s2 = BeautifulSoup(r2.text, "html.parser")

                time_tag = s2.find("time")

                if not time_tag:
                    continue

                if not time_tag.has_attr("datetime"):
                    continue

                published_dt = datetime.fromisoformat(
                    time_tag["datetime"].replace("Z", "+00:00")
                ).astimezone()

                if not is_recent(published_dt, 36):
                    continue

                seen.add(title)

                news.append({
                    "title": title,
                    "content": content,
                    "published_time": published_dt
                })

                print(f"📰 CNBC: {title}")

            except Exception as e:
                print(f"❌ CNBC article error: {e}")

    except Exception as e:
        print(f"❌ CNBC 搜尋失敗: {e}")

    return news


# =========================================================
# Firestore
# =========================================================

def save_news(news_list, collection):

    print(f"\n💾 Saving to Firestore: {collection}")

    count = 0

    for n in news_list:

        try:

            emb = generate_embedding(
                n.get("content", "")
            )

            data = {
                "title": n.get("title", ""),
                "price_change": n.get(
                    "price_change",
                    "無資料"
                ),
                "content": n.get("content", ""),
                "embedding": emb,
                "published_time": n.get(
                    "published_time"
                ).strftime("%Y-%m-%d %H:%M"),
                "created_at": firestore.SERVER_TIMESTAMP
            }

            db.collection(collection).add(data)

            count += 1

            print(f"✅ Saved: {n.get('title', '')}")

            time.sleep(1)

        except Exception as e:
            print(f"❌ Firestore 儲存失敗: {e}")

    print(f"🎉 共儲存 {count} 篇新聞")


# =========================================================
# 主程式
# =========================================================

if __name__ == "__main__":

    try:

        # 台積電
        tsmc_news = (
            fetch_technews("台積電", 20)
            + fetch_yahoo_news("台積電", 20)
            + fetch_cnbc_news(["TSMC"], 10)
        )

        print(f"📊 台積電新聞數量: {len(tsmc_news)}")

        if tsmc_news:

            tsmc_news = add_price_change(
                tsmc_news,
                "台積電"
            )

            save_news(tsmc_news, "NEWS")

        # 鴻海
        fox_news = fetch_yahoo_news("鴻海", 20)

        print(f"📊 鴻海新聞數量: {len(fox_news)}")

        if fox_news:

            fox_news = add_price_change( 
                fox_news,
                "鴻海"
            )

            save_news(fox_news, "NEWS_Foxconn")

        # 聯電
        umc_news = (
            fetch_technews("聯電", 20)
            + fetch_yahoo_news("聯電", 20)
            + fetch_cnbc_news(["UMC"], 10)
        )

        print(f"📊 聯電新聞數量: {len(umc_news)}")

        if umc_news:

            umc_news = add_price_change(
                umc_news,
                "聯電"
            )

            save_news(umc_news, "NEWS_UMC")

        print("\n🎉 全部新聞抓取完成！")

    except Exception as e:
        print(f"\n❌ 主程式錯誤: {e}")
