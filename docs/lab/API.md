# Civitas Lab HTTP API v1 与课堂材料

## 启动

```sh
.venv/bin/python -m pip install -r requirements-api.txt
.venv/bin/python -m uvicorn lab.api:app --port 8000
# 交互式文档：http://localhost:8000/docs
```

设置 `CIVITAS_LAB_TOKEN` 后，启动运行和校验归档的写接口要求 `Authorization: Bearer <token>`。未设置时仅适合本机或封闭试点网络；账号、课程权限和审计日志尚未实现（规划 R7）。

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
| `GET /v1/lessons/{id}/worksheet?level=` | 本科/硕士学生工作纸（Markdown） | 只要求课程内匿名编号 |
| `POST /v1/experiments` | 创建对照实验任务 | 门禁拒绝 422（附报告）；缺确认 403；可选令牌 |
| `GET /v1/experiments/{job_id}` | 任务状态与进度 | |
| `GET /v1/comparisons` / `{id}` | 对照归档列表与 lab manifest | id 只接受安全字符，且必须位于归档根目录下 |
| `GET /v1/comparisons/{id}/evidence?metric=` | 证据卡：带 run_id 的数值、配对差异、交互差异、限制 | NaN 输出为 null |
| `GET /v1/comparisons/{id}/steps?observable=` | 图表用逐步数据 | 价差不可定义为 null，不补 0 |
| `GET /v1/comparisons/{id}/report` | 证据报告（Markdown） | |
| `POST /v1/comparisons/{id}/verify` | 归档完整性与场景身份校验 | 可选令牌 |

## 课堂包与课堂材料

`lab/content/lessons/` 有 7 个课堂包：45 分钟旗舰课 LESSON-MKT-001，以及 EXP-01/02/05/06/07/08 各一个 20 分钟课堂包（MKT-002/003、BEH-001/002、MKT-004/005）。每个课堂包包含教学目标、连续的课堂阶段、工作纸问题（对应评分维度）、本科/硕士分层任务（规划 3.1）、教师引导策略（课前预测、操作顺序、异常追问、负结果讨论，规划 5.1）和评分量表（默认为规划附录 C）。

EXP-06 与 EXP-08 的课堂包按模型实际结果设计：前者讨论"恐慌几乎不扩散时，网络差异为何很小"，后者讨论"费用下降而成交不变说明税率只进入结算通道"。它们不承诺模型给不出的效果。

`lab/export.py` 生成 Markdown 材料：课题卡、教师指导卡、分层学生工作纸、证据报告。证据报告只读取对照归档，每个数值都能对应到 run_id，并附复核命令。PDF 导出尚未实现。

## 内容版本与回归

`tests/test_lab_scenarios.py` 有两类守护：

1. **版本**：`tests/golden/lab_content_versions.json` 记录每个场景与课堂包的版本和内容哈希。内容改了而版本号没有提升，测试失败；提升版本后用 `CIVITAS_UPDATE_GOLDEN=1 pytest tests/test_lab_scenarios.py -k version` 更新记录。
2. **课堂包中的事实**：配对组在首个差异事件前路径相同；停牌期间零成交、零订单但仍有卖出意图、停牌撤单为 0；降税后费用下降而成交量不变；默认参数下恐慌峰值不超过 12、无边网络等于种子数。模型改动使这些事实不再成立时，测试会指出需要修改的课堂包。
