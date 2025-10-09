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
def fetch_technews(limit=10):
    print("\n📡 抓取 TechNews（台灣）...")
    search_url = 'https://technews.tw/google-search/?googlekeyword=台積電'
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
def fetch_yahoo_news(limit=5):
    print("\n📡 抓取 Yahoo 新聞（台灣）...")
    base_url = "https://tw.news.yahoo.com"
    search_url = f"{base_url}/search?p=台積電"
    news_list = []
    try:
        resp = requests.get(search_url, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select('li[data-testid="search-result"] a.js-content-viewer') or soup.select('h3 a')
        for a in articles:
            if len(news_list) >= limit:
                break
            title = a.get_text(strip=True)
            href = a.get("href")
            if href and not href.startswith("http"):
                href = base_url + href
            summary = fetch_article_content(href, 'yahoo')
            news_list.append({'title': title, 'content': summary})
    except Exception as e:
        print(f"⚠️ Yahoo 抓取失敗: {e}")
    return news_list

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

# ---------------------- CNBC ---------------------- #
def fetch_cnbc_news(limit=8):
    print("\n📡 抓取 CNBC 新聞（美國）...")
    search_urls = [
        "https://www.cnbc.com/search/?query=TSMC",
        "https://www.cnbc.com/search/?query=Taiwan+Semiconductor",
        "https://www.cnbc.com/technology/",
        "https://www.cnbc.com/semiconductors/"
    ]
    news_list = []
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
                href = a.get("href")
                if not href or '/video/' in href:
                    continue
                if not href.startswith("http"):
                    href = "https://www.cnbc.com" + href
                if any(x in title.lower() for x in ['tsmc', 'semiconductor', 'chip']):
                    content = fetch_article_content(href, 'cnbc')
                    news_list.append({'title': title, 'content': content})
                    time.sleep(2)
        except:
            continue
    return news_list

# 🟢 ---------------------- 鴻海新聞 ---------------------- #
def fetch_honhai_news(limit=8):
    print("\n📡 抓取 Yahoo 鴻海新聞（台灣）...")
    base_url = "https://tw.news.yahoo.com"
    search_url = f"{base_url}/search?p=鴻海"
    news_list = []
    try:
        resp = requests.get(search_url, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select('li[data-testid="search-result"] a.js-content-viewer') or soup.select('h3 a')
        for a in articles:
            if len(news_list) >= limit:
                break
            title = a.get_text(strip=True)
            href = a.get("href")
            if href and not href.startswith("http"):
                href = base_url + href
            summary = fetch_article_content(href, 'yahoo')
            news_list.append({'title': title, 'content': summary})
    except Exception as e:
        print(f"⚠️ 鴻海新聞抓取失敗: {e}")
    return news_list

# ---------------------- 儲存合併 ---------------------- #
def save_news_to_firestore(all_news, collection_name="NEWS"):
    collection_ref = db.collection(collection_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    doc_ref = collection_ref.document(timestamp)
    doc_ref.set({f"news_{i+1}": news for i, news in enumerate(all_news)})
    print(f"✅ 已寫入 Firestore：{collection_name}/{timestamp}")

# ---------------------- 主程式 ---------------------- #
if __name__ == '__main__':
    technews = fetch_technews()
    yahoo_news = fetch_yahoo_news()
    cnbc_news = fetch_cnbc_news()
    honhai_news = fetch_honhai_news()  # 🟢 鴻海新聞

    # 存到 NEWS
    all_news = technews + yahoo_news + cnbc_news
    save_news_to_firestore(all_news, "NEWS")

    # 存到 NEWS_Foxxcon（同層不同 collection）
    if honhai_news:
        save_news_to_firestore(honhai_news, "NEWS_Foxxcon")
