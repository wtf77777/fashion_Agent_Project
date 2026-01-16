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

# --- 1. 頁面基礎配置 ---
st.set_page_config(
    page_title="AI Mirror | 智慧衣櫥顧問",
    page_icon="🧥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 套用雜誌風格 CSS
st.markdown("""
    <style>
    .stApp { background-color: #fcfcfc; }
    .main-header { font-size: 2.5rem; font-weight: 800; color: #1e1e1e; margin-bottom: 0.5rem; letter-spacing: -1px; }
    .sub-header { font-size: 1.1rem; color: #666; margin-bottom: 2rem; }
    div[data-testid="stMetricValue"] { font-size: 1.8rem; }
    .stTabs [data-baseweb="tab-list"] { gap: 20px; }
    .stTabs [data-baseweb="tab"] { font-weight: 600; border-radius: 8px 8px 0 0; }
    .wardrobe-card { border-radius: 12px; border: 1px solid #eee; padding: 15px; background: white; transition: 0.3s; }
    .wardrobe-card:hover { box-shadow: 0 10px 20px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

# --- 2. 核心初始化 ---
if 'last_request_time' not in st.session_state:
    st.session_state.last_request_time = 0
if 'user' not in st.session_state:
    st.session_state.user = None

def rate_limit_protection():
    current_time = time.time()
    time_since_last = current_time - st.session_state.last_request_time
    if time_since_last < 10: # 設為 10 秒保護
        wait_time = 10 - time_since_last
        time.sleep(wait_time)
    st.session_state.last_request_time = time.time()

def get_image_hash(img_bytes):
    return hashlib.sha256(img_bytes).hexdigest()

def get_weather(city):
    try:
        url = f"https://wttr.in/{city}?format=j1"
        res = requests.get(url, timeout=5)
        data = res.json()
        curr = data['current_condition'][0]
        return {"temp": curr['temp_C'], "desc": curr['weatherDesc'][0]['value'], "humidity": curr['humidity']}
    except:
        return {"temp": "22", "desc": "晴朗", "humidity": "50"}

# --- 3. 側邊欄與資料庫連線 ---
with st.sidebar:
    st.title("👗 AI Mirror Architect")
    st.markdown("---")
    api_key = st.text_input("Gemini API Key", type="password")
    sb_url = st.text_input("Supabase URL")
    sb_key = st.text_input("Supabase Key", type="password")
    
    if api_key and sb_url and sb_key:
        genai.configure(api_key=api_key)
        if 'supabase_client' not in st.session_state:
            st.session_state.supabase_client = create_client(sb_url, sb_key)
        st.success("✅ 服務已連線")
    
    if st.session_state.user:
        st.markdown(f"**當前使用者：{st.session_state.user['username']}**")
        if st.button("登出"):
            st.session_state.user = None
            st.rerun()

# --- 4. 登入/註冊邏輯 (完整功能) ---
if not st.session_state.user:
    st.markdown('<div class="main-header">Welcome to AI Mirror</div>', unsafe_allow_html=True)
    tab_auth1, tab_auth2 = st.tabs(["🔑 登入系統", "📝 註冊帳號"])
    
    with tab_auth1:
        u = st.text_input("帳號", key="l_u")
        p = st.text_input("密碼", type="password", key="l_p")
        if st.button("登入", use_container_width=True):
            res = st.session_state.supabase_client.table("users").select("*").eq("username", u).eq("password", p).execute()
            if res.data:
                st.session_state.user = res.data[0]
                st.rerun()
            else:
                st.error("帳號或密碼錯誤")
    
    with tab_auth2:
        nu = st.text_input("新帳號", key="r_u")
        np = st.text_input("新密碼", type="password", key="r_p")
        if st.button("註冊", use_container_width=True):
            try:
                st.session_state.supabase_client.table("users").insert({"username": nu, "password": np}).execute()
                st.success("註冊成功，請切換至登入頁面")
            except:
                st.error("此帳號已被註冊")
    st.stop()

# --- 5. 主功能介面 ---
st.markdown(f'<div class="main-header">今日靈感, {st.session_state.user["username"]}</div>', unsafe_allow_html=True)
main_tabs = st.tabs(["👔 我的衣櫥", "✨ 穿搭推薦", "📤 批次上傳"])

# --- TAB: 我的衣櫥 (網格與過濾) ---
with main_tabs[0]:
    try:
        res = st.session_state.supabase_client.table("my_wardrobe").select("*").eq("user_id", st.session_state.user["id"]).execute()
        items = res.data
        if not items:
            st.info("衣櫥空空的，去上傳一些衣服吧！")
        else:
            cats = ["全部"] + list(set([i['category'] for i in items]))
            sel_cat = st.segmented_control("類別過濾", cats, default="全部")
            filtered = items if sel_cat == "全部" else [i for i in items if i['category'] == sel_cat]
            
            cols = st.columns(4)
            for idx, item in enumerate(filtered):
                with cols[idx % 4]:
                    with st.container(border=True):
                        st.image(f"data:image/png;base64,{item['image_data']}", use_container_width=True)
                        st.markdown(f"**{item['name']}**")
                        st.caption(f"{item['style']} | {item['color']}")
                        if st.button("刪除", key=f"del_{item['id']}", size="small"):
                            st.session_state.supabase_client.table("my_wardrobe").delete().eq("id", item['id']).execute()
                            st.rerun()
    except Exception as e:
        st.error(f"獲取資料失敗: {e}")

# --- TAB: 穿搭推薦 (核心 AI 邏輯) ---
with main_tabs[1]:
    col_set1, col_set2 = st.columns(2)
    with col_set1:
        city_input = st.text_input("📍 城市", value="台北")
    with col_set2:
        pref_style = st.selectbox("🎯 風格", ["韓系清新", "美式街頭", "英倫紳士", "日系工裝", "商務正式"])
    
    if st.button("🪄 生成今日穿搭雜誌", type="primary", use_container_width=True):
        weather = get_weather(city_input)
        res = st.session_state.supabase_client.table("my_wardrobe").select("*").eq("user_id", st.session_state.user["id"]).execute()
        wardrobe = res.data
        
        if len(wardrobe) < 2:
            st.warning("衣服太少啦，至少需要兩件單品。")
        else:
            with st.status("正在運算...", expanded=True) as status:
                wardrobe_txt = "\n".join([f"- {i['name']} ({i['category']}, {i['color']}, {i['style']})" for i in wardrobe])
                model = genai.GenerativeModel('gemini-2.0-flash-exp') # 使用 2.0 版本
                
                prompt = f"""
                你是 AI 時尚顧問。請根據以下資訊給出穿搭建議。
                城市：{city_input}，天氣：{weather['temp']}度，{weather['desc']}
                偏好風格：{pref_style}
                擁有的衣物清單：{wardrobe_txt}
                
                請嚴格輸出 JSON 格式：
                {{
                  "theme": "建議的主題名稱",
                  "reason": "詳細的搭配理由",
                  "items": ["建議清單中的單品1", "建議清單中的單品2"],
                  "visual_description": "描述此穿搭的人物插圖(英文)"
                }}
                """
                rate_limit_protection()
                response = model.generate_content(prompt)
                try:
                    data = json.loads(re.search(r'\{.*\}', response.text, re.DOTALL).group())
                    
                    # 執行圖片生成 (Gemini 2.0 特色功能)
                    img_gen_prompt = f"A fashion editorial photo of a person wearing: {data['visual_description']}. High quality, magazine style."
                    gen_response = model.generate_content(img_gen_prompt)
                    
                    # 檢查是否有二進位圖片回傳 (原邏輯)
                    generated_img = None
                    for part in gen_response.candidates[0].content.parts:
                        if hasattr(part, 'inline_data'):
                            generated_img = part.inline_data.data
                    
                    status.update(label="✅ 生成完成", state="complete")
                    
                    # UI 展示
                    st.divider()
                    res_l, res_r = st.columns([1.2, 1])
                    with res_l:
                        st.subheader(f"🎨 {data['theme']}")
                        if generated_img:
                            st.image(generated_img, use_container_width=True, caption="AI 生成穿搭視覺圖")
                        else:
                            st.info("AI 正在繪製示意圖中 (或目前模型不支援繪圖)...")
                            st.caption(f"視覺描述: {data['visual_description']}")
                    
                    with res_r:
                        st.markdown("#### 💡 搭配思維")
                        st.write(data['reason'])
                        st.markdown("#### 🧥 推薦單品")
                        for item_name in data['items']:
                            match = next((i for i in wardrobe if i['name'] in item_name), None)
                            with st.container(border=True):
                                if match:
                                    c_a, c_b = st.columns([1, 3])
                                    c_a.image(f"data:image/png;base64,{match['image_data']}", width=70)
                                    c_b.write(f"**{match['name']}**")
                                    c_b.caption(match['category'])
                                else:
                                    st.write(f"• {item_name}")
                except Exception as e:
                    st.error(f"解析失敗: {e}")

# --- TAB: 批次上傳 (完整 AI 識別) ---
with main_tabs[2]:
    st.markdown("### 📸 智慧辨識上傳")
    uploaded_files = st.file_uploader("請選擇多張衣物圖片", type=['jpg','png','jpeg'], accept_multiple_files=True)
    
    if uploaded_files and st.button("🚀 開始自動化存檔"):
        model = genai.GenerativeModel('gemini-1.5-flash') # 上傳分析用 1.5 速度較快
        for f in uploaded_files:
            img_bytes = f.read()
            img_hash = get_image_hash(img_bytes)
            
            # 去重檢查
            check = st.session_state.supabase_client.table("my_wardrobe").select("id")\
                    .eq("user_id", st.session_state.user["id"]).eq("image_hash", img_hash).execute()
            if check.data:
                st.warning(f"跳過：{f.name} 已存在於衣櫥。")
                continue
            
            with st.spinner(f"AI 辨識中: {f.name}"):
                pil_img = Image.open(io.BytesIO(img_bytes))
                rate_limit_protection()
                ana_prompt = "請分析圖片中的衣物，回傳 JSON：{\"name\": \"品名\", \"category\": \"類別(上衣/下身/外套/鞋子)\", \"color\": \"顏色\", \"style\": \"風格\", \"warmth\": 1-5}"
                response = model.generate_content([ana_prompt, pil_img])
                tags = json.loads(re.search(r'\{.*\}', response.text, re.DOTALL).group())
                
                # 存入資料庫
                st.session_state.supabase_client.table("my_wardrobe").insert({
                    "user_id": st.session_state.user["id"],
                    "name": tags['name'],
                    "category": tags['category'],
                    "color": tags['color'],
                    "style": tags['style'],
                    "image_data": base64.b64encode(img_bytes).decode(),
                    "image_hash": img_hash
                }).execute()
                st.success(f"✅ 已加入衣櫥：{tags['name']}")
        st.rerun()

st.markdown("---")
st.caption("AI Agent Full-stack Architect Designed for Future Fashion.")
