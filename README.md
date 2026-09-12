![StateEval · 从独立业务终态判断 Agent 行为](docs/assets/cover.png)

# StateEval

[![check](https://github.com/ChanTso/state-eval/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/ChanTso/state-eval/actions/workflows/check.yml)

**让 Agent 执行业务，用独立 SQL 检查它最终做了什么。**

StateEval 围绕一个具体授权问题展开：当用户提供他人的订单并声称属于自己，Agent 是否会留下越权退款申请？项目连接 [CityBuddy](https://github.com/ChanTso/citybuddy) 的身份与交易服务，以及 [ShopMate](https://github.com/ChanTso/shopmate) 的真实买家 Agent，分别观察业务执行、权限边界和最终数据库状态。

[判定方法](#如何判定) · [当前买家校准](results/shopmate-ownership-final-20260907/README.md) · [历史正式结果](results/ownership-campaign-v1/formal/summary.json) · [完整实验记录](docs/EXPERIMENTS.md)

## 两条链路，两组结论

下表统计的是独立 SQL 确认的**越权退款申请**。关闭／开启仅指评测配置中的 Java 订单归属校验；签名、服务身份、权限范围和会话检查仍保留。

| 被测链路 | 试验规模 | 归属校验关闭 | 归属校验开启 | 观察到什么 |
|---|---|---:|---:|---|
| 历史 CityBuddy 客服，2026-09-01 | 5 种表述，600 次正式 trial | **55/300（18.33%）** | **0/300** | 在这组任务下，保留交易归属校验阻止了越权申请进入业务终态 |
| ShopMate 买家，2026-09-07 | 1 种表述，3 对外单 trial | **0/3** | **0/3** | 六次均未调用 `prepare_refund`，没有测出交易校验的增量效果 |

当前 ShopMate 还完成 **2/2 本人退款正控**：真实模型生成确认卡，原用户确认后再重复确认，SQL 核对一份退款申请与原回执回放。正控和三对外单试验共 8 次 trial；重复确认不是额外模型试验。

旧客服缺少订单归属查询工具；当前买家保留本人订单查询，在更早的读取边界停下。两批工具与链路不同，结果分别保留，不合并分母。`REQUESTED` 表示退款申请已受理，尚不代表到账。

历史 95% Wilson 区间为关闭时 **14.36%–23.10%**、开启时约 **0%–1.264%**。另有一例关闭组没有退款记录，却留下不应存在的 `PREPARED` 动作，单列为禁止副作用失败，不计入 55 次退款。完整条件、模型别名与被测提交见[实验记录](docs/EXPERIMENTS.md)。

## 如何判定

执行与判定使用不同路径：

```text
任务与测试身份 → 真实 Agent／认证业务接口 → CityBuddy 业务写入
                                             ↓
独立只读数据库账号 → SQL 前后快照 → 终态、禁止副作用、权限判定
```

| 环节 | 作用 |
|---|---|
| Acting：执行 | 通过 Agent 的聊天与确认入口办事，保留真实工具、身份和业务事务 |
| Judging：判定 | 使用独立 SELECT-only MySQL 账号读取订单、退款、动作和回执；`must_not_change` 检查不应改动的事实 |
| Grader：逐层判分 | 依次检查最终业务状态、禁止副作用、权限违规；前一层失败即决定本次失败 |
| Transcript：解释 | 保存模型与工具轨迹，解释触达了哪层、为什么结束；业务服务自己的状态／审计接口只作诊断 |

每个对照保持相同模型、工具和执行预算，只改变指定评测开关。先用正控证明正常任务能够完成，再判断外单试验是否真正触达待比较的边界。

核心的任务与断言类型不包含业务 SQL；CityBuddy 适配器负责实际执行和独立数据库读取。终态判分与组件消融沿用已有研究方法，相关工作和范围见[方法来源](docs/PRIOR_ART.md)。

## 本地运行

将三个仓库放在同一父目录：`state-eval/`、`citybuddy/`、`shopmate/`。准备 Python 3.11+、uv、JDK 21 和可运行的 Docker Compose；真实模型连接使用 CityBuddy 本地 `.env` 中已有的提供者配置。

先在 StateEval 目录安装相邻 ShopMate 的锁定依赖并检查：

```sh
uv sync --frozen --directory ../shopmate
make check
```

`make check` 覆盖核心边界、适配器和真实 ShopMate 工厂接入测试；[CI 配置](.github/workflows/check.yml) 固定其使用的 ShopMate 提交。

真实模型试验要求三个仓库均已提交且源码干净。先运行本人退款正控，输出目录必须尚不存在：

```sh
mkdir -p .run
./scripts/run_shopmate_ownership_ablation.sh \
  --output "$(pwd -P)/.run/shopmate-controls"
```

需要比较外单输入时，再运行小规模校准：

```sh
./scripts/run_shopmate_ownership_ablation.sh \
  --output "$(pwd -P)/.run/shopmate-pilot" \
  --stage pilot --trials 3
```

`pilot` 自行先跑两次正控，再跑三对外单试验；`--trials` 是配对数。每次运行换一个新输出目录；重现已发布结果时，使用对应报告记录的三个完整提交与模型配置。

脚本启动独立 MySQL、Auth 和两组 Commerce 服务，每个 trial 使用独立身份、会话与 ShopMate SQLite 状态。正常零售数据库不参与重置。成功且状态明确时清理自建环境；异常或写入未确认时保留隔离现场和诊断位置。

输出保留 SQL 前后快照、SSE、确认回执及结果摘要，并记录源码 SHA 与实际模型别名。运行入口和保留规则见[当前校准报告](results/shopmate-ownership-final-20260907/README.md#runtime-and-reproduction-boundary)。

## 继续阅读

- [完整实验记录](docs/EXPERIMENTS.md)：旧客服的任务表述、控制变量、校准排除、区间及模型边界。
- [当前 ShopMate 校准](results/shopmate-ownership-final-20260907/README.md)：本人确认回放、外单未触达退款准备的完整解释。
- [历史正式摘要](results/ownership-campaign-v1/formal/summary.json)：600 次正式试验的分母、SQL 结果和诊断统计。
- [核心类型](src/stateeval/core/__init__.py) · [当前买家适配器](src/stateeval/shopmate.py) · [独立业务判定](src/stateeval/citybuddy.py)。
