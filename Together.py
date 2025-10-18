# -*- coding: utf-8 -*-
import os
import re
import time
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
import firebase_admin
from firebase_admin import credentials, firestore

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# ---------------------- 初始化 Firebase ---------------------- #
key_dict = json.loads(os.environ["NEWS"])
cred = credentials.Certificate(key_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# ---------------------- TechNews ---------------------- #
def fetch_technews(keyword="台積電", limit=10):
    print(f"\n📡 抓取 TechNews（{keyword}）...")
    search_url = f'https://technews.tw/google-search/?googlekeyword={keyword}'
    links = []
    try:
        res = requests.get(search_url, headers=headers)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('https://technews.tw/') and all(x not in href for x in ['/tag/', '/page/', '/author/', '/videos/', '/about/', '/tn-rss/']):
                if href not in links:
                    links.append(href)
        links = links[:limit]
    except Exception as e:
        print(f"⚠️ TechNews 抓取失敗: {e}")
        return []

    news = []
    for link in links:
        try:
            r = requests.get(link, headers=headers)
            soup = BeautifulSoup(r.text, 'html.parser')
            title = soup.find('h1', class_='entry-title').get_text(strip=True)
            content = '\n'.join([tag.get_text(strip=True) for tag in soup.select('div.entry-content p, div.entry-content h2')])
            news.append({'title': title, 'content': content})
            time.sleep(1)
        except:
            continue
    return news

# ---------------------- Yahoo News ---------------------- #
def fetch_yahoo_news(keyword="台積電", limit=5):
    print(f"\n📡 抓取 Yahoo 新聞（{keyword}）...")
    base_url = "https://tw.news.yahoo.com"
    search_url = f"{base_url}/search?p={keyword}&sort=time"
    news_list = []
    seen_titles = set()
    try:
        resp = requests.get(search_url, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select('li[data-testid="search-result"] a.js-content-viewer') or soup.select('h3 a')
        for a in articles:
            if len(news_list) >= limit:
                break
            title = a.get_text(strip=True)
            if title in seen_titles:
                continue
            seen_titles.add(title)
            href = a.get("href")
            if href and not href.startswith("http"):
                href = base_url + href
            summary = fetch_article_content(href, 'yahoo')
            news_list.append({'title': title, 'content': summary})
    except Exception as e:
        print(f"⚠️ Yahoo 抓取失敗: {e}")
    return news_list

# ---------------------- Yahoo Finance 聯電新聞 ---------------------- #
def fetch_umc_yahoo_official(limit=8):
    print("\n📡 抓取 Yahoo Finance 聯電新聞（官方頁）...")
    base_url = "https://tw.stock.yahoo.com"
    search_url = f"{base_url}/quote/2303.TW/news"
    news_list = []
    seen_titles = set()
    try:
        resp = requests.get(search_url, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select('li.js-stream-content a') or soup.select('h3 a')
        for a in articles:
            if len(news_list) >= limit:
                break
            title = a.get_text(strip=True)
            if not title or title in seen_titles:
                continue
            # ✅ 只保留含聯電關鍵字的標題
            if not any(x in title for x in ["聯電", "UMC", "United Microelectronics"]):
                continue
            seen_titles.add(title)
            href = a.get("href")
            if href and not href.startswith("http"):
                href = base_url + href
            summary = fetch_article_content(href, 'yahoo')
            # ✅ 內容也必須包含聯電相關詞
            if not any(x in summary for x in ["聯電", "UMC", "United Microelectronics"]):
                continue
            news_list.append({'title': title, 'content': summary})
    except Exception as e:
        print(f"⚠️ Yahoo Finance UMC 抓取失敗: {e}")
    return news_list

# ---------------------- CNBC ---------------------- #
def fetch_cnbc_news(keyword_list=["TSMC"], limit=8):
    print(f"\n📡 抓取 CNBC 新聞（關鍵字：{', '.join(keyword_list)}）...")
    search_urls = [
        "https://www.cnbc.com/search/?query=" + '+'.join(keyword_list),
        "https://www.cnbc.com/technology/",
        "https://www.cnbc.com/semiconductors/"
    ]
    news_list = []
    seen_titles = set()
    for url in search_urls:
        if len(news_list) >= limit:
            break
        try:
            resp = requests.get(url, headers=headers)
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select('article a, h2 a, h3 a'):
                if len(news_list) >= limit:
                    break
                title = a.get_text(strip=True)
                if not title or title in seen_titles:
                    continue
                if not any(x.lower() in title.lower() for x in keyword_list):
                    continue
                seen_titles.add(title)
                href = a.get("href")
                if not href or '/video/' in href:
                    continue
                if not href.startswith("http"):
                    href = "https://www.cnbc.com" + href
                content = fetch_article_content(href, 'cnbc')
                news_list.append({'title': title, 'content': content})
                time.sleep(2)
        except:
            continue
    return news_list[:limit]

# ---------------------- 內容抓取 ---------------------- #
def fetch_article_content(url, source):
    try:
        r = requests.get(url, headers=headers)
        soup = BeautifulSoup(r.text, 'html.parser')
        if source == 'yahoo':
            paragraphs = soup.select('article p') or soup.select('p')
        elif source == 'cnbc':
            selectors = ['.ArticleBody-articleBody p', '.InlineArticleBody p', 'article p', 'p']
            for sel in selectors:
                paragraphs = soup.select(sel)
                if len(paragraphs) > 2:
                    break
        content = '\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40])
        return content[:1500] + ('...' if len(content) > 1500 else '')
    except:
        return "無法取得新聞內容"

# ---------------------- 鴻海新聞 ---------------------- #
def fetch_honhai_news(limit=8):
    print("\n📡 抓取 Yahoo 鴻海新聞（台灣）...")
    return fetch_yahoo_news("鴻海", limit)

# ---------------------- 聯電 TechNews ---------------------- #
def fetch_umc_technews(limit=4):
    return fetch_technews("聯電", limit)

# ---------------------- 聯電 CNBC ---------------------- #
def fetch_umc_cnbc(limit=3):
    keywords = ["UMC", "United Microelectronics", "聯電", "semiconductor"]
    return fetch_cnbc_news(keywords, limit)

# ---------------------- 儲存到 Firestore ---------------------- #
def save_news_to_firestore(all_news, collection_name="NEWS"):
    collection_ref = db.collection(collection_name)
    # ✅ 文件名稱只用日期
    timestamp = datetime.now().strftime("%Y%m%d")
    doc_ref = collection_ref.document(timestamp)
    # ✅ 若當日已存在，會覆蓋舊資料
    doc_ref.set({f"news_{i+1}": news for i, news in enumerate(all_news)})
    print(f"✅ 已寫入 Firestore：{collection_name}/{timestamp}")

# ---------------------- 主程式 ---------------------- #
if __name__ == '__main__':
    # 台積電新聞
    technews = fetch_technews(limit=10)
    yahoo_news = fetch_yahoo_news(limit=10)
    cnbc_news = fetch_cnbc_news(["TSMC"], limit=10)
    all_news = technews + yahoo_news + cnbc_news
    save_news_to_firestore(all_news, "NEWS")

    # 鴻海新聞
    honhai_news = fetch_honhai_news(limit=30)
    if honhai_news:
        save_news_to_firestore(honhai_news, "NEWS_Foxxcon")

    # 聯電新聞
    umc_yahoo = fetch_umc_yahoo_official(limit=10)
    umc_tech = fetch_umc_technews(limit=10)
    umc_cnbc = fetch_umc_cnbc(limit=10)
    umc_news = umc_yahoo + umc_tech + umc_cnbc
    if umc_news:
        save_news_to_firestore(umc_news, "NEWS_UMC")
