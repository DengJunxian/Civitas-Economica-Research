# Civitas Economica Research

用于 ABM、多智能体金融市场和行为金融研究的人工市场仿真代码库。标准实验将异质人口、社会网络、行为决策、订单撮合与账户清算连接起来，保存可追溯的配置、状态和成交数据。前端是可选的实验操作界面。

默认路径复用原有向量行为金融模型、Persona、SIRS 及 Python/C++ 订单簿。完整认知、记忆、反思、政策宏观耦合、Mesa、历史回放和监管优化继续保留为研究扩展，各自状态见 [架构说明](docs/ARCHITECTURE.md)。

## 最小可运行实验

Python 3.11+。从公开仓库获取源码后，在项目根目录执行：

```sh
git clone https://github.com/DengJunxian/Civitas-Economica-Research.git
cd Civitas-Economica-Research
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-core.txt
.venv/bin/python main.py validate --config examples/experiments/minimal.yaml
RUN_DIR="$(.venv/bin/python main.py run --config examples/experiments/minimal.yaml)"
echo "$RUN_DIR"
.venv/bin/python main.py verify "$RUN_DIR"
.venv/bin/python main.py reproduce "$RUN_DIR"
```

该配置运行 64 个 Agent、30 个时间步。每步实际计算传播与决策、提交订单、撮合和结算；无成交时沿用最后成交价、成交量为零。没有预置价格路径，没有 API 调用，不要求 Streamlit。终端会输出独立的运行目录。

## 对照、批处理与复现

```sh
.venv/bin/python main.py batch --config examples/experiments/minimal.yaml --config examples/experiments/panic.yaml --seeds 41,42,43
.venv/bin/python main.py verify "$RUN_DIR"
.venv/bin/python main.py reproduce "$RUN_DIR"
```

`batch` 按配置与种子组合运行；每个子实验保留独立 manifest。`verify` 校验文件哈希、配置/种子/初始快照身份及完成状态；`reproduce` 重新执行本地规则实验并比较 11 个数值与状态文件，将父运行、逐文件哈希、代码/环境差异及匹配状态写入新运行的 `reproduction.json`。含实时 LLM 的实验不承诺确定性，不允许该命令自动再次调用模型。

完整参数、事件和数据协议见 [实验指南](docs/EXPERIMENTS.md)；机器配置 schema 见 [experiment_config.schema.json](docs/experiment_config.schema.json)。

## 执行结构

```text
CLI / optional Streamlit
          ↓
ExperimentConfig → ExperimentRunner
          ↓
Persona + StratifiedPopulation ← SocialGraph / SIRS
          ↓
validated decisions → OrderBook (Python or C++)
          ↓
ClearingHouse: reservations, settlement, fees, invariants
          ↓
MarketMetrics + agent/order/trade/event records
          ↓
ExperimentManifest + config/source/data hashes
```

默认市场是单资产、连续撮合、离散时间研究模型；board_lot=1、T+0 可显式调整。买卖双方资金和持仓均结算，手续费进入独立费用账户，每步验证现金加费用与总持仓守恒。无融资、无卖空、无自动国家队资金、无撮合失败后的价格模型替换。

## 输出

每次运行目录包含配置、统一 manifest、代码指纹、环境版本及 `requirements-lock.txt`、初始世界与画像，以及 metrics.csv、agents.csv、decisions.csv、orders.csv、order_events.csv、trades.csv、social.csv、events.jsonl、model_calls.json、run.log 和 summary.json。失败保留已完成数据、error.log 和失败状态，不冒充成功结果。字段定义、依赖恢复方法及归档版本兼容性见 [实验指南](docs/EXPERIMENTS.md)。

来源显式区分 historical、simulation、synthetic_test、teaching_example 和 mock。带 SHA256 的历史输入用于初始化时，后续撮合价格仍是 simulation。真实行情失败默认报错；合成数据必须明确选择。examples/teaching 内的预编排 CSV 和 examples/legacy_presentation 内的旧展示资源仅作教学/历史资产，标准运行不读取它们。

## Civitas Lab 教学层

`lab/` 把课程知识点映射到可运行的教学场景：场景文档先经确定性能力门禁和教师确认，再编译为标准 `ExperimentConfig` 按配对种子运行，结果从归档读取做对照。当前提供 P1 场景 EXP-01/02/05/06/07/08、2×2 组合 EXP-09 和 45 分钟旗舰课包，课程映射待教师核对。

```sh
.venv/bin/python main.py lab catalog --course 107349
.venv/bin/python main.py lab run EXP-07 --confirm 张老师
```

AI 教学席（`main.py lab draft` / `lab explain`）只生成须经门禁和教师确认的场景草案，以及只引用证据卡数值的解释。协议、门禁规则与覆盖范围见 [Civitas Lab 教学层](docs/LAB.md)；HTTP API（`uvicorn lab.api:app`）、课堂包与材料导出见 [API 与课堂材料](docs/lab/API.md)；模型行为与基线结果见 [基线审计](docs/lab/BASELINE_AUDIT_2026-10-20.md)；EXP-17（债券久期）与 EXP-15（NPV/IRR）以确定性计算实验提供，明确区别于多智能体仿真；断网一键技术验收为 `.venv/bin/python scripts/lab_acceptance.py`，记录见 [验收记录](docs/lab/ACCEPTANCE_2026-10-20.md)。

## 可选功能

```sh
# 实验界面
.venv/bin/python -m pip install -r requirements-ui.txt
.venv/bin/python main.py ui

# LLM provider SDK；仅当配置明确 decision_mode: llm 时调用
.venv/bin/python -m pip install -r requirements-llm.txt

# C++ 订单簿；需要本机 C++17 编译器
.venv/bin/python -m pip install pybind11 setuptools
.venv/bin/python setup.py build_ext --inplace
# 在实验配置中设置 market.backend: cpp
```

复杂回放、Mesa、爬虫、文档导出、ChromaDB 等依赖在 requirements-research.txt 中按需安装。本地 GPU 后端保留独立配置。凭据只从环境读取，不内置可用 Key。标准 LLM 模式需要指定模型及调用预算，默认失败即终止，model_calls.json 记录实际返回的模型和调用状态。

## 验证与工程记录

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

测试禁止联网，覆盖 Agent/Social/Market/Simulation、确定性重跑、失败归档、CLI、可选 UI、IPC、Mesa 初始化以及可选 C++ 一致性。未编译 C++ 时相关测试明确 skip。

核心依赖环境可独立执行 README 全流程检查，保存 CLI 回执、结果数量、现金/持仓守恒和复现报告：

```sh
.venv/bin/python scripts/verify_minimal_experiment.py
```

该检查在独立进程中阻止网络连接。CI 分别执行核心依赖检查与含可选模块/C++ 的完整测试，并上传归档与回执。当前运行状态见 [GitHub Actions](https://github.com/DengJunxian/Civitas-Economica-Research/actions/workflows/tests.yml)。

- [审查与修改清单](docs/REFACTOR_AUDIT.md)
- [架构与模块状态](docs/ARCHITECTURE.md)
- [运行及输出协议](docs/EXPERIMENTS.md)
- [旧配置与接口迁移](docs/MIGRATION.md)
- [本轮验证记录](docs/VALIDATION.md)
- [公开仓库干净环境验证](docs/CLEAN_ENV_VALIDATION.md)

## 研究边界

默认模型没有完整复制现实 A 股交易时段、节假日、公司行动、融资融券和真实投资者分布。社会邻接矩阵、情绪耦合和行为偏好属于模型假设。标准向量实验不自动包含完整 LLM 认知与反思；这些机制保留在独立研究模块。MarketEnvironment 与 Mesa 的复杂路径仍需完成账本接入才能用于财富守恒研究，本轮对其状态做了显式标记。

本次工程验证证明指定本地配置可以执行、归档和重跑，不替代市场真实性校准、因果识别或对真实投资者的实证验证。初次工程验证基于没有 Git 提交的源码导出，归档 manifest 保留当时的 Git unknown 与源码指纹。此后从 Git 仓库执行的新实验会记录当前提交；既有验证记录保持原始身份。
