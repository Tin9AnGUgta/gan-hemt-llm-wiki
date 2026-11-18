# GaN HEMT 研究入門 LLM Wiki

從通道與緩衝層到元件可靠度，為正在修習電子學與電磁學的電機系學生建立可追溯、可擴充的研究知識庫。

## 目前進度

工程骨架與 Graph 1.1 已建立並實際執行。五篇 PDF 共 508 頁已完成私人副本、雜湊及分頁解析；五篇第一輪私人筆記、6 個概念頁與 5 個推導頁已交 H1，等待使用者審閱。工具的 15 項回歸測試及相對檔案連結檢查已通過。

第一輪不等於全文全部公式、圖表與實驗已驗證。研究工作稿與原始 PDF 維持私人；正式跨論文整合、大綱、HTML 與 Pages 尚未進行。進度以實際紀錄為準，本頁不代表已完成全部學術驗收。

- [Wiki 索引](wiki/index.md)
- [學習路線](wiki/learning-path.md)
- [來源清冊](sources/catalog.json)
- [Graph 操作](workflow/README.md)
- [人工審核與節點規格](workflow/node-contracts.md)
- [公開與權利原則](publication/README.md)
- [執行紀錄](records/README.md)
- [最新公開進度](records/latest.json)
- [工程與公開檢查範圍](records/implementation-checks.json)
- [反思紀錄](report/reflection.md)
- [維護規範](AGENTS.md)

## 本機與雲端操作

需要 Python 3.10 以上；以 `python -m pip install -r requirements.txt` 安裝 PDF 解析依賴。原始資料另行提供給執行環境，不能放進公開 Repo。

```sh
python tools/wiki.py check
python -m unittest discover -s tests -v
python tools/wiki.py start
python tools/wiki.py status
python tools/wiki.py ingest
```

`start` 建立私人執行狀態；`ingest` 實際登錄、保存並解析私人輸入。需要在私人工作區提供 `inputs.json`，格式參見 [操作說明](workflow/README.md)。未提供來源時會停止並清楚回報，不會宣稱匯入成功。

其他節點由 Codex 產生可核對的成果後，再經工具驗證及記錄。這是 Codex 配合小型工具執行的 Graph，不宣稱 Python 腳本能自行理解論文。

## 隱私與著作權

原始 PDF、抽取全文、原始圖表及私人研究稿不公開。來源標註不代表使用授權。公開頁面只收錄經檢查的自行組織內容；未公開或保密研究不因轉述而公開。HTML 教學簡報將於大綱人工審核後建立，Pages 尚未部署。
