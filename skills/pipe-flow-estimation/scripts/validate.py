#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A.5 验收: 连通判定/横线/箭头/编号/法距代理测度。
期望值全部由分析 JSON 推导(树数、CSK 出口数、悬浮异常边数), 不硬编码。
全部 PASS 时退出码 0, 任一 FAIL 退出码 1。
用法: validate.py FLOW.dxf NET.json [--conn-tol 1e-4]
"""
import argparse
import json
import math
from collections import Counter

import ezdxf

from flowlib import V, pt_seg_dist

FAILS = []


def check(ok, msg):
    print(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        FAILS.append(msg)


def main():
    ap = argparse.ArgumentParser(description="A.5 验收")
    ap.add_argument("flow_dxf")
    ap.add_argument("net")
    ap.add_argument("--conn-tol", type=float, default=1e-4)
    args = ap.parse_args()

    meta = json.load(open(args.net))
    flow_layer = meta["meta"]["layer"] + "flow"
    n_trees = len(meta["components"])
    n_outlets = sum(1 for c in meta["components"] if c["outlet"] is not None)
    n_float = len({a["edge_id"] for a in meta.get("anomalies", [])
                   if a.get("edge_id") is not None
                   and a["reason"] in ("reverse_edge", "duplicate_edge", "cross_tree_edge")})

    doc = ezdxf.readfile(args.flow_dxf)
    msp = doc.modelspace()
    plines = [e for e in msp if e.dxf.layer == flow_layer and e.dxftype() == "LWPOLYLINE"]
    texts = [e for e in msp if e.dxf.layer == flow_layer and e.dxftype() == "TEXT"]
    print(f"flow PLINE={len(plines)} TEXT={len(texts)} | 期望: 树={n_trees} "
          f"CSK出口={n_outlets} 悬浮边={n_float}")

    lw = Counter(e.dxf.lineweight for e in plines)
    check(set(lw) == {211}, f"线宽统一 211: {dict(lw)}")
    colw = Counter(e.dxf.color for e in plines)
    pink = sum(1 for e in plines if e.rgb == (255, 105, 180))
    print(f"白(ACI7)={colw.get(7, 0)} 粉红={pink}")

    # ---- 分类: 横线(2顶点,长≈CL) / 箭头(3顶点,翼≈WG) / 悬浮(2顶点粉红) / 折线 ----
    CL = meta["meta"]["start_crossline_len"]
    WG = meta["meta"]["end_arrow_wing"]
    allp = [([V(x, y) for x, y in e.get_points("xy")], e.rgb) for e in plines]
    crosses, arrows, floats, strokes = [], [], [], []
    for pts, rgb in allp:
        L = sum(pts[i].distance(pts[i + 1]) for i in range(len(pts) - 1))
        if len(pts) == 2 and rgb != (255, 105, 180) and abs(L - CL) < 0.05:
            crosses.append(pts)
        elif len(pts) == 3 and abs(pts[0].distance(pts[1]) - WG) < 0.05 \
                and abs(pts[2].distance(pts[1]) - WG) < 0.05:
            arrows.append(pts)
        elif len(pts) == 2 and rgb == (255, 105, 180):
            floats.append(pts)
        else:
            strokes.append(pts)
    print(f"分类: 折线={len(strokes)} 横线={len(crosses)} 箭头={len(arrows)} 悬浮={len(floats)}")

    # ---- 连通判定(共享顶点 or 顶点落在他方线段上) ----
    TOL = args.conn_tol
    ents = [([V(x, y) for x, y in e.get_points("xy")]) for e in plines]
    n = len(ents)
    par = list(range(n))

    def find(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            A, B = ents[i], ents[j]
            hit = False
            for p in A:
                for k in range(len(B) - 1):
                    if pt_seg_dist(p, B[k], B[k + 1]) <= TOL:
                        hit = True
                        break
                if hit:
                    break
            if not hit:
                for p in B:
                    for k in range(len(A) - 1):
                        if pt_seg_dist(p, A[k], A[k + 1]) <= TOL:
                            hit = True
                            break
                    if hit:
                        break
            if hit:
                ri, rj = find(i), find(j)
                if ri != rj:
                    par[ri] = rj
    groups = Counter(find(i) for i in range(n))
    expect_groups = n_trees + n_float
    check(len(groups) == expect_groups,
          f"几何连通分量 = {len(groups)} 群 (期望 = 树{n_trees} + 悬浮{n_float} = {expect_groups}); "
          f"群规模 {sorted(groups.values(), reverse=True)}")

    # ---- 横线: 数量/中点贯穿/等臂/垂直于T弦切线 ----
    def t_chord(s, from_end, budget):
        seq = list(reversed(s)) if from_end else s
        acc = 0.0
        P0 = seq[0]
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            seg = a.distance(b)
            if acc + seg >= budget:
                return P0 + (b - a) * ((budget - acc) / seg if seg > 0 else 0)
            acc += seg
        return seq[-1]

    check(len(crosses) == n_trees, f"起点横断线 = {len(crosses)} (期望{n_trees})")
    n_ok = 0
    arm_bad = 0
    for pts in crosses:
        mid = (pts[0] + pts[1]) * 0.5
        if any(mid.distance(s[0]) <= 1e-6 or mid.distance(s[-1]) <= 1e-6 for s in strokes):
            n_ok += 1
        if abs(pts[0].distance(mid) - pts[1].distance(mid)) > 1e-6:
            arm_bad += 1
    check(n_ok == len(crosses), f"横线中点贯穿 stroke 端点 {n_ok}/{len(crosses)}")
    check(arm_bad == 0, f"横线等臂(不对称 {arm_bad} 条)")
    Tval = max(5.0, CL / 4, WG / 4)
    perp = 0
    for pts in crosses:
        mid = (pts[0] + pts[1]) * 0.5
        d = (pts[1] - pts[0]).normalize()
        tang = None
        for s in strokes:
            if mid.distance(s[0]) <= 1e-6:
                c = t_chord(s, False, Tval)
                tang = (c - s[0]).normalize()
                break
            if mid.distance(s[-1]) <= 1e-6:
                c = t_chord(s, True, Tval)
                tang = (s[-1] - c).normalize()
                break
        if tang is not None and abs(d.dot(tang)) < math.sin(math.radians(2)):
            perp += 1
    check(perp == len(crosses), f"横线⊥T弦切线(±2°): {perp}/{len(crosses)}")

    # ---- 箭头: 数量=CSK出口数, 尖点=stroke末端, 等翼60° ----
    check(len(arrows) == n_outlets, f"终点箭头 = {len(arrows)} (期望{n_outlets}=CSK出口数)")
    tip_ok = 0
    wing_bad = 0
    for pts in arrows:
        if any(pts[1].distance(s[-1]) <= 1e-6 or pts[1].distance(s[0]) <= 1e-6 for s in strokes):
            tip_ok += 1
        w1 = (pts[0] - pts[1]).magnitude
        w2 = (pts[2] - pts[1]).magnitude
        if abs(w1 - WG) > 0.05 or abs(w2 - WG) > 0.05:
            wing_bad += 1
    check(tip_ok == len(arrows), f"箭头尖点接 stroke 端点 {tip_ok}/{len(arrows)}")
    check(wing_bad == 0, f"箭头等翼≈{WG} (异常 {wing_bad} 条)")

    # ---- 编号注记: 2×树数, 同号成对, 连续 1..N ----
    check(len(texts) == 2 * n_trees, f"编号注记 = {len(texts)} (期望{2 * n_trees}=2×{n_trees})")
    cnt = Counter(e.dxf.text for e in texts)
    labels_ok = (all(v == 2 for v in cnt.values())
                 and sorted(cnt) == [str(i) for i in range(1, n_trees + 1)])
    check(labels_ok, f"同号成对且连续1..{n_trees}")
    ha = Counter((e.dxf.halign, e.dxf.valign, e.dxf.rotation) for e in texts)
    hs = sorted({round(e.dxf.height, 2) for e in texts})
    print(f"  对齐: {dict(ha)}, 字高: {hs} (期望 ½横线={round(CL / 2, 2)})")

    # ---- 法距代理测度(权威值由 draw.py 所属边审计输出) ----
    srcsegs = [(V(ed["geometry"][0][0], ed["geometry"][0][1]),
                V(ed["geometry"][-1][0], ed["geometry"][-1][1])) for ed in meta["edges"]]
    dists = sorted(min(pt_seg_dist(p, a, b) for a, b in srcsegs)
                   for s in strokes for p in s)
    if dists:
        med = dists[len(dists) // 2]
        print(f"法距代理测度(最近边, 稠密并行区偏小): n={len(dists)} 中位={med:.4f} "
              f"p90={dists[int(len(dists) * 0.9)]:.3f}")

    # ---- 起点数 vs 叶子数 ----
    n_src = sum(len(c["sources"]) for c in meta["components"])
    print(f"保留起点总数={n_src} ({n_trees} 树) — 应≪全图拓扑叶数")
    print("anomalies:", meta.get("anomalies"))
    print("meta:", json.dumps(meta["meta"], ensure_ascii=False))

    if FAILS:
        print(f"\n验收失败 {len(FAILS)} 项")
        raise SystemExit(1)
    print("\n验收全部 PASS")


if __name__ == "__main__":
    main()
