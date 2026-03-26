from flask import Flask, request
import requests
from bs4 import BeautifulSoup
import os
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
import os

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)

# ✅ 先讀 env
load_dotenv()

# ✅ 再取得變數
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ✅ debug
print("🔥程式啟動了")
print("LINE TOKEN 是否存在:", bool(LINE_TOKEN))
print("GEMINI KEY 是否存在:", bool(GEMINI_API_KEY))
print("SUPABASE URL 是否存在:", bool(os.getenv("SUPABASE_URL")))
print("SUPABASE KEY 是否存在:", bool(os.getenv("SUPABASE_ANON_KEY")))

app = Flask(__name__)

def extract_og_data(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0"
        }
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")

        def get_meta(property_name):
            tag = soup.find("meta", property=property_name)
            return tag["content"] if tag and tag.get("content") else None

        og_title = get_meta("og:title")
        og_desc = get_meta("og:description")
        og_type = get_meta("og:type")
        og_url = get_meta("og:url")

        if og_title or og_desc:
            return {
                "title": og_title,
                "description": og_desc,
                "type": og_type,
                "url": og_url if og_url else url,
                "source": "og"
            }

        return None

    except Exception as e:
        print("OG error:", e)
        return None

def extract_content(url):

    # 🔥 0️⃣ FB / IG 先攔截（最重要）
    if "facebook.com" in url or "fb.watch" in url or "instagram.com" in url:
        return {
            "title": "社群貼文",
            "content": url,
            "type": "social",
            "url": url,
            "source": "fallback"
        }

    # 1️⃣ OG 優先
    og_data = extract_og_data(url)
    if og_data:
        return {
            "title": og_data["title"],
            "content": og_data["description"],
            "type": og_data["type"],
            "url": og_data["url"],
            "source": "og"
        }

    # 2️⃣ fallback：HTML
    try:
        res = requests.get(url)
        soup = BeautifulSoup(res.text, "html.parser")

        title = soup.title.string if soup.title else None
        paragraphs = soup.find_all("p")
        content = " ".join([p.get_text() for p in paragraphs[:5]])

        return {
            "title": title,
            "content": content,
            "type": "website",
            "url": url,
            "source": "html"
        }

    except:
        pass

    # 3️⃣ 最後 fallback
    return {
        "title": None,
        "content": url,
        "type": "unknown",
        "url": url,
        "source": "ai_only"
    }

def get_web_content(url):
    try:
        res = requests.get(url, timeout=5)
        res.encoding = res.apparent_encoding  # 👈 加這行
        soup = BeautifulSoup(res.text, "html.parser")

        title = soup.title.string if soup.title else "無標題"
        text = soup.get_text()[:2000]

        return title, text
    except:
        return "讀取失敗", ""

import time

from google import genai

# 初始化 client（建議只做一次）
client = genai.Client(api_key=GEMINI_API_KEY)


def get_keywords(title, content, content_type=""):
    try:
        prompt = f"""
        請根據以下資訊，產生5個「可搜尋用關鍵字」（用逗號分隔）

        規則：
        - 必須是分類詞（例如：房地產、投資、AI工具）
        - 不要句子
        - 每個2~6字
        - 以實用為主

        ---
        【標題】
        {title}

        【摘要（高權重）】
        {content[:1000]}

        【類型】
        {content_type}
        """

        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt
        )

        # 👉 Debug用（保留）
        print("👉 Gemini完整回傳:", response)

        # 👉 安全取值（避免爆炸）
        if hasattr(response, "text") and response.text:
            return response.text.strip()

        # 👉 fallback（有些版本沒有 text）
        if hasattr(response, "candidates"):
            return response.candidates[0].content.parts[0].text.strip()

        return "無法產生關鍵字"

    except Exception as e:
        print("❌ Gemini錯誤:", e)

        # 👉 針對429優化提示
        if "429" in str(e):
            return "AI請求過多，請稍後再試"

        return "AI暫時無法使用"


def reply_message(reply_token, text):
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }

    body = {
        "replyToken": reply_token,
        "messages": [{
            "type": "text",
            "text": text
        }]
    }

    res = requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers=headers,
        json=body
    )

    print("👉 LINE status:", res.status_code)
    print("👉 LINE response:", res.text)

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

                if len(results) == 0:
                    reply = f"找不到「{keyword}」相關資料 😢"
                else:
                    reply = f"找到 {len(results)} 筆：\n\n"

                    for i, item in enumerate(results[:5]):
                        reply += f"""{i+1}️⃣ {item['title']}
🔗 {item['url']}

"""

                reply_message(reply_token, reply)
                continue

            # 🟣 ② /edit
            elif user_text.startswith("/edit"):
                new_keywords = user_text.replace("/edit", "").strip()

                success = update_latest_keywords(user_id, new_keywords)

                if success:
                    reply = f"已更新關鍵字為：{new_keywords} ✅"
                else:
                    reply = "更新失敗（可能還沒有資料）😢"

                reply_message(reply_token, reply)
                continue

            # 🟡 ③ 網址處理
            elif user_text.startswith("http"):
                url = user_text

                try:
                    data = extract_content(url)

                    title = data.get("title") or ""
                    content = data.get("content") or ""
                    content_type = data.get("type") or ""
                    final_url = data.get("url") or url

                    # 🔥 關鍵：加入 user_text
                    keywords = get_keywords(title, content + "\n" + user_text, content_type)

                    save_to_supabase(user_id, final_url, title, keywords)

                    reply = f"""已收藏 ✅
🔗 {final_url}
📄 {title}
🏷 {keywords}
"""

                except Exception as e:
                    print("❌ 分析錯誤:", e)
                    import traceback
                    traceback.print_exc()

                    reply = "分析失敗，請稍後再試 😢"

                reply_message(reply_token, reply)
                continue

            # 🔴 ④ 其他
            else:
                reply_message(
                    reply_token,
                    "請傳送網址或輸入 /search 關鍵字 😊"
                )
                continue

        return "OK", 200

    except Exception as e:
        print("❌ webhook錯誤:", str(e))
        import traceback
        traceback.print_exc()
        return "OK", 200


def save_to_supabase(user_id, url, title, keywords):
    try:
        keywords_list = [k.strip() for k in keywords.split(",")]

        res = supabase.table("bookmarks").insert({
            "user_id": user_id,
            "url": url,
            "title": title,
            "keywords": keywords_list,
            "source_type": "og",
            "og_title": title,
            "og_description": ""
        }).execute()

        print("✅ INSERT 成功:", res)

    except Exception as e:
        print("❌ INSERT 失敗:", str(e))

def update_latest_keywords(user_id, new_keywords):
    try:
        # 先抓最新一筆
        res = supabase.table("bookmarks") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if len(res.data) == 0:
            return False

        latest_id = res.data[0]["id"]

        keywords_list = [k.strip() for k in new_keywords.split(",")]

        # 更新
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


if __name__ == "__main__":
    import os

    # ⭐先測 search
    print("🔍 測試 search:")
    print(search_bookmarks("Udbfa402e2c1f2c8b7b210d06f7d69dea", "旅遊"))

    port = int(os.environ.get("PORT", 10000))
    print(f"🔥 使用port: {port}")

    app.run(host="0.0.0.0", port=port)