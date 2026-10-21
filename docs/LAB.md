# Civitas Lab 教学层

Civitas Lab 是建立在标准实验服务之上的课程驱动教学层，对应《Civitas Lab 课程知识点—实验场景映射与产品开发规划 v1.0》（下称"规划"）。它自己不做任何仿真：校验教学场景文档，把受支持的部分编译成核心 `ExperimentConfig`，交给 `ExperimentRunner` 运行，再从运行归档读取数据做对照。`ExperimentRunner` 仍是唯一事实来源（规划 D3）。

```text
课程目录 (lab/content/courses.yaml) → 场景索引 (scenario_index.yaml)
          ↓
ScenarioSpec / LessonSpec v0.1 (lab/specs.py)
          ↓
确定性能力门禁 (lab/capability.py)：引擎能力、事件时序、对照设计、来源与审批
          ↓  教师确认（不可省略）
编译器 (lab/compiler.py)：每个 (组, 种子) → 核心 ExperimentConfig
          ↓
ExperimentRunner × N（核心 manifest 不变）+ lab_manifest.json (lab/service.py)
          ↓
观测量与结果指标 (lab/observables.py) → 配对对照 / 2×2 交互 / 证据卡 (lab/compare.py)
          ↓
CLI `main.py lab …` / Streamlit 课程中心、场景实验室、对照分析、AI 教学席 (ui/lab.py)
```

AI 只在两处接入（lab/ai.py，见下文"AI 教学编排"）：把教学目标写成场景草案，草案照常走门禁与教师确认；以及依据证据卡回答问题，数值经确定性核验。

## 场景协议

`ScenarioSpec` 按规划附录 B 设计，示例见 [EXP-05.yaml](../lab/content/scenarios/EXP-05.yaml)。要点：

- **只有市场/社会参数覆盖和事件可以在组间变化**。人口规模、步数、初始价格与决策语义是世界级字段，所有组相同，保证配对种子组的初始世界一致。
- `question`、`objectives`、`limitations`、`observables`、`approval`、`review` 等教学元数据**不进入核心配置**；它们留在场景文档中，通过 `lab_manifest.json` 与运行关联。
- 世界类型、事件类型、拓扑和决策模式在协议里是普通字符串，由门禁给出面向教学的拒绝原因，而不是泛化的 schema 错误。

`LessonSpec` 描述一节课：阶段必须连续且覆盖声明时长，评分量表权重和为 100，引用的场景与组别必须存在。旗舰课见 [LESSON-MKT-001.yaml](../lab/content/lessons/LESSON-MKT-001.yaml)。

## 能力门禁规则

门禁不调用 LLM，任何来源（教师手写或 `lab/ai.py` 生成的 AI 草案）都必须通过它。状态为 `supported`、`supported_with_warnings` 或 `rejected`，每条问题带代码、位置和中文说明。

| 类别 | 拒绝（error） | 提示（warning） |
| --- | --- | --- |
| 引擎能力 | 非 `single_asset_orderbook` 的世界；降准、政策利率、财政支出、汇率等事件（返回所需引擎与规划阶段，如"需要银行账本模型（P3）"）；谣言文本、辟谣、做市商撤退、卖空、融资、直接设定价格；`llm`/`mock` 决策；未知拓扑 | — |
| 事件时序 | 步数越界；panic 非整数或超过人口；sentiment 超出 [-1,1]；税率超出 [0,0.1)；重复 halt；无对应 halt 的 resume；同步停复牌；同步同类事件重复 | halt 后未复牌；印花税率高于 1%；halt/resume 带 value |
| 对照设计 | 组 id 或种子重复；参考组不存在；2×2 映射无效；运行次数 > 40；计算量 > 200 万 agent-steps | 只有一个组；与参考组设置完全相同；只有一个种子；运行次数 > 20 或计算量 > 20 万 |
| 证据与审批 | 观测量或结果指标无法由归档计算；结果来源不是 simulation；允许把教学 CSV 当运行；关闭教师确认 | — |
| 最终兜底 | 编译后的配置被核心 `ExperimentConfig` 拒绝 | — |

同一步的多个事件按固定优先级执行：复牌 → 税率 → 情绪 → 恐慌 → 停牌，不依赖前端点击顺序。

## 归档与追溯

`run_scenario` 创建 `outputs/lab/<场景>-<时间>-<id>/`：

- `scenario.json`、`capability.json`：实际运行的场景版本与门禁结论；
- `lab_manifest.json`：scenario_hash、课程代码、审核状态、确认教师、参考组、种子，以及每次运行的组、种子、config_hash、run_id 和状态。每次运行后重写，中断或失败也留下记录；
- `runs/<run_id>/`：标准运行归档，与 `main.py run` 产出完全相同，可单独 `main.py verify` / `reproduce`。

`verify_comparison` 校验 scenario_hash、每次运行的 config_hash 是否等于重新编译的结果，并对每个子运行执行核心完整性校验。

## 观测与对照口径

- 所有数值来自运行归档文件；失败运行列出但不纳入比较，不补造数据。
- 核心指标在任一侧无挂单时记 `spread=0.0`；教学视图把它标为**不可定义**（空值），并统计"价差不可定义步数"。核心归档不做修改。
- 配对差异逐种子计算 `Y(组) − Y(参考组)`，报告均值、标准差、正/负/零计数和种子集合的描述性 bootstrap 区间（固定随机种子，n≥3 才给出）。这是模型条件下的描述，不是现实因果估计。
- 2×2 设计报告逐种子 Δ = [Y(both) − Y(a_only)] − [Y(b_only) − Y(neither)]。
- 证据卡（`main.py lab compare`）只包含带 run_id 的数值、指标定义、失败运行、假设与限制，是 AI 证据解释唯一可引用的数据来源。

## AI 教学编排（规划 W10–11）

模型是不受信任的提议者：它只产出草案或文字，是否可运行由确定性门禁决定，是否运行由教师决定，数值由代码从证据卡代入。调用走仓库已有的 `core.llm` 路由（DeepSeek → 智谱），关闭离线替身（`allow_offline_fallback=False`），不引入新 SDK。未配置 API key 时 AI 功能返回明确失败，离线规则实验不受影响。

```text
教学目标 + 课程 ──LLM──▶ JSON ──▶ ScenarioSpec v0.1 校验 ──▶ 强制草稿 ──▶ 能力门禁 ──▶ 教师确认 ──▶ run_scenario
运行归档 ──verify_comparison──▶ 证据卡 ──▶ 事实表 ──LLM──▶ 带 {{事实ID}} 的回答 ──▶ 数值核验 ──▶ 代入数值 + 来源 + 模型限制
```

### 场景草案 `draft_scenario(goal_text, course_code)`

- 提示词给出白名单（世界类型、事件及其 value 语义、拓扑、观测量、结果指标）、ScenarioSpec 的 JSON Schema 和该课程的一个标准场景作格式示例。需要其他引擎的机制（如降准 `rrr_cut`）要求模型如实写出事件类型，由门禁说明缺少哪个引擎，而不是用情绪冲击近似。
- 教师目标放在 `<teacher_goal>` 数据标签内，文本中的同名标签与控制字符会被去除；防注入的最终保证不是提示词，而是下面这些确定性步骤。
- 模型输出必须是单个 JSON 对象（允许包在一个 ```json 代码块中），并通过 `ScenarioSpec` 校验（`extra="forbid"`，模型无法夹带 `auto_run` 之类字段）。不合法即返回 `invalid_json` / `schema_invalid`，不会用模板替代。
- 代码强制：`review` 固定为 `draft`（AI 不能审核自己的草案）；`scenario_id` 加 `-AI` 后缀，运行归档可区分 AI 草案与标准场景；`course_codes` 补上所选课程。每项改动都写进 `adjustments` 展示给教师。`approval` 与 `source_policy` 不改写：模型若关闭教师确认，由门禁以 `APPROVAL_REQUIRED` 拒绝。
- 草案从不运行。结果连同门禁报告写入 `outputs/lab/ai_drafts/<draft_id>/`（`request.json`、`ai_calls.jsonl`、`scenario.yaml`、`capability.json`）；运行仍须 `main.py lab run <scenario.yaml> --confirm <教师>`，或在 AI 教学席填写确认教师并勾选"已逐项审阅"。教师在页面上修改 YAML 后重新过门禁，仍为草稿。

### 证据解释 `explain_comparison(comparison_dir, question)`

- 先执行 `verify_comparison`：scenario_hash、config_hash 或子运行完整性有问题时拒绝解释（`archive_not_verified`），不调用模型。失败运行不算篡改，按证据卡列出、不纳入比较。
- 证据卡展开成事实表，每个事实带指标和背后的 run_id；NaN（如 n<3 时的 bootstrap 区间）不可引用。

| 事实 ID | 含义 |
| --- | --- |
| `v.<组>.s<种子>` | 单次运行的指标值（1 个 run_id） |
| `g.<组>.{n,mean,sd,min,max}` | 组内各种子的描述统计 |
| `d.<组>.{n,mean,sd,min,max,positive,negative,zero,bootstrap_low,bootstrap_high}` | 相对参考组的逐种子配对差汇总 |
| `d.<组>.s<种子>` | 单个种子的配对差（2 个 run_id） |
| `i.{n,mean,sd,min,max,bootstrap_low,bootstrap_high}`、`i.s<种子>` | 2×2 交互差异 |
| `f.count` | 失败且未纳入比较的运行次数 |

- 模型只输出 `{"answer": "…"}`，数值写成 `{{事实ID}}`。确定性核验（`check_explanation`）规则：
  - 占位符必须存在于事实表，否则整段作废；
  - 紧贴占位符前写出的数字必须在其精度内等于该事实（`3.12%{{d.B.s41}}` 对应 0.0312 可以；把非零值写成 0、符号相反都不行）；
  - 其余任何阿拉伯数字（含全角）和"百分之…"一律视为无来源数值，整段作废（`unsupported_number`）。例外只有归档标识：场景 id、对照 id、run_id、组 id 与组名，以及"种子"后属于本次种子集合的编号。
- 通过核验后由代码代入数值并编号，追加"数据来源"（事实 ID、数值、含义、run_id）和"模型限制"：场景 `limitations` 全文、`output_origin=simulation` 与场景版本声明、失败运行说明，无论模型是否提到。
- 每次解释（含被拒的原文与违规项）追加到对照目录的 `ai_explanations.jsonl`，供课后审计 AI 的解释责任。

### 调用日志与预算

- 每次调用（含因预算或缺少 key 未发出的尝试）写一行 JSON 到上下文目录的 `ai_calls.jsonl`（解释写入对照目录，草案写入草案目录），并写入全局账本 `outputs/lab/ai_usage.jsonl`（通过预算检查的调用在账本中另有一行预留，见下条）。字段：`call_id`、`purpose`、`provider`、`model`、`route_mode`、`fallback_chain`、`temperature`、`max_tokens`、`input_hash`（消息的 SHA-256）、`input_chars`、`prompt_tokens`/`completion_tokens`/`total_tokens`、`latency_ms`、`ok`、`failure_reason`、`budget`。不记录提示词与模型输出全文；错误信息经过脱敏，并替换掉环境中的 API key 原值。
- 预算按本地日期统计已发出的调用，超出返回 `budget_exceeded`。检查与预留在同一把锁内完成（进程内线程锁，加 POSIX 上的账本文件锁 `fcntl.flock`）：通过检查的调用先在账本写一行预留（`reserved: true`，`dispatched: true`），再调用模型；结束后写入的完整记录（同一 `call_id`）取代预留行。所以 HTTP API 的并发请求、CLI 与页面同时调用时，当日发出的调用合计也不超过上限。调用失败仍计入；在调用模型之前就失败的（如缺少 key）不计入；进程在调用中途退出时，预留行保留并计入。Windows 没有 `fcntl`，只有同一进程内的调用受锁保护。配置（环境变量，见 `.env.example`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CIVITAS_LAB_AI_DAILY_CALLS` | 30 | 每日调用上限（草案与解释合计）；0 即停用 |
| `CIVITAS_LAB_AI_MAX_TOKENS` | 3000 | 单次输出 token 上限 |
| `CIVITAS_LAB_AI_MAX_INPUT_CHARS` | 2000 | 教学目标或问题的字数上限 |
| `CIVITAS_LAB_AI_ROUTE` | auto | `core.llm` 路由模式（auto/fast/slow/zhipu） |
| `CIVITAS_LAB_AI_LEDGER` | outputs/lab/ai_usage.jsonl | 预算账本位置 |

失败代码：`llm_not_configured`、`llm_dependencies_missing`、`llm_error`、`budget_exceeded`、`empty_input`、`input_too_long`、`unknown_course`、`unknown_metric`、`invalid_json`、`schema_invalid`、`archive_not_verified`、`no_evidence`、`not_simulation`、`unsupported_number`。

### 已知限制

- 数值核验只识别阿拉伯数字和"百分之…"；"翻倍""大幅上升"这类无数字的定量措辞和其他中文数词无法自动识别，仍需教师审阅。
- 核验保证数值来自证据卡，不保证模型对方向或机制的文字描述正确；"数据来源"和事实表供教师核对。
- 核验失败直接作废，不自动重试（重试会消耗预算，也可能掩盖模型的系统性问题）。
- 预算账本的跨进程锁依赖 POSIX `fcntl.flock`；Windows 上只有同一进程内的调用受锁保护，多个进程同时调用时可能略超上限。
- 预算按教学层调用计数；`core.llm` 路由在某个供应商失败时会按回退链尝试下一个模型，所以一次计数的调用可能对应多次供应商请求，实际尝试记录在 `fallback_chain` 中。
- 自动化测试只使用假路由（覆盖注入文本、非法事件、非法 JSON、超预算、虚构数值）；尚未对真实模型做草案质量与解释质量评估。

## 使用

```sh
.venv/bin/python main.py lab catalog                     # 27 个场景及是否可运行
.venv/bin/python main.py lab catalog --course 107349     # 某课程的知识点、场景与课堂包
.venv/bin/python main.py lab check EXP-07                # 门禁结论（被拒绝时退出码 1）
.venv/bin/python main.py lab compile EXP-07              # 查看编译出的核心配置
.venv/bin/python main.py lab run EXP-07 --confirm 张老师  # 不填确认人会被拒绝
.venv/bin/python main.py lab verify outputs/lab/<id>
.venv/bin/python main.py lab compare outputs/lab/<id> --metric zero_volume_steps
.venv/bin/python main.py ui                              # 默认进入课程中心
.venv/bin/python main.py lab draft --course 107349 --goal "比较有无恐慌冲击时的卖出决策"   # 只生成草案，不运行
.venv/bin/python main.py lab run outputs/lab/ai_drafts/<draft_id>/scenario.yaml --confirm 张老师
.venv/bin/python main.py lab explain outputs/lab/<id> --question "恐慌组的卖出决策占比与基准组有何不同？"
```

场景参数也可以写成独立 YAML，用文件路径代替场景 id。

## 当前覆盖（相对规划 16 周 MVP）

| 规划项 | 状态 |
| --- | --- |
| G0 基线复现 | 完成，见 [基线审计](lab/BASELINE_AUDIT_2026-10-20.md) |
| W3–4 LessonSpec/ScenarioSpec、知识点目录、转换器与能力门禁 | v0.1 完成；课程映射待教师核对 |
| W5–6 实验服务封装、错误处理、归档索引 | 进程内服务 + lab manifest 完成；HTTP API、任务队列、跨进程隔离未做 |
| P1 六个实验 EXP-01/02/05/06/07/08 + EXP-09/10 | 场景文档可运行、可校验；模型问题 F1–F5 的处理见 [决策记录](lab/DECISIONS.md)（EXP-06 v0.2.0 公开设定 β=1.0；EXP-08/09 讲授费用通道） |
| W7–9 教师工作台与可视化 | Streamlit 原型：课程检索、场景编辑与门禁、教师确认、对照图表、配对差异、逐笔追溯、CSV/JSON 导出，以及课题卡、教师指导卡、本科/硕士工作纸、证据报告、试点记录表和学生反馈问卷的 Markdown/PDF 下载 |
| 旗舰课与课堂包 | 45 分钟旗舰课 LESSON-MKT-001，EXP-01/02/05/06/07/08/10 各一个 20 分钟课堂包，均含本科/硕士分层任务与教师引导；未经教师评审 |
| W10–11 AI 编排与证据解释 | 完成（[lab/ai.py](../lab/ai.py)）：目标 → ScenarioSpec 草案（强制草稿、必经门禁与教师确认，从不自动运行）；证据卡约束的解释（占位符代入 + 确定性数值核验，始终附模型限制）；调用日志、每日预算、CLI `lab draft` / `lab explain`、Streamlit「AI 教学席」。测试只用假路由，真实模型的草案与解释质量未评估 |
| EXP-10 多种子教学包、本科/硕士双层工作纸 | 完成（EXP-10 + LESSON-EMP-001；工作纸按层级导出，支持 PDF） |
| W14–15 技术验收 | `scripts/lab_acceptance.py` 断网、无 API key 一键验收：全部场景真实运行、校验、复现，17 个非法场景全部拒绝，计算实验公式恒等式，64×30 p95；CI 中单独运行；记录见 [验收记录](lab/ACCEPTANCE_2026-10-20.md)。课堂试点与教师审查（G3/G4）待真实课堂 |
| W5–6 跨进程隔离 | HTTP API 每个对照在独立子进程运行，超时终止并在任务与归档中记录 `timeout` / `interrupted`（默认 900 秒） |
| 规划 6.5 三级预算 | AI 调用按日、课程、教师三级限额（后两级默认关闭）；请求人是自报的教师名，只用于统计 |
| 规划 9.1 AI 安全评估 | `scripts/lab_ai_eval.py` 对真实模型运行固定用例（支持目标、需要其他引擎的目标、提示注入、证据外问题），独立预算账本；未配置 key 时跳过。本机尚未运行 |
| 规划 11.3 协议与课程表 | `docs/lab/schemas/` 三类文档的 JSON Schema、`docs/lab/spec-examples/` 通过/拒绝样例、`docs/lab/course_map_v1.csv` 课程映射表（含教师反馈列），均由测试保持同步 |
| 规划 9.2 试点汇总 | `scripts/pilot_summary.py`：匿名前后测与问卷的描述统计，少于 5 人不显示，拒绝疑似学号/身份证/邮箱，不作因果声明 |
| P2 入口：确定性计算实验 | EXP-17 债券价格、久期与凸性，EXP-15 净现值、IRR 与项目选择（[lab/calculators.py](../lab/calculators.py)，规划 10.1）。公式级测试先行（闭式解、手算值、数值导数）；标记为 `deterministic_calculation`，不是多智能体仿真，也不接入撮合内核；课堂包 LESSON-FI-001、LESSON-CF-001；Streamlit「计算实验室」与 `/v1/calculators`。EXP-15 的企业投资竞争仍需 P2 企业模型 |
