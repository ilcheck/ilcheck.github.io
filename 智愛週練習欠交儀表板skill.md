# 智愛週練習欠交儀表板

## 何時使用
教師要檢查智愛網上中文平台「教師中心」成績／練習完成度，並產出全校欠交 HTML（可切換上周／當周、班別收合）時執行本 skill。

## 前置
- 網站：`https://chinese.i-learner.com.hk/`
- 登入時平台選「智愛中文平台」（`loginsys=reading`）
- 工作目錄：`/workspace/i-learner/`（`school-practice-data.json`、`build_dashboard.py`、`apply_reports.py` 等）
- 公開站：`https://ilcheck.github.io/`（repo `ilcheck/ilcheck.github.io`）

## 憑證（優先順序）
1. **KRU（優先，長期待機）** — MCP `user-kru`；項目名 `ilearner`（username + password，皆 Hidden）。
   - 主路徑：`terminal_run` + `secretEnv` 跑 `bash /workspace/i-learner/scrape_and_publish.sh`。
   - 用 `credential_fill` 時，不要詢出或記錄明文。
2. **Bitwarden（備援）** — 項目「智愛中文平台」。
3. **Secure Form** — 兩者皆不可用時退回；不要叫用戶把密碼貼上聊天。

## 日期規則
- **上周**：對上一個完整星期一至五（相對檢查當日）
- **當周**：本周星期一至五（周中可能少於 5）
- 自訂查閱：`timep=4`，`from`/`to`，`week=0,1,2,3,4`（周一至五）

## 擷取步驟
1. 用 Playwright／curl cookie＋headed Chrome（見 `scrape_reports.mjs`）登入；平台選「智愛中文平台」。
2. 入教師中心（`sch=3147`）。
3. 班別優先「全部班級統計」（`class=0`）。
4. 分別拉上周同當周兩段自訂日期，讀每列：`platformClass`、`schoolClass`、學號、姓名、已完成練習數目／總練習數。
5. `apply_reports.py` 將**欠交與做滿**學生皆寫入 `school-practice-data.json`（`week.total` 仍＝欠交人數；`week.complete`＝做滿人數；儀表板欄位為「已完成 0..practiceTotal 次」，含做滿欄）。

## 班別顯示
- 平台 1–3 年級為 GA–GD；畫面 header 用「1A組」…「3D組」（例如 1GB → **1B組**）。
- 4–6 年級用 4A–6C。
- 每行人名格式：`班別(學號)人名`，班別字母小寫，例如 `1a(3)鍾鳴妤`。
- 班別 sub header 只顯示「全班 N 人」，**不顯示**「欠交 N 人」。

## HTML 產出
1. 更新／確認 roster（`student-roster.json`）。
2. 跑 `python3 build_dashboard.py` 生成 `site/index.html`（可 `--also-root`）。
3. **每次重建必須鎖上當下更新時間**（格式如 `YYYY-MM-DD HH:MM HKT`）。
4. 頁面必須有：上周／當周選項；全部班別預設收合，撳 header 先展開；按「已完成 0…practiceTotal 次」分欄（**含做滿欄**）。
5. 複製到 `/workspace/i-learner/site/index.html`，commit 並 push 到 `ilcheck/ilcheck.github.io` 嘅 `main`，令 `https://ilcheck.github.io/` 更新。

## 交付
- 用對方語言（通常 zh-Hant-HK）簡述兩周欠交人數，附上網站連結。
- 登入失敗時先試 KRU，再查備援；不要洩密碼。

## 日常自動化（routine 用）
每日早上（預設 7:30 當地時間）重跑本流程並交出更新 HTML／網站；優先 KRU MCP（`ilearner` + `secretEnv`）登入；不要把帳密寫進 routine。
