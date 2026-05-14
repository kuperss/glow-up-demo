# 舞光戰將 AI 助教 · Backend (OpenAI + 知識庫 / 產品 RAG)

跟 frontend（這個 repo 根目錄上的 HTML 檔案）**部署到不同地方**：

```
業務部新人訓練/
├─ index.html, products.html, ...    ← 部署到 GitHub Pages（前端）
├─ firebase-app.js                    ← 同上
└─ backend/                           ← 部署到 fly.io（這個 backend）
     ├─ app.py
     ├─ dancelight_service.py
     ├─ kb_rag.py
     ├─ product_rag.py
     ├─ data/
     │  ├─ kb_chunks.json
     │  ├─ kb_ai_embeddings.npz
     │  ├─ products_private.json
     │  └─ product_ai_embeddings.npz
     ├─ manual_login.py
     ├─ fly.toml
     ├─ Dockerfile
     └─ requirements.txt
```

GitHub Pages 不會跑 backend 的 Python 檔，fly.io 也不會碰前端 HTML。完全互不干擾。
正式建議使用「自有後端（OpenAI + 產品知識庫）」模式：OpenAI API Key、網站知識庫、產品 JSON、產品向量索引都只留在後端。

---

## 第一次部署

### 1. 本機 Python 環境（一次性，只為了取 NotebookLM cookie）

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install "notebooklm-py[browser]" playwright
playwright install chromium
python manual_login.py
```

跳出 Chromium → 完成 Google 登入（**用要使用 NotebookLM 的那個帳號**）→ 看到 NotebookLM 首頁 → 回 PowerShell 按 Enter。

完成後會在 `backend/credentials/notebooklm_storage.json` 產生 cookie 檔。

```powershell
notebooklm auth check --test
# 五項 ✓ 表示成功
```

### 2. 設定 Fly app

```powershell
cd backend  # 一定要在 backend/ 目錄裡跑

# 第一次：建 app（會用本地 fly.toml）
fly launch --no-deploy --copy-config --name dancelight-ai
# ↑ 名字可以自取（必須全 fly.io 唯一）。建好後 fly.toml 會自動更新
```

### 3. 設 secrets

```powershell
# 1. NotebookLM 筆記本 ID（這本 KB notebook 的 UUID）
fly secrets set DANCELIGHT_NOTEBOOK_ID=1af7e026-a5e0-443e-81e7-87c09ba07a6d

# 2. Cookie JSON（從本機檔案塞進 fly secrets）
$json = Get-Content credentials\notebooklm_storage.json -Raw
fly secrets set NOTEBOOKLM_STORAGE_JSON="$json"

# 3. Shared secret（前後端認證用，產一個 64 字元隨機字串）
$secret = ([Guid]::NewGuid().ToString() + [Guid]::NewGuid().ToString()).Replace("-","")
Write-Host "===== SHARED SECRET（複製貼到訓練網站 admin）=====" -ForegroundColor Yellow
Write-Host $secret -ForegroundColor Cyan
fly secrets set DANCELIGHT_SHARED_SECRET=$secret

# 4. OpenAI（正式建議）
fly secrets set DANCELIGHT_LLM_PROVIDER=openai
fly secrets set OPENAI_API_KEY=你的-openai-api-key
fly secrets set DANCELIGHT_OPENAI_MODEL=gpt-4o-mini

# 5. Embedding key（產品 / 網站知識庫語意搜尋用）
fly secrets set GEMINI_API_KEY=你的-gemini-api-key
```

### 4. 部署

```powershell
fly deploy
```

完成後 endpoint 是 `https://dancelight-ai.fly.dev`（如果 app 名字是 dancelight-ai）。

### 5. 測試 endpoint

```powershell
$secret = "貼你剛剛產的那串"
$body = @{ question = "舞光的索爾崁燈規格是什麼？" } | ConvertTo-Json
Invoke-RestMethod -Uri "https://dancelight-ai.fly.dev/api/dancelight/ask" `
  -Method POST `
  -Headers @{ Authorization = "Bearer $secret" } `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

回傳 `{"answer": "..."}` 表示通了。

### 6. 在訓練網站填設定

[admin → AI 設定](https://kuperss.github.io/glow-up-demo/admin.html)：
- AI 提供者：**自有後端（OpenAI + 產品知識庫）**
- 後端 Endpoint URL：`https://dancelight-ai.fly.dev`
- Shared Secret Token：剛產的那串
- 啟用 AI 功能 ✓
- 儲存設定 → 測試呼叫

若仍要使用 NotebookLM，把後端 `DANCELIGHT_LLM_PROVIDER` 設為 `notebooklm`，前端 Provider 選 NotebookLM。

---

## 平常維護

### 更新後端產品資料庫 / AI 產品向量

產品資料已改為後端私有載入，不再放在前端根目錄給瀏覽器下載。

```powershell
# products_private.json 更新後，重建 AI 助理用產品向量
$env:GEMINI_API_KEY="你的 Gemini Embedding API key"
python backend\build_product_ai_index.py
```

後端回答產品問題時會自動查 `backend/data/product_ai_embeddings.npz`，
再用型號回查 `backend/data/products_private.json` 的完整規格，只把少量相關產品注入 AI prompt。

### Cookie 過期（通常數週）

收到 503 錯誤 + 「NotebookLM cookie expired」時：

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
notebooklm auth check --test  # 確認確實過期
Remove-Item -Recurse -Force "$env:USERPROFILE\.notebooklm\browser_profile" -ErrorAction SilentlyContinue
python manual_login.py        # 重產 cookie
$json = Get-Content credentials\notebooklm_storage.json -Raw
fly secrets set NOTEBOOKLM_STORAGE_JSON="$json"  # 自動觸發 redeploy
```

### 更新網站知識庫 / AI KB 向量

`kb/*.md` 與主要網站頁面更新後，重建一般知識庫向量：

```powershell
$env:GEMINI_API_KEY="你的 Gemini Embedding API key"
python backend\build_kb_ai_index.py
```

會產生：

```text
backend/data/kb_chunks.json
backend/data/kb_ai_embeddings.npz
```

後端回答公司制度、福利、品牌、業務技巧與網站內容時，會先查這份 KB RAG，再把少量相關片段注入 OpenAI prompt。

完成後從本機 `backend/` 跑：

```powershell
fly deploy -a dancelight-ai --depot=false --remote-only
```

### NotebookLM KB 更新（僅 NotebookLM 模式）

若後端 `DANCELIGHT_LLM_PROVIDER=notebooklm`，才需要到 [notebooklm.google.com](https://notebooklm.google.com) 那本筆記本（ID `1af7e026-a5e0-443e-81e7-87c09ba07a6d`）裡：
- 加新 source / 移除舊版
- NotebookLM 模式不需要重建本地 KB index；OpenAI 模式以本地 `backend/data/kb_*.npz/json` 為準

### 換 KB 筆記本

```powershell
fly secrets set DANCELIGHT_NOTEBOOK_ID=新的-uuid
# 自動觸發 redeploy
```

---

## 環境變數一覽

| 變數 | 必填 | 說明 |
|---|---|---|
| `DANCELIGHT_NOTEBOOK_ID` | ✓ | NotebookLM 筆記本 UUID |
| `DANCELIGHT_SHARED_SECRET` | ✓ | 前後端共享 token，前端 admin 要填同一個 |
| `NOTEBOOKLM_STORAGE_JSON` | ✓ | cookie JSON 內容（從 storage_state.json 讀進來） |
| `NOTEBOOKLM_STORAGE` | ✗ | cookie 檔案路徑，預設 `/app/credentials/notebooklm_storage.json` |
| `DANCELIGHT_LLM_PROVIDER` | 建議 | `openai` 或 `notebooklm`；設 `OPENAI_API_KEY` 時預設會走 `openai` |
| `OPENAI_API_KEY` | OpenAI 模式必填 | 後端呼叫 OpenAI 的 key，不會進 Firestore 或瀏覽器 |
| `DANCELIGHT_OPENAI_MODEL` | ✗ | 後端 OpenAI 預設模型，預設 `gpt-4o-mini` |
| `DANCELIGHT_OPENAI_TEMPERATURE` | ✗ | OpenAI temperature；預設不送，避免 `gpt-5-nano` 等模型拒絕非預設值 |
| `GEMINI_API_KEY` / `DANCELIGHT_EMBEDDING_API_KEY` | 建議 | 產品 RAG / 知識庫 RAG 查詢用 embedding key；沒設時退回關鍵字搜尋 |
| `DANCELIGHT_KB_CHUNKS_PATH` | ✗ | 一般知識庫 chunks JSON 路徑，預設 `/app/data/kb_chunks.json` |
| `DANCELIGHT_KB_INDEX_PATH` | ✗ | 一般知識庫向量索引路徑，預設 `/app/data/kb_ai_embeddings.npz` |
| `DANCELIGHT_PRODUCT_CATALOG_PATH` | ✗ | 私有產品 JSON 路徑，預設 `/app/data/products_private.json` |
| `DANCELIGHT_PRODUCT_INDEX_PATH` | ✗ | 私有產品向量索引路徑，預設 `/app/data/product_ai_embeddings.npz` |
| `PORT` | ✗ | 預設 8000，fly.io 會自動帶 |

---

## API

### `GET /` 
健康檢查 / 服務識別。

### `GET /health`
fly.io health check 用。

### `POST /api/dancelight/products/search`
主管後台 SKU 搜尋用；需 `Authorization: Bearer <DANCELIGHT_SHARED_SECRET>`。
只回少量候選產品，不回整包 catalog。

### `POST /api/dancelight/products/lookup`
依 SKU 查單筆產品完整資料；需 `Authorization: Bearer <DANCELIGHT_SHARED_SECRET>`。

### `POST /api/dancelight/ask`

**Headers**：
```
Authorization: Bearer <DANCELIGHT_SHARED_SECRET>
Content-Type: application/json
```

**Body**：
```json
{
  "question": "客戶說太貴怎麼回？",
  "system": "你是舞光戰將的 AI 助教...",
  "messages": []
}
```

**Response**：
```json
{ "answer": "..." }
```

**Error**：
- `401 unauthorized` — secret 不對
- `400 question is empty` — 問題為空
- `503 NotebookLM cookie expired, run manual_login.py` — cookie 過期
- `500 NotebookLM error: ...` — 其他錯誤
