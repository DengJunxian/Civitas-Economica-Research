# 公开仓库干净环境验证 — 2026-10-10

本次从公开 GitHub 仓库匿名重新克隆，独立建立虚拟环境，按 README 执行最小配置、归档校验及确定性重跑。起始提交为 `1f86e3aac680b2e3785ee360bcb7c25d1e598a3c`，起始工作区干净。没有复用原项目的虚拟环境或实验输出。

## 实际结果

核心环境使用 macOS arm64、Python 3.14.7，仅安装 requirements-core.txt。没有安装 Streamlit、LLM SDK、Mesa 或构建 Python 3.14 的 C++ 扩展。配置为 64 个 Agent、30 步、seed=42、vectorized、Python 订单簿。

| 检查 | 实际结果 |
| --- | --- |
| 配置校验、执行、verify、reproduce | 全部退出码 0 |
| 市场/传播时间序列 | 各 30 行 |
| Agent 状态 | 1,984 行，包含初始步 |
| 决策 | 1,920 行 |
| 订单 / 订单事件 / 成交 | 1,593 / 2,878 / 179 行 |
| 总持仓 | 各步均为 36,035 |
| 现金加费用守恒的最大绝对误差 | 9.313225746154785e-10 模型金额单位 |
| 模型调用 | 0；model_calls.json 为 [] |
| 修复前后回归 | 11 个数值/状态文件逐字节一致 |
| 新归档校验 | 普通运行 17 个文件，复现运行 18 个文件 |
| 从 requirements-lock.txt 新建环境重跑 | 11 个文件匹配，源码一致、环境差异列表为空 |
| 完整本地测试 | Python 3.12.13，已构建 C++：78 passed，0 failed/errors/skipped；1 个 Mesa seed 弃用警告 |

成交量和价格来自实际 Agent 决策、订单撮合及清算。179 笔成交是该环境与配置的执行观测，不是跨版本的科学校准指标。核心环境的最小实验和自动回执检查均已运行；自动检查的实验子进程显式阻止网络连接。

## 已修复问题

1. 旧 verify 只校验产物哈希，会接受与 config.json 不一致的 manifest seed。现在重新核对配置身份、种子、初始快照、源码指纹、完成状态及主要时间序列数量；条件输入快照不能从 manifest 中省略。
2. 旧 reproduce 只打印比较结果，父运行字段为空。现在保存 reproduction.json 并纳入新 manifest，记录父运行、原 manifest 哈希、逐文件比较、代码/环境差异；比较不一致或运行失败也保留报告。
3. 旧环境记录没有完整传递依赖。现在同时保存 installed_packages 和可安装的 requirements-lock.txt，并实际用新虚拟环境恢复后重跑验证。

这些修改没有调整 Agent 参数、行为模型、社会传播、撮合或清算规则。旧归档按版本 1 读取，新归档标记 notes.archive_schema_version=2，不补写旧记录的 Git 身份。

## 证据与重复执行

修复后的本地工作树已运行完整测试。运行时仍基于起始提交并带有尚未提交的修改，归档准确记录 git_dirty=true；对应执行源码指纹为 `d014a34fc296a48f303762d909942c4f22e7c69649985e63cd1ada9166cca262`。提交后的 CI 会在干净 checkout 上独立运行，并在回执中记录实际提交，不将本地未提交验证冒充提交验证。

- [机器摘要](validation/clean_environment_20261010.json) 记录展开配置、环境、结果数量、命令退出码、归档哈希和依赖恢复结果。
- [CI 工作流](../.github/workflows/tests.yml) 分别执行 Python 3.11/3.12/3.14 核心环境验证和 Python 3.11/3.12 完整测试，上传实验归档与 JUnit。
- [实时 GitHub Actions 记录](https://github.com/DengJunxian/Civitas-Economica-Research/actions/workflows/tests.yml) 提供各提交的实际状态；本页不会提前声明尚未完成的远程检查成功。

独立环境中的重复检查命令：

```sh
.venv/bin/python scripts/verify_minimal_experiment.py
```

每次生成唯一的 outputs/clean-validation/check-* 目录，包含 validation.json、原运行、复现运行及 reproduction.json。原生编译工具链和实时模型服务端状态不由 Python 依赖版本清单恢复；本次验证覆盖离线规则模型。
