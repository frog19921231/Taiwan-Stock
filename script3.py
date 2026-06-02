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
    """雙通道高抗壓即時量價解析"""
    if not stock_set: return []
    
    params_list = []
    for sid in stock_set:
        params_list.append(f"tse_{sid}.tw")
        params_list.append(f"otc_{sid}.tw")
        
    stock_params = "|".join(params_list)
    timestamp = int(time.time() * 1000)
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={stock_params}&_={timestamp}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    results = []
    try:
        response = requests.get(url, headers=headers, timeout=5)
        data = response.json()
        if "msgArray" in data and data["msgArray"]:
            seen_stocks = set()
            for info in data["msgArray"]:
                stock_id = info.get("c")
                if not stock_id or stock_id in seen_stocks:
                    continue
                
                stock_name = info.get("n", st.session_state.stock_names.get(stock_id, "未知個股"))
                st.session_state.stock_names[stock_id] = stock_name
                
                if stock_id not in st.session_state.stock_ma_notes:
                    st.session_state.stock_ma_notes[stock_id] = check_ma_breakthrough(stock_id)

                # 三層價格防禦機制 (成交價 'z' -> 買進價 'b' -> 昨收價 'y')
                price_str = info.get("z", "").strip()
                if not price_str or price_str == "-":
                    price_str = info.get("b", "").split("_")[0].strip()
                if not price_str or price_str == "-":
                    price_str = info.get("y", "0").strip()

                current_price = float(price_str) if price_str and price_str != "-" else 0.0
                yesterday_price = float(info.get("y", 0))
                volume = int(info.get("v", 0))

                change_percent = ((current_price - yesterday_price) / yesterday_price) * 100 if yesterday_price > 0 else 0.0

                if current_price == 0:
                    continue

                seen_stocks.add(stock_id)

                if change_percent >= alert_threshold and stock_id not in st.session_state.alerted_stocks:
                    st.toast(f"🚨 警報！{stock_name} 漲幅突破 {alert_threshold}% (現價 {current_price})", icon="🚀")
                    st.session_state.alerted_stocks.add(stock_id) 
                    
                    st.session_state.alert_history.insert(0, {
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                        "id": stock_id,
                        "name": stock_name,
                        "price": current_price,
                        "pct": change_percent
                    })

                results.append({
                    "代號": stock_id,
                    "名稱": stock_name,
                    "技術註記": st.session_state.stock_ma_notes.get(stock_id, ""),
                    "現價": current_price,
                    "漲跌幅(%)": change_percent,
                    "成交量": volume
                })
        return results
    except Exception:
        return []

def fetch_kline_chart(stock_id, period, interval, label_name):
    try:
        ticker = f"{stock_id}.TW"
        hist = yf.download(ticker, period=period, interval=interval, progress=False)
        if hist.empty: return None

        fig = go.Figure(data=[go.Candlestick(
            x=hist.index,
            open=hist['Open'].values.flatten(),
            high=hist['High'].values.flatten(),
            low=hist['Low'].values.flatten(),
            close=hist['Close'].values.flatten(),
            increasing_line_color='red',
            decreasing_line_color='green'
        )])
        fig.update_layout(
            title=f"{stock_id} 近期趨勢 ({label_name})",
            yaxis_title="股價 (元)",
            xaxis_title="時間",
            template="plotly_dark",
            margin=dict(l=20, r=20, t=40, b=20),
            height=350,
            xaxis_rangeslider_visible=False
        )
        return fig
    except Exception:
        return None

def fetch_real_institutional(stock_id):
    """🌟 波段升級版：固定抓取並解算最近 5 個交易日的三大法人詳細買賣超數據 """
    today = datetime.date.today()
    # 往前推 20 天，確保扣除週休二日與連續假期後，一定能湊滿 5 個真正的交易日
    start_date = (today - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    
    is_etf = len(str(stock_id).strip()) == 5
    dataset = "TaiwanStockInstitutionalInvestorsBuySell" if not is_etf else "TaiwanEeceptInvestorsBuySell"
    
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "end_date": end_date
    }
    
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if data.get("msg") == "success" and len(data.get("data", [])) > 0:
            df = pd.DataFrame(data["data"])
            
            # 統一個股與 ETF 的法人欄位名稱對應字典
            name_mapping = {
                "Foreign_Investor_Buy_and_Sell": "外資",
                "Investment_Trust_Buy_and_Sell": "投信",
                "Dealer_Buy_and_Sell": "自營商",
                "外資及陸資買賣超股數": "外資",
                "投信買賣超股數": "投信",
                "自營商買賣超股數": "自營商"
            }
            
            # 如果是個股，FinMind 欄位是 'name'；如果是 ETF，有時是別的欄位，做防呆
            if 'name' in df.columns:
                df['法人'] = df['name'].map(lambda x: next((v for k, v in name_mapping.items() if k in str(x) or x in k), "其他"))
            else:
                return None, "籌碼資料格式解析失敗"
                
            # 計算買賣超張數 (FinMind 預設是股數，除以 1000 換算為張)
            df['張數'] = (df['buy'] - df['sell']) // 1000
            
            # 依照日期與法人進行資料透視 (Pivot Table)，把不同法人的數據擠在同一行
            pivot_df = df.pivot_table(index='date', columns='法人', values='張數', aggfunc='sum').reset_index()
            
            # 確保外資、投信、自營商三個欄位都有出來，沒有的話補 0
            for col in ["外資", "投信", "自營商"]:
                if col not in pivot_df.columns:
                    pivot_df[col] = 0
            
            # 只要保留這三個核心法人，並按日期降序排列（最新的在最上面）
            result_df = pivot_df[['date', '外資', '投信', '自營商']].sort_values(by='date', ascending=False)
            
            # 精準切出最近的 5 個交易日
            final_df = result_df.head(5).copy()
            final_df.rename(columns={'date': '交易日期'}, inplace=True)
            
            return final_df, "OK"
    except Exception as e:
        print(f"籌碼解析異常: {e}")
        
    return None, "網路連線或資料庫重整中"

def fetch_real_news(stock_id):
    is_etf = len(str(stock_id).strip()) == 5
    url_id = f"{stock_id}.TW" if is_etf else stock_id
    url = f"https://tw.stock.yahoo.com/quote/{url_id}/news"
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    news_list = []
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            title = a.text.strip()
            link = a['href']
            if len(title) > 10 and ('news' in link or 'article' in link):
                full_link = link if link.startswith("http") else f"https://tw.stock.yahoo.com{link}"
                if not any(n['title'] == title for n in news_list):
                    news_list.append({"title": title, "link": full_link})
            if len(news_list) >= 5: break
        return news_list if news_list else [{"title": "目前查無最新新聞或網頁改版", "link": url}]
    except Exception:
        return [{"title": "新聞抓取連線失敗", "link": "#"}]

def get_marquee_html():
    """🌟 聲量完全匹配版：跑馬燈放棄 Yahoo 排行，100% 同步顯示 PTT 挖出來的自選監控標的"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    # 🌟 核心匹配邏輯：直接讀取 PTT 掃描出來的自選監控清單
    dynamic_hot_list = list(st.session_state.monitored_stocks)
        
    # 防呆機制：如果一剛開始打開網頁，PTT 還沒掃描、清單太少時，用經典權值股與 ETF 填補畫面
    if len(dynamic_hot_list) < 5:
        backup_list = ["2330", "2317", "3231", "2603", "0050", "2382", "00878", "2618"]
        for b_sid in backup_list:
            if b_sid not in dynamic_hot_list:
                dynamic_hot_list.append(b_sid)

    # 限制跑馬燈最多顯示 12 檔最熱門的，避免過長
    dynamic_hot_list = dynamic_hot_list[:12]

    # 建立雙通道參數
    params_list = []
    for sid in dynamic_hot_list:
        params_list.append(f"tse_{sid}.tw")
        params_list.append(f"otc_{sid}.tw")
    params = "|".join(params_list)
    
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={params}&_={int(time.time() * 1000)}"

    try:
        res = requests.get(url, headers=headers, timeout=3).json()
        stocks = []
        if "msgArray" in res:
            seen_marquee_stocks = set()
            for info in res["msgArray"]:
                stock_id = info.get("c")
                if not info.get("n") or info.get("n") == "-" or stock_id in seen_marquee_stocks:
                    continue
                    
                # 三層價格防禦機制
                price_str = info.get("z", "").strip()
                if not price_str or price_str == "-":
                    price_str = info.get("b", "").split("_")[0].strip()
                if not price_str or price_str == "-":
                    price_str = info.get("y", "0").strip()

                price = float(price_str) if price_str and price_str != "-" else 0.0
                y_price = float(info.get("y", 0))
                vol = int(info.get("v", 0))

                # 黃金防護線
                if price == 0 or y_price == 0:
                    continue

                pct = ((price - y_price) / y_price) * 100

                stocks.append({"id": stock_id, "name": info.get("n"), "price": price, "pct": pct, "vol": vol})
                seen_marquee_stocks.add(stock_id)

        # 依據當下成交量，對 PTT 股票進行由大到小排序排序
        stocks.sort(key=lambda x: x['vol'], reverse=True)

        html_content = ""
        for s in stocks:
            if s['pct'] > 0: color, arrow = "#ff4b4b", "▲"
            elif s['pct'] < 0: color, arrow = "#00fa9a", "▼"
            else: color, arrow = "white", "-"
            
            # 點擊跑馬燈個股，右側一樣能完美連動切換深度分析
            html_content += f"<a href='/?target_stock={s['id']}' target='_self' style='text-decoration:none; color:{color}; margin-right: 40px; font-size: 18px; font-weight: bold;' title='聲量匹配標的。點擊分析 {s['name']}'>{s['name']} {s['price']} {arrow} {s['pct']:.2f}%</a>"

        return f"""
        <div style="background-color: #1E1E1E; padding: 12px; border-radius: 8px; border: 1px solid #333; margin-bottom: 20px;">
            <marquee behavior="scroll" direction="left" scrollamount="6" onmouseover="this.stop();" onmouseout="this.start();">
                <span style="color: #FFD700; margin-right: 40px; font-size: 18px; font-weight: bold; border-right: 2px solid #555; padding-right: 15px;">🔥 鄉民熱議即時行情</span>
                {html_content}
            </marquee>
        </div>
        """
    except Exception:
        return "<div style='color: gray;'>即時行情動態跑馬燈載入中...</div>"

def fetch_five_levels(stock_id):
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{stock_id}.tw|otc_{stock_id}.tw&_={int(time.time() * 1000)}"
    try:
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3).json()
        if "msgArray" in res and res["msgArray"]:
            info = res["msgArray"][0]
            bids = info.get('b', '').split('_')[:-1]
            bid_vols = info.get('g', '').split('_')[:-1]
            asks = info.get('a', '').split('_')[:-1]
            ask_vols = info.get('f', '').split('_')[:-1]
            
            bids += ['-'] * (5 - len(bids))
            bid_vols += ['-'] * (5 - len(bid_vols))
            asks += ['-'] * (5 - len(asks))
            ask_vols += ['-'] * (5 - len(ask_vols))
            
            return {
                "open": info.get('o', '-'),
                "high": info.get('h', '-'),
                "low": info.get('l', '-'),
                "bids": bids, "bid_vols": bid_vols,
                "asks": asks, "ask_vols": ask_vols
            }
    except Exception:
        pass
    return None

# ================= 網頁介面與排版 =================

if "target_stock" in st.query_params:
    target_id = st.query_params["target_stock"]
    if target_id:
        st.session_state.monitored_stocks.add(target_id)
        st.session_state.force_select_stock = target_id
    st.query_params.clear()

st.title("📈 決策支持系統 (DSS) - 聲量與量價分析儀表板")

# --- 側邊欄設定與警報中心 ---
st.sidebar.header("⚙️ 系統設定")
show_marquee = st.sidebar.checkbox("顯示上方即時跑馬燈", value=True)
auto_refresh = st.sidebar.checkbox("🔄 啟動 30 秒自動監控引擎", value=False)
st.sidebar.markdown("---")
alert_threshold = st.sidebar.slider("🚨 漲幅警報門檻 (%)", 1.0, 9.5, 3.0, step=0.5)
st.sidebar.markdown("---")
pages_to_crawl = st.sidebar.slider("PTT 掃描頁數", 1, 10, 3)
min_push = st.sidebar.number_input("觸發監控推文門檻", min_value=10, max_value=100, value=50)

st.sidebar.markdown("---")
st.sidebar.subheader("🚨 即時警報監控台")
if st.session_state.alert_history:
    st.sidebar.caption("點擊下方按鈕可快速查看該檔個股")
    for idx, alert in enumerate(st.session_state.alert_history):
        btn_label = f"[{alert['time']}] {alert['name']} 🚀 +{alert['pct']:.1f}%"
        if st.sidebar.button(btn_label, key=f"alert_btn_{alert['id']}_{idx}"):
            st.session_state.force_select_stock = alert['id']
            st.rerun()
else:
    st.sidebar.info("今日尚無觸發警報之標的")

# --- 主畫面佈局 ---
if show_marquee:
    st.markdown(get_marquee_html(), unsafe_allow_html=True)

current_time = time.time()
ptt_scan_interval = 3600

if auto_refresh and (current_time - st.session_state.last_ptt_scan >= ptt_scan_interval):
    st.session_state.ptt_results = scan_ptt_logic(pages_to_crawl, min_push)
    st.session_state.last_ptt_scan = current_time
    st.session_state.ptt_last_update_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

col_left, col_right = st.columns([1.2, 2])

with col_left:
    st.subheader("🔥 網路聲量監控 (PTT)")
    st.caption(f"聲量最後更新: {st.session_state.ptt_last_update_str}")

    if st.button("手動強制更新 PTT"):
        with st.spinner("正在爬取 PTT Stock 板..."):
            st.session_state.ptt_results = scan_ptt_logic(pages_to_crawl, min_push)
            st.session_state.last_ptt_scan = time.time()
            st.session_state.ptt_last_update_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            st.rerun()

    if st.session_state.ptt_results:
        st.dataframe(
            st.session_state.ptt_results,
            use_container_width=True,
            hide_index=True,
            column_config={
                "文章網址": st.column_config.LinkColumn("前往原文", display_text="👉 點擊閱讀", width="small")
            }
        )
    else:
        st.info("目前無符合條件之標的文。")

    st.markdown("---")
    st.subheader("📊 即時量價總表")
    st.caption(f"量價最後更新: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (每 30 秒刷新)")
    
    twse_data = fetch_twse_realtime(st.session_state.monitored_stocks, alert_threshold)
    if twse_data:
        st.dataframe(twse_data, use_container_width=True, hide_index=True)
    else:
        st.write("目前無監控中的股票。")

    if st.button("🗑️ 清空即時監控總表"):
        st.session_state.monitored_stocks.clear()
        st.session_state.stock_ma_notes.clear() 
        st.session_state.alerted_stocks.clear()
        st.rerun()

with col_right:
    st.subheader("🎯 互動式個股深度分析")

    if st.session_state.monitored_stocks:
        monitored_list = list(st.session_state.monitored_stocks)
        
        default_index = 0
        if 'force_select_stock' in st.session_state and st.session_state.force_select_stock in monitored_list:
            default_index = monitored_list.index(st.session_state.force_select_stock)
            del st.session_state.force_select_stock 

        selected_stock = st.selectbox(
            "請選擇要深入分析的標的：",
            monitored_list,
            index=default_index,
            format_func=lambda x: f"{x} {st.session_state.stock_names.get(x, '')} {st.session_state.stock_ma_notes.get(x, '')}"
        )

        tab1, tab2, tab3, tab4 = st.tabs(["📈 K線與技術面", "🏦 法人真實籌碼", "📰 相關即時新聞", "⚡ 即時量價明細"])

        with tab1:
            time_frame = st.radio("選擇 K 線線圖週期：", ["日線", "週線", "月線", "季線"], horizontal=True)
            timeframe_mapping = {
                "日線": {"period": "3mo", "interval": "1d"},
                "週線": {"period": "1y", "interval": "1wk"},
                "月線": {"period": "3y", "interval": "1mo"},
                "季線": {"period": "10y", "interval": "3mo"}
            }
            cfg = timeframe_mapping[time_frame]
            with st.spinner(f"載入 {time_frame} 資料中..."):
                fig = fetch_kline_chart(selected_stock, cfg["period"], cfg["interval"], time_frame)
                if fig: st.plotly_chart(fig, use_container_width=True)
                else: st.warning("無法取得該週期的歷史 K 線資料。")

        with tab2:
            with st.spinner("向 FinMind 請求近 5 日真實籌碼中..."):
                chip_df, status = fetch_real_institutional(selected_stock)
                
                if status == "OK" and chip_df is not None:
                    st.markdown(f"##### 📊 {selected_stock} 近 5 個交易日主力籌碼趨勢明細 (單位: 張)")
                    st.caption("💡 正數代表買超 (主力吸籌)，負數代表賣超 (主力出貨)。")
                    
                    # 使用 Streamlit 內建的高級表格元件展示 5 日數據
                    st.dataframe(
                        chip_df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "交易日期": st.column_config.TextColumn("交易日期", width="medium"),
                            "外資": st.column_config.NumberColumn("外資買賣超", format="%d 張"),
                            "投信": st.column_config.NumberColumn("投信買賣超", format="%d 張"),
                            "自營商": st.column_config.NumberColumn("自營商買賣超", format="%d 張")
                        }
                    )
                else:
                    st.info(f"📅 提示：{status}。盤中 16:00 前若查無資料，系統會自動遞補展示前 5 日的歷史結算數據。")

        with tab3:
            with st.spinner("📰 正在即時抓取 Yahoo 股市新聞..."):
                news_list = fetch_real_news(selected_stock)
                st.markdown(f"**{selected_stock} 最新市場消息：**")
                for news in news_list: st.markdown(f"- [{news['title']}]({news['link']})")
                st.caption("資料來源：Yahoo 奇摩股市")

        with tab4:
            st.subheader("⚡ 即時五檔報價與量價明細")
            with st.spinner("獲取即時五檔報價中..."):
                level_data = fetch_five_levels(selected_stock)
                current_info = next((item for item in twse_data if item["代號"] == selected_stock), None)
                
                if level_data and current_info:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("開盤價", level_data['open'])
                    m2.metric("最高價", level_data['high'])
                    m3.metric("最低價", level_data['low'])
                    m4.metric("總成交量", f"{current_info['成交量']} 張")
                    
                    st.write("") 
                    c_bid, c_ask = st.columns(2)
                    
                    with c_bid:
                        st.markdown("##### 🔽 委買 (Bids)")
                        bid_df = pd.DataFrame({"買價": level_data['bids'], "委買張數": level_data['bid_vols']})
                        st.dataframe(bid_df, use_container_width=True, hide_index=True)
                        
                    with c_ask:
                        st.markdown("##### 🔼 委賣 (Asks)")
                        ask_df = pd.DataFrame({"賣價": level_data['asks'], "委賣張數": level_data['ask_vols']})
                        st.dataframe(ask_df, use_container_width=True, hide_index=True)
                else:
                    st.info("目前無法取得該檔股票的五檔報價資訊（可能未在盤中或 API 延遲）。")
    else:
        st.info("👈 請先在左側取得標的，或等待系統自動掃描。")

# ================= 30 秒自動刷新機制 =================
if auto_refresh:
    time.sleep(30)
    st.rerun()
