---
name: pipe-flow-estimation
description: 从 DXF 管网图生成管线流向图（flow map）与流量估算拓扑。触发：用户要求画流向图、流向分析、管网主干/分支提取、管线流量估算，或提到 YSLINE/LX/CSK 图层块、多汇拆树、雨水管网 DXF。产出 flow DXF + PNG 预览 + 拓扑 JSON 三件套。
---

# 管线流量估算（Pipe Flow Estimation）

从源 DXF 的管线图层提取管网 → 拼接连通分量 → 提取主干与分支 → 绘制法向偏移的管线流向图。五个单一功能脚本按序运行，以一份逐层充实的 JSON 为步骤间契约。

## 管线（脚本在本 skill 的 scripts/ 目录下）

```bash
S=<本skill目录>/scripts; NET=<输出名>.json
python3 $S/extract.py 源图.dxf $NET --layer YSLINE     # 步骤1 提取: 管段/LX箭头/CSK出水口 → 定向 JSON
python3 $S/join.py   $NET $NET                         # 步骤2 拼接: 无向连通分量骨架
python3 $S/trunk.py  $NET $NET                         # 步骤3 主路径: 标定/拆树/主干/分支裁剪/起点
python3 $S/draw.py   $NET 源图.dxf <输出名>_flow.dxf    # 步骤4 绘制: 偏移流向图 DXF + node_offset 回填
python3 $S/preview.py <输出名>_flow.dxf $NET <输出名>_flow.png   # PNG 预览
python3 $S/validate.py <输出名>_flow.dxf <输出名>_flow.json      # A.5 验收, 必须 exit 0
```

三件套同名落盘：`<输出名>_flow.dxf / .png / .json`（draw 的 `--json-out` 默认写到 DXF 同名 .json）。

## 核心不变量（每步都不得违反）

- **单一汇点**：一棵排水树有且仅一个出口；多汇先拆树，拆不动记 `cross_tree_edge`。
- **先主干后分支**：入度 0 的节点绝大多数是就地接入，不是起点；只有主干最上游端是必然起点，其余叶子按「绝对下限 + 干支数量级比」双条件淘汰。
- **节点统一偏移**：偏移逐节点算一次（顶层 `node_offset`），主干与分支共享顶点坐标——逐管段独立偏移会让 flow 断裂。
- **白可信、粉红存疑**：白 = LX 箭头定向（arrow）；粉红 = 猜测（guessed）及一切异常边。异常边不参与拓扑，悬浮绘制。

## 验收与自检

`validate.py` 全 PASS（exit 0）才算完成；`draw.py` 末行法距测度中位数须等于偏移量 ±2 cm。

典型病症 → 病因：

- 起点数量暴涨到接近叶子总数 → 漏做「先主干后分支」，把每个端点都当成了 source。
- 一个 flow 在 DXF 里拆成多群互不相连的 PLINE → 漏做「节点统一偏移」。
- flow 出现跨越源图拐点的长直弦 → 拐点被误省略，过度简化。

## 新图适配

图层名、容差、阈值全部 CLI 参数化，默认值即规范值。换图只需改源文件与 `--layer`；标定默认自动（两遍标定法），结果写入 `meta`。参数清单与默认值见 [reference/implementation-spec.md](reference/implementation-spec.md) 附录 A.3。

## 参考

- [reference/json-schema.md](reference/json-schema.md) — 拓扑 JSON 六层契约：各步骤填什么、中间字段（`node_ids`/`edge_ids`）、字段语义。
- [reference/implementation-spec.md](reference/implementation-spec.md) — 唯一规范（normative）：四步规则全文、方向判定、分支裁剪程序（A.1）、拆树归属（A.2）、参数标定（A.3）、偏移衔接几何（A.4）、复现性与验收（A.5）。改脚本、调几何、排查异常前必读。

块语义速查：`LX` = 流向箭头块（管线图层，唯一定向来源）；`CSK` = 出水口块（YSPOINT 层，绑定到容差内聚类节点）；普通井（YB/YJ/YLK）不识别、不参与任何判定。完整块字典见 [reference/blocks.md](reference/blocks.md)。
