# 智愛週練習欠交儀表板（本地流程）

公開站：<https://ilcheck.github.io/>  
工作目錄：`/workspace/i-learner/`

## 檔案

| 檔案 | 用途 |
|------|------|
| `student-roster.json` | 靜態名冊（platformStudentId → 班／學號／姓名／應做總數） |
| `school-practice-data.json` | 上周／當周欠交名單（只含 `done < total`） |
| `scrape-inbox/prev.html` · `current.html` | 教師中心**按學生**報告 HTML |
| `week_ranges.py` | 以香港時間計算上周／當周周一至五 |
| `report_urls.py` | 產生上周／當周 `r_teachers_report_student.php` URL |
| `scrape_reports.mjs` | Playwright 登入＋下載兩份報告 HTML |
| `scrape_and_publish.sh` | 晨檢一鍵：scrape → apply → publish |
| `apply_reports.py` | 解析 HTML → 合併名冊 → 寫 `school-practice-data.json` |
| `build_dashboard.py` | 由 JSON 產生 `site/index.html` |
| `publish_dashboard.sh` | 建置並 commit／push 到 `origin main` |

## 憑證（KRU `ilearner`）

密碼**只**存在 KRU vault 項目 `ilearner`（modules: `username` + `password`，皆 Hidden）。

- **永遠不要**把密碼寫進 repo、`.env`、Secure Form，或印到 log。
- **推薦（無人值守）**：用 KRU MCP `terminal_run` 注入環境變數後跑 pipeline（`secretEnv` 由 KRU 本機代入，不會回傳明文）：

```text
item: ilearner
secretEnv:
  ILEARNER_USERNAME: username
  ILEARNER_PASSWORD: password
command: bash /workspace/i-learner/scrape_and_publish.sh
```

- **備選（agent 互動）**：`KRU_FILL=1` 啟動並載入 KRU unpacked extension  
  `/home/box/.local/opt/KRU_current/browser-extension/`，bridge 需已 pair（`kru browser status`）。  
  腳本會依序 focus 欄位，agent 呼叫 MCP `credential_fill`（item=`ilearner`，target=`browser`）：  
  ① username → ② password → ③ 腳本選平台「智愛中文平台」→ ④ Submit。

## 晨檢一鍵

```bash
bash /workspace/i-learner/scrape_and_publish.sh
```

（實際晨檢應透過上方 KRU `terminal_run` + `secretEnv` 啟動，否則會因缺少環境變數而失敗。）

選項：

| 變數 | 作用 |
|------|------|
| `DRY_RUN=1` | 不登入；寫 stub HTML，測腳本結構 |
| `SKIP_PUBLISH=1` | scrape+apply 後不 push |
| `HEADED=1` | 有頭瀏覽器（`DISPLAY=:12`） |
| `KRU_FILL=1` | 改用 extension + `credential_fill` |
| `SCRAPE_OVERALL_SEC` | 整段 scrape 逾時秒數（預設 720 ≈ 12 分） |

結構測試：

```bash
DRY_RUN=1 bash scrape_and_publish.sh
```

## Playwright 安裝（本專案本地）

```bash
cd /workspace/i-learner
npm install
npx playwright install chromium
```

依賴在 `node_modules/`（已 gitignore），不污染系統 global。

## 手動步驟（無 Playwright 時）

1. 登入 [智愛中文平台](https://chinese.i-learner.com.hk/)（平台選「智愛中文平台」）。
2. 開啟**按學生**報告（`r_teachers_report_student.php`，全校 `class=0&sch=3147`，自訂周一至五），分別把上周／當周 HTML 存成：
   - `scrape-inbox/prev.html`
   - `scrape-inbox/current.html`
3. 套用報告：

```bash
python3 apply_reports.py \
  --prev-html scrape-inbox/prev.html \
  --curr-html scrape-inbox/current.html
```

4. 發佈（建置 HTML + push Pages）：

```bash
bash publish_dashboard.sh
```

只預覽、不推送：

```bash
python3 build_dashboard.py --also-root
# 或
DRY_RUN=1 bash publish_dashboard.sh
```

## 報告 HTML 欄位（預期）

需要**逐學生列**，常見欄名（可容錯同義詞）：

- 班別／班級
- 學號
- 姓名／名字
- 已完成練習數目（或完成數／完成次數）
- （可選）總練習數、學生／平台 ID

**不要**用班級匯總表「本地練習學生表現統計」（只有班別／參與人數／完成率等，沒有逐生學號姓名）。

不確定欄位時：

```bash
python3 apply_reports.py --dump-sample scrape-inbox/current.html
```

有名冊時只更新 `done`，**不會改姓名**。全校報告若匹配率低於約 50%，腳本會拒絕把其餘學生當 done=0（可加 `--allow-partial` 強制）。重建名冊（需平台學生 ID）：

```bash
python3 apply_reports.py ... --rebuild-roster
```

## 顯示規則

- 1–3 年級標題：`1A組`…`3D組`（平台 `1GB` → `1B組`）
- 4–6 年級：`4A`…`6C`
- 名單：`1a(3)鍾鳴妤`（班別小寫字母 + 學號 + 姓名）
- 班別預設收合；副標只顯示「全班 N 人」

## Login 表單選擇器（已探測）

- Form: `#login_form` → `login.php`
- Username: `input[name="username"]`
- Password: `input[name="password"]`
- Platform: `select#platform[name="loginsys"]` → **第 2 項** value `reading`（智愛中文平台）；登入順序 username → password → **然後**選平台 → Submit
- Submit: `#login_form input[type="submit"]`
- `validateForm()` 要求先選平台（腳本在填完帳密後、Submit 前選 `reading`）
