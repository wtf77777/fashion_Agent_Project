import streamlit as st
import google.generativeai as genai
import requests
import json
from PIL import Image
import io
from supabase import create_client, Client
import base64
from datetime import datetime
import hashlib
import time
import re

# --- 1. 核心功能函數 ---

def rate_limit_protection():
    """API 速率限制保護 - 確保不超過 RPM"""
    current_time = time.time()
    time_since_last = current_time - st.session_state.last_request_time
    
    # 如果距離上次請求不到 15 秒，就等待
    if time_since_last < 15:
        wait_time = 15 - time_since_last
        with st.spinner(f"⏳ API 速率保護中，等待 {int(wait_time)} 秒..."):
            time.sleep(wait_time)
    
    st.session_state.last_request_time = time.time()

def get_image_hash(img_bytes):
    """計算圖片的 SHA256 hash 值"""
    return hashlib.sha256(img_bytes).hexdigest()

def check_duplicate_image(img_hash):
    """檢查圖片是否已存在"""
    try:
        result = st.session_state.supabase_client.table("my_wardrobe")\
            .select("id, name")\
            .eq("user_id", st.session_state.user_id)\
            .eq("image_hash", img_hash)\
            .execute()
        
        if result.data:
            return True, result.data[0]['name']
        return False, None
    except Exception as e:
        st.error(f"檢查重複失敗: {str(e)}")
        return False, None

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
    except Exception as e:
        st.error(f"天氣獲取失敗: {str(e)}")
        return None

def handle_city_change():
    """當城市選單變動時立刻執行的邏輯"""
    # 透過 key 取得目前選單的值
    new_display_name = st.session_state.city_selector
    new_city_eng = TAIWAN_CITIES[new_display_name]
    
    # 立刻更新狀態，這樣頂部重新執行時就會拿到新城市
    st.session_state.selected_city = new_city_eng
    st.session_state.weather_data = None  # 強制清除舊天氣以重新抓取
    
def auto_tagging(img_bytes, api_key):
    """AI 自動標籤衣服 - 單張模式"""
    try:
        rate_limit_protection()
        
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
        
        clean_text = response.text.strip()
        clean_text = clean_text.replace('```json', '').replace('```', '').strip()
        
        tags = json.loads(clean_text)
        
        required_fields = ['name', 'category', 'color', 'warmth']
        for field in required_fields:
            if field not in tags:
                raise ValueError(f"缺少必要欄位: {field}")
        
        tags['warmth'] = int(tags['warmth'])
        
        return tags
        
    except json.JSONDecodeError as e:
        st.error(f"AI 回應格式錯誤,無法解析 JSON: {str(e)}")
        if 'response' in locals():
            st.code(response.text)
        return None
    except Exception as e:
        st.error(f"AI 標籤失敗: {str(e)}")
        return None

def batch_auto_tagging(img_bytes_list, api_key):
    """批量 AI 自動標籤 - 一次分析多張衣服"""
    try:
        rate_limit_protection()
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        content_parts = [f"""請仔細分析這 {len(img_bytes_list)} 件衣服，為每件衣服分別回傳 JSON 格式的標籤。

回傳格式必須是一個 JSON 陣列，包含 {len(img_bytes_list)} 個物件:
[
  {{
    "name": "衣服名稱(如:白色T恤、牛仔褲)",
    "category": "上衣|下身|外套|鞋子|配件",
    "color": "主要顏色",
    "style": "風格(如:休閒、正式、運動)",
    "warmth": 保暖度1-10的數字
  }},
  ... (依序對應每張圖片)
]

重要規則:
1. 只回傳 JSON 陣列，不要任何其他文字
2. 不要包含 ```json 或任何 Markdown 標籤
3. 陣列中的順序必須與圖片順序一致
4. 每個物件都必須包含所有 5 個欄位
"""]
        
        for img_bytes in img_bytes_list:
            content_parts.append({
                "mime_type": "image/jpeg",
                "data": img_bytes
            })
        
        response = model.generate_content(content_parts)
        
        clean_text = response.text.strip()
        clean_text = clean_text.replace('```json', '').replace('```', '').strip()
        
        tags_list = json.loads(clean_text)
        
        if not isinstance(tags_list, list):
            raise ValueError("AI 回傳格式錯誤: 應為陣列")
        
        if len(tags_list) != len(img_bytes_list):
            raise ValueError(f"AI 回傳數量不符: 預期 {len(img_bytes_list)} 件，實際 {len(tags_list)} 件")
        
        required_fields = ['name', 'category', 'color', 'warmth']
        for idx, tags in enumerate(tags_list):
            for field in required_fields:
                if field not in tags:
                    raise ValueError(f"第 {idx+1} 件衣服缺少必要欄位: {field}")
            
            tags['warmth'] = int(tags['warmth'])
        
        return tags_list
        
    except json.JSONDecodeError as e:
        st.error(f"AI 回應格式錯誤，無法解析 JSON: {str(e)}")
        if 'response' in locals():
            st.code(response.text)
        return None
    except Exception as e:
        st.error(f"批量 AI 標籤失敗: {str(e)}")
        return None

def save_to_supabase(tags, img_bytes, img_hash):
    """儲存衣服資料到 Supabase"""
    try:
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
        
        data = {
            **tags,
            "image_data": img_base64,
            "image_hash": img_hash,
            "user_id": st.session_state.user_id,
            "created_at": datetime.now().isoformat()
        }
        
        result = st.session_state.supabase_client.table("my_wardrobe").insert(data).execute()
        return True, result.data
        
    except Exception as e:
        return False, str(e)

def get_wardrobe():
    """從 Supabase 讀取該使用者的衣櫥"""
    try:
        response = st.session_state.supabase_client.table("my_wardrobe")\
            .select("*")\
            .eq("user_id", st.session_state.user_id)\
            .order("created_at", desc=True)\
            .execute()
        return response.data
    except Exception as e:
        st.error(f"讀取衣櫥失敗: {str(e)}")
        return []

def delete_item(item_id):
    """刪除衣服"""
    try:
        st.session_state.supabase_client.table("my_wardrobe")\
            .delete()\
            .eq("id", item_id)\
            .eq("user_id", st.session_state.user_id)\
            .execute()
        return True
    except Exception as e:
        st.error(f"刪除失敗: {str(e)}")
        return False

def batch_delete_items(item_ids):
    """批量刪除衣服"""
    try:
        success_count = 0
        fail_count = 0
        
        for item_id in item_ids:
            try:
                st.session_state.supabase_client.table("my_wardrobe")\
                    .delete()\
                    .eq("id", item_id)\
                    .eq("user_id", st.session_state.user_id)\
                    .execute()
                success_count += 1
            except:
                fail_count += 1
        
        return True, success_count, fail_count
    except Exception as e:
        return False, 0, 0

def login_user(username, password):
    """使用者登入"""
    try:
        result = st.session_state.supabase_client.table("users")\
            .select("*")\
            .eq("username", username)\
            .eq("password", password)\
            .execute()
        
        if result.data:
            return True, result.data[0]['id']
        else:
            return False, "帳號或密碼錯誤"
            
    except Exception as e:
        return False, str(e)

def register_user(username, password):
    """使用者註冊"""
    try:
        existing = st.session_state.supabase_client.table("users")\
            .select("id")\
            .eq("username", username)\
            .execute()
        
        if existing.data:
            return False, "使用者名稱已存在"
        
        result = st.session_state.supabase_client.table("users")\
            .insert({"username": username, "password": password})\
            .execute()
        
        return True, result.data
        
    except Exception as e:
        return False, str(e)

def parse_outfit_recommendation(ai_response, wardrobe):
    """
    解析 AI 推薦文字，提取推薦的衣服 ID
    使用模糊匹配找出 AI 推薦的衣服對應到衣櫥中的實際物品
    """
    recommended_items = []
    
    # 將 AI 回應轉成小寫以便比對
    response_lower = ai_response.lower()
    
    # 遍歷衣櫥中的每件衣服
    for item in wardrobe:
        item_name = item.get('name', '').lower()
        item_category = item.get('category', '').lower()
        item_color = item.get('color', '').lower()
        
        # 檢查 AI 回應中是否提到這件衣服
        # 使用名稱、類別、顏色進行匹配
        if (item_name and item_name in response_lower) or \
           (item_color and item_category and f"{item_color}{item_category}" in response_lower.replace(' ', '')):
            recommended_items.append(item)
    
    return recommended_items

def generate_outfit_image(recommended_items, weather_info, api_key):
    """
    使用 Gemini 2.5 的原生多模態能力生成穿搭圖片
    """
    try:
        rate_limit_protection()
        genai.configure(api_key=api_key)
        
        # 1. 使用支援圖像生成的模型名稱
        model = genai.GenerativeModel('( gemini-3-pro')

        # 2. 構建英文 Prompt
        item_desc = ", ".join([f"{i.get('color','')} {i.get('name','')}" for i in recommended_items])
        prompt = (
            f"Generate a high-quality fashion photo of a person wearing: {item_desc}. "
            f"The overall style is {weather_info.get('selected_style', 'modern')}. "
            f"Environment: A stylish street in {weather_info.get('city', 'Taipei')}. "
            f"Lighting: Soft daylight. High resolution, 8k, realistic."
        )

        # 3. 呼叫 API (指定輸出包含 IMAGE)
        with st.spinner("🎨 Gemini 正在為您繪製穿搭圖..."):
            response = model.generate_content(
                prompt,
                generation_config={"response_modalities": ["TEXT", "IMAGE"]}
            )

            # 4. 解析回傳的圖片
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'inline_data'):
                    img_data = part.inline_data.data
                    return Image.open(io.BytesIO(img_data)), prompt
            
            return None, prompt # 若沒產出圖片則退回文字模式
            
    except Exception as e:
        st.error(f"圖像生成失敗: {str(e)}")
        return None, None


# --- 2. 初始化設定 ---
st.set_page_config(page_title="2026 AI 時尚顧問 (雲端版)", page_icon="☁️")
st.markdown("""
    <style>
    /* 隱藏頂部所有圖標 (Share, Star, GitHub 等) */
    header[data-testid="stHeader"] {
        visibility: hidden;
        display: none;
    }
    /* 修正頂部空白 */
    .block-container {
        padding-top: 2rem;
    }
    
    /* ✨ 新增：回到頂端按鈕 */
    .scroll-to-top {
        position: fixed;
        bottom: 20px;
        left: 20px;
        z-index: 9999;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 50%;
        width: 60px;
        height: 60px;
        cursor: pointer;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3);
        font-size: 24px;
        transition: all 0.3s ease;
        display: flex;
        align-items: center;
        justify-content: center;
        text-decoration: none;
    }
    .scroll-to-top:hover {
        transform: translateY(-5px);
        box-shadow: 0 6px 20px rgba(0,0,0,0.4);
        background: linear-gradient(135deg, #764ba2 0%, #667eea 100%);
    }
    .scroll-to-top:active {
        transform: translateY(-2px);
    }
    </style>
    
    <a href="#ai-agent-cloud" class="scroll-to-top" title="回到頂端">⬆️</a>
    """, unsafe_allow_html=True)

st.title("👗 AI 個人穿搭 Agent (Cloud)")

# 初始化 session state
if 'supabase_client' not in st.session_state:
    st.session_state.supabase_client = None
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'username' not in st.session_state:
    st.session_state.username = None
if 'last_request_time' not in st.session_state:
    st.session_state.last_request_time = 0
if 'weather_data' not in st.session_state:
    st.session_state.weather_data = None
if 'weather_update_time' not in st.session_state:
    st.session_state.weather_update_time = None
if 'selected_city' not in st.session_state:
    st.session_state.selected_city = None

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

# 台灣城市資料
TAIWAN_CITIES = {
    "台北 (Taipei)": "Taipei",
    "新北 (New Taipei)": "New Taipei",
    "桃園 (Taoyuan)": "Taoyuan",
    "台中 (Taichung)": "Taichung",
    "台南 (Tainan)": "Tainan",
    "高雄 (Kaohsiung)": "Kaohsiung",
    "基隆 (Keelung)": "Keelung",
    "新竹 (Hsinchu)": "Hsinchu",
    "苗栗 (Miaoli)": "Miaoli",
    "彰化 (Changhua)": "Changhua",
    "南投 (Nantou)": "Nantou",
    "雲林 (Yunlin)": "Yunlin",
    "嘉義 (Chiayi)": "Chiayi",
    "屏東 (Pingtung)": "Pingtung",
    "宜蘭 (Yilan)": "Yilan",
    "花蓮 (Hualien)": "Hualien",
    "台東 (Taitung)": "Taitung",
    "澎湖 (Penghu)": "Penghu",
    "金門 (Kinmen)": "Kinmen",
    "馬祖 (Matsu)": "Matsu"
}

# ✨ 找出預設城市顯示值
default_city_display = "台北 (Taipei)"
for display, english in TAIWAN_CITIES.items():
    if english.lower() == default_city.lower():
        default_city_display = display
        break

# ✨ 初始化選中的城市（避免 None 錯誤）
if st.session_state.selected_city is None:
    st.session_state.selected_city = default_city

# ✨ 天氣資訊顯示區域（紅框位置）
if st.session_state.user_id and weather_key:
    weather_container = st.container()
    with weather_container:
        # 自動更新天氣
        from datetime import datetime, timedelta
        now = datetime.now()
        need_update = False
        
        # 使用當前選中的城市
        current_city = st.session_state.selected_city
        
        if st.session_state.weather_data is None:
            need_update = True
        elif st.session_state.weather_update_time is None:
            need_update = True
        elif (now - st.session_state.weather_update_time) > timedelta(hours=1):
            need_update = True
        
        if need_update:
            try:
                weather = get_weather(current_city, weather_key)
                if weather:
                    st.session_state.weather_data = weather
                    st.session_state.weather_update_time = now
            except Exception as e:
                st.error(f"天氣更新失敗: {str(e)}")
        
        # 顯示天氣資訊
        if st.session_state.weather_data:
            weather = st.session_state.weather_data
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
            with col1:
                st.markdown(f"### 📡 {current_city} 即時天氣")
            with col2:
                st.metric("🌡️ 溫度", f"{weather['temp']}°C")
            with col3:
                st.metric("🤔 體感", f"{weather['feels_like']}°C")
            with col4:
                st.metric("☁️", weather['desc'])
            
            if st.session_state.weather_update_time:
                update_time = st.session_state.weather_update_time.strftime("%H:%M")
                st.caption(f"更新時間: {update_time} (每小時自動更新)")
            
            st.divider()

with st.sidebar:
    st.header("🔑 API 設定")
    
    if use_secrets:
        st.success("✅ 使用雲端設定")
        st.caption("API Keys 已從安全儲存區載入")
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
    
    # ⚠️ Gemini API 限制提醒
    with st.expander("⚡ Gemini API 使用提醒", expanded=False):
        st.success("""
        **批量模式優勢：**
        - ✅ 10 張圖 = 1 次 API 呼叫
        - ✅ 大幅減少 RPM 限制風險
        - ✅ 處理速度提升 10 倍
        - 建議每批 5-10 張
        """)
    
    # ✨ 只在登入後顯示使用者資訊和登出按鈕
    if st.session_state.user_id:
        st.divider()
        st.success(f"👤 目前使用者: **{st.session_state.username}**")
        
        st.divider()
        if st.button("🚪 登出", use_container_width=True):
            st.session_state.user_id = None
            st.session_state.username = None
            st.session_state.weather_data = None
            st.session_state.weather_update_time = None
            st.session_state.selected_city = None
            st.rerun()

# 設定當前使用的城市（登入後用選擇的，未登入用預設台北）
city = st.session_state.selected_city if st.session_state.selected_city else default_city



# --- 3. 介面操作 ---

# ✨ 初始化選中的城市為預設台北
if st.session_state.selected_city is None:
    st.session_state.selected_city = default_city

# 登入/註冊系統
if not st.session_state.user_id:
    st.info("👋 請先登入或註冊以使用個人衣櫥")
    
    tab_login, tab_register = st.tabs(["🔑 登入", "📝 註冊"])
    
    with tab_login:
        with st.form("login_form"):
            st.subheader("登入帳號")
            login_username = st.text_input("使用者名稱", key="login_user")
            login_password = st.text_input("密碼", type="password", key="login_pass")
            login_button = st.form_submit_button("登入", use_container_width=True)
            
            if login_button:
                if not st.session_state.supabase_client:
                    st.error("請先在左側設定 Supabase 連接")
                elif not login_username or not login_password:
                    st.warning("請輸入使用者名稱和密碼")
                else:
                    success, result = login_user(login_username, login_password)
                    if success:
                        st.session_state.user_id = result
                        st.session_state.username = login_username
                        st.success(f"歡迎回來, {login_username}! 🎉")
                        st.rerun()
                    else:
                        st.error(f"登入失敗: {result}")
    
    with tab_register:
        with st.form("register_form"):
            st.subheader("註冊新帳號")
            reg_username = st.text_input("使用者名稱", key="reg_user")
            reg_password = st.text_input("密碼", type="password", key="reg_pass")
            reg_password2 = st.text_input("確認密碼", type="password", key="reg_pass2")
            register_button = st.form_submit_button("註冊", use_container_width=True)
            
            if register_button:
                if not st.session_state.supabase_client:
                    st.error("請先在左側設定 Supabase 連接")
                elif not reg_username or not reg_password:
                    st.warning("請輸入使用者名稱和密碼")
                elif reg_password != reg_password2:
                    st.error("兩次密碼輸入不一致")
                elif len(reg_password) < 6:
                    st.warning("密碼至少需要 6 個字元")
                else:
                    success, result = register_user(reg_username, reg_password)
                    if success:
                        st.success("註冊成功! 請登入 ✅")
                    else:
                        st.error(f"註冊失敗: {result}")
    
    st.stop()

# 檢查必要設定
def check_setup(need_weather=False):
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
    
    upload_mode = st.radio("上傳模式", ["單張上傳", "批量上傳 (推薦)"], horizontal=True)
    
    if upload_mode == "單張上傳":
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
                    
                    with st.spinner("正在檢查重複..."):
                        img_byte_arr = io.BytesIO()
                        img.save(img_byte_arr, format='JPEG')
                        img_bytes = img_byte_arr.getvalue()
                        img_hash = get_image_hash(img_bytes)
                        
                        is_duplicate, existing_name = check_duplicate_image(img_hash)
                        
                        if is_duplicate:
                            st.warning(f"⚠️ 這件衣服已存在: **{existing_name}**")
                            st.info("💡 請上傳不同的衣服照片")
                            st.stop()
                    
                    with st.spinner("AI 正在分析衣服..."):
                        tags = auto_tagging(img_bytes, google_key)
                        
                        if tags:
                            st.success("✅ AI 辨識完成!")
                            st.json(tags)
                            
                            with st.spinner("正在存入雲端..."):
                                success, result = save_to_supabase(tags, img_bytes, img_hash)
                                
                                if success:
                                    st.success(f"🎉 已存入雲端: **{tags['name']}**")
                                    st.balloons()
                                else:
                                    st.error(f"存入失敗: {result}")
    
    else:
        uploaded_files = st.file_uploader(
            "選取多張衣服照片（建議 5-10 張最佳）...", 
            type=["jpg", "png", "jpeg"],
            accept_multiple_files=True
        )
        
        if uploaded_files:
            if len(uploaded_files) > 20:
                st.error(f"❌ 一次最多只能上傳 20 張照片，您選擇了 {len(uploaded_files)} 張")
                st.info("💡 請重新選擇不超過 20 張照片")
                st.stop()
            
            st.success(f"✅ 已選擇 {len(uploaded_files)} 張照片")
            
            with st.expander("📷 預覽所有照片", expanded=True):
                cols = st.columns(4)
                for idx, file in enumerate(uploaded_files):
                    with cols[idx % 4]:
                        img = Image.open(file)
                        st.image(img, caption=file.name, use_container_width=True)
            
            if st.button("🚀 批量辨識並上傳全部", type="primary", use_container_width=True):
                if not check_setup():
                    st.stop()
                
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                status_text.text("📦 正在準備圖片資料...")
                img_data_list = []
                img_hash_list = []
                file_names = []
                duplicate_count = 0
                
                for file in uploaded_files:
                    img = Image.open(file)
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG')
                    img_bytes = img_byte_arr.getvalue()
                    img_hash = get_image_hash(img_bytes)
                    
                    is_duplicate, existing_name = check_duplicate_image(img_hash)
                    if is_duplicate:
                        duplicate_count += 1
                        st.warning(f"⚠️ {file.name} 重複 (已存在: {existing_name})")
                        continue
                    
                    img_data_list.append(img_bytes)
                    img_hash_list.append(img_hash)
                    file_names.append(file.name)
                
                if not img_data_list:
                    st.warning("所有圖片都已存在，沒有新圖片需要上傳")
                    st.stop()
                
                progress_bar.progress(0.3)
                status_text.text(f"🤖 AI 正在批量分析 {len(img_data_list)} 件衣服...")
                st.info(f"⚡ 批量模式：{len(img_data_list)} 張圖片只需 1 次 API 呼叫（約 20-40 秒）")
                
                tags_list = batch_auto_tagging(img_data_list, google_key)
                
                if not tags_list:
                    st.error("❌ 批量辨識失敗，請重試")
                    st.stop()
                
                st.success(f"✅ AI 辨識完成! 共 {len(tags_list)} 件衣服")
                
                progress_bar.progress(0.6)
                status_text.text("💾 正在存入資料庫...")
                
                success_count = 0
                fail_count = 0
                
                for idx, (tags, img_bytes, img_hash, file_name) in enumerate(zip(tags_list, img_data_list, img_hash_list, file_names)):
                    progress = 0.6 + 0.4 * (idx + 1) / len(img_data_list)
                    progress_bar.progress(progress)
                    status_text.text(f"正在存入: {file_name} ({idx + 1}/{len(img_data_list)})")
                    
                    try:
                        success, result = save_to_supabase(tags, img_bytes, img_hash)
                        
                        if success:
                            success_count += 1
                            st.success(f"✅ {file_name} → {tags['name']}")
                        else:
                            fail_count += 1
                            st.error(f"❌ {file_name} 存入失敗: {result}")
                    
                    except Exception as e:
                        fail_count += 1
                        st.error(f"❌ {file_name} 處理失敗: {str(e)}")
                
                progress_bar.progress(1.0)
                status_text.empty()
                
                st.divider()
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("📊 處理數", len(img_data_list))
                with col2:
                    st.metric("✅ 成功", success_count)
                with col3:
                    st.metric("⚠️ 重複", duplicate_count)
                with col4:
                    st.metric("❌ 失敗", fail_count)
                
                if success_count > 0:
                    st.balloons()
                    st.success(f"🎉 批量上傳完成！成功 {success_count} 件")
                    st.info("💡 批量模式大幅減少 API 呼叫次數，避免 RPM 限制！")
    
    st.divider()
    st.info("""
    **📌 使用提示:**
    1. 拍攝清晰的單件衣服照片
    2. 背景簡潔有助於 AI 辨識
    3. **✨ 批量上傳模式: 5-10 張最佳** (只需 1 次 API 呼叫)
    4. 系統會自動過濾重複的衣服
    5. 批量模式速度提升 10 倍，避免 RPM 限制
    """)

with tab2:
    st.header("我的雲端衣櫥")
    
    if not check_setup():
        st.stop()
    
    col1, col2 = st.columns([3, 1])
    with col1:
        if st.button("🔄 重新整理", use_container_width=True):
            st.rerun()
    with col2:
        if 'batch_delete_mode' not in st.session_state:
            st.session_state.batch_delete_mode = False
        
        if st.button("🗑️ 批量刪除" if not st.session_state.batch_delete_mode else "✅ 完成", 
                     use_container_width=True,
                     type="secondary" if not st.session_state.batch_delete_mode else "primary"):
            st.session_state.batch_delete_mode = not st.session_state.batch_delete_mode
            if 'selected_items' in st.session_state:
                del st.session_state.selected_items
            st.rerun()
    
    items = get_wardrobe()
    
    if items:
        st.write(f"共有 **{len(items)}** 件衣服")
        
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
        
        if st.session_state.batch_delete_mode:
            st.warning("🗑️ 批量刪除模式：勾選要刪除的衣服")
            
            if 'selected_items' not in st.session_state:
                st.session_state.selected_items = []
            
            col1, col2, col3 = st.columns([1, 1, 4])
            with col1:
                if st.button("☑️ 全選", use_container_width=True):
                    st.session_state.selected_items = [item['id'] for item in items]
                    st.rerun()
            with col2:
                if st.button("⬜ 取消", use_container_width=True):
                    st.session_state.selected_items = []
                    st.rerun()
            with col3:
                if st.session_state.selected_items:
                    if st.button(f"🗑️ 刪除選中的 {len(st.session_state.selected_items)} 件", 
                                 type="primary", 
                                 use_container_width=True):
                        success, success_count, fail_count = batch_delete_items(st.session_state.selected_items)
                        if success:
                            st.success(f"✅ 已刪除 {success_count} 件衣服")
                            if fail_count > 0:
                                st.warning(f"⚠️ {fail_count} 件刪除失敗")
                            st.session_state.selected_items = []
                            st.session_state.batch_delete_mode = False
                            time.sleep(1)
                            st.rerun()
            
            st.divider()
        
        cols = st.columns(3)
        for idx, item in enumerate(items):
            with cols[idx % 3]:
                with st.container(border=True):
                    if st.session_state.batch_delete_mode:
                        is_selected = item['id'] in st.session_state.selected_items
                        if st.checkbox("選擇", value=is_selected, key=f"check_{item['id']}"):
                            if item['id'] not in st.session_state.selected_items:
                                st.session_state.selected_items.append(item['id'])
                        else:
                            if item['id'] in st.session_state.selected_items:
                                st.session_state.selected_items.remove(item['id'])
                    
                    if 'image_data' in item and item['image_data']:
                        try:
                            img_bytes = base64.b64decode(item['image_data'])
                            img = Image.open(io.BytesIO(img_bytes))
                            st.image(img, use_container_width=True)
                        except:
                            st.write("🖼️ 圖片載入失敗")
                    
                    st.subheader(item.get('name', '未命名'))
                    st.write(f"**類別:** {item.get('category', 'N/A')}")
                    st.write(f"**顏色:** {item.get('color', 'N/A')}")
                    st.write(f"**風格:** {item.get('style', 'N/A')}")
                    st.write(f"**保暖度:** {'🔥' * item.get('warmth', 0)}")
                    
                    if not st.session_state.batch_delete_mode:
                        if st.button("🗑️ 刪除", key=f"del_{item.get('id')}", use_container_width=True):
                            if delete_item(item.get('id')):
                                st.success("已刪除")
                                st.rerun()
    else:
        st.info("目前衣櫥是空的,去上傳一些衣服吧! 👕")

with tab3:
    st.header("今日穿搭推薦")
    
    # ✨ 初始化 session state 來保存 AI 推薦結果
    if 'ai_recommendation' not in st.session_state:
        st.session_state.ai_recommendation = None
    if 'recommended_items_cache' not in st.session_state:
        st.session_state.recommended_items_cache = None
    if 'current_weather' not in st.session_state:
        st.session_state.current_weather = None
    if 'current_style' not in st.session_state:
        st.session_state.current_style = None
    
    # ✨ 城市設定區域
    with st.expander("🌍 城市設定", expanded=True):
        city_display = st.selectbox(
            "選擇城市", 
            options=list(TAIWAN_CITIES.keys()),
            index=list(TAIWAN_CITIES.keys()).index(default_city_display),
            help="選擇台灣縣市以獲取天氣資訊",
            key="city_selector",
            on_change=handle_city_change
        )
        
        st.caption(f"📍 當前城市: **{st.session_state.selected_city}**")
    
    st.divider()
    
    # 🆕 風格選單
    style_options = {
        "🇫🇷 法式優雅": "法式優雅風格，強調輕鬆隨性但精緻的搭配，色調以黑白灰為主，搭配經典單品如條紋衫、西裝外套、牛仔褲",
        "🇺🇸 美式休閒": "美式休閒風格，強調舒適實用，常見單品包括 T-shirt、牛仔褲、運動鞋、棒球帽",
        "🇬🇧 英倫紳士": "英倫紳士風格，講究剪裁與質感，常見格紋、西裝、風衣、皮鞋等正式單品",
        "🎨 巴洛克華麗": "巴洛克風格，強調誇張華麗、金色裝飾、刺繡、蕾絲等繁複元素",
        "👣 地雷系": "日系地雷系風格，以黑色為主，搭配蕾絲、蝴蝶結、哥德元素，呈現病嬌可愛感",
        "🎮 宅男舒適": "宅男風格，強調舒適實用，寬鬆T恤、運動褲、帽T、球鞋為主",
        "🌊 潮流街頭": "街頭潮流風格，強調個性與品牌，oversize、球鞋、帽子、配件等",
        "💼 都會型男": "都會型男風格，簡約俐落，商務休閒兼具，重視品質與細節",
        "🏃 運動機能": "運動機能風格，強調功能性與科技感，防水、透氣、速乾材質",
        "🌸 韓系清新": "韓系風格，色調柔和，oversized、寬鬆剪裁、淺色系為主",
        "🎭 暗黑哥德": "哥德風格，以黑色為主，皮革、鉚釘、十字架等元素，神秘感十足",
        "🌈 多元混搭": "不限定風格，AI 將根據天氣與衣櫥自由搭配出創意組合"
    }
    
    selected_style = st.selectbox(
        "🎨 選擇穿搭風格",
        options=list(style_options.keys()),
        index=11,
        help="選擇您想要的穿搭風格，AI 會根據此風格進行推薦"
    )
    
    style_description = style_options[selected_style]
    
    with st.expander("ℹ️ 風格說明", expanded=False):
        st.info(style_description)
    
    # ✅ 獲取推薦按鈕
    if st.button("✨ 獲取今日推薦", type="primary", use_container_width=True):
        # 清除舊的推薦
        st.session_state.ai_recommendation = None
        st.session_state.recommended_items_cache = None
        st.session_state.carousel_index = 0
        
        if not check_setup(need_weather=True):
            st.stop()
        
        current_city = st.session_state.selected_city if st.session_state.selected_city else default_city
        
        if st.session_state.weather_data is None:
            with st.spinner("正在查詢天氣..."):
                weather = get_weather(current_city, weather_key)
                if weather:
                    st.session_state.weather_data = weather
                    st.session_state.weather_update_time = datetime.now()
        
        weather = st.session_state.weather_data
        
        with st.spinner("正在讀取衣櫥..."):
            wardrobe = get_wardrobe()
        
        if not weather:
            st.error("無法獲取天氣資訊")
            st.stop()
        
        if not wardrobe:
            st.warning("衣櫥是空的,請先上傳一些衣服!")
            st.stop()
        
        st.divider()
        
        wardrobe_summary = [
            {k: v for k, v in item.items() if k != 'image_data'}
            for item in wardrobe
        ]
        
        with st.spinner("AI 時尚顧問正在為您搭配..."):
            try:
                rate_limit_protection()
                
                genai.configure(api_key=google_key)
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                prompt = f"""
                你是一位專業的 AI 時尚顧問。請根據以下資訊推薦今日穿搭:
                
                **天氣資訊:**
                - 城市: {current_city}
                - 溫度: {weather['temp']}°C (體感 {weather['feels_like']}°C)
                - 天氣: {weather['desc']}
                
                **指定風格:**
                {selected_style}
                {style_description}
                
                **使用者衣櫥:**
                {json.dumps(wardrobe_summary, ensure_ascii=False, indent=2)}
                
                **請提供:**
                1. 推薦的完整穿搭組合 (從頭到腳)，必須符合指定的「{selected_style}」風格
                2. 每件單品的選擇理由 (考慮天氣與風格)
                3. 整體風格說明 (如何體現{selected_style}的特色)
                4. 搭配小技巧 (針對此風格的進階建議)
                
                請用親切、專業的口吻回答,使用繁體中文。
                """
                
                response = model.generate_content(prompt)
                
                st.session_state.ai_recommendation = response.text
                st.session_state.current_weather = weather
                st.session_state.current_style = selected_style
                
                st.rerun()
                
            except Exception as e:
                st.error(f"AI 推薦失敗: {str(e)}")
                if "quota" in str(e).lower() or "limit" in str(e).lower():
                    st.warning("💡 可能是 API 額度用完或超過速率限制，請稍後再試")
    
    # ✅ 顯示已有的推薦結果（不重新呼叫 AI）
    if st.session_state.ai_recommendation:
        st.markdown("### 🎨 今日穿搭建議")
        st.markdown(f"**風格主題:** {st.session_state.current_style}")
        st.divider()
        st.markdown(st.session_state.ai_recommendation)
        
        st.divider()
        
        # ✨ 推薦單品展示
        st.markdown("### 👔 推薦單品展示")
        
        if st.session_state.recommended_items_cache is None:
            wardrobe = get_wardrobe()
            st.session_state.recommended_items_cache = parse_outfit_recommendation(
                st.session_state.ai_recommendation, 
                wardrobe
            )
        
        recommended_items = st.session_state.recommended_items_cache
        
        if recommended_items:
            st.markdown("""
                <style>
                .carousel-container {
                    position: relative;
                    width: 100%;
                    overflow: hidden;
                    border-radius: 15px;
                    background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                    padding: 20px;
                    margin: 20px 0;
                }
                .carousel-indicator {
                    text-align: center;
                    color: #667eea;
                    font-weight: bold;
                    font-size: 18px;
                    margin: 10px 0;
                }
                </style>
            """, unsafe_allow_html=True)
            
            if 'carousel_index' not in st.session_state:
                st.session_state.carousel_index = 0
            
            col1, col2, col3 = st.columns([1, 2, 1])
            
            with col1:
                if st.button("⬅️ 上一件", key="prev_item", use_container_width=True):
                    st.session_state.carousel_index = (st.session_state.carousel_index - 1) % len(recommended_items)
                    st.rerun()
            
            with col2:
                st.markdown(f"<div class='carousel-indicator'>第 {st.session_state.carousel_index + 1} / {len(recommended_items)} 件</div>", unsafe_allow_html=True)
            
            with col3:
                if st.button("下一件 ➡️", key="next_item", use_container_width=True):
                    st.session_state.carousel_index = (st.session_state.carousel_index + 1) % len(recommended_items)
                    st.rerun()
            
            current_item = recommended_items[st.session_state.carousel_index]
            
            with st.container():
                st.markdown("<div class='carousel-container'>", unsafe_allow_html=True)
                
                col_img, col_info = st.columns([3, 2])
                
                with col_img:
                    if 'image_data' in current_item and current_item['image_data']:
                        try:
                            img_bytes = base64.b64decode(current_item['image_data'])
                            img = Image.open(io.BytesIO(img_bytes))
                            st.image(img, use_container_width=True)
                        except:
                            st.error("🖼️ 圖片載入失敗")
                    else:
                        st.info("📷 無圖片資料")
                
                with col_info:
                    st.markdown("### 📋 單品資訊")
                    st.markdown(f"**名稱**: {current_item.get('name', '未命名')}")
                    st.markdown(f"**類別**: {current_item.get('category', 'N/A')}")
                    st.markdown(f"**顏色**: {current_item.get('color', 'N/A')}")
                    st.markdown(f"**風格**: {current_item.get('style', 'N/A')}")
                    st.markdown(f"**保暖度**: {'🔥' * current_item.get('warmth', 0)}")
                
                st.markdown("</div>", unsafe_allow_html=True)
            
            st.markdown("---")
            quick_nav_cols = st.columns(len(recommended_items))
            for idx, col in enumerate(quick_nav_cols):
                with col:
                    emoji = "🔵" if idx == st.session_state.carousel_index else "⚪"
                    if st.button(f"{emoji}", key=f"nav_{idx}", use_container_width=True):
                        st.session_state.carousel_index = idx
                        st.rerun()
        
        else:
            st.info("💡 AI 推薦的衣物未在您的衣櫥中找到對應圖片")
        
        st.divider()
        
        st.markdown("### 🎭 穿搭視覺化")
        
        with st.spinner("正在生成穿搭示意圖..."):
            outfit_image, image_prompt = generate_outfit_image(
                recommended_items if recommended_items else [], 
                st.session_state.current_weather, 
                google_key
            )
            
                # 準備傳入參數
            weather['selected_style'] = selected_style
            weather['city'] = current_city

                # 呼叫剛修改好的生成函數
            outfit_image, image_prompt = generate_outfit_image(recommended_items, weather, google_key)
                
            if outfit_image:
                    # ✅ 如果有圖片，直接顯示
                st.image(outfit_image, caption="Gemini 2.5 生成的穿搭參考", use_container_width=True)
                    
                    # 製作下載按鈕
                buf = io.BytesIO()
                outfit_image.save(buf, format="PNG")
                st.download_button("📥 下載穿搭圖", buf.getvalue(), "outfit.png", "image/png")
            else:
                    # ❌ 如果沒有圖片，顯示原本的文字描述與說明
                if image_prompt:
                    st.info(f"📝 圖像描述已生成：\n{image_prompt}")
                    st.warning("提示：目前模型未回傳圖片，您可以複製上方描述至 DALL-E 3 生成。")
                    if st.button("📋 複製圖像描述", key="copy_btn"):
                        st.code(image_prompt)
        
        st.success("穿搭推薦完成! 祝您有美好的一天 ✨")
    
    st.divider()
    st.info("""
    **💡 推薦功能說明:**
    - 結合即時天氣與您的衣櫥
    - 考慮 2026 流行趨勢
    - 提供個人化穿搭建議
    - ✨ 顯示推薦衣服的實際圖片
    - ✨ 生成穿搭人物圖像描述
    - 使用 Gemini 2.5 Flash 模型
    """)
st.divider()
with st.expander("📋 Supabase 資料表結構說明"):
    st.code("""
-- 使用者資料表
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 衣櫥資料表
CREATE TABLE my_wardrobe (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    color TEXT NOT NULL,
    style TEXT,
    warmth INTEGER CHECK (warmth >= 1 AND warmth <= 10),
    image_data TEXT,
    image_hash TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 建立 hash 索引以加速重複檢查
CREATE INDEX idx_wardrobe_hash ON my_wardrobe(user_id, image_hash);
    """, language="sql")

st.caption("Made with ❤️ by AI Fashion Agent v2.0 | Powered by Gemini 2.0 Flash & Supabase")








