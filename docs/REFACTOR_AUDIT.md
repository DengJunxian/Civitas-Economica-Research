# 工程审查与实施清单（2026-10-09）

## 基线

本次对象为当前目录中的源码导出副本，不是 Desktop/AAMAS 下的研究仓库。外层 Git 为 unborn `main`，无 HEAD、无远程；子目录没有独立 Git。原始 266 个文件、204 个 Python 文件、69,073 行 Python 已完成 AST 解析、模块导入/定义清点，原件快照在父目录 `.refactor-baseline/source-before.zip`，完整清点在同目录 `inventory.json`。这份导出不含 tests 或 CI，旧 requirements-lock 是另一平台的记录，不能视作本机通过验证。

## 执行链与诊断

| 路径 | 实际职责与证据 | 处置 |
| --- | --- | --- |
| main.py → app.py → ui/default_showcase.py | 默认启动 Streamlit，展示页将预设成果放在首位；main 无 API key 时暗示自动 Mock | 替换应用入口为 CLI 实验与可选 UI，共享实验服务 |
| core/competition_demo.py → demo_scenarios/*/metrics.csv | 从 CSV 推進展示游标，不调用 Agent 与撮合；同时混入 Plotly | 删除展示执行器，保留场景资产为明确标识的教学数据 |
| core/competition_compliance.py、启动脚本、主题/画廊 | 比赛检查、材料导出、固定展示路线 | 移除执行代码；展示资源归档到 examples，不删除原图和历史数值 |
| engine/simulation_loop.MarketEnvironment | 宏观→传播→认知→ExecutionAdapter→IPC；撮合异常转 impact_model；混合回放改变价格且标记 visualization_only；未形成完整账户清算 | 保留宏观/政策/生态研究原型，撮合失败默认报错；不得称作已验收的账户实验 |
| simulation_runner.OASISRunner | 子进程订单簿与意图缓冲，无资金/持仓账本，硬编码 seed=42；不是完整实验 runner | 保留隔离撮合工具，显式后端、seed 与错误传播 |
| core/mesa.CivitasModel | 社会 SIRS + TraderAgent + 向量人口 + MarketDataManager；忽略 initial_price，真实行情默认加载，隐含 5,000 人及国家队，PYTEST 环境改变行为 | 保留可选 Mesa 适配，移除测试专用行为与隐含人口/干预设置，标记研究原型 |
| core/system/runner | 可选 Ray 批决策调度，非订单簿/结算系统 | 保留，文档说明其职责，不与市场引擎机械合并 |
| core/exchange/OrderBook、MarketKernel | 真实逐笔撮合、事件延迟与交易时段；未成交 IOC 状态不终止、重复 ID、同时间价格/优先级可能错误；C++ 包装器未同步挂单对象 | 优先修复并测试 Python/C++ 语义；增加账本和预交易资源预留 |
| agents/persona、population、fast_agent_kernel、cognition | 画像、分层人口、行为金融、记忆与快慢决策都有实现；population 重置全局 RNG，N=1 邻居生成不终止，现金 float32 不适合作为账本 | 保留算法，RNG 实例化；状态与结算分离；增加数量/画像校验 |
| core/society、core/social | 前者语义网络 SIRS，后者事件消息传播模型，语义不同 | 均保留；修复 SIRS 混用全局 random 的种子问题 |
| core/data/market_data_provider、RealMarketLoader | 所有真实源失败后默认 synthetic；旧 loader 最后返回有成交量的固定 Candle；最新现货覆盖历史收盘 | 真实源失败报错；显式 synthetic 与独立缓存 provenance；移除固定历史/现货覆盖 |
| MarketDataManager.finalize_step | 无成交时随机漂移并改 last_price | 无成交保留最后成交价、volume=0 |
| core/model_router、core/llm、core/inference | 兼容路由、provider 客户端、本地分层后端；多处离线回退 | 保留不同接口，正式服务默认失败即报错；离线/Mock 显式标识，测试禁止联网 |
| core/experiment_manifest、reproducibility | 已有配置 hash、实验 ID、来源及调用元数据 | 复用并补足唯一 run 目录、生命周期、源码指纹、逐步归档、输出 hash |

源码未发现核心引擎依赖 Streamlit；主要耦合来自包级 eager import、默认加载与固定输出路径，而不是必须重写的底层算法。config.py 存在非空默认凭据：移除，不用于任何测试。未运行付费 API。

## 实施顺序与风险

1. 保存基线；记录可离线复现的订单状态、同价优先级、数据回退问题。
2. 移除竞赛入口与默认展示，保全教学/历史资产及泛用分析模块。
3. 修复订单簿、账户预留/清算、RNG 与严格失败策略；撮合语义变更以针对性测试为验收依据。
4. 建立轻量配置 + 实验生命周期服务，复用画像、向量决策、SIRS、订单簿、manifest；提供固定步数、事件、多种子/对照运行及原始输出。
5. 验证无网络运行、同配置重跑、资金/持仓守恒、事件时序、失败归档、Python/C++ 一致性与 UI 服务共用。
6. 更新安装、架构、数据 schema、迁移和已知边界，提供真实运行回执。

高风险：订单成交价/优先级、重复/无效订单、资源预留、手续费归集、同日卖出限制、C++ 镜像状态。保持现有行为金融公式及参数默认值，输出变更说明区分随机流修复与模型改写。宏观耦合、Mesa、Ray、复杂监管和本地 GPU 推理不通过删代码伪装完成验收；以模块状态表准确标识。

## 实施结果

上述七阶段已在当前副本中落地：默认竞赛路径移除，标准实验服务与 CLI/UI 共用；订单、资源预留、双边清算和失败策略已修改；复用既有 manifest 完成归档、完整性与重跑工具。最终 64 项测试和六组小实验通过，C++ 也通过同轨迹对照。具体证据见 [VALIDATION.md](VALIDATION.md)；按文件的删除、迁移、修改和新增见 [change_inventory.json](validation/change_inventory.json)。研究原型保留并列明尚需完成的接口工作，没有把这些模块声明为全部动态验收完成。
