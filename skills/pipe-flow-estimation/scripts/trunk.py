#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""步骤3 — 主路径: 连通分量 JSON → 单出口树 + 主干/起点/分支。
两遍标定 branch_min_length(A.3) → 剪短断头 → 多汇拆树(A.2) →
反向提取主干 → 剪头部短桩 → 分支双条件迭代剔除(A.1) → 回设起点, 编号 1..N。
用法: trunk.py IN.json OUT.json [--branch-min L] [--trunk-ratio 0.05] [--crossline L]
"""
import argparse
from collections import defaultdict

from flowlib import Net, save_net

T_DEFAULT = 11.25     # 标定 pass 中剪头部短桩用(正式 T 随横线标定后回填重跑)
DEFAULT_BRANCH_MIN = 25.0


# ---------------------------------------------------------------- 剪短断头
def prune_dead_heads(net, edges, limit):
    """非CSK死端, 逐入边链回溯(纯穿越续走), 全链<limit 才整簇剔; 迭代至稳"""
    alive = set(edges)
    while True:
        outm, inm = net.make_graph(alive)
        sinks = [n for n in {net.EF[i] for i in alive} | {net.ET[i] for i in alive}
                 if not outm.get(n) and n not in net.csk]
        removed = set()
        for s in sinks:
            chains = []
            for e0 in inm.get(s, ()):
                chain = [e0]
                cur = net.EF[e0]
                while len(inm.get(cur, ())) == 1 and len(outm.get(cur, ())) == 1:
                    nxt = inm[cur][0]
                    chain.append(nxt)
                    cur = net.EF[nxt]
                chains.append(chain)
            if chains and all(sum(net.elen[c] for c in ch) < limit for ch in chains):
                for ch in chains:
                    removed.update(ch)
        if not removed:
            return alive
        alive -= removed


def dead_chain_lengths(net, edges):
    """无剪枝前提下的原始非CSK断头链长分布(标定 pass1 采样)"""
    outm, inm = net.make_graph(edges)
    nodes = {net.EF[i] for i in edges} | {net.ET[i] for i in edges}
    lens = []
    for s in nodes:
        if outm.get(s) or s in net.csk:
            continue
        for e0 in inm.get(s, ()):
            chain = [e0]
            cur = net.EF[e0]
            while len(inm.get(cur, ())) == 1 and len(outm.get(cur, ())) == 1:
                nxt = inm[cur][0]
                chain.append(nxt)
                cur = net.EF[nxt]
            lens.append(sum(net.elen[c] for c in chain))
    return lens


# ---------------------------------------------------------------- 拆树
def split_multi_sink(net, edges, compno, anomalies):
    """多汇拆树(A.2): 边按流向实际到达的汇点归属; 叉口取下游累计最大出边; 到不了汇=cross_tree"""
    outm, inm = net.make_graph(edges)
    dwn = net.downstream_len(edges)
    sinks = sorted(n for n in {net.EF[i] for i in edges} | {net.ET[i] for i in edges}
                   if not outm.get(n))
    if len(sinks) <= 1:
        return [edges], False
    reached = {}
    for e in edges:
        n = net.ET[e]
        seen = set()
        while True:
            if n in sinks:
                reached[e] = n
                break
            if n in seen or not outm.get(n):
                reached[e] = None
                break
            seen.add(n)
            n = net.ET[max(outm[n], key=lambda x: (dwn[x], -x))]
    trees = []
    for s in sinks:
        es = [e for e in edges if reached[e] == s]
        if es:
            trees.append(es)
    for e in edges:
        if reached[e] is None:
            anomalies.append({"reason": "cross_tree_edge", "edge_id": e, "node_id": None,
                              "detail": f"edge {e} (comp {compno}) 到达不了任何汇点(有向环)"})
    return trees, True


# ---------------------------------------------------------------- 主干
def extract_tree(net, edges, compno):
    """单汇树 → 主干/分支骨架(未含头部短桩与分支裁剪)"""
    outm, inm = net.make_graph(edges)
    nodes = sorted({net.EF[i] for i in edges} | {net.ET[i] for i in edges})
    sinks = [n for n in nodes if not outm.get(n)]
    if len(sinks) != 1:
        return None
    sink = sinks[0]
    acc = net.upstream_acc(edges)
    main = []
    cur = sink
    visited = {sink}
    while inm.get(cur):
        cands = [e for e in inm[cur] if net.EF[e] not in visited]
        if not cands:
            break
        e = max(cands, key=lambda x: (acc[x], -x))
        main.append(e)
        visited.add(net.EF[e])
        cur = net.EF[e]
    main.reverse()
    return {"comp": compno, "edges": set(edges), "sink": sink, "main": main}


def prune_leading_stub(net, tr, T, branch_min):
    """头部短桩: 主干开头累计<T 连续短边剔除; 再按绝对下限补剪新增死端"""
    main = tr["main"]
    alive = set(tr["edges"])
    if not main:
        return tr
    prefix = 0.0
    cut = 0
    for e in main:
        if prefix + net.elen[e] < T and cut < len(main) - 1:   # 至少留一条
            prefix += net.elen[e]
            cut += 1
        else:
            break
    if cut:
        stub = set(main[:cut])
        alive -= stub
        alive = prune_dead_heads(net, alive, branch_min)
    tr["edges"] = alive
    tr["main"] = [e for e in main if e in alive]
    return tr


def prune_branches(net, tr, thresh):
    """分支双条件迭代剔除(快照轮次, 主干保护, 主链延续豁免)"""
    alive = set(tr["edges"])
    main = set(tr["main"])
    while True:
        cont = net.continuation(alive)
        acc = net.upstream_acc(alive)
        roots = [e for e in alive if cont.get(net.ET[e]) != e]
        doomed = set()
        for r in roots:
            if acc[r] < thresh:
                stack = [r]
                while stack:                       # 整棵子树
                    e = stack.pop()
                    if e in doomed:
                        continue
                    doomed.add(e)
                    for p in alive:
                        if net.ET[p] == net.EF[e] and p not in main:
                            stack.append(p)
        doomed -= main                              # 主干显式保护
        if not doomed:
            break
        alive -= doomed
    tr["edges"] = alive
    tr["main"] = [e for e in tr["main"] if e in alive]
    return tr


def finalize_tree(net, tr):
    outm, inm = net.make_graph(tr["edges"])
    nodes = sorted({net.EF[i] for i in tr["edges"]} | {net.ET[i] for i in tr["edges"]})
    tr["nodes"] = nodes
    tr["outm"], tr["inm"] = outm, inm
    tr["sources"] = sorted(n for n in nodes if not inm.get(n))
    dwn = net.downstream_len(list(tr["edges"]))
    chains = []
    for s in tr["sources"]:
        seq = []
        cur = s
        L = 0.0
        while outm.get(cur):
            es = outm[cur]
            e = max(es, key=lambda x: (dwn[x], -x))
            seq.append(e)
            L += net.elen[e]
            cur = net.ET[e]
            if cur == tr["sink"]:
                break
        chains.append({"source": s, "sink": tr["sink"], "edge_sequence": seq,
                       "length": round(L, 3)})
    tr["chains"] = chains
    mn = []
    if tr["main"]:
        mn.append(net.EF[tr["main"][0]])
        for e in tr["main"]:
            mn.append(net.ET[e])
    tr["main_nodes"] = mn
    mains = set(mn)
    cont = net.continuation(tr["edges"])
    tr["branch_count"] = sum(1 for e in tr["edges"]
                             if cont.get(net.ET[e]) != e and net.ET[e] in mains)
    tr["source"] = mn[0] if mn else tr["sink"]
    tr["outlet"] = tr["sink"] if tr["sink"] in net.csk else None
    tr["is_valid"] = True
    return tr


# ---------------------------------------------------------------- 流水
def run_analysis(net, comp_edges, branch_min, anomalies):
    trees_all = []
    for c in sorted(comp_edges):
        edges = comp_edges[c]
        alive = prune_dead_heads(net, edges, branch_min)
        if not alive:
            continue
        subs, multi = split_multi_sink(net, list(alive), c, anomalies)
        if multi:
            anomalies.append({"reason": "multi_sink", "edge_id": None, "node_id": None,
                              "detail": f"comp {c} 多汇点, 拆为 {len(subs)} 棵单出口树"})
        for es in subs:
            tr = extract_tree(net, es, c)
            if tr:
                trees_all.append(tr)
    return trees_all


def calibrate_pass1(net, comp_edges):
    """两遍标定第一遍: 原始断头链长分布中, 就地主峰之后首个显著空档(≥5 m)的中点为初值
    (本图 14.22↔19.31 → 16.76, 对应 md 为本图标定的 16.15)"""
    lens = sorted(set(round(l, 3) for edges in comp_edges.values()
                      for l in dead_chain_lengths(net, edges)))
    for i in range(len(lens) - 1):
        if lens[i + 1] - lens[i] >= 5.0:
            return round((lens[i] + lens[i + 1]) / 2.0, 2)
    return DEFAULT_BRANCH_MIN


def calibrate_pass2(net, comp_edges, t1, anomalies):
    """两遍标定第二遍: 保留分支累计长度库存中就地主峰与支管峰之间空档的中点(就近 0.5 m)"""
    saved = len(anomalies)
    inv = []
    for tr in run_analysis(net, comp_edges, t1, anomalies):
        tr = prune_branches(net, prune_leading_stub(net, tr, T_DEFAULT, t1), t1)
        cont = net.continuation(tr["edges"])
        acc = net.upstream_acc(tr["edges"])
        inv += [round(acc[e], 3) for e in tr["edges"] if cont.get(net.ET[e]) != e]
    del anomalies[saved:]          # 标定轮不产生正式异常记录
    inv = sorted(set(inv))
    t2 = t1
    if len(inv) >= 3:
        gaps = [inv[i + 1] - inv[i] for i in range(len(inv) - 1)]
        gmed = sorted(gaps)[len(gaps) // 2]
        # 单链聚类(链距≤5×中位空档者同簇): 第1簇=就地主峰, 第2簇=支管峰
        link = 5 * gmed
        clusters, cur = [], [inv[0]]
        for i in range(len(inv) - 1):
            if inv[i + 1] - inv[i] <= link:
                cur.append(inv[i + 1])
            else:
                clusters.append(cur)
                cur = [inv[i + 1]]
        clusters.append(cur)
        if len(clusters) >= 2:
            t2 = round((clusters[0][-1] + clusters[1][0]) / 2.0 * 2) / 2   # 空档中点, 就近 0.5 m
    return max(t2, t1), inv


def main():
    ap = argparse.ArgumentParser(description="步骤3 — 主路径")
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--branch-min", type=float, default=None,
                    help="跳过自动标定, 手动指定短支剔除绝对下限")
    ap.add_argument("--trunk-ratio", type=float, default=0.05, help="干支数量级比")
    ap.add_argument("--crossline", type=float, default=None,
                    help="手动覆盖起点横断线总长(默认 1.5×主干边长中位数)")
    args = ap.parse_args()

    import json
    net = Net(json.load(open(args.infile)))
    anomalies = net.data["anomalies"]
    comp_edges = {c["id"]: c["edge_ids"] for c in net.data["components"]}

    # ---- 两遍标定 ----
    if args.branch_min is not None:
        BRANCH_MIN = args.branch_min
        print(f"标定: 手动指定 branch_min = {BRANCH_MIN}")
    else:
        t1 = calibrate_pass1(net, comp_edges)
        BRANCH_MIN, inventory = calibrate_pass2(net, comp_edges, t1, anomalies)
        if not BRANCH_MIN:
            BRANCH_MIN = DEFAULT_BRANCH_MIN
        print(f"标定: pass1 阈值 {t1:.2f}, 库存 {len(inventory)} 条, pass2 阈值 {BRANCH_MIN}")

    trees = run_analysis(net, comp_edges, BRANCH_MIN, anomalies)

    # ---- 横线标定: 1.5 × 主干边长中位数(池化所有树的主干边, 头部短桩剪除前) ----
    mainlens = sorted(net.elen[e] for tr in trees for e in tr["main"])
    med = mainlens[len(mainlens) // 2] if mainlens else 30.0
    CROSSLEN = round(args.crossline, 2) if args.crossline else round(1.5 * med, 2)
    WING = round(CROSSLEN * 2 / 3, 2)
    T = round(max(5.0, CROSSLEN / 4, WING / 4), 2)
    print(f"横线总长 {CROSSLEN} (=1.5×中位 {med:.2f}), 翼长 {WING}, 显著性阈值 T {T}")

    finals = []
    for tr in trees:
        tr = prune_leading_stub(net, tr, T, BRANCH_MIN)
        trunk_total = sum(net.elen[e] for e in tr["main"])
        thresh = max(BRANCH_MIN, trunk_total * args.trunk_ratio)
        tr = prune_branches(net, tr, thresh)
        if not tr["main"]:
            continue
        if tr["sink"] not in net.csk and trunk_total < BRANCH_MIN:   # A.1.5
            continue
        finals.append(finalize_tree(net, tr))
    finals.sort(key=lambda t: (t["comp"], t["sink"]))
    for k, tr in enumerate(finals):
        tr["id"] = k + 1

    j_comps = []
    for tr in finals:
        j_comps.append({
            "id": tr["id"], "outlet": tr["outlet"], "source": tr["source"],
            "sink": tr["sink"], "sources": tr["sources"],
            "node_ids": tr["nodes"], "edge_ids": sorted(tr["edges"]),
            "node_count": len(tr["nodes"]), "edge_count": len(tr["edges"]),
            "chains": tr["chains"],
            "main_path": {"edge_sequence": tr["main"],
                          "total_weight": round(sum(net.elen[e] for e in tr["main"]), 3)},
            "branch_count": tr["branch_count"], "is_valid": tr["is_valid"]})

    data = net.data
    data["components"] = j_comps
    data["anomalies"] = anomalies
    data["meta"]["branch_min_length"] = BRANCH_MIN
    data["meta"]["branch_trunk_ratio"] = args.trunk_ratio
    data["meta"]["start_crossline_len"] = CROSSLEN
    data["meta"]["end_arrow_wing"] = WING
    data["meta"]["significance_threshold"] = T
    save_net(data, args.outfile)
    for tr in finals:
        gt = sum(net.elen[e] for e in tr["main"])
        print(f"  #{tr['id']} comp{tr['comp']}: edges={len(tr['edges'])} main={len(tr['main'])} "
              f"trunk={gt:.1f}m sink={tr['sink']} outlet={tr['outlet']} branches={tr['branch_count']}")
    print(f"主路径: {len(finals)} 棵单出口树, 异常累计 {len(anomalies)} → {args.outfile}")


if __name__ == "__main__":
    main()
