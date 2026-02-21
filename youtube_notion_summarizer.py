#!/usr/bin/env python3
"""Summarize YouTube transcript pages in Notion using Gemini (via gemini_bot.py).

Flow per page:
1. Read page blocks from the YouTube 摘要牆 database.
2. Skip if a heading block with text "內容摘要" already exists.
3. Concatenate transcript paragraph text as input to Gemini (truncate to 60,000 chars).
4. Call gemini_bot.py with a Chinese prompt to produce a ~10-bullet summary + ~300-char conclusion.
5. Archive existing page blocks and rebuild content as:
   - heading_2: 內容摘要
   - summary paragraphs
   - (optional) divider
   - reconstructed transcript paragraphs (chunked to avoid Notion limits).

Environment variables required:
- NOTION_API_KEY
- YTSUMMARY_NOTION_DATABASE_ID

Assumptions:
- gemini_bot.py is located at /home/azureuser/gemini_bot.py
- python and required libs (notion-client, requests) are available in the active venv.
"""

import os
import sys
import textwrap
from typing import List, Dict

from notion_client import Client
import subprocess


NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("YTSUMMARY_NOTION_DATABASE_ID")
GEMINI_BOT = "/home/azureuser/gemini_bot.py"

# Safety limits
MAX_TRANSCRIPT_CHARS = 60000  # 上限 60,000 字元，粗略對應 ~20,000 個中文字
CHUNK_SIZE = 1500             # Notion 單個 rich_text block 長度控制


def ensure_env():
    if not NOTION_API_KEY:
        raise SystemExit("NOTION_API_KEY is not set in environment")
    if not NOTION_DATABASE_ID:
        raise SystemExit("YTSUMMARY_NOTION_DATABASE_ID is not set in environment")
    if not os.path.exists(GEMINI_BOT):
        raise SystemExit(f"gemini_bot.py not found at {GEMINI_BOT}")


def list_pages(notion: Client, limit: int = 5) -> List[Dict]:
    """Query the Notion database for pages to summarize.

    For now: just take the most recent pages (default 5). The caller can slice further.
    Note: older notion-client versions expose search via `client.search` instead
    of `databases.query`, so we use `search` + database filter here for
    compatibility.
    """
    resp = notion.search(
        **{
            "page_size": limit * 5,
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        }
    )
    # 只保留來自目標資料庫的 page
    results = []
    for page in resp.get("results", []):
        parent = page.get("parent", {})
        if parent.get("type") == "data_source_id" and parent.get("database_id") == NOTION_DATABASE_ID:
            results.append(page)
            if len(results) >= limit:
                break
    return results


def has_summary_heading(notion: Client, page_id: str) -> bool:
    """Return True if the page already contains a heading with text 包含 "內容摘要"."""
    cursor = None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        for blk in resp.get("results", []):
            t = blk.get("type")
            if t in ("heading_1", "heading_2", "heading_3"):
                rich = blk[t].get("rich_text", [])
                text = "".join(r.get("plain_text", "") for r in rich)
                if "內容摘要" in text:
                    return True
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return False


def extract_transcript_text(notion: Client, page_id: str) -> str:
    """Concatenate paragraph text blocks as transcript.

    假設原本的 youtube_summary.py 已經把字幕寫成多個 paragraph blocks。
    這裡只取 paragraph 文字，忽略其他型別（heading / divider / callout 等）。
    """
    parts: List[str] = []
    cursor = None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        for blk in resp.get("results", []):
            if blk.get("type") == "paragraph":
                rich = blk["paragraph"].get("rich_text", [])
                txt = "".join(r.get("plain_text", "") for r in rich).strip()
                if txt:
                    parts.append(txt)
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    full = "\n".join(parts)
    if len(full) > MAX_TRANSCRIPT_CHARS:
        return full[:MAX_TRANSCRIPT_CHARS]
    return full


def build_gemini_prompt(title: str, transcript: str) -> str:
    """建構給 Gemini 的摘要指令。

    要求：
    - 全程使用繁體中文。
    - 僅輸出兩個部分：
      1) 「重點整理」：大約 10 點條列，每點獨立一行，以「- 」開頭，
         並將每一點中的關鍵詞或主題用 **粗體** 標記。
      2) 「總結」：一段約 300 個字的完整段落，以「總結：」開頭，
         並在段落中適度對關鍵名詞或重要概念加上 **粗體**。
    - 不要有任何開場白、自我介紹、結語或行動呼籲（例如「如果覺得有幫助…」）。
    - 不要提到你是模型或 AI，不要出現「我」或「Gemini」之類字眼。
    """
    return textwrap.dedent(
        f"""請根據以下 YouTube 影片《{title}》的逐字稿，進行內容整理與摘要。

        請嚴格遵守以下格式與規則：
        1. 全程使用繁體中文。
        2. 只允許輸出兩個段落區塊，不要有任何其它文字說明：
           (1) 第一部分標題為「重點整理」，底下使用條列式，整理大約 10 點重點。
               - 每一點單獨一行，使用「- 」開頭。
               - 每一點中，請將最重要的關鍵詞或主題使用 **粗體** 標記，例如：
                 - **NAS 儲存空間**：說明其用途與適合族群...
           (2) 第二部分標題為「總結：」，接著是一段約 300 個字的完整總結段落。
               - 在總結段落中，也請適度將關鍵名詞或重要概念使用 **粗體** 標記。
        3. 不要有任何開場白，不要自我介紹，不要寫「如果覺得有幫助」等結語或行動呼籲。
        4. 不要提及你是 AI 或模型，不要出現「我認為」「Gemini 說」等主詞。

        逐字稿如下：
        {transcript}
        """
    ).strip()


def run_gemini(prompt: str) -> str:
    """Call gemini_bot.py with the given prompt and return the summarized text.

    We rely on gemini_bot.py printing a line like `已輸出到: /path/to/file.txt`.
    The summary text is read from the DELTA section in that file.
    """
    # 呼叫外部 gemini_bot.py
    proc = subprocess.run(
        ["python", GEMINI_BOT, prompt],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gemini_bot.py failed: {proc.stderr}")

    # stdout 中尋找輸出檔路徑
    out_path = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("已輸出到:"):
            out_path = line.split(":", 1)[1].strip()
            break
    if not out_path or not os.path.exists(out_path):
        raise RuntimeError(f"Cannot locate Gemini output file from stdout: {proc.stdout}")

    # 解析輸出檔：gemini_bot.py 現在只寫入模型輸出的純文字結果
    with open(out_path, "r", encoding="utf-8") as f:
        content = f.read()

    return content.strip()


def archive_existing_blocks(notion: Client, page_id: str) -> None:
    """Archive all existing child blocks of the page.

    簡單起見，逐一呼叫 blocks.update(archived=True)。
    """
    cursor = None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        blocks = resp.get("results", [])
        for blk in blocks:
            blk_id = blk["id"]
            try:
                notion.blocks.update(block_id=blk_id, archived=True)
            except Exception as e:
                print(f"[WARN] 無法 archive block {blk_id}: {e}")
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")


def md_to_rich_text(text: str) -> List[Dict]:
    """Very small subset of Markdown: supports **bold** segments.

    We parse the text and toggle bold when encountering `**`.
    """
    parts: List[Dict] = []
    bold = False
    i = 0
    while i < len(text):
        if text.startswith("**", i):
            bold = not bold
            i += 2
            continue
        j = text.find("**", i)
        if j == -1:
            segment = text[i:]
            i = len(text)
        else:
            segment = text[i:j]
            i = j
        if segment:
            parts.append(
                {
                    "type": "text",
                    "text": {"content": segment},
                    "annotations": {"bold": bold},
                }
            )
    if not parts:
        parts.append({"type": "text", "text": {"content": text}})
    return parts


def build_paragraph_blocks(text: str) -> List[Dict]:
    """Split text into chunks and turn into Notion paragraph blocks.

    支援簡單的 **粗體** 標記，會轉成 Notion 的 bold annotation。
    """
    blocks: List[Dict] = []
    if not text:
        return blocks
    # 以雙換行視為段落邊界，單段再依長度切塊
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for para in paragraphs:
        start = 0
        while start < len(para):
            chunk = para[start : start + CHUNK_SIZE]
            start += CHUNK_SIZE
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": md_to_rich_text(chunk),
                    },
                }
            )
    return blocks


def build_summary_blocks(summary_text: str) -> List[Dict]:
    """Build Notion blocks for the summary section.

    - 上半部「重點整理」用 numbered_list_item 呈現，並將每條第一個子句加粗。
    - 下半部「總結」用 paragraph 呈現，開頭「總結：」加粗。
    """
    blocks: List[Dict] = []
    if not summary_text:
        return blocks

    lines = [ln.strip() for ln in summary_text.splitlines() if ln.strip()]

    # 分割重點整理與總結
    bullet_lines: List[str] = []
    summary_lines: List[str] = []
    in_summary = False
    for ln in lines:
        if ln.startswith("總結：") or ln.startswith("總結:"):
            in_summary = True
            # 去掉前綴後再加入
            summary_lines.append(ln)
            continue
        if not in_summary:
            bullet_lines.append(ln)
        else:
            summary_lines.append(ln)

    # 處理重點整理列點
    for idx, ln in enumerate(bullet_lines, start=1):
        raw = ln
        # 去掉開頭的符號：- 、數字. 等
        if raw.startswith("- "):
            raw = raw[2:].lstrip()
        elif raw[:2].isdigit() and raw[2:3] in [".", "、", "．"]:
            raw = raw[3:].lstrip()

        # 嘗試用「：」分成「標題 + 說明」
        if "：" in raw:
            title, rest = raw.split("：", 1)
        elif ":" in raw:
            title, rest = raw.split(":", 1)
        else:
            title, rest = raw, ""

        title = title.strip()
        rest = rest.strip()

        rich_text = []
        if title:
            rich_text.append(
                {
                    "type": "text",
                    "text": {"content": title},
                    "annotations": {"bold": True},
                }
            )
        if rest:
            # 若沒有 title，直接用 rest；有 title 則在後面補「：說明」
            prefix = "：" if title else ""
            rich_text.append(
                {
                    "type": "text",
                    "text": {"content": f"{prefix}{rest}"},
                }
            )

        blocks.append(
            {
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": rich_text},
            }
        )

    # 處理總結段落
    if summary_lines:
        summary_body = " ".join(summary_lines)
        # 確保只有一個「總結：」前綴
        if summary_body.startswith("總結："):
            body = summary_body[len("總結：") :].lstrip()
        elif summary_body.startswith("總結:"):
            body = summary_body[len("總結:") :].lstrip()
        else:
            body = summary_body

        rich = [
            {
                "type": "text",
                "text": {"content": "總結："},
                "annotations": {"bold": True},
            }
        ]
        if body:
            rich.append({"type": "text", "text": {"content": body}})

        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": rich},
            }
        )

    return blocks


def write_summary_and_transcript(
    notion: Client, page_id: str, summary_text: str, transcript_text: str
) -> None:
    """Rebuild the page content as:
    - heading_2: 內容摘要
    - summary section (numbered list + 總結段落)
    - divider
    - transcript paragraphs
    """
    children: List[Dict] = []

    # 內容摘要 heading
    children.append(
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": "內容摘要"},
                    }
                ]
            },
        }
    )

    # 摘要內容（重點整理 + 總結）
    children.extend(build_summary_blocks(summary_text))

    # 分隔線
    children.append(
        {
            "object": "block",
            "type": "divider",
            "divider": {},
        }
    )

    # 原始逐字稿
    children.extend(build_paragraph_blocks(transcript_text))

    # 寫回 Notion
    notion.blocks.children.append(block_id=page_id, children=children)


def main(limit_pages: int = 1):
    """Summarize up to `limit_pages` pages that do not yet have a 內容摘要 heading."""
    ensure_env()
    notion = Client(auth=NOTION_API_KEY)

    pages = list_pages(notion, limit=limit_pages * 3)  # 多抓一些，因為可能有已整理過的
    processed = 0

    for page in pages:
        if processed >= limit_pages:
            break
        page_id = page["id"]
        props = page.get("properties", {})
        title_prop = props.get("Name") or props.get("Title") or {}
        title_text = ""
        if title_prop.get("type") == "title":
            title_text = "".join(
                r.get("plain_text", "") for r in title_prop["title"]
            ).strip()

        print(f"[INFO] 準備處理 page: {page_id} / 標題：{title_text}")

        if has_summary_heading(notion, page_id):
            print("[INFO] 已包含『內容摘要』區塊，略過。")
            continue

        transcript = extract_transcript_text(notion, page_id)
        if not transcript:
            print("[INFO] 找不到任何逐字稿內容，略過。")
            continue

        prompt = build_gemini_prompt(title_text or "(untitled)", transcript)
        print("[INFO] 呼叫 Gemini 產生摘要...")
        try:
            summary = run_gemini(prompt)
        except RuntimeError as e:
            print(f"[WARN] 第一次呼叫 Gemini 失敗，重試一次。錯誤：{e}")
            try:
                summary = run_gemini(prompt)
            except RuntimeError as e2:
                print(f"[ERROR] 第二次呼叫 Gemini 仍失敗，略過本頁。錯誤：{e2}")
                continue

        if not summary:
            print("[WARN] Gemini 沒有產生有效摘要，略過本頁。")
            continue

        # 簡單尾端清理：去掉常見的客套或行動呼籲句
        lines = [ln.rstrip() for ln in summary.splitlines()]
        cleaned_lines = []
        for ln in lines:
            lower = ln.replace(" ", "").lower()
            if any(
                key in lower
                for key in [
                    "如果覺得這個摘要有幫助",
                    "如果覺得有幫助",
                    "歡迎再提供更多內容",
                    "您可以直接提供",
                    "後續行動",
                    "gemini",
                ]
            ):
                # 丟掉這種尾端句或提到 Gemini 的內容
                continue
            cleaned_lines.append(ln)
        summary_cleaned = "\n".join(cleaned_lines).strip()

        if not summary_cleaned:
            print("[WARN] 清理後摘要為空，略過本頁。")
            continue

        print("[INFO] Archive 現有 blocks 並寫入摘要 + 原始逐字稿...")
        archive_existing_blocks(notion, page_id)
        write_summary_and_transcript(notion, page_id, summary_cleaned, transcript)

        print("[OK] 已完成摘要並更新 Notion。")
        processed += 1

    print(f"[DONE] 本次共處理頁數：{processed}")


if __name__ == "__main__":
    # optional: allow custom limit from CLI
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except ValueError:
            limit = 1
    else:
        limit = 1
    main(limit_pages=limit)
