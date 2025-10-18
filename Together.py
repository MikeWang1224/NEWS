# -*- coding: utf-8 -*-
"""
多公司新聞抓取程式（台積電 + 鴻海 + 聯電）
修正版：
✅ Firestore 檔名只用日期（無時間尾碼）
✅ 自動覆蓋當天新聞文件
✅ 增強 Yahoo / TechNews / CNBC 抓取穩定性
✅ 已可與分析程式搭配使用
"""

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

# ---------------------- 爬蟲設定 ---------------------- #
headers = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/91.0.4472.124 Safari/537.36'
    )
}

# ---------------------- 初始化 Firebase ---------------------- #
key_dict = json.loads(os.environ["NEWS"])
cred = credentials.Certificate(key_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

# ---------------------- 共用工具 ---------------------- #
def fetch_article_content(url, source):
    """抓取各新聞頁內容"""
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        if source == 'yahoo':
            paragraphs = soup.select('article p') or soup.select('p')
        elif source == 'cnbc':
            selectors = ['.ArticleBody-articleBody p', '.InlineArticleBody p', 'article p', 'p']
            for sel in selectors:
                paragraphs = soup.select(sel)
                if len(paragraphs) > 2:
                    break
        else:
            paragraphs = soup.select('div.entry-content p, div.entry-content h2')
        content = '\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40])
        return content[:1500] + ('...' if len(content) > 1500 else '')
    except Exception:
        return "無法取得新聞內容"

# ---------------------- TechNews ---------------------- #
def fetch_technews(keyword="台積電", limit=10):
    print(f"\n📡 抓取 TechNews（{keyword}）...")
    search_url = f'https://technews.tw/google-search/?googlekeyword={keyword}'
    links, news = [], []
    try:
        res = requests.get(search_url, headers=headers)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('https://technews.tw/') and all(
                x not in href for x in ['/tag/', '/page/', '/author/', '/videos/', '/about/', '/tn-rss/']
            ):
                if href not in links:
                    links.append(href)
        links = links[:limit]
    except Exception as e:
        print(f"⚠️ TechNews 抓取失敗: {e}")
        return []
    for link in links:
        try:
            r = requests.get(link, headers=headers)
            soup = BeautifulSoup(r.text, 'html.parser')
            title = soup.find('h1', class_='entry-title').get_text(strip=True)
            content = fetch_article_content(link, 'technews')
            news.append({'title': title, 'content': content})
            time.sleep(1)
        except Exception:
            continue
    return news

# ---------------------- Yahoo News ---------------------- #
def fetch_yahoo_news(keyword="台積電", limit=5):
    print(f"\n📡 抓取 Yahoo 新聞（{keyword}）...")
    base_url = "https://tw.news.yahoo.com"
    search_url = f"{base_url}/search?p={keyword}&sort=time"
    news_list, seen_titles = [], set()
    try:
        resp = requests.get(search_url, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select('li[data-testid="search-result"] a.js-content-viewer') or soup.select('h3 a')
        for a in articles:
            if len(news_list) >= limit:
                break
            title = a.get_text(strip=True)
            if not title or title in seen_titles:
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

# ---------------------- Yahoo Finance（聯電） ---------------------- #
def fetch_umc_yahoo_official(limit=8):
    print("\n📡 抓取 Yahoo Finance 聯電新聞（官方頁）...")
    base_url = "https://tw.stock.yahoo.com"
    search_url = f"{base_url}/quote/2303.TW/news"
    news_list, seen_titles = [], set()
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
            if not any(x in title for x in ["聯電", "UMC", "United Microelectronics"]):
                continue
            seen_titles.add(title)
            href = a.get("href")
            if href and not href.startswith("http"):
                href = base_url + href
            summary = fetch_article_content(href, 'yahoo')
            if not any(x in summary for x in ["聯電", "UMC", "United Microelectronics"]):
                continue
            news_list.append({'title': title, 'content': summary})
    except Exception as e:
        print(f"⚠️ Yahoo Finance 聯電抓取失敗: {e}")
    return news_list

# ---------------------- CNBC ---------------------- #
def fetch_cnbc_news(keyword_list=["TSMC"], limit=8):
    print(f"\n📡 抓取 CNBC 新聞（關鍵字：{', '.join(keyword_list)}）...")
    search_urls = [
        "https://www.cnbc.com/search/?query=" + '+'.join(keyword_list),
        "https://www.cnbc.com/technology/",
        "https://www.cnbc.com/semiconductors/"
    ]
    news_list, seen_titles = [], set()
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
        except Exception:
            continue
    return news_list[:limit]

# ---------------------- 儲存 Firestore ---------------------- #
def save_news_to_firestore(all_news, collection_name="NEWS"):
    collection_ref = db.collection(collection_name)
    doc_id = datetime.now().strftime("%Y%m%d")  # ✅ 只用日期
    doc_ref = collection_ref.document(doc_id)
    doc_ref.set({f"news_{i+1}": news for i, news in enumerate(all_news)})
    print(f"✅ 已寫入 Firestore：{collection_name}/{doc_id}")

# ---------------------- 主程式 ---------------------- #
if __name__ == "__main__":
    # 台積電
    technews = fetch_technews("台積電", limit=10)
    yahoo_news = fetch_yahoo_news("台積電", limit=10)
    cnbc_news = fetch_cnbc_news(["TSMC"], limit=10)
    all_tsmc = technews + yahoo_news + cnbc_news
    if all_tsmc:
        save_news_to_firestore(all_tsmc, "NEWS")

    # 鴻海
    honhai_news = fetch_yahoo_news("鴻海", limit=15)
    if honhai_news:
        save_news_to_firestore(honhai_news, "NEWS_Foxxcon")

    # 聯電
    umc_yahoo = fetch_umc_yahoo_official(limit=10)
    umc_tech = fetch_technews("聯電", limit=8)
    umc_cnbc = fetch_cnbc_news(["UMC", "United Microelectronics", "聯電"], limit=6)
    umc_news = umc_yahoo + umc_tech + umc_cnbc
    if umc_news:
        save_news_to_firestore(umc_news, "NEWS_UMC")

    print("\n🎉 全部新聞抓取完成！")
