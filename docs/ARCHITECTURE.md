# 架构与模块状态

标准执行路径为 `main.py → ExperimentConfig → ExperimentRunner → StratifiedPopulation / SocialGraph / InformationDiffusion → OrderBook → ClearingHouse → ExperimentManifest`。`app.py` 是同一服务的可选界面。导入和执行标准实验不会加载 Streamlit、OpenAI、Mesa 或 ChromaDB。

## 模块边界

| 层 | 实现 | 运行职责与状态 |
| --- | --- | --- |
| World / Experiment | `core/experiments/config.py`、`runner.py` | 校验有效配置、拥有 RNG 与世界、事件时序、运行生命周期、错误与归档；标准路径 |
| Agent | `agents/persona.py`、`population.py` | 原有异质画像、认知类型、置信度、参考点、损失厌恶、风险与意图公式；默认向量化决策 |
| Decision adapter | `ExperimentRunner._decisions` | `vectorized`、`profile_heuristic`、`llm`、显式 `mock`；输出等长 action/quantity/price 数组，边界再次校验 |
| Interaction | `core/society/network.py` | 小世界/环形/无边网络、语义压力、同步 SIRS 转移、组合相似度边权；种子和 RNG 归实例所有 |
| Market | `core/exchange/order_book.py`、`order_book_cpp.py`、`clearing.py` | 同一 Order/Trade 契约，撮合、状态、预留与双边结算；显式 Python/C++ 选择 |
| Reproducibility | `experiment_manifest.py`、`reproducibility.py`、`experiments/verify.py` | 复用已有实验 ID/hash/manifest；唯一运行目录、源代码指纹、文件完整性校验与重新执行对比 |
| Analysis | `market_metrics.py`、`eval/`、`backtester.py`、`calibration/` | 标准路径直接计算交易量、价差、深度、VWAP 等；回测和校准保留独立语义 |
| Application | `main.py`、`app.py` | CLI 运行/批处理/校验/重跑；UI 配置、实际结果与日志对照 |
| Teaching | `lab/`、`ui/lab.py` | 课程目录、ScenarioSpec/LessonSpec、确定性能力门禁、编译为标准配置、配对对照与证据卡；只调用 ExperimentRunner，不改核心归档。见 [LAB.md](LAB.md) |

## 实际机制

标准实验创建 `n_agents` 个原有向量个体与等量异质 Persona。资金、持仓、成本锚、认知类型、情绪和置信度采用原有初始化分布，不按展示效果重设参数。Persona 的从众度、影响力与损失厌恶进入社会节点；向量交易仍采用原来的行为金融公式。**向量交易公式没有因此自动变成完整 Persona 认知模型**；`profile_heuristic` 显式选择既有 FastAgentKernel 的另一种决策语义。

社会网络的实际邻接表输入原有向量情绪更新。可变度数通过循环填充到矩形矩阵，无邻居时使用自身；这一近似会改变邻居权重，已作为标准运行的模型假设固定下来。SIRS 感染状态以 -1/0 信号进入原有意见影响通道；没有额外拟合传播系数。实验改变拓扑时，邻接关系和实际输入均改变。

每步先取消到期订单、执行显式事件，再更新传播、决策、撮合、清算及指标。无成交时沿用最后成交价，成交量为零；不插入虚拟成交或强行制造行情。初始价格由配置决定，或使用具备 SHA256 的输入快照最后一个 close；后一种只初始化价格，后续路径仍来自仿真。

账本具有唯一账户 ID、现金与持仓。未成交买单按限价加佣金预留现金，卖单预留可卖持仓；卖空、透支和自成交被拒绝。手续费进入独立费用账户，检查 `sum(cash) + fees == initial_cash` 和 `sum(shares) == initial_shares`。T+1 打开时，当天买入持仓在下一模型交易日释放。成交同步成本价，资金账本使用 float64，向量认知状态保持原有 float32。

默认市场是假设明确的单资产、连续撮合、离散时间模型，board_lot=1、T+0。一个模型交易日为 `steps_per_day` 步；日界更新参考收盘价。它没有宣称完整复刻 A 股：未包含完整节假日、公司行动、融资融券、现实经纪商最低佣金、真实投资者校准。日内时段、集合竞价、延迟与复杂监管保留在现有 MarketKernel/AShareSession 模块中，未被默认入口隐式启用。

## 保留的研究扩展

| 实现 | 保留能力 | 本轮验收边界 |
| --- | --- | --- |
| `engine/simulation_loop.py` / `core/policy_session.py` | 宏观家庭、企业、银行、政府，政策传导，策略生态、干预、反事实 | 耦合研究原型；撮合失败默认终止。未建立该路径的完整资金结算，报告明确 `account_settlement=false`，不作为财富守恒实验 |
| `simulation_runner.py` | 真实独立子进程、意图缓冲、订单撮合、交易 tape | 通过 IPC 交易 smoke；明确无账户结算，seed/backend 显式；不是完整实验入口 |
| `core/mesa/` | Mesa 群体编排、TraderAgent、SIRS、可选向量人口 | 初始化与显式参数通过测试；完整多轮账户结算仍需接入 ClearingHouse，结果标记 prototype |
| `agents/trader_agent.py`、`trading_agent_core.py`、`cognition/`、`reflection.py` | 完整认知、记忆、反思、快慢路径及协作 | 保留；没有把这些模块的存在描述成标准向量实验已调用它们；真实模型质量和记忆机制需单独实验 |
| `core/social/` | 消息、谣言、辟谣及事件传播 | 与 SIRS 是不同模型，保留，不机械合并 |
| `core/market_engine.py` | 旧回放/可视化市场适配与政策解释 | 消除无成交随机漂移及行情造数；仍有显式外部国家队流动性模型，未纳入闭合账本研究路径 |
| `core/system/runner.py` | Ray 分布式批决策 | 可选调度器；本轮未部署 Ray 集群 |
| `core/model_router.py`、`core/llm/`、`core/inference/` | 兼容调用、统一 provider 客户端、可选 llama.cpp/vLLM | 前两者默认禁止离线替代；标准 LLM 模式使用 provider 层固定模型。GPU 路由保留为实验后端，未在本机验收 |
| `core/agent_replay.py`、`news_policy_replay.py`、`calibration/`、`optimization/` | 历史回放、校准、监管优化 | 原代码和研究资产保留；不将初始化 smoke 推广为这些模式的科学验证 |

`SMART`/`DEEP` 仍是旧研究扩展的推理配置，不能映射为标准实验的可信程度。标准入口明确区分规则决策、模型调用和 Mock；实时 LLM 即使 temperature=0，也不承诺完全确定性复现。

## 扩展规范

新增决策实现应返回通过边界校验的批决策，让订单通过同一个 ClearingHouse；不要在 Agent 内直接扣账或在图表层改价格。新增事件应有明确生效步数、可序列化参数和事件记录。接入多资产、信用、资金注入、融资融券时必须同时扩展守恒式，不能通过跳过 invariant checks 实现。

后续接入复杂世界应从现有 MarketEnvironment/Mesa 适配开始，而不是再次写一套画像、传播或撮合算法。

旧完整认知扩展中仍有异常后 HOLD 和关键词分析分支；这些分支未作为标准 LLM 决策使用。迁移时须保留快慢行为的科学差异，并把故障状态与明确规则模式区分，再接入统一账本。标准入口的 LLM schema 错误或服务失败会中止并归档，不能静默替代。
