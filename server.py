from flask import Flask, request
import requests
from bs4 import BeautifulSoup
import os
from dotenv import load_dotenv

# ✅ 先讀 env（只需一次）
load_dotenv()

from supabase import create_client
from google import genai

# ✅ 取得環境變數
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)

# ✅ Gemini client（只初始化一次）
client = genai.Client(api_key=GEMINI_API_KEY)

# ⭐️ 新增：將模型名稱獨立成變數，以後要換模型只要改這裡
MODEL_ID = "gemini-2.5-flash-lite"

# ✅ debug 區塊
print("🔥 程式啟動了")
print("LINE TOKEN 是否存在:", bool(LINE_TOKEN))
print("GEMINI KEY 是否存在:", bool(GEMINI_API_KEY))
print("SUPABASE URL 是否存在:", bool(os.getenv("SUPABASE_URL")))
print("SUPABASE KEY 是否存在:", bool(os.getenv("SUPABASE_ANON_KEY")))

# ⭐️ 新增：開機自動測試 Gemini 2.5 連線
try:
    print("⏳ 正在測試 Gemini 2.5 連線...")
    response = client.models.generate_content(
        model=MODEL_ID,
        contents="這是一個測試連線，請回覆：『Gemini 2.5 已上線！』"
    )
    print(f"✅ Gemini 連線測試成功！回覆內容：{response.text}")
except Exception as e:
    print(f"❌ Gemini 啟動失敗，錯誤訊息: {e}")

app = Flask(__name__)

# ==============================================================
# 🔧 強化版 OG 抓取
# 修正：
#   1. User-Agent 改為完整瀏覽器字串，避免被網站擋掉
#   2. 加入 Accept-Language，讓部分網站回傳正確語言內容
#   3. allow_redirects=True 處理短網址跳轉
#   4. timeout 從 5 秒拉長到 10 秒
#   5. 同時支援 property 和 name 屬性的 OG 標籤
#   6. 一次抓完 HTML 同時處理 OG + HTML fallback，避免重複請求
# ==============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def fetch_soup(url):
    """統一抓取網頁，回傳 (soup, final_url)，失敗回傳 (None, url)"""
    try:
        res = requests.get(
            url,
            headers=HEADERS,
            timeout=10,
            allow_redirects=True  # 處理短網址、跳轉
        )
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        return soup, res.url  # res.url 是最終跳轉後的網址
    except Exception as e:
        print(f"❌ fetch_soup 失敗 ({url}):", e)
        return None, url


def extract_content(url):
    """
    解析網頁內容，優先順序：
    0️⃣ FB / IG → 直接 fallback（抓不到）
    1️⃣ OG 標籤
    2️⃣ HTML title + <p> 段落
    3️⃣ 全部失敗 → ai_only
    """

    # 0️⃣ 社群平台直接 fallback
    if any(domain in url for domain in ["facebook.com", "fb.watch", "instagram.com"]):
        return {
            "title": "社群貼文",
            "content": url,
            "type": "social",
            "url": url,
            "source": "fallback"
        }

    # 一次抓網頁，同時用於 OG 和 HTML fallback（避免重複請求）
    soup, final_url = fetch_soup(url)

    if soup:
        # 1️⃣ 嘗試 OG 標籤
        def get_meta(attr, value):
            """同時支援 property="og:xxx" 和 name="og:xxx" 兩種寫法"""
            tag = soup.find("meta", {attr: value})
            return tag["content"].strip() if tag and tag.get("content") else None

        og_title = get_meta("property", "og:title") or get_meta("name", "og:title")
        og_desc  = get_meta("property", "og:description") or get_meta("name", "og:description")
        og_type  = get_meta("property", "og:type") or get_meta("name", "og:type")
        og_url   = get_meta("property", "og:url") or get_meta("name", "og:url")

        if og_title or og_desc:
            print(f"✅ OG 解析成功：{og_title}")
            return {
                "title": og_title,
                "content": og_desc,
                "type": og_type,
                "url": og_url if og_url else final_url,
                "source": "og"
            }

        # 2️⃣ HTML fallback（同一個 soup，不重新抓）
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        paragraphs = soup.find_all("p")
        content = " ".join([p.get_text(strip=True) for p in paragraphs[:5]])
        print(f"⚠️ OG 失敗，使用 HTML fallback：{title}")
        return {
            "title": title,
            "content": content,
            "type": "website",
            "url": final_url,
            "source": "html"
        }

    # 3️⃣ 完全失敗
    print(f"❌ 無法抓取網頁：{url}")
    return {
        "title": None,
        "content": url,
        "type": "unknown",
        "url": url,
        "source": "ai_only"
    }


# ==============================================================
# 🔧 Gemini 關鍵字生成
# 修正：模型名稱改為 "gemini-2.0-flash"
# ==============================================================

def get_keywords(title, content, content_type=""):
    try:
        prompt = f"""請根據以下資訊，產生5個「可搜尋用關鍵字」（用逗號分隔）

規則：
- 必須是分類詞（例如：房地產、投資、AI工具）
- 不要句子
- 每個2~6字
- 以實用為主
- 只輸出關鍵字，不要任何說明文字

【標題】
{title or "無"}

【摘要（高權重）】
{(content or "")[:1000]}

【類型】
{content_type or "無"}"""

        response = client.models.generate_content(
            model="gemini-2.0-flash",  # ✅ 修正：原本 "gemini-3-flash-preview" 不存在
            contents=prompt
        )

        print("👉 Gemini 回傳:", response)

        if hasattr(response, "text") and response.text:
            return response.text.strip()

        if hasattr(response, "candidates"):
            return response.candidates[0].content.parts[0].text.strip()

        return "無法產生關鍵字"

    except Exception as e:
        print("❌ Gemini 錯誤:", e)
        if "429" in str(e):
            return "AI請求過多，請稍後再試"
        return "AI暫時無法使用"


# ==============================================================
# LINE 回覆
# ==============================================================

def reply_message(reply_token, text):
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    body = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }
    res = requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=headers,
        json=body
    )
    print("👉 LINE status:", res.status_code)
    print("👉 LINE response:", res.text)


# ==============================================================
# Webhook
# ==============================================================

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json()
        print("📩 收到 webhook:", data)

        events = data.get("events", [])

        for event in events:
            if event["type"] != "message":
                continue

            user_id = event["source"]["userId"]
            user_text = event["message"]["text"]
            reply_token = event["replyToken"]

            # 🟢 ① /search
            if user_text.startswith("/search"):
                keyword = user_text.replace("/search", "").strip()
                results = search_bookmarks(user_id, keyword)

                if not results:
                    reply = f"找不到「{keyword}」相關資料 😢"
                else:
                    reply = f"找到 {len(results)} 筆：\n\n"
                    for i, item in enumerate(results[:5]):
                        reply += f"{i+1}️⃣ {item['title']}\n🔗 {item['url']}\n\n"

                reply_message(reply_token, reply)
                continue

            # 🟣 ② /edit
            elif user_text.startswith("/edit"):
                new_keywords = user_text.replace("/edit", "").strip()
                success = update_latest_keywords(user_id, new_keywords)
                reply = f"已更新關鍵字為：{new_keywords} ✅" if success else "更新失敗（可能還沒有資料）😢"
                reply_message(reply_token, reply)
                continue

            # 🟡 ③ 網址處理
            elif user_text.startswith("http"):
                url = user_text.strip()
                try:
                    data = extract_content(url)

                    title        = data.get("title") or ""
                    content      = data.get("content") or ""
                    content_type = data.get("type") or ""
                    final_url    = data.get("url") or url
                    source       = data.get("source") or ""

                    keywords = get_keywords(title, content, content_type)

                    save_to_supabase(user_id, final_url, title, keywords, source)

                    reply = f"已收藏 ✅\n🔗 {final_url}\n📄 {title or '（無標題）'}\n🏷 {keywords}"

                except Exception as e:
                    print("❌ 分析錯誤:", e)
                    import traceback
                    traceback.print_exc()
                    reply = "分析失敗，請稍後再試 😢"

                reply_message(reply_token, reply)
                continue

            # 🔴 ④ 其他
            else:
                reply_message(reply_token, "請傳送網址或輸入 /search 關鍵字 😊")
                continue

        return "OK", 200

    except Exception as e:
        print("❌ webhook 錯誤:", str(e))
        import traceback
        traceback.print_exc()
        return "OK", 200


# ==============================================================
# /ping endpoint（防止 Render 冷啟動）
# 設定方式：用 cron-job.org 每 14 分鐘 GET 你的網址/ping
# ==============================================================

@app.route("/ping", methods=["GET"])
def ping():
    print("🏓 ping received")
    return "pong", 200


# ==============================================================
# Supabase 操作
# ==============================================================

def save_to_supabase(user_id, url, title, keywords, source_type="og"):
    try:
        print("🟡 準備寫入 Supabase")
        keywords_list = [
            k.strip()
            for k in keywords.replace("\n", ",").split(",")
            if k.strip()
        ]
        print("👉 keywords_list:", keywords_list)

        res = supabase.table("bookmarks").insert({
            "user_id": user_id,
            "url": url,
            "title": title,
            "keywords": keywords_list,
            "source_type": source_type,  # ✅ 修正：原本寫死 "og"，現在傳實際來源
            "og_title": title,
            "og_description": ""
        }).execute()

        print("✅ INSERT 成功:", res)

    except Exception as e:
        print("❌ INSERT 失敗:", e)
        import traceback
        traceback.print_exc()


def update_latest_keywords(user_id, new_keywords):
    try:
        res = supabase.table("bookmarks") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if not res.data:
            return False

        latest_id = res.data[0]["id"]
        keywords_list = [k.strip() for k in new_keywords.split(",")]

        supabase.table("bookmarks") \
            .update({"keywords": keywords_list}) \
            .eq("id", latest_id) \
            .execute()

        return True

    except Exception as e:
        print("❌ UPDATE ERROR:", e)
        return False


def search_bookmarks(user_id, keyword):
    try:
        res = supabase.table("bookmarks") \
            .select("*") \
            .eq("user_id", user_id) \
            .execute()

        results = []
        for item in res.data:
            if keyword.lower() in ",".join(item["keywords"]).lower():
                results.append(item)

        return results

    except Exception as e:
        print("❌ SEARCH ERROR:", e)
        return []


# ==============================================================
# 啟動
# ==============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🔥 使用 port: {port}")
    app.run(host="0.0.0.0", port=port)
