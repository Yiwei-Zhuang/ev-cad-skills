#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享工具库: 向量/并查集/JSON 管网重建/有向图累计量。
各步骤脚本(extract/join/trunk/draw)唯一的公共依赖。"""
import json
from collections import defaultdict, deque

import ezdxf

V = ezdxf.math.Vec2
EPS = 1e-9


def left(u):
    return V(-u.y, u.x)          # 流向左侧法向


def unit(a):
    return a.normalize()


def cross2(a, b):
    return a.x * b.y - a.y * b.x


def pt_seg_dist(p, a, b):
    ab = b - a
    L2 = ab.dot(ab)
    if L2 == 0:
        return p.distance(a)
    t = max(0.0, min(1.0, (p - a).dot(ab) / L2))
    return p.distance(a + ab * t)


class DSU:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[ri] = rj


def load_net(path):
    with open(path) as f:
        return json.load(f)


def save_net(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


class Net:
    """从步骤1 JSON 重建的管网视图: 节点坐标/类型、有向边数组、异常边集合。"""

    def __init__(self, data):
        self.data = data
        self.meta = data["meta"]
        self.node_recs = data["nodes"]
        self.edge_recs = data["edges"]
        self.xy = [V(n["x"], n["y"]) for n in self.node_recs]
        self.ntype = [n["type"] for n in self.node_recs]
        self.csk = {i for i, t in enumerate(self.ntype) if t == "CSK"}
        self.E = len(self.edge_recs)
        self.EF = [e["from"] for e in self.edge_recs]
        self.ET = [e["to"] for e in self.edge_recs]
        self.elen = [float(e["length"]) for e in self.edge_recs]
        self.esrc = [e["direction_source"] for e in self.edge_recs]
        self.geom = [[V(p[0], p[1]) for p in e["geometry"]] for e in self.edge_recs]
        self.anom_edge_ids = {a["edge_id"] for a in data.get("anomalies", [])
                              if a.get("edge_id") is not None}

    def valid_edges(self):
        return [i for i in range(self.E) if i not in self.anom_edge_ids]

    def edge_unit(self, e):
        return unit(self.xy[self.ET[e]] - self.xy[self.EF[e]])

    def make_graph(self, edges):
        """返回 out-in 邻接结构"""
        outm = defaultdict(list)
        inm = defaultdict(list)
        for i in edges:
            outm[self.EF[i]].append(i)
            inm[self.ET[i]].append(i)
        return outm, inm

    def upstream_acc(self, edges):
        """acc(e) = e.length + max(acc(上游入边)); DAG 迭代, 环内贡献记 0"""
        outm, inm = self.make_graph(edges)
        acc = {}
        state = {}  # 0=新 1=栈内 2=完成

        def dfs(e):
            if e in acc:
                return acc[e]
            if state.get(e) == 1:
                return 0.0          # 环保护
            state[e] = 1
            n = self.EF[e]
            best = 0.0
            for p in inm.get(n, ()):
                best = max(best, dfs(p))
            state[e] = 2
            acc[e] = self.elen[e] + best
            return acc[e]

        for e in edges:
            dfs(e)
        return acc

    def downstream_len(self, edges):
        outm, inm = self.make_graph(edges)
        dwn = {}
        state = {}

        def dfs(e):
            if e in dwn:
                return dwn[e]
            if state.get(e) == 1:
                return 0.0
            state[e] = 1
            n = self.ET[e]
            best = 0.0
            for s in outm.get(n, ()):
                best = max(best, dfs(s))
            state[e] = 2
            dwn[e] = self.elen[e] + best
            return dwn[e]

        for e in edges:
            dfs(e)
        return dwn

    def continuation(self, edge_set):
        """cont(node) = 入边中上游累计最大者(并列取小ID) — 主链延续"""
        inm = defaultdict(list)
        for i in edge_set:
            inm[self.ET[i]].append(i)
        acc = self.upstream_acc(edge_set)
        cont = {}
        for n, es in inm.items():
            cont[n] = max(es, key=lambda x: (acc[x], -x))
        return cont
