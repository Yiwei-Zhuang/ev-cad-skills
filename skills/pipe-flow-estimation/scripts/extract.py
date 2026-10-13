#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""步骤1 — 提取: DXF → 管网 JSON (meta/nodes/edges/anomalies)。
端点聚类为 nodes, LX 箭头定向(arrow), 无箭头按 bfs-to-csk 猜测(guessed),
CSK 出水口绑定节点类型, 同节点对多余边记 reverse/duplicate 异常。
用法: extract.py SRC.dxf OUT.json --layer YSLINE [--point-layer YSPOINT]
      [--arrow-block LX] [--arrow-layer LAYER] [--outlet-block CSK]
      [--snap 0.3] [--outlet-tol 1.0] [--arrow-tol 0.01] [--unit meter]
箭头块默认全局搜(不限层); --arrow-layer 提供时按层过滤。
"""
import argparse
import math
from collections import defaultdict, deque

import ezdxf

from flowlib import V, EPS, DSU, cross2, pt_seg_dist, save_net


def main():
    ap = argparse.ArgumentParser(description="步骤1 — 提取")
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--layer", default="YSLINE", help="管线图层名")
    ap.add_argument("--point-layer", default="YSPOINT", help="出水口块所在图层")
    ap.add_argument("--snap", type=float, default=0.3, help="端点聚类容差")
    ap.add_argument("--outlet-tol", type=float, default=1.0, help="CSK 出水口绑定容差")
    ap.add_argument("--arrow-tol", type=float, default=0.01, help="LX 箭头匹配容差")
    ap.add_argument("--arrow-layer", default=None,
                    help="LX 箭头块所在图层过滤, 默认全局搜(不限层)")
    ap.add_argument("--arrow-block", default="LX", help="流向箭头块名")
    ap.add_argument("--outlet-block", default="CSK", help="出水口块名")
    ap.add_argument("--unit", default="meter")
    args = ap.parse_args()
    LAYER = args.layer

    doc = ezdxf.readfile(args.src)
    msp = doc.modelspace()

    # ---- 管段收集 (LINE / LWPOLYLINE / POLYLINE) ----
    raw_edges = []          # (geometry[V], length)
    for e in msp:
        if e.dxf.layer != LAYER:
            continue
        if e.dxftype() == "LINE":
            a, b = V(e.dxf.start.x, e.dxf.start.y), V(e.dxf.end.x, e.dxf.end.y)
            if a.distance(b) < EPS:
                continue
            raw_edges.append(([a, b], a.distance(b)))
        elif e.dxftype() == "LWPOLYLINE":
            pts = [V(x, y) for x, y in e.get_points("xy")]
            if len(pts) < 2:
                continue
            L = sum(pts[i].distance(pts[i + 1]) for i in range(len(pts) - 1))
            if L < EPS:
                continue
            raw_edges.append((pts, L))
        elif e.dxftype() == "POLYLINE":     # R12 2D 多段线(视同开多段线, 与 LWPOLYLINE 同规则, 忽略 z/bulge)
            if e.is_poly_face_mesh or e.is_polygon_mesh:
                continue
            pts = [V(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            if len(pts) < 2:
                continue
            L = sum(pts[i].distance(pts[i + 1]) for i in range(len(pts) - 1))
            if L < EPS:
                continue
            raw_edges.append((pts, L))

    # ---- 端点聚类 (single-linkage, snap_tolerance) ----
    pts = []
    for geom, _ in raw_edges:
        pts += [geom[0], geom[-1]]
    dsu = DSU(len(pts))
    cell = defaultdict(list)
    for i, p in enumerate(pts):
        gx, gy = int(p.x // args.snap), int(p.y // args.snap)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cell[(gx + dx, gy + dy)]:
                    if p.distance(pts[j]) <= args.snap:
                        dsu.union(i, j)
        cell[(gx, gy)].append(i)
    grp = defaultdict(list)
    for i in range(len(pts)):
        grp[dsu.find(i)].append(pts[i])
    node_xy = []
    for g in grp.values():  # 节点坐标 = 簇均值
        node_xy.append(V(sum(p.x for p in g) / len(g), sum(p.y for p in g) / len(g)))
    node_xy.sort(key=lambda p: (p.x, p.y))          # 确定性: 坐标排序定 ID
    kdtree = defaultdict(list)

    def snap(p):
        gx, gy = int(p.x // args.snap), int(p.y // args.snap)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in kdtree[(gx + dx, gy + dy)]:
                    d = p.distance(node_xy[i])
                    if d <= args.snap and (best is None or (d, i) < best):
                        best = (d, i)
        return best[1] if best else None

    for i, p in enumerate(node_xy):
        kdtree[(int(p.x // args.snap), int(p.y // args.snap))].append(i)

    E = len(raw_edges)
    e_geom = [raw_edges[i][0] for i in range(E)]
    e_len = [raw_edges[i][1] for i in range(E)]
    e_fr = [snap(g[0]) for g in e_geom]
    e_to = [snap(g[-1]) for g in e_geom]
    assert all(v is not None for v in e_fr + e_to), "存在未聚类端点"

    # ---- CSK 出水口绑定 (point_layer 层 CSK 块, outlet_tol) ----
    node_type = ["junction"] * len(node_xy)
    for e in msp:
        if e.dxftype() != "INSERT" or e.dxf.name != args.outlet_block \
                or e.dxf.layer != args.point_layer:
            continue
        p = V(e.dxf.insert.x, e.dxf.insert.y)
        best = None
        for i, q in enumerate(node_xy):
            d = p.distance(q)
            if d <= args.outlet_tol and (best is None or (d, i) < best):
                best = (d, i)
        if best:
            node_type[best[1]] = "CSK"
    CSK = {i for i, t in enumerate(node_type) if t == "CSK"}

    # ---- LX 箭头匹配 (arrow_tol; 多候选取最平行者, 平行并列取近者, 再取边ID) ----
    e_src = ["guessed"] * E
    e_dir = [None] * E      # 流向单位向量(箭头确定)
    for e in msp:
        if e.dxftype() != "INSERT" or e.dxf.name != args.arrow_block:
            continue
        if args.arrow_layer and e.dxf.layer != args.arrow_layer:
            continue
        p = V(e.dxf.insert.x, e.dxf.insert.y)
        th = math.radians(e.dxf.rotation)
        d = V(math.cos(th), math.sin(th))
        cands = []
        for i in range(E):
            a, b = e_geom[i][0], e_geom[i][-1]
            dist = pt_seg_dist(p, a, b)
            if dist <= args.arrow_tol:
                u = (b - a).normalize()
                cands.append((-abs(d.dot(u)), dist, i))
        if not cands:
            continue
        cands.sort()
        i = cands[0][2]
        u = (e_geom[i][-1] - e_geom[i][0]).normalize()
        e_src[i] = "arrow"
        e_dir[i] = u if d.dot(u) >= 0 else -u

    # ---- 无向连通分量(供方向猜测分组) ----
    adj = defaultdict(list)
    for i in range(E):
        adj[e_fr[i]].append(i)
        adj[e_to[i]].append(i)
    comp_id = [-1] * len(node_xy)
    nc = 0
    for s in range(len(node_xy)):
        if comp_id[s] != -1:
            continue
        q = deque([s])
        comp_id[s] = nc
        while q:
            u = q.popleft()
            for eid in adj[u]:
                v = e_to[eid] if e_fr[eid] == u else e_fr[eid]
                if comp_id[v] == -1:
                    comp_id[v] = nc
                    q.append(v)
        nc += 1

    # ---- 方向判定: 箭头优先; 无箭头 → bfs-to-csk 猜测 ----
    efrom = [None] * E
    eto = [None] * E
    for i in range(E):
        if e_src[i] == "arrow":
            u = (e_geom[i][-1] - e_geom[i][0]).normalize()
            if e_dir[i].dot(u) >= 0:
                efrom[i], eto[i] = e_fr[i], e_to[i]
            else:
                efrom[i], eto[i] = e_to[i], e_fr[i]
    for c in range(nc):
        ns = [n for n in range(len(node_xy)) if comp_id[n] == c]
        es = [i for i in range(E) if comp_id[e_fr[i]] == c]
        seeds = sorted(n for n in ns if n in CSK)          # 多源: CSK 出水口
        if not seeds:                                       # 无 CSK → 最小 (y,x)
            seeds = [min(ns, key=lambda n: (node_xy[n].y, node_xy[n].x))]
        dist = {n: 1 << 30 for n in ns}
        q = deque()
        for s in seeds:
            dist[s] = 0
            q.append(s)
        while q:
            u = q.popleft()
            for eid in adj[u]:
                v = e_to[eid] if e_fr[eid] == u else e_fr[eid]
                if dist[v] > dist[u] + 1:
                    dist[v] = dist[u] + 1
                    q.append(v)
        for i in es:
            if e_src[i] == "arrow":
                continue
            a, b = e_fr[i], e_to[i]
            if (dist[a], a) > (dist[b], b):   # 高距为上游
                efrom[i], eto[i] = a, b
            else:
                efrom[i], eto[i] = b, a
    assert all(v is not None for v in efrom + eto), "存在未定向边"

    # ---- 重复边检测(同一对无序节点) — reverse/duplicate 异常 ----
    anomalies = []
    anom_edges = set()
    pair = defaultdict(list)
    for i in range(E):
        pair[(min(efrom[i], eto[i]), max(efrom[i], eto[i]))].append(i)
    upstream_ids = set()
    for (u, v), ids in pair.items():
        if len(ids) < 2:
            continue
        ids.sort(key=lambda i: (0 if e_src[i] == "arrow" else 1, -e_len[i], i))
        keep = ids[0]
        for i in ids[1:]:
            if efrom[i] == efrom[keep] and eto[i] == eto[keep]:
                reason = "duplicate_edge"
            else:
                reason = "reverse_edge"
                upstream_ids.add(i)
            anomalies.append({"reason": reason, "edge_id": i, "node_id": None,
                              "detail": f"edge {i} 与 edge {keep} 同节点对({u},{v})"})
            anom_edges.add(i)

    # ---- JSON 输出 ----
    out = {
        "meta": {
            "layer": LAYER,
            "source_file": args.src,
            "coordinate_unit": args.unit,
            "snap_tolerance": args.snap,
            "outlet_bind_tolerance": args.outlet_tol,
            "arrow_match_tolerance": args.arrow_tol,
            "arrow_block": args.arrow_block,
            "arrow_layer": args.arrow_layer,
            "direction_rule": "arrow | guessed",
            "branch_min_length": None,       # 步骤3 标定后回填
            "branch_trunk_ratio": 0.05,      # 步骤3 可覆盖
            "guess_rule": "bfs-to-csk",
            "start_crossline_len": None,     # 步骤3 标定后回填
            "end_arrow_wing": None,
        },
        "nodes": [{"id": i, "x": round(p.x, 3), "y": round(p.y, 3), "type": node_type[i]}
                  for i, p in enumerate(node_xy)],
        "edges": [{
            "id": i,
            "from": efrom[i],
            "to": eto[i],
            "direction": "upstream" if i in upstream_ids else "downstream",
            "direction_source": e_src[i],
            "geometry": [[round(p.x, 3), round(p.y, 3)] for p in e_geom[i]],
            "length": round(e_len[i], 3),
        } for i in range(E)],
        "components": [],                    # 步骤2 填充
        "anomalies": anomalies,
        "node_offset": {},                   # 步骤4 回填
    }
    save_net(out, args.out)
    print(f"提取: {E} 边, {len(node_xy)} 节点, CSK {len(CSK)}, "
          f"arrow {sum(1 for s in e_src if s == 'arrow')}, 异常边 {len(anom_edges)} → {args.out}")


if __name__ == "__main__":
    main()
