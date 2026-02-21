#!/usr/bin/env python3
"""Append pre-translated Chinese transcript to the bottom of a Notion page.

- Reads text from applemi_transcript_zh.txt
- Appends to the specified page as:
  - heading_2: 英文逐字稿中文翻譯
  - paragraphs (chunked)
"""
import os
import sys
from typing import List, Dict

from notion_client import Client

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
TEXT_PATH = os.path.join(os.path.dirname(__file__), "applemi_transcript_zh.txt")


def build_paragraph_blocks(text: str, chunk_size: int = 1500) -> List[Dict]:
    blocks: List[Dict] = []
    if not text:
        return blocks
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for para in paragraphs:
        start = 0
        while start < len(para):
            chunk = para[start : start + chunk_size]
            start += chunk_size
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": chunk},
                            }
                        ],
                    },
                }
            )
    return blocks


def main():
    if not NOTION_API_KEY:
        raise SystemExit("NOTION_API_KEY is not set in environment")
    if len(sys.argv) < 2:
        raise SystemExit("Usage: append_translated_transcript.py <page_id>")

    page_id = sys.argv[1]
    if not os.path.exists(TEXT_PATH):
        raise SystemExit(f"Translated text file not found: {TEXT_PATH}")

    with open(TEXT_PATH, "r", encoding="utf-8") as f:
        text = f.read().strip()

    notion = Client(auth=NOTION_API_KEY)

    children: List[Dict] = []

    # Heading for translated section
    children.append(
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": "英文逐字稿中文翻譯"},
                    }
                ]
            },
        }
    )

    # Paragraphs of translated text
    children.extend(build_paragraph_blocks(text))

    notion.blocks.children.append(block_id=page_id, children=children)
    print("[OK] 已將中文翻譯逐字稿附加到頁面底部。")


if __name__ == "__main__":
    main()
