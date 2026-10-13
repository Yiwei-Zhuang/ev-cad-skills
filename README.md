# ev-cad-skills

CAD 领域的 agent skill 合集：每个 skill 把一类 DXF 图纸分析任务封装成「规范文档 + 单一功能脚本管线」，供 Claude Code / opencode 等 coding agent 安装使用。

## Skills

| Skill | 说明 |
|---|---|
| [pipe-flow-estimation](skills/pipe-flow-estimation/SKILL.md) | 从管网 DXF 提取管线拓扑，绘制法向偏移的管线流向图（flow map）并输出流量估算拓扑 JSON |

## 仓库结构

```
ev-cad-skills/
├── skills/<skill-name>/     # skill 本体（SKILL.md + scripts/ + reference/）
└── examples/<skill-name>/   # 每个 skill 一个示例：输入 DXF + 提示词 + 产出三件套
```

## 安装

把目标 skill 目录复制到 agent 的 skills 目录即可：

```bash
# 项目级（仅当前仓库生效）
cp -r skills/pipe-flow-estimation  <your-project>/.agents/skills/

# 用户级（全局生效）
cp -r skills/pipe-flow-estimation  ~/.agents/skills/
```

安装后对 agent 说该 skill 的触发提示词即可，例如：

> 为 src_dxf/xxx.dxf 的 YSLINE 绘制 flow

完整示例见 [examples/pipe-flow-estimation](examples/pipe-flow-estimation)。

## 约定

- 每个 skill 一目录，入口为 `SKILL.md`（触发描述 + 管线用法）。
- 复杂规则放 `reference/`，可执行脚本放 `scripts/`，`SKILL.md` 只保留用法速查。
- 新 skill 落地时同步在 `examples/<skill-name>/` 附一份最小可复现示例。

## License

[Apache-2.0](LICENSE)
