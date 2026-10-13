#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""步骤4 — 绘制: 分析 JSON + 源 DXF → 流向图 DXF(并回填 node_offset)。
法向等距偏移(节点统一偏移坐标)、一笔画简化(拐点/汇流/分叉保留)、
起点横断线、终点箭头(仅 CSK)、同号双标注记、悬浮粉红异常边。
用法: draw.py NET.json SRC.dxf OUT.dxf [--json-out OUT.json]
      [--offset 0.5] [--miter-clamp 1.5] [--colinear-deg 0.5] [--flow-suffix flow]
"""
import argparse
import json
import math
from collections import deque

import ezdxf
from ezdxf.enums import TextEntityAlignment

from flowlib import V, Net, left, unit, cross2, save_net

AUDIT = []        # (point, [所属源边], kind) — 法距测度审计


def miter_point(p, u_in, u_out, D_OFFSET, MITER_CLAMP):
    """A.4 miter 交点: 两偏移线交点; 平行退化→入边法向点; 距节点>钳制距离则钳制"""
    A, B = p + left(u_in) * D_OFFSET, p + left(u_out) * D_OFFSET
    cr = cross2(u_in, u_out)
    if abs(cr) < 1e-9:
        return p + left(u_in) * D_OFFSET
    s = cross2(B - A, u_out) / cr
    X = A + u_in * s
    if X.distance(p) > MITER_CLAMP:
        X = p + unit(X - p) * MITER_CLAMP
    return X


def compute_offsets(net, tr, D_OFFSET, MITER_CLAMP):
    """A.4.1 节点偏移归属(树内保留边度数), 每节点恰算一次"""
    edges = tr["edges"]
    outm, inm = tr["outm"], tr["inm"]
    cont = net.continuation(edges)
    dwn = net.downstream_len(list(edges))
    acc = net.upstream_acc(list(edges))
    offs = {}
    for n in tr["nodes"]:
        ie, oe = inm.get(n, []), outm.get(n, [])
        if ie and not oe:                                   # 汇/死端
            e = max(ie, key=lambda x: (acc[x], -x))
            offs[n] = net.xy[n] + left(net.edge_unit(e)) * D_OFFSET
        elif oe and not ie:                                 # 叶/纯源
            e = max(oe, key=lambda x: (dwn[x], -x))
            offs[n] = net.xy[n] + left(net.edge_unit(e)) * D_OFFSET
        else:                                               # 穿越/汇流/叉口 → miter
            ein = cont.get(n) or max(ie, key=lambda x: (acc[x], -x))
            eout = max(oe, key=lambda x: (dwn[x], -x))
            offs[n] = miter_point(net.xy[n], net.edge_unit(ein), net.edge_unit(eout),
                                  D_OFFSET, MITER_CLAMP)
    return offs


def dist_to_sink(net, tr):
    """沿流向到汇点的跳数(BFS 逆流)"""
    q = deque([tr["sink"]])
    d = {tr["sink"]: 0}
    while q:
        u = q.popleft()
        for e in tr["inm"].get(u, []):
            v = net.EF[e]
            if v not in d:
                d[v] = d[u] + 1
                q.append(v)
    return d


def branch_chains(net, tr):
    """分支根 → 一笔画链(沿主链延续上溯到叶子); 返回[(root, [edges 上→下])]"""
    cont = net.continuation(tr["edges"])
    mains = set(tr["main"])
    roots = [e for e in sorted(tr["edges"]) if cont.get(net.ET[e]) != e and e not in mains]
    chains = []
    for r in roots:
        ch = [r]
        cur = net.EF[r]
        while tr["inm"].get(cur):
            e2 = cont[cur]
            ch.append(e2)
            cur = net.EF[e2]
        chains.append((r, list(reversed(ch))))
    return chains


def accumulate_chord(pts, start_idx, budget, reverse=False):
    """自 pts[start_idx] 沿折线累计 budget 米取弦端点(不足取末点)"""
    seq = pts if not reverse else list(reversed(pts))
    i0 = len(pts) - 1 - start_idx if reverse else start_idx
    acc = 0.0
    P0 = seq[i0]
    for i in range(i0, len(seq) - 1):
        a, b = seq[i], seq[i + 1]
        seg = a.distance(b)
        if acc + seg >= budget:
            return P0 + (b - a) * ((budget - acc) / seg if seg > 0 else 0)
        acc += seg
    return seq[-1]


def join_to_parent(net, tr, head_node, e_last, offs, drawn_segs, D_OFFSET):
    """A.4.2 分支末顶点 = 分支自身偏移线与本树已绘制父段的交点(s∈[-0.75,len+0.75], t∈[0,1]);
    无有效交点钉回共享节点偏移顶点; 多交点取离共享顶点最近者"""
    u = net.edge_unit(e_last)
    L = net.elen[e_last]
    Q0 = net.xy[head_node] + left(u) * D_OFFSET
    anchor = offs[head_node]
    best = None
    for (A, B) in drawn_segs:
        AB = B - A
        den = cross2(u, AB)
        if abs(den) < 1e-12:
            continue
        s = cross2(A - Q0, AB) / den
        t = cross2(A - Q0, u) / den
        if -0.75 - 1e-6 <= s <= L + 0.75 + 1e-6 and -1e-6 <= t <= 1 + 1e-6:
            X = Q0 + u * s
            d = X.distance(anchor)
            if best is None or d < best[0]:
                best = (d, X)
    return best[1] if best else anchor


def emit_stroke(net, tr, edges_seq, offs, drawn_segs, out_ents, head_join,
                D_OFFSET, SIN_COLIN):
    """一笔画: 节点偏移折线 → 颜色分段 → 近共线纯穿越点省略 → PLINE 输出
    返回绘制顶点表(全颜色段合并, 供切线用)"""
    nodes_seq = [net.EF[edges_seq[0]]] + [net.ET[e] for e in edges_seq]
    # 末顶点衔接(分支画到已绘制处即止; 主干画到汇点为止)
    pts_raw = [offs[n] for n in nodes_seq]
    mains = set(tr["main"])
    if head_join and edges_seq[-1] not in mains:
        pts_raw[-1] = join_to_parent(net, tr, nodes_seq[-1], edges_seq[-1], offs,
                                     drawn_segs, D_OFFSET)
        AUDIT.append((pts_raw[-1], [edges_seq[-1]], "join"))
    for k in range(len(nodes_seq) - 1 if head_join and edges_seq[-1] not in mains
                   else len(nodes_seq)):
        if k == 0:
            own = [edges_seq[0]]
        elif k == len(edges_seq):
            own = [edges_seq[-1]]
        else:
            own = [edges_seq[k - 1], edges_seq[k]]
        AUDIT.append((pts_raw[k], own, "vertex"))
    # 颜色分段(切换点为顶点)
    runs = []
    for i, e in enumerate(edges_seq):
        c = "white" if net.esrc[e] == "arrow" else "pink"
        if runs and runs[-1][2] == c:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, c])
    indeg = {n: len(tr["inm"].get(n, [])) for n in tr["nodes"]}
    outdeg = {n: len(tr["outm"].get(n, [])) for n in tr["nodes"]}
    all_pts = []
    for (a, b, c) in runs:
        keep = [0] + [k for k in range(1, b - a)
                      if not (indeg.get(nodes_seq[a + k], 0) == 1
                              and outdeg.get(nodes_seq[a + k], 0) == 1
                              and net.edge_unit(edges_seq[a + k - 1]).dot(
                                  net.edge_unit(edges_seq[a + k])) > 0
                              and abs(cross2(net.edge_unit(edges_seq[a + k - 1]),
                                             net.edge_unit(edges_seq[a + k]))) < SIN_COLIN)
                      ] + [b - a]
        keep = sorted(set(keep))
        pts = [pts_raw[a + k] for k in keep]
        out_ents.append(("pline", pts, c))
        for i in range(len(pts) - 1):
            drawn_segs.append((pts[i], pts[i + 1]))
        if not all_pts:
            all_pts = pts
        else:
            if pts[0].distance(all_pts[-1]) < 1e-6:
                all_pts += pts[1:]
            else:
                all_pts += pts
    return all_pts


def draw_tree(net, tr, D_OFFSET, MITER_CLAMP, SIN_COLIN, T, CROSSLEN, WING):
    """一棵树 → 全部绘制实体 [(kind, ...)] 及主 Stroke 折线(切线用)"""
    offs = compute_offsets(net, tr, D_OFFSET, MITER_CLAMP)
    dsink = dist_to_sink(net, tr)
    out_ents, drawn_segs = [], []
    # 1) 主干(沿主干顺序先画)
    main_pts = emit_stroke(net, tr, tr["main"], offs, drawn_segs, out_ents, False,
                           D_OFFSET, SIN_COLIN)
    # 2) 分支链(拓扑下游优先)
    chains = branch_chains(net, tr)
    chains.sort(key=lambda rc: (dsink.get(net.ET[rc[0]], 1 << 20), net.ET[rc[0]], rc[0]))
    for r, ch in chains:
        emit_stroke(net, tr, ch, offs, drawn_segs, out_ents, True, D_OFFSET, SIN_COLIN)
    # 3) 起点横断线(中点=flow 首顶点, 垂直起点切线, 等臂)
    P0 = main_pts[0]
    chord = accumulate_chord(main_pts, 0, T)
    tang = unit(chord - P0) if chord.distance(P0) > 1e-9 else unit(main_pts[1] - main_pts[0])
    nrm = left(tang) * (CROSSLEN / 2)
    out_ents.append(("pline", [P0 - nrm, P0 + nrm], "marker"))
    out_ents.append(("label", str(tr["id"]), P0 - tang * (CROSSLEN / 2)))
    # 4) 终点箭头(仅 CSK 汇点): 尖=末顶点, 沿末端切线, 翼 2/3 横线长, 半角30°
    Pe = main_pts[-1]
    chordb = accumulate_chord(main_pts, len(main_pts) - 1, T, reverse=True)
    tend = unit(Pe - chordb) if Pe.distance(chordb) > 1e-9 else tang
    if tr["sink"] in net.csk:
        back = -tend
        w1 = Pe + V(back.x * math.cos(math.pi / 6) - back.y * math.sin(math.pi / 6),
                    back.x * math.sin(math.pi / 6) + back.y * math.cos(math.pi / 6)) * WING
        w2 = Pe + V(back.x * math.cos(math.pi / 6) + back.y * math.sin(math.pi / 6),
                    -back.x * math.sin(math.pi / 6) + back.y * math.cos(math.pi / 6)) * WING
        out_ents.append(("pline", [w1, Pe, w2], "marker"))
    out_ents.append(("label", str(tr["id"]), Pe + tend * (CROSSLEN / 2)))
    return out_ents, main_pts, offs


def main():
    ap = argparse.ArgumentParser(description="步骤4 — 绘制")
    ap.add_argument("net")
    ap.add_argument("src")
    ap.add_argument("out_dxf")
    ap.add_argument("--json-out", default=None,
                    help="回填 node_offset 后的 JSON 输出路径(默认 OUT.dxf 换 .json)")
    ap.add_argument("--offset", type=float, default=0.5, help="法向偏移量(流向左侧)")
    ap.add_argument("--miter-clamp", type=float, default=1.5, help="miter 交点钳制距离")
    ap.add_argument("--colinear-deg", type=float, default=0.5, help="近共线容差(度)")
    ap.add_argument("--flow-suffix", default="flow", help="流向图层后缀")
    args = ap.parse_args()

    data = json.load(open(args.net))
    net = Net(data)
    CROSSLEN = net.meta["start_crossline_len"]
    WING = net.meta["end_arrow_wing"]
    T = net.meta.get("significance_threshold") or max(5.0, CROSSLEN / 4, WING / 4)
    SIN_COLIN = math.sin(math.radians(args.colinear_deg))
    FLOW_LAYER = net.meta["layer"] + args.flow_suffix

    node_offset_global = {}
    all_flow_ents = []          # (tree_id, kind, ...)
    for comp in data["components"]:
        tr = {"id": comp["id"], "edges": set(comp["edge_ids"]), "main": comp["main_path"]["edge_sequence"],
              "sink": comp["sink"], "comp": comp["id"], "nodes": comp["node_ids"]}
        tr["outm"], tr["inm"] = net.make_graph(tr["edges"])
        ents, mpts, offs = draw_tree(net, tr, args.offset, args.miter_clamp,
                                     SIN_COLIN, T, CROSSLEN, WING)
        all_flow_ents.append((tr["id"], ents))
        for n, p in offs.items():                            # A.4.3 先到先得
            node_offset_global.setdefault(n, [round(p.x, 4), round(p.y, 4)])
        gt = sum(net.elen[e] for e in tr["main"])
        print(f"  #{tr['id']} comp{tr['comp']}: ents={len(ents)} trunk={gt:.1f}m "
              f"sink={tr['sink']} outlet={comp['outlet']}")

    # 悬浮异常边(粉红, 独立偏移): reverse/duplicate/cross_tree 全部
    float_ids = sorted({a["edge_id"] for a in data.get("anomalies", [])
                        if a.get("edge_id") is not None
                        and a["reason"] in ("reverse_edge", "duplicate_edge", "cross_tree_edge")})
    float_ents = []
    for i in float_ids:
        u = net.edge_unit(i)
        a, b = net.xy[net.EF[i]] + left(u) * args.offset, net.xy[net.ET[i]] + left(u) * args.offset
        float_ents.append(("pline", [a, b], "pink"))

    # ---- DXF 输出(以源 DXF 复制为底图) ----
    doc = ezdxf.readfile(args.src)
    if doc.dxfversion < ezdxf.const.DXF2000:   # R12 等旧版不支持 LWPOLYLINE, 升格底图
        from ezdxf.addons import Importer
        base = ezdxf.new("R2000", setup=True)
        imp = Importer(doc, base)
        imp.import_modelspace()
        imp.finalize()
        issues = len(getattr(imp, "issues", []))
        print(f"底图升格: {doc.dxfversion} → R2000 "
              f"(Importer 告警 {issues} 条; 不支持的实体被丢弃)")
        data["meta"]["base_dxf_upgraded_from"] = doc.dxfversion
        doc = base
    msp = doc.modelspace()
    doc.layers.add(FLOW_LAYER, color=7, dxfattribs={"lineweight": 211})

    def add_pline(pts, color):
        attrs = {"layer": FLOW_LAYER, "lineweight": 211}
        pl = msp.add_lwpolyline([(p.x, p.y) for p in pts], dxfattribs=attrs)
        if color == "pink":
            pl.rgb = (255, 105, 180)
        else:
            pl.dxf.color = 7
        return pl

    counts = {"white": 0, "pink": 0, "marker": 0, "label": 0}
    for tid, ents in all_flow_ents:
        for ent in ents:
            if ent[0] == "pline":
                add_pline(ent[1], ent[2])
                counts[ent[2]] += 1
            else:
                _, txt, pos = ent
                t = msp.add_text(txt, dxfattribs={"layer": FLOW_LAYER, "height": CROSSLEN / 2,
                                                  "color": 7, "rotation": 0})
                t.set_placement(pos, align=TextEntityAlignment.MIDDLE_CENTER)
                counts["label"] += 1
    for ent in float_ents:
        add_pline(ent[1], ent[2])
        counts["pink"] += 1
    doc.saveas(args.out_dxf)
    print("DXF:", args.out_dxf, counts)

    # ---- node_offset 回填 ----
    data["node_offset"] = {str(k): v for k, v in node_offset_global.items()}
    json_out = args.json_out or args.out_dxf.rsplit(".", 1)[0] + ".json"
    save_net(data, json_out)
    print("JSON:", json_out, f"(node_offset {len(node_offset_global)} 节点)")

    # ---- 法距测度(A.5): 顶点至其所属保留边源管段的最小法距 ----
    def pt_seg(p, a, b):
        ab = b - a
        L2 = ab.dot(ab)
        if L2 == 0:
            return p.distance(a)
        t = max(0.0, min(1.0, (p - a).dot(ab) / L2))
        return p.distance(a + ab * t)

    vd = []
    for p, own, kind in AUDIT:
        if kind == "join":                      # 衔接点按设计偏离, 豁免
            continue
        best = min(pt_seg(p, net.geom[e][0], net.geom[e][-1]) for e in own)
        vd.append(best)
    vd.sort()
    if vd:
        med = vd[len(vd) // 2]
        print(f"法距测度(所属边): n={len(vd)} 中位={med:.4f} "
              f"(期望{args.offset}±0.02): {'PASS' if abs(med - args.offset) <= 0.02 else 'FAIL'} "
              f"p10={vd[len(vd) // 10]:.3f} p90={vd[int(len(vd) * 0.9)]:.3f} max={vd[-1]:.3f}")


if __name__ == "__main__":
    main()
