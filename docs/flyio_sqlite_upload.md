# Fly.io SQLite Upload Playbook

本文件說明如何把本機 [`backend/db/tw_stock.db`](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/db/tw_stock.db) 安全上傳到 Fly.io `always-stock-api` 的 volume。

## 為什麼 DB 會上傳到 API app

這個專案使用 SQLite，不是獨立的資料庫服務。

Fly.io 上真正讀寫 DB 的地方是 `always-stock-api` 這個 app 掛載的 volume：

- app: `always-stock-api`
- remote DB path: `/data/tw_stock.db`
- volume mount: `/data`

所以「上傳 DB 到 API」的意思其實是：

- 把本機 SQLite 檔傳到 `always-stock-api` 的 persistent volume
- API 重啟後就會直接讀新的 `/data/tw_stock.db`

## 為什麼不用直接 `sftp put tw_stock.db`

本機 DB 目前約 4.9 GB，直接整顆上傳容易遇到：

- 長連線在十幾 MB 就中斷
- 上傳到一半就失敗，必須整顆重來
- 直接覆蓋正式 DB 檔風險較高

因此這份流程採用：

1. 本機壓縮成 `.zst`
2. 分割成 8 MB 小檔
3. 逐片上傳到遠端暫存目錄
4. 遠端合併、解壓、驗證
5. 備份舊 DB
6. 原子替換正式 DB
7. 重啟 `always-stock-api`

## 執行方式

### 方案 A：tmux

如果本機有 `tmux`：

```bash
tmux new -s fly-db-upload
bash backend/scripts/upload_fly_sqlite.sh
```

中斷後可回來看：

```bash
tmux attach -t fly-db-upload
```

### 中斷後續傳

如果上傳在某個分片中斷，可以沿用同一個 `UPLOAD_ID` 續傳。

例如你的遠端暫存目錄若是：

```bash
/data/upload_20260410_111448
```

就代表這次的 `UPLOAD_ID` 是 `20260410_111448`，可用下面方式續跑：

```bash
UPLOAD_ID=20260410_111448 bash backend/scripts/upload_fly_sqlite.sh
```

新版腳本會：

- 重用本機 `/tmp/always-stock-upload-20260410_111448/`
- 重用既有 `.zst` 壓縮檔與 split 分片
- 檢查遠端每一片大小
- 跳過已成功上傳的分片
- 只重傳缺失或大小不符的分片
- 在本機 `upload_state.txt` 記錄最後成功分片
- 每傳完一片預設 pause 1 秒，降低連續長連線失敗機率

### 方案 B：nohup

如果本機沒有 `tmux`，可改用：

```bash
mkdir -p backend/logs
nohup bash backend/scripts/upload_fly_sqlite.sh \
  > backend/logs/fly_db_upload.log 2>&1 &
```

看進度：

```bash
tail -f backend/logs/fly_db_upload.log
```

## 腳本做的事

[`upload_fly_sqlite.sh`](/Users/brian.yh.chien/.gstack/projects/always-stock/backend/scripts/upload_fly_sqlite.sh) 會：

1. 找出 `always-stock-api` 的 machine id
2. 啟動 machine（若目前為 stopped）
3. 將本機 `tw_stock.db` 壓縮為 `.zst`
4. 分割為 8 MB 小檔
5. 逐片上傳到 `/data/upload_<timestamp>/`
6. 在遠端合併並解壓
7. 用 SQLite `PRAGMA quick_check` 驗證新檔
8. 把遠端舊 DB 備份成 `/data/tw_stock.pre_upload.<timestamp>.db`
9. 以新 DB 取代 `/data/tw_stock.db`
10. 重啟 `always-stock-api`

## 注意事項

- 上傳過程中 `always-stock-api` 可能短暫使用舊 DB，直到 restart 後才切換到新 DB
- 腳本會先建立遠端備份，不會直接無備份覆蓋
- 如果某個分片上傳失敗，可用同一個 `UPLOAD_ID` 重跑腳本續傳；正式 DB 不會在驗證前被替換
- 這份流程只處理 `always-stock-api` 的 DB，不影響 `always-stock-web`

## 觀察進度

本機狀態檔：

```bash
cat /tmp/always-stock-upload-<UPLOAD_ID>/upload_state.txt
```

例如：

```bash
cat /tmp/always-stock-upload-20260410_111448/upload_state.txt
```

## 目前這台機器的狀態

- `tmux`：已安裝
- 建議優先用 `tmux` 執行長任務
