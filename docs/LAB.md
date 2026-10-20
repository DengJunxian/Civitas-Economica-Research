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
CLI `main.py lab …` / Streamlit 课程中心、场景实验室、对照分析 (ui/lab.py)
```

## 场景协议

`ScenarioSpec` 按规划附录 B 设计，示例见 [EXP-05.yaml](../lab/content/scenarios/EXP-05.yaml)。要点：

- **只有市场/社会参数覆盖和事件可以在组间变化**。人口规模、步数、初始价格与决策语义是世界级字段，所有组相同，保证配对种子组的初始世界一致。
- `question`、`objectives`、`limitations`、`observables`、`approval`、`review` 等教学元数据**不进入核心配置**；它们留在场景文档中，通过 `lab_manifest.json` 与运行关联。
- 世界类型、事件类型、拓扑和决策模式在协议里是普通字符串，由门禁给出面向教学的拒绝原因，而不是泛化的 schema 错误。

`LessonSpec` 描述一节课：阶段必须连续且覆盖声明时长，评分量表权重和为 100，引用的场景与组别必须存在。旗舰课见 [LESSON-MKT-001.yaml](../lab/content/lessons/LESSON-MKT-001.yaml)。

## 能力门禁规则

门禁不调用 LLM，任何来源（教师手写或日后的 AI 草案）都必须通过它。状态为 `supported`、`supported_with_warnings` 或 `rejected`，每条问题带代码、位置和中文说明。

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
- 证据卡（`main.py lab compare`）只包含带 run_id 的数值、指标定义、失败运行、假设与限制，供日后的证据约束解释使用。

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
```

场景参数也可以写成独立 YAML，用文件路径代替场景 id。

## 当前覆盖（相对规划 16 周 MVP）

| 规划项 | 状态 |
| --- | --- |
| G0 基线复现 | 完成，见 [基线审计](lab/BASELINE_AUDIT_2026-10-20.md) |
| W3–4 LessonSpec/ScenarioSpec、知识点目录、转换器与能力门禁 | v0.1 完成；课程映射待教师核对 |
| W5–6 实验服务封装、错误处理、归档索引 | 进程内服务 + lab manifest 完成；HTTP API、任务队列、跨进程隔离未做 |
| P1 六个实验 EXP-01/02/05/06/07/08 + EXP-09 | 场景文档可运行、可校验；EXP-06/08/09 的教学效果受模型限制（见基线审计第 4 节），需决策 |
| W7–9 教师工作台与可视化 | Streamlit 原型：课程检索、场景编辑与门禁、教师确认、对照图表、配对差异、逐笔追溯、导出 |
| 旗舰课 LESSON-MKT-001 | 课堂包文档完成（阶段、工作纸、量表、解释规范）；未经教师评审 |
| W10–11 AI 编排与证据解释 | 未开始；门禁和证据卡是其前置接口 |
| EXP-10 多种子完整教学包、本科/硕士双层工作纸、PDF 导出 | 未开始 |
