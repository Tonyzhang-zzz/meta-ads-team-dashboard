# Meta Ads 团队 MCP

这是一个只读的 Meta Ads MCP 服务，供团队成员通过 Codex 拉取、分析和监控广告数据。不提供数据看板，也不会修改广告、预算或投放状态。

## Codex 接入地址

https://meta-ads-team-dashboard.onrender.com/mcp

## 同事接入

在 Codex 的 MCP 设置中添加远程服务器：

- 名称：meta-ads-team
- 类型：Streamable HTTP
- URL：https://meta-ads-team-dashboard.onrender.com/mcp

也可以在安装了 Codex CLI 的终端运行：

```powershell
codex mcp add meta-ads-team --url https://meta-ads-team-dashboard.onrender.com/mcp
```

添加后重启 Codex 或新建任务，然后说：

```text
使用 meta-ads-team 列出我能访问的 Meta 广告账户
```

## 可用能力

- `list_ad_accounts`：列出共享 Token 能访问的广告账户
- `list_campaigns`：列出指定账户的广告系列
- `get_ad_insights`：按账户、广告系列、广告组或广告层级查询成效
- `get_creative_performance`：聚合素材表现，查 CPI、ROAS、花费和安装
- `compare_periods`：比较两个日期区间并计算变化率

## 指令示例

```text
拉取 act_2027145677891682 过去7天数据，分析花费、安装、CPI、购买和ROAS。
```

```text
找出过去7天安装数至少20、CPI最低的10条素材，并说明哪些值得放量。
```

```text
比较最近7天和前7天，找出CPI恶化超过20%的广告系列。
```

## 监控示例

在 Codex 中说：

```text
创建一个每天北京时间10点运行的监控：使用 meta-ads-team 拉取 act_2027145677891682 昨天的数据；如果账户CPI比前7天均值上升20%，或者任一素材花费超过100美元且安装少于10，生成中文告警和处理建议。若首次请求因服务唤醒失败，5分钟后重试一次。
```

Render 免费实例空闲后会休眠，首次请求可能需要约50秒。正式用于稳定定时监控时，建议升级为不会休眠的实例。
