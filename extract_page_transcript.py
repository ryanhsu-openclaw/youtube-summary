#!/usr/bin/env python3
import os
import sys
from typing import List, Dict

from notion_client import Client

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")


def extract_transcript_text(notion: Client, page_id: str) -> str:
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
    return "\n".join(parts)


def main():
    if not NOTION_API_KEY:
        raise SystemExit("NOTION_API_KEY is not set in environment")
    if len(sys.argv) < 2:
        raise SystemExit("Usage: extract_page_transcript.py <page_id>")
    page_id = sys.argv[1]
    notion = Client(auth=NOTION_API_KEY)
    txt = extract_transcript_text(notion, page_id)
    print(txt)


if __name__ == "__main__":
    main()
