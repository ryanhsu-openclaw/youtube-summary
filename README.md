# YouTube Summary to Notion

每天定時抓指定 YouTube 頻道的「最新長影片」字幕（優先中文，否則英文），寫入 Notion 資料庫，並在稍後用 Gemini 幫每支影片產出條列重點＋總結，全部放在同一個 Notion 頁面裡，當作你的「YouTube 摘要牆」。

目前預設頻道：
- 大耳朵TV
- APPLEFANS 蘋果迷
- 塔科女子

> 這個專案主要有兩支腳本：
> - `youtube_summary.py`：每天抓最新影片 + 抓字幕 + 寫入 Notion
> - `youtube_notion_summarizer.py`：用 Gemini 幫 Notion 裡的逐字稿產生摘要，寫回同一頁最上方

---

## 專案架構

- `youtube_summary.py`
  - 透過 YouTube RSS 抓各頻道「最新一支長影片」（忽略 Shorts）
  - 只處理「**今天發布**」的影片（依台北時間粗略判斷）
  - 使用 [TranscriptAPI](https://transcriptapi.com) 抓字幕：
    - 若有中文字幕（`language` 以 `zh` 開頭）→ 優先使用
    - 否則若只有英文字幕 → 使用英文字幕
    - 若完全沒有字幕 → 略過，不寫入
  - 將以下資訊寫入 Notion 的指定資料庫：
    - `Name`：影片標題（不再重複頻道名稱）
    - `Channel`：頻道名稱（例如「蘋果迷」）
    - `Video Title`：影片標題
    - `Video URL`：影片網址
    - `Published At`：發布時間
    - `Summary`：目前留空（未在這支腳本內直接做摘要）
  - 逐字稿全文會寫在 page 的內文區塊（多個 paragraph），方便之後摘要與搜尋。

- `youtube_notion_summarizer.py`
  - 從同一個 Notion 資料庫中挑出尚未有「內容摘要」的頁面（實作上是：
    - parent.database_id = `YTSUMMARY_NOTION_DATABASE_ID`
    - 沒有 heading block 含有「內容摘要」文字）
  - 對每一頁：
    1. 讀取頁面正文的 paragraph blocks，合併為一段 transcript（超過 60,000 字元會截斷）
    2. 組一個中文 prompt，請 Gemini：
       - 以繁中整理出「大約 10 點」條列重點
       - 產生一段約 300 字的「總結」
       - 內文中對關鍵詞加上粗體效果（透過簡化版 Markdown `**...**` 解析）
       - 禁止自我介紹、禁止「如果覺得有幫助」這類尾巴
    3. 呼叫 `/home/azureuser/gemini_bot.py` 把 prompt 丟給 Gemini，讀回純文字結果
    4. 做一層簡單清理：移除包含「Gemini」「如果覺得有幫助」「您可以直接提供」等字眼的行
    5. 將原頁面的所有 blocks 標記為 `archived`，再重建內容：
       - `heading_2`：`內容摘要`
       - **重點整理**：使用 `numbered_list_item`，格式類似：
         - `1.` 粗體標題 + `：` 說明文字
       - **總結段落**：
         - `總結：` 粗體
         - 後面是一段普通文字
       - `divider`
       - 原始 transcript：切成多個 paragraph blocks

---

## 安裝與相依環境

### 1. Python / venv

建議使用虛擬環境，例如：

```bash
cd /home/azureuser
ython3 -m venv selenium-env
source selenium-env/bin/activate
pip install feedparser requests notion-client
```

> 本 repo 預設是搭配 `/home/azureuser/selenium-env` 使用，如果你的路徑不同，請自行調整相關指令。

### 2. 必要環境變數

- `NOTION_API_KEY`：你的 Notion integration API key
- `YTSUMMARY_NOTION_DATABASE_ID`：存放 YouTube 摘要的 Notion 資料庫 ID
- `TRANSCRIPT_API_KEY`：從 TranscriptAPI 取得的 API key

範例（寫在 `~/.bashrc` 或相似檔案中）：

```bash
export NOTION_API_KEY="ntn_xxx"
export YTSUMMARY_NOTION_DATABASE_ID="YOUR_NOTION_DATABASE_ID"
export TRANSCRIPT_API_KEY="sk_xxx"
```

> 程式也會嘗試從 `~/.openclaw/openclaw.json` 讀取 `skills.entries.transcriptapi.apiKey`，這是配合 OpenClaw 的自動設定；若你不用 OpenClaw，可以把那段程式刪掉，改成只用環境變數。

### 3. TranscriptAPI 註冊

到 <https://transcriptapi.com/signup> 註冊帳號取得 `TRANSCRIPT_API_KEY`。

如果你在 OpenClaw 環境中，也可以用 `youtube-full` skill 的 `tapi-auth` 腳本來註冊與寫入 config（這部分不一定適用你的環境）。

### 4. Notion 資料庫 schema 建議

請建立一個資料庫（Database），欄位至少包含：

- `Name`（Title）
- `Channel`（Rich text）
- `Video Title`（Rich text）
- `Video URL`（URL）
- `Published At`（Date）
- `Summary`（Rich text，可留空）

程式假設欄位名稱與上列完全相同；若你改名，記得同步修改 `youtube_summary.py` 中 `create_page()` 的 properties。

---

## 使用方式

### 單次執行：抓最新影片 + 寫入 Notion

```bash
cd /home/azureuser/youtube-summary
source ../selenium-env/bin/activate
python youtube_summary.py
```

### 單次執行：為既有 Notion 逐字稿加上摘要

```bash
cd /home/azureuser/youtube-summary
source ../selenium-env/bin/activate
python youtube_notion_summarizer.py 1   # 處理最多 1 筆
python youtube_notion_summarizer.py 5   # 處理最多 5 筆
```

對應的 Notion 頁面會變成：

- 頁首：`內容摘要`（heading_2）
- 緊接著：
  - 編號清單 `1. / 2. / ...`，每項的標題粗體，後面是說明
  - 一段 `總結：` 開頭的段落（"總結：" 粗體）
- 分隔線
- 原本的逐字稿全文（多個 paragraph）

---

## 調整頻道與 Notion 設定

### 調整頻道列表

在 `youtube_summary.py` 中修改 `CHANNELS`：

```python
CHANNELS = [
    {
        "name": "Your Channel",
        "rss": "https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxxxxxxxxxxxxxxx",
    },
]
```

### 調整 Notion 資料庫欄位名稱

如果你的 Notion 資料庫欄位名稱不一樣，請同步修改：

- `youtube_summary.py` 裡 `create_page()` 的 `properties` key
- `youtube_notion_summarizer.py` 裡讀取 `Name`（標題）的地方（目前預期 `properties["Name"].title`）

---

## 注意事項

- 這個專案不會刪除 Notion 的既有資料，只會新增頁面或重寫頁面的 blocks 結構（在摘要步驟時會先 archive 舊 blocks，再用新布局重建）。
- TranscriptAPI 有免費額度與速率限制，請依實際使用調整頻道數量與排程頻率。
- 請勿把 `NOTION_API_KEY`、`TRANSCRIPT_API_KEY` 或 `~/.openclaw/openclaw.json` commit 到 GitHub；建議使用環境變數或 CI secret 來管理機敏設定。
