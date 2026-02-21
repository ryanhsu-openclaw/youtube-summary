#!/usr/bin/env python3
import os
import datetime as dt
from typing import List, Dict
from pathlib import Path
import json

import feedparser
import requests
from notion_client import Client


NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("YTSUMMARY_NOTION_DATABASE_ID")

# TranscriptAPI key: 優先用環境變數，其次讀 ~/.openclaw/openclaw.json
TRANSCRIPT_API_KEY = os.environ.get("TRANSCRIPT_API_KEY")
if not TRANSCRIPT_API_KEY:
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            TRANSCRIPT_API_KEY = (
                data
                .get("skills", {})
                .get("entries", {})
                .get("transcriptapi", {})
                .get("apiKey")
            )
        except Exception:
            TRANSCRIPT_API_KEY = None

# 頻道清單改從外部 JSON 讀取，方便擴充 / 調整
CHANNELS_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "channels.json")


def load_channels() -> List[Dict[str, str]]:
    """Load channel list from channels.json.

    JSON 格式：[{"name": "...", "rss": "..."}, ...]
    若檔案不存在或解析失敗，會丟出例外，避免悄悄用錯設定。
    """
    if not os.path.exists(CHANNELS_CONFIG_PATH):
        raise SystemExit(f"channels.json not found at {CHANNELS_CONFIG_PATH}")
    try:
        with open(CHANNELS_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("channels.json must be a list of objects")
        for ch in data:
            if not isinstance(ch, dict) or "name" not in ch or "rss" not in ch:
                raise ValueError("each channel must be an object with 'name' and 'rss'")
        return data
    except Exception as e:
        raise SystemExit(f"Failed to load channels.json: {e}")


def get_latest_video(feed_url: str):
    feed = feedparser.parse(feed_url)
    if not feed.entries:
        return None
    # 優先挑非 Shorts 的長影片
    for entry in feed.entries:
        link = getattr(entry, "link", "") or ""
        if "/shorts/" not in link:
            return entry
    # 如果全部都是 shorts，就退而求其次拿第一個
    return feed.entries[0]


def extract_video_id_from_link(link: str) -> str:
    # 標準形式：https://www.youtube.com/watch?v=VIDEO_ID
    if "v=" in link:
        return link.split("v=")[-1].split("&")[0]
    # shorts 或其他形式可以再慢慢補
    return link.rsplit("/", 1)[-1]


def get_transcript_via_tapi(video_url: str) -> str:
    """透過 TranscriptAPI (youtube-full skill 背後的服務) 抓字幕。

    行為：
    - 若 `language` 為 zh-*（繁中 / 簡中）→ 優先使用中文字幕。
    - 否則若只有其他語系（多數情況是 en）→ 退而求其次使用該語系字幕。
    - 完全沒有字幕或發生錯誤 → 回傳空字串，呼叫端自行決定是否略過。
    """
    if not TRANSCRIPT_API_KEY:
        return ""

    try:
        resp = requests.get(
            "https://transcriptapi.com/api/v2/youtube/transcript",
            params={
                "video_url": video_url,
                "format": "text",
                "include_timestamp": "false",
                "send_metadata": "true",  # 回傳 language 等資訊，方便判斷
            },
            headers={"Authorization": f"Bearer {TRANSCRIPT_API_KEY}"},
            timeout=60,
        )
        if resp.status_code != 200:
            # 404 = 沒字幕、402 = 沒額度…都先當作抓不到
            return ""
        data = resp.json()
        # language 例如 zh-Hant、en 等
        lang = (data.get("language") or "").lower()
        text = data.get("transcript")
        if not isinstance(text, str):
            return ""
        # 優先：如果是 zh 開頭（繁中 / 簡中），視為中文字幕
        if lang.startswith("zh"):
            return text
        # 否則：沒有中文，就退而求其次用英文（或其他語系）
        return text
    except Exception:
        return ""


def build_summary_prompt(channel_name: str, title: str, transcript: str) -> str:
    # 這裡暫時只把全文寫進 Notion，摘要你目前是用 OpenClaw / 其他環境來跑
    # 之後如果要在這支腳本內直接 call OpenAI/Gemini 再一起補
    return f"頻道：{channel_name}\n標題：{title}\n\n內文：{transcript[:4000]}"


def create_page(notion: Client, channel_name: str, entry, transcript: str, summary: str):
    # 發布時間
    published = None
    if getattr(entry, "published", None):
        try:
            published = dt.datetime(*entry.published_parsed[:6]).isoformat()
        except Exception:
            published = None

    video_url = entry.link
    video_title = entry.title

    # 先建立一個空白頁面，只在屬性寫基本資訊
    page = notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties={
            "Name": {
                "title": [
                    {
                        "text": {
                            # 這裡只用影片標題即可，頻道名稱已經在 Channel 欄位中
                            "content": video_title,
                        }
                    }
                ]
            },
            "Channel": {
                "rich_text": [
                    {
                        "text": {
                            "content": channel_name,
                        }
                    }
                ]
            },
            "Video Title": {
                "rich_text": [
                    {
                        "text": {
                            "content": video_title,
                        }
                    }
                ]
            },
            "Video URL": {
                "url": video_url,
            },
            "Published At": {
                "date": {"start": published} if published else None,
            },
            "Summary": {
                "rich_text": [
                    {
                        "text": {
                            "content": summary,
                        }
                    }
                ] if summary else [],
            },
        },
    )

    # 影片內文（transcript 或描述）寫在頁面的內容裡，不佔用資料庫欄位
    if transcript:
        # Notion 單一 rich_text block 有長度限制，簡單切塊
        chunks = []
        step = 1500
        for i in range(0, len(transcript), step):
            chunks.append(transcript[i : i + step])

        notion.blocks.children.append(
            block_id=page["id"],
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": chunk},
                            }
                        ]
                    },
                }
                for chunk in chunks
            ],
        )


def main():
    if not NOTION_API_KEY:
        raise SystemExit("NOTION_API_KEY is not set in environment")
    if not NOTION_DATABASE_ID:
        raise SystemExit("YTSUMMARY_NOTION_DATABASE_ID is not set in environment")

    notion = Client(auth=NOTION_API_KEY)

    channels = load_channels()
    for ch in channels:
        name = ch["name"]
        rss = ch["rss"]
        print(f"[INFO] 處理頻道：{name}")
        entry = get_latest_video(rss)
        if not entry:
            print(f"[WARN] {name} 沒有抓到任何影片")
            continue

        # 只處理「發布日期是今天」的影片（依台北時間粗略判定）
        today_tpe = (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
        published_date = None
        if getattr(entry, "published_parsed", None):
            try:
                published_date = dt.date(
                    entry.published_parsed.tm_year,
                    entry.published_parsed.tm_mon,
                    entry.published_parsed.tm_mday,
                )
            except Exception:
                published_date = None
        if published_date != today_tpe:
            print(
                f"[INFO] 最新影片不是今天發的（published={published_date}，today={today_tpe}），略過：{entry.title}"
            )
            continue

        transcript = get_transcript_via_tapi(entry.link)
        if not transcript:
            print(f"[INFO] 找不到可用字幕（中文 / 英文），略過寫入 Notion：{entry.title}")
            continue

        summary = ""  # 暫時先不在這裡下 AI 摘要
        prompt_preview = build_summary_prompt(name, entry.title, transcript)

        print(f"[INFO] 最新影片：{entry.title}")
        print(f"[INFO] 影片網址：{entry.link}")
        print(f"[INFO] transcript 長度：{len(transcript)} 字元")

        create_page(notion, name, entry, transcript, summary)
        print(f"[OK] 已寫入 Notion（含字幕）：{name} - {entry.title}")

    print("[DONE] 全部頻道處理完畢")


if __name__ == "__main__":
    main()
