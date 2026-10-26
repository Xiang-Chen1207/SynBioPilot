# SynBioPilot

**面向底盘感知治疗分子发现的 AI Scientist — SUSTech-MED，iGEM 2026。**

[English](README.md) · [团队 Wiki](https://2026.igem.wiki/sustech-med/model-synbiopilot) · [工作流与实现](docs/wiki-workflow.md) · [安装与输入](docs/setup.md)

SynBioPilot 将疾病驱动的研究、靶点引导的分子筛选、微生物底盘路径评估
与分子分析连接起来。四个模块分别生成关联来源证据的候选分子与靶点、
整合四模型评分和 ADMET 评价、通过 ChassisGate 判断底盘实现路径，
并支持选定研究问题的分子对接与动力学分析。

本仓库提供 SynBioScout 研究应用、筛选与后处理入口，以及分子分析脚本。
各模块分别说明所需输入、配置方式和输出文件。

## 四模块工作流

| 模块 | 主要操作 | 输出 |
|---|---|---|
| AI Scientist / SynBioScout | 从疾病问题自主发现候选与靶点 | 关联证据的候选、靶点和研究计划 |
| Target-Guided Virtual Screening | 四模型各取 Top1000，合并去重，计算百分位并整合 ADMET8 | 综合排序的候选集合 |
| ChassisGate | 整合文献、KEGG/GEM 与生长约束 FBA 证据，按生物学规则分类 | 底盘路径分类及跟进列表 |
| Molecular Docking & Dynamics Analysis | 靶点特定的对接及所选轨迹分析 | 姿势、相互作用与轨迹指标 |

本项目以疾病、表型和研究问题为起点开展自主发现：AI Scientist 从 ASD 研究问题自主识别
候选分子 NAC 和筛选靶点 HDAC2；四个模型对合并去重后的完整分子库自动评分与筛选，
SFN 由自动筛选及统一排序流程自然产生。SFN、NAC 的 ChassisGate 结果均为**自动分类得到的 D1**。
项目流程和对应记录见[结果来源](docs/result-provenance.md)。

软件另提供输入候选评估模式；各运行分别保留任务意图和候选来源。

四模型 Top1000/ADMET8 工作流使用根目录 `run.py`；SynBioScout 和分子分析通过各自模块入口运行。

## 快速体验：离线合成示例

```bash
source scripts/runtime.sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python run.py --config configs/demo.json postprocess
```

离线示例使用 8 条**合成输入**和 Top3，展示候选合并、综合评分和路径分层。
预期合成输出为 6 个并集候选、4 个完整评分、2 个 pending、3 个路径跟进候选和 1 个 deliverable-now 候选。
结果保存在 `runs/synthetic_demo/summary.json`、`composite/` 和 `stratification/`。
再次运行时将 `run.output` 设为新的目录。

## 使用真实输入

按[安装说明](docs/setup.md)准备模型环境、官方权重、分子库、代谢模型和 API。
复制 `configs/hdac2.json` 为 `configs/hdac2.local.json` 并配置资源路径，在计算节点运行：

```bash
python3 run.py --config configs/hdac2.local.json plan
python3 run.py --config configs/hdac2.local.json run --until composite
python3 run.py --config configs/hdac2.local.json run --until stratify --resume
```

已有评分、ADMET 和 Gate CSV 可通过 `postprocess` 重新排序与分层，字段见[数据契约](docs/data-contracts.md)。

| 模块 | 实现 | 入口 |
|---|---|---|
| AI Scientist / SynBioScout | `components/synbioscout/scientist/` | [应用指南](components/synbioscout/README.md) |
| 四模型筛选与 ADMET8 | `target2lead/adapters/`、`synbiopilot/scoring.py` | `run.py --config configs/hdac2.local.json plan` |
| ChassisGate | `vendor/chassisgate/deliverability/` 和 Gate 适配器 | `run.py --config configs/hdac2.local.json run --until stratify` |
| 对接与分子分析 | `molecular/docking/`、`molecular/dynamics/` | [分子输入与命令](molecular/README.md) |

## 输入与方法

- 八个天然产物数据库合并并按结构去重，四模型对同一分子库独立评分，各取 Top1000 后合并去重。
- 百分位参考池为各模型完整有效评分集，同分使用平均名次；ADMET8 在候选合并后评价。
- 综合评分为 `0.5 × 四模型等权百分位共识 + 0.5 × ADMET8`。
- ADMET8 权重依次为 BBB 25、PAMPA 10、溶解度 10、hERG 20、DILI 20、CYP3A4 5、CYP1A2 5、P-gp 抑制 5。
- 缺失模型或 ADMET 输入的候选保留为 pending。A–D 为路径跟进分类；A1/A2/B1/B2 构成 deliverable-now 集合，E 类保留未知状态。
- 参考菌株为 WCFS1，参考 GEM 为 iBT721；L168 对应结论需要菌株与条件匹配的证据。

完整公式和分类规则见[工作流与实现](docs/wiki-workflow.md)，模型资源见[安装说明](docs/setup.md)，项目结果及记录见[结果来源](docs/result-provenance.md)。

## 代码目录

```text
synbiopilot/            工作流、评分和分子库准备
target2lead/            四模型、ADMET 和 ChassisGate 适配器
components/synbioscout/ AI Scientist 应用、界面、示例和测试
vendor/chassisgate/     生物学证据整合与分类规则
molecular/             靶点特定的对接、氢键和端点能量分析
configs/               真实资源模板和离线合成示例
examples/              HDAC2 输入与合成预测记录
environment/           各模型依赖清单
docs/                  安装、数据契约、工作流映射和来源说明
```

MIT 适用于团队自有代码；外部模型、数据与第三方材料遵循各自条款，见[第三方说明](THIRD_PARTY_NOTICES.md)。
