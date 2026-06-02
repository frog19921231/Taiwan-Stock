import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
import time
import datetime
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd

# ================= 網頁全局設定 =================
st.set_page_config(page_title="台股決策支持系統 (DSS)", page_icon="📈", layout="wide")

# ================= 狀態管理 (Session State) =================
if 'monitored_stocks' not in st.session_state:
    st.session_state.monitored_stocks = {"2330", "2317", "0050"}
if 'stock_names' not in st.session_state:
    st.session_state.stock_names = {}
if 'stock_ma_notes' not in st.session_state:
    st.session_state.stock_ma_notes = {}
if 'alerted_stocks' not in st.session_state:
    st.session_state.alerted_stocks = set()
if 'alert_history' not in st.session_state:
    st.session_state.alert_history = []
if 'last_ptt_scan' not in st.session_state:
    st.session_state.last_ptt_scan = 0.0
if 'ptt_results' not in st.session_state:
    st.session_state.ptt_results = []
if 'ptt_last_update_str' not in st.session_state:
    st.session_state.ptt_last_update_str = "尚未執行"

# ================= 核心邏輯函數 =================

def get_push_count(tag):
    if not tag: return 0
    text = tag.text.strip()
    if text == "爆": return 100
    if text.startswith("X"): return -10
    try: return int(text)
    except ValueError: return 0

# PTT 俗稱與常用股字典
STOCK_KEYWORD_DICT = {
    "台積電": "2330", "神山": "2330", "鴻海": "2317", "海公公": "2317",
    "長榮": "2603", "聯發科": "2454", "緯創": "3231", "廣達": "2382",
    "台灣50": "0050", "高股息": "0056", "永續高股息": "00878"
}

def parse_stock_id(title):
    match = re.search(r'\b(\d{4,5})\b', title)
    if match: return match.group(1)
    for keyword, stock_id in STOCK_KEYWORD_DICT.items():
        if keyword in title: return stock_id
    return None

def scan_ptt_logic(pages, min_push):
    """強化防錯版 PTT 爬蟲"""
    base_url = "https://www.ptt.cc"
    url = f"{base_url}/bbs/Stock/index.html"
    cookies = {'over18': '1'}
    headers = {'User-Agent': 'Mozilla/5.0'}

    article_data = []
    for _ in range(pages):
        try:
            res = requests.get(url, headers=headers, cookies=cookies, timeout=5)
            if res.status_code != 200: break
                
            soup = BeautifulSoup(res.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')
            if not articles: break

            for art in articles:
                title_div = art.find('div', class_='title')
                if not title_div or not title_div.find('a'): continue
                    
                title = title_div.text.strip()
                a_tag = title_div.find('a')
                article_url = base_url + a_tag['href'] if a_tag and 'href' in a_tag.attrs else "#"

                if "[標的]" in title:
                    nrec_tag = art.find('div', class_='nrec')
                    push_count = get_push_count(nrec_tag)

                    if push_count >= min_push or (nrec_tag and nrec_tag.text.strip() == "爆"):
                        stock_id = parse_stock_id(title)
                        if stock_id and stock_id not in st.session_state.monitored_stocks:
                            st.session_state.monitored_stocks.add(stock_id)

                        heat_icon = "🔥 爆" if push_count >= 100 else f"👍 {push_count}"
                        article_data.append({
                            "推文熱度": heat_icon,
                            "股票代號": stock_id if stock_id else "未解析",
                            "文章標題": title,
                            "文章網址": article_url 
                        })
                        
            paging_div = soup.find('div', class_='btn-group btn-group-paging')
            if paging_div:
                prev_page_a = paging_div.find_all('a')[1]
                if prev_page_a and 'href' in prev_page_a.attrs:
                    url = base_url + prev_page_a['href']
                else: break
            else: break

        except Exception as e:
            print(f"PTT 爬取發生錯誤: {e}")
            break
    return article_data

def check_ma_breakthrough(stock_id):
    """檢測個股/ETF最新一天是否突破週線、月線、季線"""
    try:
        ticker = f"{stock_id}.TW"
        df = yf.download(ticker, period="6mo", interval="1d", progress=False)
        if df.empty or len(df) < 65: return ""
        
        df['MA5'] = df['Close'].rolling(window=5).mean()
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA60'] = df['Close'].rolling(window=60).mean()
        
        today_close = float(df['Close'].iloc[-1])
        yesterday_close = float(df['Close'].iloc[-2])
        
        ma5_today, ma5_yest = float(df['MA5'].iloc[-1]), float(df['MA5'].iloc[-2])
        ma20_today, ma20_yest = float(df['MA20'].iloc[-1]), float(df['MA20'].iloc[-2])
        ma60_today, ma60_yest = float(df['MA60'].iloc[-1]), float(df['MA60'].iloc[-2])
        
        notes = []
        if yesterday_close <= ma5_yest and today_close > ma5_today: notes.append("🚀突5MA")
        if yesterday_close <= ma20_yest and today_close > ma20_today: notes.append("🔥突月線")
        if yesterday_close <= ma60_yest and today_close > ma60_today: notes.append("👑突季線")
            
        return " ".join(notes) if notes else ""
    except Exception:
        return ""

def fetch_twse_realtime(stock_set, alert_threshold):
    if not stock_set: return []
    stock_params = "|".join([f"tse_{sid}.tw" for sid in stock_set])
    timestamp = int(time.time() * 1000)
    url = f"https://mis.twse.com.
