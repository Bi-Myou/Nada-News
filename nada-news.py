import requests
from bs4 import BeautifulSoup
import feedparser
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import os
import html
import json
import time


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_FILE = os.path.join(ROOT_DIR, "rss.txt")
API = "https://api.telegra.ph"
TELEGRAPH_ALLOWED_TAGS = {
    'a', 'aside', 'b', 'blockquote', 'br', 'code', 'em', 'figcaption', 'figure',
    'h3', 'h4', 'hr', 'i', 'iframe', 'img', 'li', 'ol', 'p', 'pre', 's',
    'strong', 'u', 'ul', 'video'
}
TELEGRAPH_TAGS_ALLOW_ATTR = {
    'a': 'href',
    'img': 'src',
    'iframe': 'src',
    'video': 'src',
}
TELEGRAPH_REPLACE_CLASS_TAGS = {
    'wp-caption-text': 'figcaption'
}

CHAT_ID = os.environ.get("CHAT_ID")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.telegram.org")
API_URL = f"{API_BASE_URL}/bot{BOT_TOKEN}/"

def create_account(short_name="nada-news", author_name="智寶國際", author_url="https://nadaholdings.com/press/"):
    """建立 Telegraph 帳號"""
    r = requests.post(f"{API}/createAccount", data={
        "short_name": short_name,
        "author_name": author_name,
        "author_url": author_url,
    })
    j = r.json()
    if not j.get("ok"):
        raise Exception("createAccount failed", j)
    return j["result"]["access_token"]


def create_page(access_token, title, content_html, author_name="智寶國際", author_url=None):
    """建立 Telegraph 文章"""
    content_nodes = html_to_nodes(content_html)
    payload = {
        "access_token": access_token,
        "title": title,
        "author_name": author_name,
        "content": json.dumps(content_nodes, ensure_ascii=False),
        "return_content": False
    }
    if author_url:
        payload["author_url"] = author_url
    r = requests.post(f"{API}/createPage", data=payload)
    j = r.json()
    if j.get("ok"):
        return j["result"]["url"]
    else:
        print(f"📄 JSON nodes sent: {payload['content']}") 
        print("❌ createPage failed:", j)
        return None


def html_to_nodes(html):
    soup = BeautifulSoup(html, "html.parser")

    def process(el):
        # 空文字略過
        if isinstance(el, str):
            text = el.replace("\xa0", " ").strip()
            return text if text else None

        # 空 <p> 或 &nbsp; 略過
        if el.name == 'p' and not el.get_text(strip=True):
            return None

        # div 拆解子節點，不生成 div
        if el.name == 'div':
            nodes = []
            for c in el.contents:
                child_node = process(c)
                if child_node:
                    if isinstance(child_node, list):
                        nodes.extend(child_node)
                    else:
                        nodes.append(child_node)
            return nodes

        # class 替換 tag
        tag = el.name
        classes = el.attrs.get('class', [])
        if isinstance(classes, str):
            classes = [classes]
        for cls in classes:
            if cls in TELEGRAPH_REPLACE_CLASS_TAGS:
                tag = TELEGRAPH_REPLACE_CLASS_TAGS[cls]

        if tag not in TELEGRAPH_ALLOWED_TAGS:
            # 不在允許列表，拆解子節點
            nodes = []
            for c in el.contents:
                child_node = process(c)
                if child_node:
                    if isinstance(child_node, list):
                        nodes.extend(child_node)
                    else:
                        nodes.append(child_node)
            return nodes

        node = {'tag': tag}

        # 處理允許的屬性
        allowed_attr = TELEGRAPH_TAGS_ALLOW_ATTR.get(tag)
        if allowed_attr and allowed_attr in el.attrs:
            val = el.attrs[allowed_attr]
            if isinstance(val, str):
                val = quote(val, safe="/:?=&%#@+!~,;")  # 保留常用 URL 字元
            node['attrs'] = {allowed_attr: val}
            # node['attrs'] = {allowed_attr: el.attrs[allowed_attr]}

        # 處理 children
        children = []
        for c in el.contents:
            child_node = process(c)
            if child_node:
                if isinstance(child_node, list):
                    children.extend(child_node)
                else:
                    children.append(child_node)
        if children:
            node['children'] = children

        return node

    result = []
    for child in soup.contents:
        node = process(child)
        if node:
            if isinstance(node, list):
                result.extend(node)
            else:
                result.append(node)
    return result


def get_article_html(url, pub_date_str):
    """抓取 NADA 新聞頁內容"""
    res = requests.get(url)
    res.encoding = "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")

    title_tag = soup.select_one("h1.entry-title")
    if not title_tag:
        title_tag = soup.select_one("h3.entry-title")
    content_tag = soup.select_one("div.entry-content")
    if not content_tag:
        content_tag = soup.select_one("div.mkdf-post-text-main")
    if not title_tag or not content_tag:
        return None, None, None, None, None

    title = title_tag.text.strip()

    # 清理不必要的標籤
    for tag in content_tag.select("script, style, iframe, .social-share"):
        tag.decompose()

    # 修正圖片絕對路徑
    for img in content_tag.find_all("img"):
        src = img.get("src")
        if src and src.startswith("/"):
            img["src"] = "https://nadaholdings.com" + src

    # 轉換時間格式為 UTC+8
    dt_utc = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %z")
    dt_local = dt_utc.astimezone(timezone(timedelta(hours=8)))
    date_str = dt_local.strftime("%Y-%m-%d %H:%M:%S")

    # 時間 + 小編資訊
    info_html = f"<p><em>發布時間：{date_str}</em></p>"

    a_tag = soup.select_one("li.meta-author a[rel='author']")
    if a_tag:
        author_url = a_tag.get("href", "")
        author_name_tag = a_tag.select_one("span.fn")
        author_name = author_name_tag.get_text(strip=True) if author_name_tag else ""
    else:
        author_url = ""
        author_name = ""
    if author_name:
        if author_url != "":
            info_html = f'<p><em>發布時間：{date_str} by&nbsp;<a href="{author_url}" target="_blank">{html.escape(author_name)}</a></em></p>'
        else:
            info_html = f'<p><em>發布時間：{date_str} by&nbsp;{html.escape(author_name)}</em></p>'

    # 組合完整內容
    content_html = info_html + str(content_tag)
    return title, date_str, author_name, author_url, content_html


def load_done_links():
    """載入已建立過的 RSS 連結"""
    if not os.path.exists(SAVE_FILE):
        return set()
    with open(SAVE_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_done_link(link):
    """儲存已處理過的連結"""
    with open(SAVE_FILE, "a", encoding="utf-8") as f:
        f.write(link + "\n")


def create_articles_from_rss(rss_url, short_name, author_name, tag, news_url, access_token=None):
    """讀取 RSS 並依序建立 Telegraph 文章"""
    print("📡 讀取 RSS feed:", rss_url)
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        print("❌ 沒有找到任何文章。")
        return

    host = rss_url.split("/")[2]
    done_links = load_done_links()
    # print(f"🧾 已建立過 {len(done_links)} 篇文章。")

    # 建立帳號（匿名）
    if access_token is None:
        access_token = create_account(short_name, author_name, news_url)
        print(access_token)

    # 倒序讀取 (RSS 原本是新到舊，我們反轉成舊到新)
    entries = list(reversed(feed.entries))

    for entry in entries:
        link = entry.link
        title = entry.title
        pub_date = entry.published
        guid = entry.id
        check_rss = f"{author_name},{guid},{title}"

        if check_rss in done_links:
            continue

        
        if f"//{host}/" not in link:
            description = entry.description
            # 轉換時間格式為 UTC+8
            dt_utc = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
            dt_local = dt_utc.astimezone(timezone(timedelta(hours=8)))
            date_str = dt_local.strftime("%Y-%m-%d %H:%M:%S")
            send_text = (
                f"<blockquote>{html.escape(description)}…</a></blockquote>\n"
                f"——————————\n"
                f"#{tag} #NEWS\n"
                f"<blockquote>時間： {date_str}\n"
                f"頻道： <a href='{news_url}'>{author_name}</a>\n"
                f"貼文： <a href='{link}'>{html.escape(title)}</a></blockquote>"
            )
            send_message(CHAT_ID, send_text, 9)
            save_done_link(check_rss)
            continue

        print(f"📰 建立文章: {title}")
        try:
            title, date_str, editor_name, editor_url, content_html = get_article_html(link, pub_date)
            if not title:
                print("⚠️ 無法解析內容:", link)
                continue

            url = create_page(access_token, title, content_html, author_name, link)
            if url:
                print("✅ 建立成功:", url)
                url_text = f" <a href='{url}'>\u2063</a>"
            else:
                print("❌ 建立失敗:", title)
                url_text = ""
            # 組合發送到 Telegram 的內容
            if editor_name != "" and editor_url != "":
                send_text = (
                    f"#{tag} #NEWS{url_text}\n"
                    f"<blockquote>時間： {date_str}\n"
                    f"頻道： <a href='{news_url}'>{author_name}</a>\n"
                    f"小編： <a href='{editor_url}'>{html.escape(editor_name or '')}</a>\n"
                    f"貼文： <a href='{link}'>{html.escape(title)}</a></blockquote>"
                )
            elif editor_name != "":
                send_text = (
                    # f"<blockquote><a href='{url}'>{html.escape(title)}</a></blockquote>\n"
                    # f"——————————\n"
                    f"#{tag} #NEWS{url_text}\n"
                    f"<blockquote>時間： {date_str}\n"
                    f"頻道： <a href='{news_url}'>{author_name}</a>\n"
                    f"小編： {html.escape(editor_name or '')}\n"
                    f"貼文： <a href='{link}'>{html.escape(title)}</a></blockquote>"
                )
            else:
                send_text = (
                    # f"<blockquote><a href='{url}'>{html.escape(title)}</a></blockquote>\n"
                    # f"——————————\n"
                    f"#{tag} #NEWS{url_text}\n"
                    f"<blockquote>時間： {date_str}\n"
                    f"頻道： <a href='{news_url}'>{author_name}</a>\n"
                    f"貼文： <a href='{link}'>{html.escape(title)}</a></blockquote>"
                )
            send_message(CHAT_ID, send_text, 9)
            save_done_link(check_rss)
        except Exception as e:
            import traceback, sys
            error_class = e.__class__.__name__ #取得錯誤類型
            detail = e.args[0] #取得詳細內容
            cl, exc, tb = sys.exc_info() #取得Call Stack
            lastCallStack = traceback.extract_tb(tb)[-1] #取得Call Stack的最後一筆資料
            fileName = lastCallStack[0] #取得發生的檔案名稱
            lineNum = lastCallStack[1] #取得發生的行號
            funcName = lastCallStack[2] #取得發生的函數名稱
            errMsg = "File \"{}\", line {}, in {}: [{}] {}".format(fileName, lineNum, funcName, error_class, detail)
            print(errMsg)
            print("⚠️ 發生錯誤:", e)
            continue

def send_message(chat_id, text, thread_id=0, reply_id=0):
    url = API_URL + "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        'link_preview_options': json.dumps({"is_disabled": False})
    }
    if thread_id and thread_id != 0:
        payload["chat_id"] = CHAT_ID
        payload["message_thread_id"] = thread_id
    else:
        payload["chat_id"] = "-1002291115765"
    payload["text"] = text.replace("<MY_CHAT_ID>", payload["chat_id"].replace("-100", ""))
    if reply_id != 0:
        reply_parameters = {
            "message_id": reply_id
        }
        payload["reply_parameters"] = json.dumps(reply_parameters)

    for attempt in range(10):
        if attempt >= 5:
            payload.pop("reply_parameters", None)
        response = requests.post(url, json=payload)
        if response.ok:
            return response.json()
        time.sleep(30)
    return None


NADA_RSS = os.environ.get("NADA_RSS")
NADA_TOKEN = os.environ.get("NADA_TOKEN")
TROPIC_RSS = os.environ.get("TROPIC_RSS")
TROPIC_TOKEN = os.environ.get("TROPIC_TOKEN")
SHOEI_RSS = os.environ.get("SHOEI_RSS")
SHOEI_TOKEN = os.environ.get("SHOEI_TOKEN")
if __name__ == "__main__":
    create_articles_from_rss(NADA_RSS, "nada-news", "智寶國際", "智寶", "https://nadaholdings.com/press/", NADA_TOKEN)
    create_articles_from_rss(TROPIC_RSS, "tropic-news", "回歸線娛樂", "回歸線", "https://tropicse.com/press/", TROPIC_TOKEN)
    create_articles_from_rss(SHOEI_RSS, "shoei-news", "翔英融創", "翔英", "https://shoeicontents.com/news/", SHOEI_TOKEN)

