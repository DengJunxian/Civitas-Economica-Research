# Civitas Lab HTTP API v1 与课堂材料

## 启动

```sh
.venv/bin/python -m pip install -r requirements-api.txt
.venv/bin/python -m uvicorn lab.api:app --port 8000
# 交互式文档：http://localhost:8000/docs
```

设置 `CIVITAS_LAB_TOKEN` 后，启动运行、校验归档和调用 AI 的接口要求 `Authorization: Bearer <token>`。未设置时仅适合本机或封闭试点网络；账号、课程权限和审计日志尚未实现（规划 R7）。

## 运行模型

- `POST /v1/experiments` 先过能力门禁，再检查 `confirmed_by`；通过后进入后台队列，立即返回 202 和 `job_id`。默认串行执行，课堂时延可预测。
- 同一 `idempotency_key` 重复提交相同请求返回原任务；用于不同请求返回 409。
- 任务状态在内存中；进程重启后任务记录消失，但对照归档保存在 `outputs/lab/`，可通过 `/v1/comparisons` 读取。
- 失败运行保留在归档与任务状态中，不显示为成功。

## 接口

| 方法与路径 | 用途 | 约束 |
| --- | --- | --- |
| `GET /v1/courses?q=&program=` | 课程、知识点与关联场景 | 每项带 `review_status`（课程映射待教师核对） |
| `GET /v1/courses/{code}` | 单门课程、可运行场景与课堂包 | |
| `GET /v1/scenarios` / `{id}` | 27 个场景索引；可运行场景返回 ScenarioSpec 与 scenario_hash | 未开放场景 `spec` 为 null，并给出所需引擎 |
| `GET /v1/scenarios/{id}/card` | 课题卡（Markdown） | |
| `POST /v1/scenarios/validate` | 门禁结论 | `scenario_id` 与内联 `scenario` 二选一；结构错误 422 |
| `GET /v1/registry` | 观测量、结果指标定义与逐步列 | |
| `GET /v1/lessons` / `{id}` | 课堂包列表与 LessonSpec | |
| `GET /v1/lessons/{id}/guide` | 教师指导卡（Markdown） | |
| `GET /v1/lessons/{id}/worksheet?level=` | 本科/硕士学生工作纸 | 只要求课程内匿名编号 |
| `GET /v1/lessons/{id}/pilot-record` | 课堂试点记录表（规划 9.2：用时、中断、匿名产出统计、教师评价、审查签字） | |
| `GET /v1/lessons/{id}/feedback-form` | 匿名学生反馈问卷（告知用途、可见范围与保留期限；概念测验题由授课教师编写） | 不收集姓名学号 |
| `POST /v1/experiments` | 创建对照实验任务 | 门禁拒绝 422（附报告）；缺确认 403；可选令牌 |
| `GET /v1/experiments/{job_id}` | 任务状态与进度 | |
| `GET /v1/comparisons` / `{id}` | 对照归档列表与 lab manifest | id 只接受安全字符，且必须位于归档根目录下 |
| `GET /v1/comparisons/{id}/evidence?metric=` | 证据卡：带 run_id 的数值、配对差异、交互差异、限制 | NaN 输出为 null |
| `GET /v1/comparisons/{id}/steps?observable=` | 图表用逐步数据 | 价差不可定义为 null，不补 0 |
| `GET /v1/comparisons/{id}/report` | 证据报告（Markdown） | |
| `POST /v1/comparisons/{id}/verify` | 归档完整性与场景身份校验 | 可选令牌 |
| `POST /v1/scenarios/draft` | AI 场景草案：`{"course_code", "goal"}` → `ScenarioDraft.to_dict()` | 从不运行；`runnable=false` 表示门禁拒绝；失败见下文；可选令牌 |
| `POST /v1/comparisons/{id}/explain` | AI 证据解释：`{"question", "metric"?}` → `Explanation.to_dict()` | 对照只按 id 解析；数值只来自证据卡；失败见下文；可选令牌 |

## AI 教学接口

两个接口是 `lab/ai.py`（见 [LAB.md](../LAB.md)"AI 教学编排"）的薄封装，规则与 CLI、AI 教学席相同：

- **草案从不运行。** `POST /v1/scenarios/draft` 只生成草案、强制 `review.status=draft`、过能力门禁，并存入 `<归档根>/ai_drafts/<draft_id>/`；不创建任务，也不写对照归档。门禁拒绝的草案仍返回 200（`status=draft`、`runnable=false`、`capability` 中给出原因），供教师修改。运行草案只能把返回的 `scenario` 原样或修改后提交给 `POST /v1/experiments`，并照常需要 `confirmed_by`（缺失 403；模型若关闭教师确认，门禁以 `APPROVAL_REQUIRED` 拒绝，422）。
- **解释只读证据卡。** `POST /v1/comparisons/{id}/explain` 用与其他对照接口相同的规则解析 id（安全字符、必须是归档根目录下带 `lab_manifest.json` 的目录），不接受客户端传入的文件路径；请求体只允许 `question` 和 `metric`，多余字段 422。解释前先做归档完整性校验；回答中的数值由代码从证据卡代入并编号，`citations` 给出事实 ID、数值、指标和 run_id，`answer` 末尾总是附"数据来源"和"模型限制"。
- 每次调用写入草案目录或对照目录的 `ai_calls.jsonl`，解释另写 `ai_explanations.jsonl`；每日预算与 CLI、页面共用同一账本（`CIVITAS_LAB_AI_LEDGER`，默认 `outputs/lab/ai_usage.jsonl`）。
- 预算检查与预留在同一把锁内完成，通过检查的调用在调用模型之前就记入账本，所以同时到达的请求（以及同时运行的 CLI 与页面）合计也不会超过当日上限，多出的请求返回 429；跨进程的文件锁只在 POSIX 上有效（见 [LAB.md](../LAB.md)"调用日志与预算"）。

### 失败响应

失败不会降级为模板或离线占位文本，也不会合并成笼统的 500。HTTP 状态按失败代码区分，响应体为 `{"detail": <to_dict()>}`，其中 `status` 为 `"failed"`，`failure` 为失败代码，`message` 为中文说明，`call` 为本次调用尝试的日志记录（输入、课程、指标或归档检查未通过时为 null；预算不足或未配置模型时有记录，`dispatched=false`）。解释失败时 `answer` 为空串；`unsupported_number` 另带 `violations`（违规项）和 `rejected_text`（被拒的模型原文，仅供教师审计，不得作为结论展示）。

| HTTP | 失败代码 | 含义 | 接口 |
| --- | --- | --- | --- |
| 404 | `unknown_course` | 课程目录中没有该课程代码 | 草案 |
| 422 | `empty_input` / `input_too_long` | 目标或问题为空，或超过 `CIVITAS_LAB_AI_MAX_INPUT_CHARS` | 两者 |
| 422 | `unknown_metric` | `metric` 不是已定义的结果指标 | 解释 |
| 409 | `archive_not_verified` | 对照归档未通过完整性校验，不解释被修改的归档 | 解释 |
| 409 | `no_evidence` / `not_simulation` | 没有完成的运行，或归档不是实际仿真 | 解释 |
| 429 | `budget_exceeded` | 当日调用已达 `CIVITAS_LAB_AI_DAILY_CALLS`，未调用模型 | 两者 |
| 502 | `llm_error` | 模型调用失败或没有返回有效结果 | 两者 |
| 502 | `invalid_json` / `schema_invalid` | 模型输出不是单个 JSON 对象，或不符合 ScenarioSpec v0.1 | 两者 / 草案 |
| 502 | `unsupported_number` | 解释含证据卡中不存在或未标注来源的数值，整段作废 | 解释 |
| 503 | `llm_not_configured` / `llm_dependencies_missing` | 服务器未配置 API key 或未安装 LLM 依赖；离线规则实验不受影响 | 两者 |

401（令牌）、404（未知对照）和请求体结构错误（422，`detail` 为字段错误列表，没有 `failure`）与其他接口相同。表中状态码与失败代码的对应在 `lab/api.py` 的 `AI_FAILURE_STATUS` 中，`/docs` 中每个接口也列出了各状态码对应的失败代码；`tests/test_lab_api.py` 检查 `lab/ai.py` 的每个失败代码都有映射，并且都写进了本文档。测试用替换 `lab.ai.default_router` 的假路由，不访问网络。

## 课堂包与课堂材料

`lab/content/lessons/` 有 8 个课堂包：45 分钟旗舰课 LESSON-MKT-001，以及 EXP-01/02/05/06/07/08/10 各一个 20 分钟课堂包（MKT-002/003、BEH-001/002、MKT-004/005、EMP-001）。每个课堂包包含教学目标、连续的课堂阶段、工作纸问题（对应评分维度）、本科/硕士分层任务（规划 3.1）、教师引导策略（课前预测、操作顺序、异常追问、负结果讨论，规划 5.1）和评分量表（默认为规划附录 C）。

EXP-06 与 EXP-08 的课堂包按模型实际结果设计：前者讨论"恐慌几乎不扩散时，网络差异为何很小"，后者讨论"费用下降而成交不变说明税率只进入结算通道"。它们不承诺模型给不出的效果。

`lab/export.py` 生成 Markdown 材料：课题卡、教师指导卡、分层学生工作纸、证据报告、试点记录表、学生反馈问卷；Streamlit 的课程中心、场景实验室和对照分析页提供对应下载，侧栏可切换 Markdown/PDF。证据报告只读取对照归档，每个数值都能对应到 run_id，并附复核命令。

所有返回课堂材料的接口（课题卡、指导卡、工作纸、试点记录表、反馈问卷、证据报告）都接受 `?format=pdf`。PDF 由 `lab/pdf.py` 按同一份 Markdown 渲染，中文使用 reportlab 自带的 STSong-Light CID 字体（不需要字体文件；该字体缺少的 `·`、`−` 分别替换为 `｜`、`-`）；未安装 reportlab 时接口返回 501。

## 内容版本与回归

`tests/test_lab_scenarios.py` 有两类守护：

1. **版本**：`tests/golden/lab_content_versions.json` 记录每个场景与课堂包的版本和内容哈希。内容改了而版本号没有提升，测试失败；提升版本后用 `CIVITAS_UPDATE_GOLDEN=1 pytest tests/test_lab_scenarios.py -k version` 更新记录。
2. **课堂包中的事实**：配对组在首个差异事件前路径相同；停牌期间零成交、零订单但仍有卖出意图、停牌撤单为 0；降税后费用下降而成交量不变；默认参数下恐慌峰值不超过 12、无边网络等于种子数；EXP-10 中配对差的标准差远小于种子间的组标准差。模型改动使这些事实不再成立时，测试会指出需要修改的课堂包。
