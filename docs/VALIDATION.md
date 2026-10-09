# 工程验证记录（2026-10-09）

验证对象是当前 civitas 目录内的源码导出副本。本地 Python 3.14.7 / macOS，原始 Git 为无 HEAD 的 main，未创建虚构提交。运行 manifest 记录 `git_commit=unknown`、dirty 状态及实际源码指纹。下述记录是工程执行与一致性证据，不作为行为金融效应、政策有效性或真实市场拟合的研究结论。

## 自动化测试

在新建 `.venv-check` 中，仅由 requirements-dev.txt 安装依赖后运行：

```sh
.venv-check/bin/python -m pytest -q --junitxml=docs/validation/pytest.xml
```

最终结果：**64 passed，0 failed，0 skipped，1 warning，3.72 秒**。机器记录在 [pytest.xml](validation/pytest.xml)，依赖实录在 [environment.txt](validation/environment.txt)。

覆盖画像与人口约束、原始初始状态回归、实例 RNG、SIRS 转移与数量守恒、订单有效性/FIFO/IOC/FOK/Post-only、撤单与改单、价格限制与步长、费用/成本/资源预留/T+1、现金及持仓守恒、事件/停牌/网络对照、逐步输出、失败归档、单次运行隔离、配置与输入快照校验、文件篡改检测、跨工作目录/进程/时区/PYTHONHASHSEED 的重跑，以及 CLI、Streamlit AppTest、真实 IPC 子进程和 Mesa 初始化。

测试中网络连接被禁止；模型依赖使用受控客户端。固定模型失败、显式离线标记、成功返回的模型身份及不合法决策均有测试。没有真实模型请求。C++ 已在本机编译并加载，包含 200 次固定种子的订单/撤单序列对照，以及非默认价格步长与队列次序测试。本轮没有跳过 C++ 测试；扩展文件/源码哈希及编译器记录见 [native_backend.json](validation/native_backend.json)。

唯一警告来自 Mesa 3.5.1：`seed` 参数被标记弃用，建议使用 `rng`，提示无功能变化。本轮保留其兼容接口并记录此警告，不将结果写成“零警告”。

## 独立依赖环境

另建 `.venv-core-check`，只安装 requirements-core.txt 后完成 64 人、30 步完整实验及全部 16 个归档文件校验。未导入 Streamlit、Mesa、OpenAI、httpx、SciPy、ChromaDB。记录见 [core_dependencies.json](validation/core_dependencies.json)，精确依赖见 [core-environment.txt](validation/core-environment.txt)。因此 UI、模型 SDK 与研究扩展均不是标准主链路的启动条件。

## 小规模实际实验

执行可复用脚本：

```sh
.venv-check/bin/python scripts/validate_experiments.py --with-cpp
```

脚本实际调用 CLI batch、run、reproduce。两份配置 × 三个种子，均为 64 Agent / 30 步，无行情联网或模型调用。另有基准的独立重跑和 C++ 对照，全部完成。

| 配置 | Seed | 后端 | 实际成交笔数 | 实际成交量 | 最后价格 |
| --- | ---: | --- | ---: | ---: | ---: |
| minimal_market | 41 | Python | 184 | 1,433 | 2,739.64 |
| minimal_market | 42 | Python | 179 | 1,190 | 2,719.38 |
| minimal_market | 43 | Python | 188 | 1,850 | 2,740.03 |
| panic_intervention | 41 | Python | 184 | 1,424 | 2,749.19 |
| panic_intervention | 42 | Python | 185 | 1,225 | 2,753.06 |
| panic_intervention | 43 | Python | 188 | 1,834 | 2,746.47 |
| minimal_market | 42 | C++ | 179 | 1,190 | 2,719.38 |

这些价格来自实际 Agent 订单与撮合，未读取旧展示路径，也未为了展示而平滑。panic_intervention 配置同时包含恐慌、停牌/复牌及税率变化，用于机制时序与归档验证，不能从这个工程组合推断单一政策的因果效果。

归档再次独立检查逐步现金、持仓、成交量、最后成交价、无成交持价和停牌零成交。总持仓逐步一致；所有运行中现金加费用守恒的最大绝对残差为约 `1.863e-9` 模型金额单位，处于浮点容差内。没有负现金、负持仓、自成交或未标记的 Mock。

基准重跑的 **11 个状态/结果文件逐字节一致**；相同配置的 Python 与 C++ 也对这 11 个文件逐字节一致。不同 run ID、墙钟时间、机器和后端元数据不纳入数值文件比较。原始完整目录、文件 SHA256、实际命令、退出码及耗时见 [experiment_runs.json](validation/experiment_runs.json)。耗时是带启动、文件输出和校验的本机观察；本轮没有设计性能基准，不报告加速倍数。

## 基线与资产完整性

原版已离线记录三个明确问题：挂单 100、买单 101 时误以 101 成交；空簿 IOC 仍为 pending；真实来源全失败后生成 365 行 synthetic。相应回归测试已覆盖。原 seed=42、12 人向量初始状态的 SHA256 保持一致，见 [baseline_behavior.json](validation/baseline_behavior.json)。

原 `data/` 的 **22 个文件逐文件 SHA256 全部保持不变**；归档教学情景、画廊及主题也按原字节校验。没有删除历史科研数据。全部当前 Python 源码完成 AST 解析，移除模块的运行代码引用检查无残留。文件处置清单和哈希见 [change_inventory.json](validation/change_inventory.json)，静态检查见 [static_checks.json](validation/static_checks.json)。

## 未覆盖范围与后续接口工作

- MarketEnvironment、Mesa、完整认知/反思、复杂政策与监管模块继续保留；其完整链路没有全部接入本轮的 ClearingHouse。不能将标准路径的守恒结论外推到这些研究原型。
- 旧认知扩展仍有失败后返回 HOLD、解析默认值或关键词分析的分支。标准 LLM 实验绕开这些分支并严格失败；迁移旧认知流时需继续将这些分支改为明确模式及调用状态，再验收。完整认知机制不因文件存在而视为已接入标准实验。
- 本轮未验收真实外部行情、实时 LLM 质量与费用、GPU/vLLM/llama.cpp、Ray 集群、长期回放、真实市场校准和大规模运行。预交易和 invariant 检查保留了逐账户扫描；大人口长期运行仍需要带守恒对照的性能工作。
- GitHub Actions 已加入 Python 3.11/3.12 配置，但尚无该副本的远程运行回执。当前本机结论仅对应上述 Python 3.14.7 环境。
- 结果目录支持失败留痕和从初始条件重跑，尚不支持中途 checkpoint 恢复。实时 LLM 结果不是仅靠随机种子即可完全复现。
