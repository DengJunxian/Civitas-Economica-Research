# 实验运行与数据协议

## 完整运行

在项目根目录运行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-core.txt
.venv/bin/python main.py validate --config examples/experiments/minimal.yaml
RUN_DIR="$(.venv/bin/python main.py run --config examples/experiments/minimal.yaml)"
echo "$RUN_DIR"
.venv/bin/python main.py verify "$RUN_DIR"
.venv/bin/python main.py reproduce "$RUN_DIR"
```

运行路径打印到终端。没有 API Key 也可以完整执行，默认不读取外部行情、不调用模型、不加载 UI。一次运行拥有独立目录，不覆盖其他运行。

```sh
.venv/bin/python main.py batch \
  --config examples/experiments/minimal.yaml \
  --config examples/experiments/panic.yaml \
  --seeds 41,42,43
.venv/bin/python main.py verify "$RUN_DIR"
.venv/bin/python main.py reproduce "$RUN_DIR"
```

`batch` 是配置文件 × 种子的笛卡尔积，`batch.json` 记录每个子运行的实际状态。可以用不同配置比较人口规模、网络、政策事件、费用、T+1、决策模式；比较不同决策模式需要说明其模型差异，不把它们自动命名为“理性/非理性”对照。

`verify` 检查文件完整性及完成状态，并重新计算配置、种子、初始世界/人口快照与实验身份，核对时间序列数量。历史输入必须有归档快照。`reproduce` 重新执行本地规则模型并逐字节比较人口、世界、市场、账户、订单、成交、决策、传播、事件和模型调用记录。运行 ID、运行墙钟时间、机器环境等不属于数值结果对比。源代码/依赖版本不同时可能出现真实差异。实时 LLM 不支持这个确定性命令，避免它隐式再产生付费调用。

复现命令打印新运行路径和报告路径。新 manifest 的 `parent_run_id` 指向原运行；新目录中的 `reproduction.json` 保存原 manifest 哈希、双方代码身份、每个比较文件的 SHA256、环境差异和 `matched` / `different` / `rerun_failed` 状态。报告纳入新 manifest 的文件校验，原归档保持不变。数值不一致时命令返回非零退出码，并保留比较报告；运行失败时保留失败归档与错误类型。新仿真成功与复现匹配是两个独立状态。

## 恢复依赖环境

新实验保存全部已安装分发包的精确版本，包含核心包的传递依赖。使用与 `environment.json` 一致的 Python 和兼容平台，从新虚拟环境恢复：

```sh
python3 -m venv .venv-replay
.venv-replay/bin/python -m pip install -r "$RUN_DIR/requirements-lock.txt"
.venv-replay/bin/python -m pip check
.venv-replay/bin/python main.py reproduce "$RUN_DIR"
```

版本清单记录运行时环境，不包含私有索引凭据、模型服务端状态或原生编译工具链。C++ 后端仍需按 README 构建；原生二进制哈希另存于 environment.json。相同种子不能消除 Python、依赖、平台或服务端推理的差异，复现报告会单独记录环境与源码是否一致。

## 配置

配置是严格校验的 YAML/JSON；未知字段、非法范围和超出运行长度的事件会报错。完整默认值见 `main.py validate`，机器 schema 见 `experiment_config.schema.json`。

事件类型：`panic` 的 value 为感染种子数，`sentiment` 为 [-1,1] 情绪增量，`halt`/`resume` 暂停/恢复接受订单，`tax_change` 为新的卖方印花税率。事件发生在指定步的传播和决策之前。停牌取消当时的挂单，传播继续，输出零成交。

默认 `vectorized` 调用原有向量行为模型；`profile_heuristic` 调用原有 FastAgentKernel。`mock` 需要 `mode: test` 或 `example`，输出明确标记 mock。`llm` 需要另装 requirements-llm.txt、配置凭据、指定 provider/model，以及至少 `n_agents * steps` 的 `max_calls` 逻辑调用预算；重试通过 `max_retries` 显式设置，默认 0，不自动切换其他模型或规则。模型版本由服务端可用字段记录；没有提供版本时写 `unavailable`，不猜测不可变版本。

标准配置不读取环境变量来替换 seed、推理模式或人口机制。凭据与服务地址来自环境，且不写入结果配置。LLM 请求中的 temperature、timeout、retry budget 均记录。请求结果的实际 model、provider、延迟、usage、provider fingerprint 和失败状态记录在 model_calls.json。

## 来源分类

| origin | 含义 |
| --- | --- |
| historical | 带来源说明及 SHA256 的历史输入快照；来源是声明，哈希证明文件一致性，不单独证明外部真实性 |
| simulation | 本次 Agent 决策、撮合和清算实际生成的输出 |
| synthetic_test | 明确选用的合成测试输入；真实行情源失败不会转到这个类别 |
| teaching_example | 预置教学资产；位于 examples/teaching，不进入标准实验 |
| mock | 显式 Mock 运行或数据飞轮 Mock 新闻 |

没有外部输入时，World 标记 `model_initial_condition`，人口标记 `synthetic_model_population`。研究 ABM 的合成人口不是伪造的真实人口，也不是 Mock 决策。

可选 `historical_input` 包含 path、sha256、origin、description；相对路径按配置文件位置解析。运行拷贝为 input_snapshot.csv 并校验哈希，使用最后一个 close 初始化世界。所有后续价格仍标记 simulation，不拿历史序列绕过 Agent 和撮合。research 模式拒绝将 synthetic_test/teaching_example/mock 输入当成历史来源。

## 归档字段

所有 CSV 均有表头、UTF-8、逗号分隔。价格和金额使用模型计价单位，数量是资产单位，step 是模型时间步。不存在日期到真实交易日的隐含映射。

| 文件 | 内容 |
| --- | --- |
| config.json | 展开默认值后的实际配置；无凭据 |
| `*.manifest.json` | 复用 ExperimentManifest：run_id/experiment_id、Git、seed、config_hash、snapshot hash、参数 ID、模型、状态、文件 SHA256 |
| source.json / environment.json | 代码与模型配置文件逐文件 SHA256、整体指纹、Git dirty、Python/依赖/平台、全部 installed_packages |
| requirements-lock.txt | 全部已安装分发包的 name==version 清单，可在兼容 Python/平台的新环境安装 |
| world.json / population.json | 初价、交易规则、实际网络边、社会节点、逐人画像、初始现金、持仓、成本锚及认知类型 |
| metrics.csv | step、price、return、volume、halted、费用累计、感染人数及原有 MarketMetrics 指标；有无报价不齐时价差代理为 0，应结合 bid/ask 深度解释 |
| agents.csv | 初始 step=0 及每步的现金、持仓、成本价、财富、情绪、SIRS、风险与意图 |
| decisions.csv | 原始批决策，action=1 买/-1 卖/0 持有、期望数量和价格、决策来源 |
| orders.csv | 每个订单的最终状态、filled_qty、价格、数量、ID、拒绝理由；未成交挂单没有伪造为成交 |
| order_events.csv | 提交响应、TTL 撤单、停牌撤单的发生步数和理由 |
| trades.csv | 实际撮合的价量、双方账户、maker/taker、模型时间戳、买/卖佣金、卖方税 |
| social.csv / events.jsonl | 每步传播统计、实际外生事件和受影响节点 |
| model_calls.json | 实际调用元数据；离线规则运行是空数组 |
| run.log / error.log / summary.json | 完成步数、守恒结果、异常堆栈、完成/失败/中断状态和摘要 |
| reproduction.json | 复现运行才有：父运行与原 manifest 哈希、双方代码身份、逐文件比较、环境差异与匹配/失败状态 |

配置 `schema_version` 保持 1；归档新增字段版本在 manifest 的 `notes.archive_schema_version` 中记录为 2。旧版未记录该字段的归档按版本 1 读取，不强制补写依赖清单或更改其原始 Git 身份。已有归档的复现会生成当前版本的新归档与报告。

执行中失败时保留已完成步数的数据、订单、错误和失败 manifest。结果目录不是数据库；不支持中途 checkpoint 恢复，复现应从归档初始配置重新开始。金融指标用于描述模型，不代表策略收益有效性或真实市场拟合已经通过科学验证。
