# -*- coding: utf-8 -*-
"""
多公司新聞抓取程式（台積電 + 鴻海 + 聯電）
版本：v9（強化新聞相關性過濾）
------------------------------------------------
✔ 移除 Embedding
✔ Firestore 只存 title/content/漲跌/時間
✔ 所有公司統一關鍵字過濾
✔ 聯電移除 Yahoo（避免垃圾新聞）
✔ 新聞限制最近 36 小時
✔ 強化 CNBC 關鍵字
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

# --------------------------------------------------
# 基本設定
# --------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# Firestore 初始化
key_dict = json.loads(os.environ["NEWS"])
cred = credentials.Certificate(key_dict)

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

ticker_map = {
    "台積電": "2330.TW",
    "鴻海": "2317.TW",
    "聯電": "2303.TW"
}

# --------------------------------------------------
# 關鍵字設定
# --------------------------------------------------

KEYWORDS = {
    "台積電": [
        "台積電",
        "TSMC",
        "台灣積體電路",
        "晶圓代工",
        "CoWoS",
    ],

    "鴻海": [
        "鴻海",
        "Foxconn",
        "富士康",
        "郭台銘",
        "鴻準",
    ],

    "聯電": [
        "聯電",
        "聯華電子",
        "UMC",
        "United Microelectronics",
        "晶圓代工",
        "成熟製程",
    ]
}

# --------------------------------------------------
# 時間判斷
# --------------------------------------------------

def is_recent(published_time, hours=36):
    now = datetime.now().astimezone()
    return (now - published_time) <= timedelta(hours=hours)

# --------------------------------------------------
# 新聞過濾
# --------------------------------------------------

def filter_news_by_keywords(news_list, stock_name):

    keywords = KEYWORDS.get(stock_name, [])

    if not keywords:
        return news_list

    result = []

    for n in news_list:

        title = n.get("title", "").lower()
        content = n.get("content", "").lower()

        full_text = f"{title} {content}"

        matched = 0

        for kw in keywords:
            if kw.lower() in full_text:
                matched += 1

        # 至少命中 1 個關鍵字
        if matched >= 1:
            result.append(n)
        else:
            print(f"⛔ [{stock_name}] 過濾不相關新聞：{title[:50]}")

    print(
        f"✅ [{stock_name}] 過濾後剩餘："
        f"{len(result)} 則（原始：{len(news_list)} 則）"
    )

    return result

# --------------------------------------------------
# 股價漲跌
# --------------------------------------------------

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

# --------------------------------------------------
# 抓文章內容
# --------------------------------------------------

def fetch_article_content(url, source):

    try:

        r = requests.get(
            url,
            headers=HEADERS,
            timeout=10
        )

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

        return text[:2000]

    except:
        return ""

# --------------------------------------------------
# TechNews
# --------------------------------------------------

def fetch_technews(keyword="台積電", limit=20):

    print(f"\n📡 TechNews：{keyword}")

    links = []
    news = []

    url = f"https://technews.tw/google-search/?googlekeyword={keyword}"

    try:

        res = requests.get(url, headers=HEADERS)

        soup = BeautifulSoup(res.text, "html.parser")

        for a in soup.find_all("a", href=True):

            href = a["href"]

            if (
                href.startswith("https://technews.tw/")
                and "/tag/" not in href
            ):

                if href not in links:
                    links.append(href)

        links = links[:limit]

    except:
        return []

    for link in links:

        try:

            r = requests.get(link, headers=HEADERS)

            s = BeautifulSoup(r.text, "html.parser")

            title_tag = s.find("h1")

            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)

            time_tag = s.find("time", class_="entry-date")

            if not time_tag:
                continue

            published_str = time_tag.get_text(strip=True)

            published_dt = datetime.strptime(
                published_str,
                "%Y/%m/%d %H:%M"
            ).astimezone()

            if not is_recent(published_dt, 36):
                continue

            content = fetch_article_content(link, "technews")

            news.append({
                "title": title,
                "content": content,
                "published_time": published_dt
            })

            time.sleep(0.5)

        except:
            continue

    return news

# --------------------------------------------------
# Yahoo
# --------------------------------------------------

def fetch_yahoo_news(keyword="台積電", limit=20):

    print(f"\n📡 Yahoo：{keyword}")

    base = "https://tw.news.yahoo.com"

    url = f"{base}/search?p={keyword}&sort=time"

    news_list = []
    seen = set()

    try:

        r = requests.get(url, headers=HEADERS)

        soup = BeautifulSoup(r.text, "html.parser")

        links = soup.select("a.js-content-viewer") \
                or soup.select("h3 a")

        for a in links:

            if len(news_list) >= limit:
                break

            title = a.get_text(strip=True)

            if not title or title in seen:
                continue

            seen.add(title)

            href = a.get("href")

            if href and not href.startswith("http"):
                href = base + href

            try:

                r2 = requests.get(
                    href,
                    headers=HEADERS,
                    timeout=10
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

                content = fetch_article_content(href, "yahoo")

                news_list.append({
                    "title": title,
                    "content": content,
                    "published_time": published_dt
                })

            except:
                continue

    except:
        pass

    return news_list

# --------------------------------------------------
# CNBC
# --------------------------------------------------

def fetch_cnbc_news(keyword_list, limit=20):

    print(f"\n📡 CNBC：{'/'.join(keyword_list)}")

    news = []
    seen = set()

    url = (
        "https://www.cnbc.com/search/?query="
        + "+".join(keyword_list)
    )

    try:

        r = requests.get(url, headers=HEADERS)

        soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.select("article a")

        for a in articles:

            if len(news) >= limit:
                break

            title = a.get_text(strip=True)

            href = a.get("href")

            if not title or not href:
                continue

            if title in seen:
                continue

            # 必須含 keyword
            if not any(
                k.lower() in title.lower()
                for k in keyword_list
            ):
                continue

            if not href.startswith("http"):
                href = "https://www.cnbc.com" + href

            try:

                r2 = requests.get(
                    href,
                    headers=HEADERS,
                    timeout=10
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

                content = fetch_article_content(href, "cnbc")

                seen.add(title)

                news.append({
                    "title": title,
                    "content": content,
                    "published_time": published_dt
                })

            except:
                continue

    except:
        pass

    return news

# --------------------------------------------------
# Firestore
# --------------------------------------------------

def save_news(news_list, collection):

    doc_id = datetime.now().strftime("%Y%m%d")

    ref = db.collection(collection).document(doc_id)

    data = {}

    for i, n in enumerate(news_list, 1):

        data[f"news_{i}"] = {
            "title": n.get("title", ""),
            "price_change": n.get("price_change", "無資料"),
            "content": n.get("content", ""),
            "published_time": n.get(
                "published_time"
            ).strftime("%Y-%m-%d %H:%M")
        }

    ref.set(data)

    print(
        f"✅ Firestore 儲存完成："
        f"{collection}/{doc_id}"
        f"，共 {len(news_list)} 則新聞"
    )

# --------------------------------------------------
# 主程式
# --------------------------------------------------

if __name__ == "__main__":

    # --------------------------------------------------
    # 台積電
    # --------------------------------------------------

    print("\n========== 台積電 ==========")

    tsmc_news = (
        fetch_technews("台積電", 20) +
        fetch_yahoo_news("台積電", 20) +
        fetch_cnbc_news(
            ["TSMC", "Taiwan Semiconductor"],
            20
        )
    )

    tsmc_news = filter_news_by_keywords(
        tsmc_news,
        "台積電"
    )

    if tsmc_news:

        tsmc_news = add_price_change(
            tsmc_news,
            "台積電"
        )

        save_news(tsmc_news, "NEWS")

    else:
        print("⚠️ 台積電：沒有相關新聞")

    # --------------------------------------------------
    # 鴻海
    # --------------------------------------------------

    print("\n========== 鴻海 ==========")

    fox_news = (
        fetch_yahoo_news("鴻海", 20)
    )

    fox_news = filter_news_by_keywords(
        fox_news,
        "鴻海"
    )

    if fox_news:

        fox_news = add_price_change(
            fox_news,
            "鴻海"
        )

        save_news(fox_news, "NEWS_Foxxcon")

    else:
        print("⚠️ 鴻海：沒有相關新聞")

    # --------------------------------------------------
    # 聯電
    # --------------------------------------------------

    print("\n========== 聯電 ==========")

    # ❗不再使用 Yahoo（垃圾新聞太多）
    umc_news = (
        fetch_technews("聯電", 20) +
        fetch_cnbc_news(
            [
                "UMC",
                "United Microelectronics"
            ],
            20
        )
    )

    umc_news = filter_news_by_keywords(
        umc_news,
        "聯電"
    )

    if umc_news:

        umc_news = add_price_change(
            umc_news,
            "聯電"
        )

        save_news(umc_news, "NEWS_UMC")

    else:
        print("⚠️ 聯電：沒有相關新聞")

    print("\n🎉 全部新聞抓取完成！")
