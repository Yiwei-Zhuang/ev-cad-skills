# 拓扑 JSON 契约（图引用式六层）

管线五步共用一份 JSON，逐步充实：extract 建 `meta`/`nodes`/`edges`/`anomalies` → join 填 `components` 骨架 → trunk 补全 `components` 并回填 `meta` 标定值 → draw 回填 `node_offset`。

```jsonc
{
  "meta": {
    "layer": "YSLINE",                    // 用户指定的管线图层名称，全局只看这个
    "source_file": "src_dxf/rain_only_test.dxf",
    "coordinate_unit": "meter",
    "snap_tolerance": 0.3,                // 线端点聚类容差（把端点聚成网络节点）
    "outlet_bind_tolerance": 1.0,         // CSK 出水口到聚类节点的绑定容差
    "arrow_match_tolerance": 0.01,        // LX 箭头块到所在线段的匹配容差
    "direction_rule": "arrow | guessed",  // 方向来源规则（见实现规范「方向判定」）
    "branch_min_length": 25.0,            // 短支剔除绝对下限（trunk 两遍标定回填，可 --branch-min 手动指定）
    "branch_trunk_ratio": 0.05,           // 干支数量级比：保留分支还需 ≥ 主干累计长度×该值
    "guess_rule": "bfs-to-csk",           // guessed 边所用启发式的规则名（规则自由选择，但必须记录）
    "start_crossline_len": 42.93,         // 起点横断线总长（= 1.5×主干边长中位数，可 --crossline 覆盖）
    "end_arrow_wing": 28.62,              // 终点箭头翼长（= 横线总长 × 2/3）
    "significance_threshold": 10.73       // 显著性阈值 T：头部短桩剪除与起点/末端切线累计弦共用的同一把尺
  },

  "nodes": [
    {
      "id": int,                          // 节点唯一 ID（簇均值坐标按 (x,y) 排序定序，确定性）
      "x": float,
      "y": float,
      "type": "CSK | junction"            // CSK=出水口井；junction=其余纯拓扑节点
    }
  ],

  "edges": [
    {
      "id": int,                          // 管段唯一 ID（源 DXF modelspace 遍历序，确定性）
      "from": int,                        // 引用 nodes[].id，上游端
      "to": int,                          // 引用 nodes[].id，下游端
      "direction": "downstream | upstream",  // downstream=沿 from→to 为流向；upstream=与主流向相反（reverse_edge）
      "direction_source": "arrow | guessed", // arrow=由 LX 箭头确定（可信，白）；guessed=猜测（存疑，粉红）
      "geometry": [[x, y], [x, y]],       // 管线几何（LINE 为两端点，多段线为有序点列）
      "length": float                     // 管段长度（几何累计长度，单位同 coordinate_unit）
    }
  ],

  "components": [                         // 独立排水树（单出口树）。join 只建骨架；trunk 补全
    {
      "id": int,                          // 分量唯一 ID，trunk 按 (原分量, sink) 排序后取 1..N
      "outlet": "int | null",             // 出水口节点 ID：汇点恰为 CSK 时记录；否则 null（仍属合法拓扑）
      "source": int,                      // 主干路径起点节点 ID（剪除头部短桩后回设；∈ sources，不必然是拓扑源）
      "sink": int,                        // 唯一汇点节点 ID（出度为 0，非必为 CSK）
      "sources": [int],                   // 保留分支的源头节点 ID 列表（保留边子图入度为 0，含主干起点）
      "node_ids": [int],                  // 保留节点集合（中间字段，随裁剪更新；复现与调试用）
      "edge_ids": [int],                  // 保留边集合（中间字段，draw 由此重建树）
      "node_count": int,
      "edge_count": int,                  // 保留边数（剔除短支后）
      "chains": [                         // 从某个起点到汇点的一整条拼接管串
        {
          "source": int,                  // 引用 nodes[].id
          "sink": int,                    // 引用 nodes[].id
          "edge_sequence": [int],         // 按流向有序的管段 ID 列表，引用 edges[].id
          "length": float                 // 该链累计长度
        }
      ],
      "main_path": {                      // 主干路径
        "edge_sequence": [int],           // 沿流向有序的主干管段 ID 列表
        "total_weight": float             // 主干累计长度
      },
      "branch_count": int,                // 汇入主干的保留支管数
      "is_valid": bool                    // 拓扑合法性（单一汇点）
    }
  ],

  "anomalies": [                          // 集中记录异常/错误元素，供人工核查定位
    {
      "reason": "reverse_edge | duplicate_edge | cross_tree_edge | multi_sink",
      "edge_id": "int | null",            // 涉及的边（如有）
      "node_id": "int | null",            // 涉及的节点（如有）
      "detail": "str"                     // 说明
    }
  ],

  "node_offset": {                        // 全局节点统一偏移坐标（draw 回填；每个节点只偏移一次）
    "<node_id>": [x, y]
  }
}
```

## 说明

- **nodes 与 edges 解耦**：拓扑关系（`from`/`to`）不依赖坐标，拼接不再需要重新做坐标匹配。
- **`direction` 与 `direction_source` 正交**：`direction` 是纯几何态（是否与主流向相反），`direction_source` 单独记录方向来源是「箭头确定」还是「猜的」，两者互不污染。
- **`node_offset` 顶层一张表**：节点偏移坐标只算一次，所有分量共享；拆树共享节点按 A.4.3 先到先得。
- **异常边不参与拓扑**：`reverse_edge`/`duplicate_edge`/`cross_tree_edge` 的边被 join 之后的步骤跳过，仅由 draw 悬浮绘制为粉红线。
