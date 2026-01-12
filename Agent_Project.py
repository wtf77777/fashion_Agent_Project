import streamlit as st
import google.generativeai as genai
import requests
import json
from PIL import Image
import io
from supabase import create_client, Client
import base64
from datetime import datetime

# --- 1. 初始化設定 ---
st.set_page_config(page_title="2026 AI 時尚顧問 (雲端版)", page_icon="☁️")
st.title("👗 AI 個人穿搭 Agent (Cloud)")

# 初始化 session state
if 'supabase_client' not in st.session_state:
    st.session_state.supabase_client = None

# 嘗試從 Streamlit Secrets 讀取設定
try:
    google_key = st.secrets.get("GEMINI_KEY", "")
    weather_key = st.secrets.get("WEATHER_KEY", "")
    supabase_url = st.secrets.get("SUPABASE_URL", "")
    supabase_key = st.secrets.get("SUPABASE_KEY", "")
    default_city = st.secrets.get("DEFAULT_CITY", "Taipei")
    use_secrets = True
except:
    use_secrets = False
    google_key = ""
    weather_key = ""
    supabase_url = ""
    supabase_key = ""
    default_city = "Taipei"

with st.sidebar:
    st.header("🔑 API 設定")
    
    if use_secrets:
        st.success("✅ 使用雲端設定")
        st.caption("API Keys 已從安全儲存區載入")
        
        # 讓使用者可以選擇覆寫
        if st.checkbox("🔧 手動覆寫設定"):
            google_key = st.text_input("Gemini API Key", value=google_key, type="password")
            weather_key = st.text_input("OpenWeather Key", value=weather_key, type="password")
            supabase_url = st.text_input("Supabase URL", value=supabase_url)
            supabase_key = st.text_input("Supabase Anon Key", value=supabase_key, type="password")
    else:
        st.info("💡 本地模式: 請輸入 API Keys")
        google_key = st.text_input("Gemini API Key", type="password", help="前往 Google AI Studio 取得")
        weather_key = st.text_input("OpenWeather Key", type="password", help="前往 openweathermap.org 註冊")
        
        st.divider()
        st.subheader("☁️ Supabase 設定")
        supabase_url = st.text_input("Supabase URL", help="格式: https://xxx.supabase.co")
        supabase_key = st.text_input("Supabase Anon Key", type="password")
    
    # 連接 Supabase
    if supabase_url and supabase_key:
        try:
            st.session_state.supabase_client = create_client(supabase_url, supabase_key)
            if not use_secrets:
                st.success("✅ Supabase 已連接")
        except Exception as e:
            st.error(f"❌ Supabase 連接失敗: {str(e)}")
    
    st.divider()
    city = st.text_input("城市名稱", value=default_city, help="英文城市名,如: Tokyo, London")

# --- 2. 核心功能函數 ---

def get_weather(city, api_key):
    """獲取天氣資訊"""
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric&lang=zh_tw"
        res = requests.get(url, timeout=5).json()
        
        if 'main' in res:
            return {
                "temp": round(res['main']['temp'], 1),
                "desc": res['weather'][0]['description'],
                "feels_like": round(res['main']['feels_like'], 1)
            }
        else:
            st.error(f"天氣 API 錯誤: {res.get('message', '未知錯誤')}")
            return None
    except requests.exceptions.Timeout:
        st.error("天氣 API 請求超時")
        return None
    except Exception as e:
        st.error(f"天氣獲取失敗: {str(e)}")
        return None

def auto_tagging(img_bytes, api_key):
    """AI 自動標籤衣服"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        prompt = """請仔細分析這件衣服,回傳純 JSON 格式(不要包含 ```json 或任何 Markdown 標籤):
        {
            "name": "衣服名稱(如:白色T恤、牛仔褲)",
            "category": "上衣|下身|外套|鞋子|配件",
            "color": "主要顏色",
            "style": "風格(如:休閒、正式、運動)",
            "warmth": 保暖度1-10的數字
        }
        只回傳 JSON,不要其他文字。"""
        
        response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": img_bytes}])
        
        # 清理回應文字
        clean_text = response.text.strip()
        clean_text = clean_text.replace('```json', '').replace('```', '').strip()
        
        # 解析 JSON
        tags = json.loads(clean_text)
        
        # 驗證必要欄位
        required_fields = ['name', 'category', 'color', 'warmth']
        for field in required_fields:
            if field not in tags:
                raise ValueError(f"缺少必要欄位: {field}")
        
        # 確保 warmth 是整數
        tags['warmth'] = int(tags['warmth'])
        
        return tags
        
    except json.JSONDecodeError as e:
        st.error(f"AI 回應格式錯誤,無法解析 JSON: {str(e)}")
        st.code(response.text)  # 顯示原始回應供除錯
        return None
    except Exception as e:
        st.error(f"AI 標籤失敗: {str(e)}")
        return None

def save_to_supabase(tags, img_bytes):
    """儲存衣服資料到 Supabase"""
    try:
        # 將圖片轉為 base64
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
        
        # 準備資料
        data = {
            **tags,
            "image_data": img_base64,
            "created_at": datetime.now().isoformat()
        }
        
        # 插入資料庫
        result = st.session_state.supabase_client.table("my_wardrobe").insert(data).execute()
        
        return True, result.data
        
    except Exception as e:
        return False, str(e)

def get_wardrobe():
    """從 Supabase 讀取衣櫥"""
    try:
        response = st.session_state.supabase_client.table("my_wardrobe").select("*").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        st.error(f"讀取衣櫥失敗: {str(e)}")
        return []

def delete_item(item_id):
    """刪除衣服"""
    try:
        st.session_state.supabase_client.table("my_wardrobe").delete().eq("id", item_id).execute()
        return True
    except Exception as e:
        st.error(f"刪除失敗: {str(e)}")
        return False

# --- 3. 介面操作 ---

# 檢查必要設定
def check_setup(need_weather=False):
    """檢查必要的 API 設定"""
    if not google_key:
        st.warning("⚠️ 請在左側輸入 Gemini API Key")
        return False
    if not st.session_state.supabase_client:
        st.warning("⚠️ 請在左側輸入 Supabase 設定")
        return False
    if need_weather and not weather_key:
        st.warning("⚠️ 請在左側輸入 OpenWeather API Key")
        return False
    return True

tab1, tab2, tab3 = st.tabs(["📸 上傳入庫", "👔 我的衣櫥", "💡 今日推薦"])

with tab1:
    st.header("上傳新衣到雲端")
    
    uploaded_file = st.file_uploader("選取衣服照片...", type=["jpg", "png", "jpeg"])
    
    if uploaded_file:
        img = Image.open(uploaded_file)
        
        col1, col2 = st.columns([1, 2])
        with col1:
            st.image(img, caption="預覽", use_container_width=True)
        
        with col2:
            if st.button("🤖 AI 辨識並存入資料庫", type="primary", use_container_width=True):
                if not check_setup():
                    st.stop()
                
                with st.spinner("AI 正在分析衣服..."):
                    # 轉換圖片
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG')
                    img_bytes = img_byte_arr.getvalue()
                    
                    # AI 辨識
                    tags = auto_tagging(img_bytes, google_key)
                    
                    if tags:
                        # 顯示辨識結果
                        st.success("✅ AI 辨識完成!")
                        st.json(tags)
                        
                        # 存入資料庫
                        with st.spinner("正在存入雲端..."):
                            success, result = save_to_supabase(tags, img_bytes)
                            
                            if success:
                                st.success(f"🎉 已存入雲端: **{tags['name']}**")
                                st.balloons()
                            else:
                                st.error(f"存入失敗: {result}")
    
    st.divider()
    st.info("""
    **📌 使用提示:**
    1. 拍攝清晰的單件衣服照片
    2. 背景簡潔有助於 AI 辨識
    3. 確保照片光線充足
    """)

with tab2:
    st.header("我的雲端衣櫥")
    
    if not check_setup():
        st.stop()
    
    if st.button("🔄 重新整理", use_container_width=True):
        st.rerun()
    
    items = get_wardrobe()
    
    if items:
        st.write(f"共有 **{len(items)}** 件衣服")
        
        # 分類統計
        categories = {}
        for item in items:
            cat = item.get('category', '其他')
            categories[cat] = categories.get(cat, 0) + 1
        
        col1, col2, col3, col4 = st.columns(4)
        cols = [col1, col2, col3, col4]
        for i, (cat, count) in enumerate(categories.items()):
            with cols[i % 4]:
                st.metric(cat, count)
        
        st.divider()
        
        # 顯示衣服卡片
        cols = st.columns(3)
        for idx, item in enumerate(items):
            with cols[idx % 3]:
                with st.container(border=True):
                    # 顯示圖片
                    if 'image_data' in item and item['image_data']:
                        try:
                            img_bytes = base64.b64decode(item['image_data'])
                            img = Image.open(io.BytesIO(img_bytes))
                            st.image(img, use_container_width=True)
                        except:
                            st.write("🖼️ 圖片載入失敗")
                    
                    # 顯示資訊
                    st.subheader(item.get('name', '未命名'))
                    st.write(f"**類別:** {item.get('category', 'N/A')}")
                    st.write(f"**顏色:** {item.get('color', 'N/A')}")
                    st.write(f"**風格:** {item.get('style', 'N/A')}")
                    st.write(f"**保暖度:** {'🔥' * item.get('warmth', 0)}")
                    
                    # 刪除按鈕
                    if st.button("🗑️ 刪除", key=f"del_{item.get('id')}", use_container_width=True):
                        if delete_item(item.get('id')):
                            st.success("已刪除")
                            st.rerun()
    else:
        st.info("目前衣櫥是空的,去上傳一些衣服吧! 👕")

with tab3:
    st.header("今日穿搭推薦")
    
    if st.button("✨ 獲取今日推薦", type="primary", use_container_width=True):
        if not check_setup(need_weather=True):
            st.stop()
        
        with st.spinner("正在查詢天氣..."):
            weather = get_weather(city, weather_key)
        
        with st.spinner("正在讀取衣櫥..."):
            wardrobe = get_wardrobe()
        
        if not weather:
            st.error("無法獲取天氣資訊,請檢查城市名稱和 API Key")
            st.stop()
        
        if not wardrobe:
            st.warning("衣櫥是空的,請先上傳一些衣服!")
            st.stop()
        
        # 顯示天氣資訊
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("🌡️ 溫度", f"{weather['temp']}°C")
        with col2:
            st.metric("🤔 體感", f"{weather['feels_like']}°C")
        with col3:
            st.metric("☁️ 天氣", weather['desc'])
        
        st.divider()
        
        # 準備衣櫥資料 (不包含圖片 base64)
        wardrobe_summary = [
            {k: v for k, v in item.items() if k != 'image_data'}
            for item in wardrobe
        ]
        
        # AI 推薦
        with st.spinner("AI 時尚顧問正在為您搭配..."):
            try:
                genai.configure(api_key=google_key)
                model = genai.GenerativeModel('gemini-1.5-flash')
                
                prompt = f"""
                你是一位專業的 AI 時尚顧問。請根據以下資訊推薦今日穿搭:
                
                **天氣資訊:**
                - 城市: {city}
                - 溫度: {weather['temp']}°C (體感 {weather['feels_like']}°C)
                - 天氣: {weather['desc']}
                
                **2026 流行趨勢:**
                - 機能風格當道
                - 雲舞白、科技藍為主流色
                - 永續材質受歡迎
                
                **使用者衣櫥:**
                {json.dumps(wardrobe_summary, ensure_ascii=False, indent=2)}
                
                **請提供:**
                1. 推薦的完整穿搭組合 (從頭到腳)
                2. 每件單品的選擇理由
                3. 整體風格說明
                4. 搭配小技巧
                
                請用親切、專業的口吻回答,使用繁體中文。
                """
                
                response = model.generate_content(prompt)
                
                st.markdown("### 🎨 今日穿搭建議")
                st.markdown(response.text)
                
                st.success("穿搭推薦完成! 祝您有美好的一天 ✨")
                
            except Exception as e:
                st.error(f"AI 推薦失敗: {str(e)}")
    
    st.divider()
    st.info("""
    **💡 推薦功能說明:**
    - 結合即時天氣與您的衣櫥
    - 考慮 2026 流行趨勢
    - 提供個人化穿搭建議
    """)

# --- 4. 底部資訊 ---
st.divider()
with st.expander("📋 Supabase 資料表結構說明"):
    st.code("""
CREATE TABLE my_wardrobe (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    color TEXT NOT NULL,
    style TEXT,
    warmth INTEGER CHECK (warmth >= 1 AND warmth <= 10),
    image_data TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
    """, language="sql")
    st.caption("請在 Supabase 中建立此資料表")

st.caption("Made with ❤️ by AI Fashion Agent | Powered by Gemini & Supabase")