#!/usr/bin/env python3
"""Debug helper: run summarization for a single Notion page id.

Usage:
  python3 youtube_notion_summarizer_single.py <page_id>

This reuses youtube_notion_summarizer.py logic but only for one page.
"""
import os
import sys

from notion_client import Client

from youtube_notion_summarizer import (
    ensure_env,
    NOTION_API_KEY,
    extract_transcript_text,
    has_summary_heading,
    is_mostly_english,
    translate_transcript_to_zh,
    build_gemini_prompt,
    strip_non_bmp,
    run_gemini,
    archive_existing_blocks,
    write_summary_and_transcript,
)


def main(page_id: str):
    ensure_env()
    notion = Client(auth=NOTION_API_KEY)

    page = notion.pages.retrieve(page_id=page_id)
    props = page.get("properties", {})
    title_prop = props.get("Name") or props.get("Title") or {}
    title_text = ""
    if title_prop.get("type") == "title":
        title_text = "".join(r.get("plain_text", "") for r in title_prop["title"]).strip()

    print(f"[INFO] 單頁模式：準備處理 page: {page_id} / 標題：{title_text}")

    if has_summary_heading(notion, page_id):
        print("[INFO] 已包含『內容摘要』區塊，略過。")
        return

    transcript = extract_transcript_text(notion, page_id)
    if not transcript:
        print("[INFO] 找不到任何逐字稿內容，略過。")
        return

    transcript_for_summary = transcript
    if is_mostly_english(transcript):
        try:
            transcript_for_summary = translate_transcript_to_zh(
                notion,
                page_id,
                title_text or "(untitled)",
                transcript,
            )
        except RuntimeError as e:
            print(f"[WARN] 英文逐字稿翻譯失敗，略過本頁。錯誤：{e}")
            return

    prompt = build_gemini_prompt(title_text or "(untitled)", transcript_for_summary)
    prompt = strip_non_bmp(prompt)
    print("[INFO] 呼叫 Gemini 產生摘要...")
    try:
        summary = run_gemini(prompt)
    except RuntimeError as e:
        print(f"[ERROR] 呼叫 Gemini 失敗。錯誤：{e}")
        return

    if not summary:
        print("[WARN] Gemini 沒有產生有效摘要，略過本頁。")
        return

    # 簡單尾端清理
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
            continue
        cleaned_lines.append(ln)
    summary_cleaned = "\n".join(cleaned_lines).strip()

    if not summary_cleaned:
        print("[WARN] 清理後摘要為空，略過本頁。")
        return

    print("[INFO] Archive 現有 blocks 並寫入摘要 + 原始逐字稿...")
    archive_existing_blocks(notion, page_id)
    write_summary_and_transcript(notion, page_id, summary_cleaned, transcript_for_summary)
    print("[OK] 單頁摘要處理完成。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: youtube_notion_summarizer_single.py <page_id>")
    main(sys.argv[1])
