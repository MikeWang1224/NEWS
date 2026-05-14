# -*- coding: utf-8 -*-
"""
多公司新聞抓取程式（台積電 + 鴻海 + 聯電）
版本：v7-huggingface（embedding 版 / GitHub Secret 相容）
---------------------- --------------------------
✔ Firestore 只用日期當 ID 
✔ 儲存新聞 title + content + 漲跌 + embedding
✔ Hugging Face 免費 Embedding API
✔ 若 embedding 失敗，自動存 []
✔ 新增新聞時間解析，只抓 36 小時內新聞
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
    'User-Agent': 'Mozilla/5.0'
}

HF_API_URL = "https://api-inference.huggingface.co/pipeline/feature-extraction/sentence-transformers/all-MiniLM-L6-v2"
HF_TOKEN = os.environ.get("HF_TOKEN")

if not HF_TOKEN:
    raise ValueError("⚠️ 找不到 HF_TOKEN，請在 GitHub Secrets 設定！")

HF_HEADERS = {
    "Authorization": f"Bearer {HF_TOKEN}"
}

# Firestore 初始化
key_dict = json.loads(os.environ["NEWS"])
cred = credentials.Certificate(key_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

ticker_map = {
    "台積電": "2330.TW",
    "鴻海": "2317.TW",
    "聯電": "2303.TW"
}

# ---------------------- 新增：時間過濾 ---------------------- #
def is_recent(published_time, hours=36):
    """判斷新聞是否在最近幾小時內"""
    now = datetime.now().astimezone()
    return (now - published_time) <= timedelta(hours=hours)

# ---------------------- 抓股價漲跌 ---------------------- #
def fetch_stock_change(stock_name):
    ticker = ticker_map.get(stock_name)
    if not ticker:
        return "無資料"
    try:
        df = yf.Ticker(ticker).history(period="2d")
        if len(df) < 2:
            return "無資料"
        last = df['Close'].iloc[-1]
        prev = df['Close'].iloc[-2]
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

# ---------------------- Embedding（Hugging Face） ---------------------- #
def generate_embedding(text):
    if not text:
        return []
    try:
        res = requests.post(
            HF_API_URL,
            headers=HF_HEADERS,
            json={"inputs": text[:1000]},  # 避免太長
            timeout=20
        )
        data = res.json()
        if isinstance(data, list):
            return data
    except Exception as e:
        print(f"⚠️ Embedding 失敗: {e}")
    return []

# ---------------------- 文章內文抓取 ---------------------- #
def fetch_article_content(url, source):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')

        if source == 'yahoo':
            paragraphs = soup.select('article p') or soup.select('p')
        elif source == 'cnbc':
            paragraphs = soup.select('article p') or soup.select('p')
        else:
            paragraphs = soup.select('div.entry-content p, div.entry-content h2')

        text = '\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40])
        return text[:1500] + ('...' if len(text) > 1500 else '')
    except:
        return "無法取得新聞內容"

# ---------------------- TechNews ---------------------- #
def fetch_technews(keyword="台積電", limit=30):
    print(f"\n📡 TechNews：{keyword}")
    links, news = [], []
    url = f'https://technews.tw/google-search/?googlekeyword={keyword}'
    try:
        res = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('https://technews.tw/') and '/tag/' not in href:
                if href not in links:
                    links.append(href)
        links = links[:limit]
    except:
        return []

    for link in links:
        try:
            r = requests.get(link, headers=HEADERS)
            s = BeautifulSoup(r.text, 'html.parser')

            # 標題
            title_tag = s.find('h1')
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)

            # 發布時間
            time_tag = s.find("time", class_="entry-date")
            if not time_tag:
                continue
            published_str = time_tag.get_text(strip=True)
            published_dt = datetime.strptime(published_str, "%Y/%m/%d %H:%M").astimezone()
            if not is_recent(published_dt, 36):
                continue  # 太舊的新聞跳過

            # 內容
            content = fetch_article_content(link, 'technews')
            news.append({'title': title, 'content': content, 'published_time': published_dt})
            time.sleep(0.5)
        except:
            continue
    return news

# ---------------------- Yahoo 新聞 ---------------------- #
def fetch_yahoo_news(keyword="台積電", limit=30):
    print(f"\n📡 Yahoo：{keyword}")
    base = "https://tw.news.yahoo.com"
    url = f"{base}/search?p={keyword}&sort=time"
    news_list, seen = [], set()

    try:
        r = requests.get(url, headers=HEADERS)
        soup = BeautifulSoup(r.text, 'html.parser')
        links = soup.select('a.js-content-viewer') or soup.select('h3 a')

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

            # 文章內容與時間
            content = fetch_article_content(href, 'yahoo')
            try:
                r2 = requests.get(href, headers=HEADERS)
                s2 = BeautifulSoup(r2.text, 'html.parser')
                time_tag = s2.find("time")
                if not time_tag or not time_tag.has_attr("datetime"):
                    continue
                published_dt = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00")).astimezone()
                if not is_recent(published_dt, 36):
                    continue
            except:
                continue

            news_list.append({'title': title, 'content': content, 'published_time': published_dt})
    except:
        pass

    return news_list

# ---------------------- CNBC ---------------------- #
def fetch_cnbc_news(keyword_list=["TSMC"], limit=20):
    print(f"\n📡 CNBC：{'/'.join(keyword_list)}")
    urls = [
        "https://www.cnbc.com/search/?query=" + '+'.join(keyword_list)
    ]
    news, seen = [], set()

    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS)
            soup = BeautifulSoup(r.text, 'html.parser')
            articles = soup.select("article a")

            for a in articles:
                if len(news) >= limit:
                    break
                title = a.get_text(strip=True)
                href = a.get("href")

                if not title or title in seen or not href:
                    continue
                if not any(k.lower() in title.lower() for k in keyword_list):
                    continue

                if not href.startswith("http"):
                    href = "https://www.cnbc.com" + href

                # 內容
                content = fetch_article_content(href, 'cnbc')

                # 時間解析
                try:
                    r2 = requests.get(href, headers=HEADERS)
                    s2 = BeautifulSoup(r2.text, 'html.parser')
                    time_tag = s2.find("time")
                    if not time_tag or not time_tag.has_attr("datetime"):
                        continue
                    published_dt = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00")).astimezone()
                    if not is_recent(published_dt, 36):
                        continue
                except:
                    continue

                seen.add(title)
                news.append({'title': title, 'content': content, 'published_time': published_dt})
        except:
            continue

    return news

# ---------------------- Firestore ---------------------- #
def save_news(news_list, collection):
    doc_id = datetime.now().strftime("%Y%m%d")
    ref = db.collection(collection).document(doc_id)

    data = {}
    for i, n in enumerate(news_list, 1):
        emb = generate_embedding(n.get("content", ""))
        data[f"news_{i}"] = {
            "title": n.get("title", ""),
            "price_change": n.get("price_change", "無資料"),
            "content": n.get("content", ""),
            "embedding": emb,
            "published_time": n.get("published_time").strftime("%Y-%m-%d %H:%M")
        }

    ref.set(data)
    print(f"✅ Firestore 儲存完成：{collection}/{doc_id}")

# ---------------------- 聯電關鍵字過濾 ---------------------- #
def filter_umc_news(news_list):
    """過濾掉標題與內容都不含聯電相關關鍵字的新聞"""
    keywords = ["聯電", "聯華電子", "UMC"]
    result = []
    for n in news_list:
        title = n.get("title", "")
        content = n.get("content", "")
        if any(kw in title or kw in content for kw in keywords):
            result.append(n)
        else:
            print(f"⛔ 過濾不相關新聞：{title[:30]}")
    return result

# ---------------------- 主程式 ---------------------- #
if __name__ == "__main__":

    # 台積電
    tsmc_news = fetch_technews("台積電", 30) + fetch_yahoo_news("台積電", 30) + fetch_cnbc_news(["TSMC"], 20)
    if tsmc_news:
        tsmc_news = add_price_change(tsmc_news, "台積電")
        save_news(tsmc_news, "NEWS")

    # 鴻海
    fox_news = fetch_yahoo_news("鴻海", 30)
    if fox_news:
        fox_news = add_price_change(fox_news, "鴻海")
        save_news(fox_news, "NEWS_Foxxcon")

    # 聯電（加入關鍵字過濾）
    umc_news = fetch_technews("聯電", 20) + fetch_yahoo_news("聯電", 30) + fetch_cnbc_news(["UMC"], 20)
    umc_news = filter_umc_news(umc_news)
    if umc_news:
        umc_news = add_price_change(umc_news, "聯電")
        save_news(umc_news, "NEWS_UMC")

    print("\n🎉 全部新聞抓取完成！")
