#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预览图输出: 流向图 DXF + 分析 JSON → PNG 栅格预览。
深色底, 源管线灰阶弱化(JSON edges 几何), flow 图层按白/粉红着色,
长边固定像素数, 同输入可复现。与输出 DXF 同名(仅扩展名不同)。
用法: preview.py FLOW.dxf NET.json OUT.png [--px 2000] [--margin 40]
"""
import argparse
import json

import ezdxf
from PIL import Image, ImageDraw, ImageFont

from flowlib import V


def main():
    ap = argparse.ArgumentParser(description="PNG 预览")
    ap.add_argument("flow_dxf")
    ap.add_argument("net")
    ap.add_argument("out_png")
    ap.add_argument("--px", type=int, default=2000, help="长边固定像素数")
    ap.add_argument("--margin", type=float, default=40.0, help="图幅外边距(坐标单位)")
    args = ap.parse_args()

    data = json.load(open(args.net))
    flow_layer = data["meta"]["layer"] + "flow"

    doc = ezdxf.readfile(args.flow_dxf)
    msp = doc.modelspace()
    plines = [e for e in msp if e.dxf.layer == flow_layer and e.dxftype() == "LWPOLYLINE"]
    texts = [e for e in msp if e.dxf.layer == flow_layer and e.dxftype() == "TEXT"]

    # ---- 图幅范围: 源管线几何 + 外边距 ----
    xs, ys = [], []
    for ed in data["edges"]:
        for p in ed["geometry"]:
            xs.append(p[0])
            ys.append(p[1])
    M = args.margin
    x0, x1, y0, y1 = min(xs) - M, max(xs) + M, min(ys) - M, max(ys) + M
    scale = args.px / max(x1 - x0, y1 - y0)
    Wpx, Hpx = max(2, int((x1 - x0) * scale)), max(2, int((y1 - y0) * scale))
    img = Image.new("RGB", (Wpx, Hpx), (16, 16, 20))
    dr = ImageDraw.Draw(img)

    def XY(p):
        return ((p.x - x0) * scale, (y1 - p.y) * scale)

    # ---- 源管线灰阶弱化 ----
    for ed in data["edges"]:
        g = ed["geometry"]
        dr.line([XY(V(p[0], p[1])) for p in g], fill=(96, 96, 96), width=1)

    # ---- flow 实体 ----
    CL = data["meta"]["start_crossline_len"]
    WG = data["meta"]["end_arrow_wing"]

    def font(fs):
        try:
            return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", fs)
        except Exception:
            return ImageFont.load_default()

    for e in plines:
        pts = [V(x, y) for x, y in e.get_points("xy")]
        pink = e.rgb == (255, 105, 180)
        L = sum(pts[i].distance(pts[i + 1]) for i in range(len(pts) - 1))
        marker = ((len(pts) == 2 and not pink and abs(L - CL) < 0.05)
                  or (len(pts) == 3 and abs(pts[0].distance(pts[1]) - WG) < 0.05
                      and abs(pts[2].distance(pts[1]) - WG) < 0.05))
        col = (255, 105, 180) if pink else (245, 245, 245)
        wd = 2 if marker else 3
        dr.line([XY(p) for p in pts], fill=col, width=wd, joint="curve")
    for e in texts:
        pos = e.dxf.align_point if e.dxf.halign or e.dxf.valign else e.dxf.insert
        fs = max(9, int(e.dxf.height * scale))
        px, py = XY(V(pos.x, pos.y))
        dr.text((px, py), e.dxf.text, fill=(245, 245, 245), font=font(fs), anchor="mm")

    img.save(args.out_png)
    print(f"PNG: {args.out_png} ({Wpx}x{Hpx}, flow PLINE={len(plines)} TEXT={len(texts)})")


if __name__ == "__main__":
    main()
