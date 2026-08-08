# pawMYjob

一个本地优先的求职流水线：每天从配置的 LinkedIn 搜索中抓取职位，保留原始字段和快照，只对新增/描述变化的职位做门槛筛选、语言/技能/简历匹配，并为高分职位生成 LaTeX 简历和 cover letter。

## 重构后的流程

```text
config/searches.json
        ↓ 每日 15:00（TZ）
Playwright headless crawl + raw daily snapshot
        ↓ upsert / compare previous state
SQLite jobs + observations
        ↓ only new or changed jobs
Swedish / citizenship-security / senior tags
        ↓ exclude citizenship-security jobs
language score + existing explicit-skills score + Gemini fit score
        ↓ average > 6
resume.tex + cover_letter.tex (+ optional resume.pdf)
        ↓
Flask :8000  ·  email daily summary
```

数据库在 `worker_env/stored_data/pawmyjob.sqlite3`，原始每日快照在 `worker_env/stored_data/snapshots/`，生成文件在 `worker_env/stored_data/artifacts/<job_id>/`。旧的 `linkedin_jobs_*.json` 也继续写出，原始抓取字段不会丢失。

## Playwright 在服务器上如何工作

服务器通常没有图形界面，因此 Docker 默认使用 `PLAYWRIGHT_HEADLESS=true`。第一次 LinkedIn 登录不能在无头模式里“手动完成”，只需要做一次 headed bootstrap：

```bash
# 在有桌面的本机执行；浏览器打开后手动登录并在终端按 Enter
python -m worker_env.src.login_and_save_state

# 确认下面文件作为受保护的 secret volume 复制到服务器
worker_env/stored_data/linkedin_state.json
```

如果通过 Google 登录时出现“此浏览器或应用可能不安全”，先尝试使用本机已安装的 Chrome：

```bash
python -m worker_env.src.login_and_save_state --channel chrome
```

如果仍被拦截，可以使用一个独立的、由你手动操作的 Chrome profile，再通过 CDP 导出登录态：

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/worker_env/stored_data/chrome-login-profile" \
  https://www.linkedin.com/login
```

保持这个 Chrome 窗口打开并完成登录，另开终端执行：

```bash
python -m worker_env.src.login_and_save_state \
  --cdp-url http://127.0.0.1:9222
```

不要把日常 Chrome 的默认 profile 作为 `user-data-dir` 传入自动化程序；登录态文件和临时 profile 都应视为敏感数据。

远程服务器可以在本机完成登录后用安全复制方式传到服务器，之后每日抓取只读这个 storage state。不要把它提交到 Git；它等价于登录态。若 LinkedIn 让它失效，重新执行 bootstrap 并替换文件。若服务器确实有桌面/远程桌面，也可以在那里运行 bootstrap，但生产服务仍建议 headless。

## 搜索配置

编辑 `config/searches.json`，可以增加 Sweden、Germany、New Zealand/Auckland 或任何关键词组合：

```json
[
  {
    "name": "Germany · Data Engineer",
    "country": "germany",
    "location": "Germany",
    "geo_id": "101282230",
    "query": "data engineer",
    "enabled": true,
    "posted_window": "7days"
  }
]
```

也可以直接在网页顶部编辑并保存。UI 的 `Past 24h / 7 days / All time` 是展示过滤，其中 `Past 24h` 与 LinkedIn 的 `f_TPR=r86400` 保持一致；LinkedIn 抓取窗口由每项 `posted_window` 控制。

## Gemini 与 apply skill

将 `.env.example` 复制为 `.env`，填写 `GEMINI_API_KEY`。当前默认模型是 `gemini-3.6-flash`，可通过 `GEMINI_MODEL` 覆盖。

Gemini 请求分为两种节流策略：匹配评估请求默认间隔 8 秒（`GEMINI_REQUEST_DELAY_SECONDS`），生成 resume/cover letter 的请求默认间隔 30 秒（`GEMINI_GENERATION_DELAY_SECONDS`）。生成节流使用 `worker_env/stored_data/.gemini_generation_rate` 加跨进程文件锁，因此 Docker 的 web 和 scheduler 共享同一个生成请求节奏；每次重试也会重新等待完整间隔。若 Google 返回 429，程序会读取 `Retry-After`/`retryDelay` 并采用退避等待。

旧配置中的 `GEMINI_DELAY_SECONDS` 仍可作为评估请求的兼容别名。Google AI Studio/API 的免费层和限额会随时间变化，不要把它当作无限生产配额。

`apply` 仓库本身是一个 `SKILL.md` 规范，要求不虚构简历事实、输出 ATS 友好的 LaTeX，并在有 `pdflatex` 时编译 PDF。此项目用 Gemini agent 执行同一套约束；如果本地已 clone skill，可以设置：

```env
APPLY_SKILL_PATH=/path/to/apply
COMPILE_LATEX=true
```

没有 `pdflatex` 时仍会生成 `.tex`，UI 可以直接下载。只有语言、技能、Gemini 三项平均分 `> 6` 才会生成申请材料。

## 本地运行

```bash
python3 -m venv worker_env/.venv
source worker_env/.venv/bin/activate
pip install -r worker_env/requirements.txt
playwright install chromium

cp .env.example .env
# 放入 resume.md 或 resume.pdf，以及上面的 storage state
python -m worker_env.src.run_pipeline --no-email
python -m worker_env.src.app
```

打开 <http://localhost:8000>。页面支持三阶段 tab、国家/岗位过滤、JSON 展开、拖拽推进和申请状态：未投递、已投递、已投递被挂、已面被挂、Offer。

## Docker 与每日 15:00

```bash
cp .env.example .env
# 先把 resume、linkedin_state.json 放到 worker_env/stored_data/
docker compose up -d --build
```

`web` 暴露 8000，`scheduler` 按 `TZ` 在每天 15:00 执行一次。邮件汇报只在 `.env` 填好 `SMTP_HOST`、`REPORT_FROM_EMAIL`、`REPORT_TO_EMAIL` 后发送；没有 SMTP 配置不会尝试联网发信。

## 安全边界

- 系统只生成材料和提供下载，不自动提交申请。
- `.env`、LinkedIn storage state、简历和生成材料都应留在受保护的 volume。
- 现有 `.env` 中如曾放过真实 API key，建议立即轮换，并只用 `.env.example` 作为模板。
