#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""步骤2 — 拼接: 管网 JSON → 无向连通分量骨架。
在有效边(剔除异常边)上求连通分量, 填 components[] 的
id/node_ids/edge_ids/node_count/edge_count; 其余字段留给步骤3。
用法: join.py IN.json OUT.json
"""
import argparse
from collections import defaultdict

from flowlib import Net, save_net


def main():
    ap = argparse.ArgumentParser(description="步骤2 — 拼接")
    ap.add_argument("infile")
    ap.add_argument("outfile")
    args = ap.parse_args()

    net = Net(__import__("json").load(open(args.infile)))
    valid = net.valid_edges()

    adj = defaultdict(list)
    for i in valid:
        adj[net.EF[i]].append(i)
        adj[net.ET[i]].append(i)

    comps = []
    seen = set()
    cid = 0
    for s in range(len(net.xy)):          # 按节点 ID 升序起扫, 确定性
        if s in seen or s not in adj:
            continue
        nodes, edges = set([s]), set()
        stack = [s]
        seen.add(s)
        while stack:
            u = stack.pop()
            for eid in adj[u]:
                edges.add(eid)
                v = net.ET[eid] if net.EF[eid] == u else net.EF[eid]
                if v not in seen:
                    seen.add(v)
                    nodes.add(v)
                    stack.append(v)
        comps.append({
            "id": cid,
            "node_ids": sorted(nodes),
            "edge_ids": sorted(edges),
            "node_count": len(nodes),
            "edge_count": len(edges),
            # 以下由步骤3 补全
            "outlet": None, "source": None, "sink": None, "sources": [],
            "chains": [], "main_path": None, "branch_count": None, "is_valid": None,
        })
        cid += 1

    data = net.data
    data["components"] = comps
    save_net(data, args.outfile)
    print(f"拼接: {len(comps)} 个连通分量 → {args.outfile}")


if __name__ == "__main__":
    main()
