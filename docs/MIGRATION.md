# 迁移说明

- `python main.py` 显示命令帮助；`main.py run --config ...` 运行实验，`main.py ui` 启动可选界面。默认不再打开成果展示，也不提示缺 Key 时自动使用 Mock。
- 删除 core/competition_demo.py、core/competition_compliance.py、ui/default_showcase.py、ui/demo_wind_tunnel.py 和三个展示启动脚本。gallery、theme、展示清单移到 examples/legacy_presentation；三个预编排场景移到 examples/teaching。原始 metrics.csv、narration、图片原件保留，标为 teaching_example。其余历史回放与 hybrid 示例及 data 研究资产原位保留。
- `ui.reporting.export_defense_bundle` 通用化为 `export_research_bundle`，参数 defense_outline_markdown 改为 research_outline_markdown；移除 competition_snapshot/compliance_artifacts。不再导出比赛检查和评委材料。
- 标准实验配置统一由 ExperimentConfig 负责。config.py/config.yaml 保留供旧认知与研究扩展使用，不隐式覆盖标准实验。实验 schema 拒绝未知字段。`competition_safe_mode` 已移除。
- 行情 provider 失败直接抛错。需要合成行情时使用 `MarketDataProvider(provider_priority=["synthetic"])`；不能与真实 provider 混合作为 fallback。旧 `CIVITAS_DISABLE_SYNTHETIC_MARKET_FALLBACK` 不再控制行为。旧的无 provider 标签缓存不再被当作真实历史。
- RealMarketLoader 不再读取来源不明的默认 CSV，不返回固定 3000 点/固定成交量，不用实时现货修改历史收盘。MarketDataManager 默认 load_real_data=False；需要实时数据时显式开启。
- 两个 API 路由器默认禁止离线替代。只有明确的测试/教学代码可以传 `allow_offline_fallback=True`；返回值继续带失败/离线来源。NlpProcessor 使用 `processing_mode="rules"` 显式离线处理；`allow_rule_fallback=True` 会将降级标记为 rule_fallback。新闻 Mock 默认关闭，`python -m data_flywheel --mock` 同时显式选择 Mock 输入与规则处理。
- config.py 内的非空默认凭据已移除；.env.example 不包含可用默认 Key。如旧副本中的凭据曾实际有效，应由凭据所有者轮换。原件快照只留在 Git 忽略的本地 .refactor-baseline，权限 0600。
- Population 和 SIRS 采用实例 RNG；种子 42 的初始向量状态与原版哈希一致。全局 random/NumPy 被其他模块消费不再改变这些实例的输出。SmartAgent.market_value 不再返回无条件 0；调用 market_value_at(price) 显式提供报价。
- Mesa 显式接受 n_vectorized、include_national_team、use_llm、load_real_data，默认分别为 0/False/False/False；initial_price 真正生效。删除 PYTEST_CURRENT_TEST 改变采样数量的行为。完整 Mesa 资金结算尚未接入标准账本。
- IPC SimulationRunner 显式接受 seed、matching_backend，默认 Python。请求 C++ 而缺少扩展时报错，不悄悄换内核。MarketEnvironment 的撮合异常默认终止；教学代码若确实需要旧冲击近似可显式 allow_matching_fallback=True，其结果仍标记 visualization_only。
- 修复同时间戳挂单价格、FIFO、IOC 终态、重复 ID、撤单幂等、C++ Python 订单镜像和日界价格限制。这些是撮合正确性变更，旧受影响轨迹不应要求逐字节复刻。并未重估原有行为金融参数。
- 原 requirements-lock.txt 为 Windows 历史记录，迁到 docs/validation/legacy-lock-windows.txt；本机验证环境保留在 docs/validation/environment.txt。标准离线安装用 requirements-core.txt，其他依赖按 UI、LLM、研究扩展分别安装。
- 改单先校验后撤销旧单，无效修改返回 REJECTED 替换对象并保留原挂单。schema 模式的涨跌停与委托价格按配置 tick 量化，半 tick 使用 HALF_EVEN；C++ 包装层复用 Python 的归一化结果，避免二次舍入。队列位置按实际到达序而非订单 ID 推断。
- ExperimentRunner 每个实例只可执行一次。批量重复实验应为同一配置创建新实例或使用 CLI batch，避免账户及调用记录跨运行混用。失败批处理项会指向其失败归档目录。
