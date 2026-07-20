import json
import os
from collections import defaultdict
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mcp.server.fastmcp import FastMCP

TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
DEFAULT_ACCOUNT = os.environ.get("META_AD_ACCOUNT_ID", "")
VERSION = os.environ.get("META_API_VERSION", "v23.0")
PORT = int(os.environ.get("PORT", "10000"))

mcp = FastMCP(
    "Meta Ads Team",
    instructions=(
        "只读 Meta Ads 数据工具。先列出可访问账户，再按账户、日期和层级查询。"
        "可使用结果计算 CPI、ROAS、趋势、素材排名和异常，不要修改广告。"
    ),
    host="0.0.0.0",
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


def graph_get(path, params):
    if not TOKEN:
        raise RuntimeError("服务器未配置 META_ACCESS_TOKEN")
    query = dict(params)
    query["access_token"] = TOKEN
    url = "https://graph.facebook.com/{}/{}?{}".format(
        VERSION, path.lstrip("/"), urlencode(query)
    )
    try:
        with urlopen(Request(url), timeout=50) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(body)["error"]["message"]
        except Exception:
            message = body
        raise RuntimeError("Meta API 错误：" + message) from exc


def graph_all(path, params, max_rows=1000):
    payload = graph_get(path, params)
    rows = []
    while True:
        rows.extend(payload.get("data", []))
        if len(rows) >= max_rows:
            return rows[:max_rows]
        next_url = payload.get("paging", {}).get("next")
        if not next_url:
            return rows
        with urlopen(Request(next_url), timeout=50) as response:
            payload = json.loads(response.read().decode("utf-8"))


def action_value_preferred(items, names):
    """Return the first available Meta action metric to avoid duplicate counting."""
    values = {
        item.get("action_type"): float(item.get("value", 0) or 0)
        for item in (items or [])
    }
    for name in names:
        if name in values:
            return values[name]
    return 0


def normalize(row):
    spend = float(row.get("spend", 0) or 0)
    impressions = float(row.get("impressions", 0) or 0)
    clicks = float(row.get("clicks", 0) or 0)
    installs = action_value_preferred(
        row.get("actions"), ["omni_app_install", "mobile_app_install"]
    )
    purchases = action_value_preferred(
        row.get("actions"),
        ["omni_purchase", "purchase", "app_custom_event.fb_mobile_purchase"],
    )
    revenue = action_value_preferred(
        row.get("action_values"),
        ["purchase", "omni_purchase", "app_custom_event.fb_mobile_purchase"],
    )
    result = {
        key: value
        for key, value in row.items()
        if key not in ("actions", "action_values")
    }
    result.update(
        {
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
            "installs": installs,
            "cpi": round(spend / installs, 4) if installs else None,
            "purchases": purchases,
            "cost_per_purchase": round(spend / purchases, 4) if purchases else None,
            "revenue": revenue,
            "roas": round(revenue / spend, 4) if spend else None,
            "calculated_ctr": round(clicks / impressions * 100, 4)
            if impressions
            else 0,
        }
    )
    return result


def insight_rows(
    account_id,
    level,
    date_preset,
    since,
    until,
    limit,
    breakdowns="",
):
    account_id = account_id or DEFAULT_ACCOUNT
    if not account_id:
        raise ValueError("请提供 account_id，例如 act_123456789")
    if not account_id.startswith("act_"):
        account_id = "act_" + account_id

    params = {
        "level": level,
        "fields": ",".join(
            [
                "account_id",
                "account_name",
                "campaign_id",
                "campaign_name",
                "adset_id",
                "adset_name",
                "ad_id",
                "ad_name",
                "spend",
                "impressions",
                "reach",
                "clicks",
                "ctr",
                "cpc",
                "cpm",
                "frequency",
                "actions",
                "action_values",
            ]
        ),
        "limit": min(max(int(limit), 1), 500),
    }
    if since and until:
        params["time_range"] = json.dumps({"since": since, "until": until})
    else:
        params["date_preset"] = date_preset or "last_7d"
    if breakdowns:
        params["breakdowns"] = breakdowns
    return [
        normalize(row)
        for row in graph_all(account_id + "/insights", params, max_rows=1000)
    ]


def summary(rows):
    totals = {
        key: sum(float(row.get(key, 0) or 0) for row in rows)
        for key in (
            "spend",
            "impressions",
            "reach",
            "clicks",
            "installs",
            "purchases",
            "revenue",
        )
    }
    totals["cpi"] = (
        round(totals["spend"] / totals["installs"], 4)
        if totals["installs"]
        else None
    )
    totals["cost_per_purchase"] = (
        round(totals["spend"] / totals["purchases"], 4)
        if totals["purchases"]
        else None
    )
    totals["roas"] = (
        round(totals["revenue"] / totals["spend"], 4)
        if totals["spend"]
        else None
    )
    totals["ctr"] = (
        round(totals["clicks"] / totals["impressions"] * 100, 4)
        if totals["impressions"]
        else 0
    )
    return totals


@mcp.tool()
def list_ad_accounts() -> dict:
    """列出当前共享凭证可读取的全部 Meta 广告账户。"""
    rows = graph_all(
        "me/adaccounts",
        {
            "fields": "id,name,account_status,currency,timezone_name,business",
            "limit": 200,
        },
    )
    return {"count": len(rows), "accounts": rows}


@mcp.tool()
def list_campaigns(account_id: str = "", limit: int = 100) -> dict:
    """列出指定广告账户中的广告系列及其状态。account_id 留空时使用默认账户。"""
    account_id = account_id or DEFAULT_ACCOUNT
    if not account_id:
        raise ValueError("请提供 account_id")
    rows = graph_all(
        account_id + "/campaigns",
        {
            "fields": "id,name,status,effective_status,objective,created_time,updated_time",
            "limit": min(max(limit, 1), 500),
        },
        max_rows=1000,
    )
    return {"account_id": account_id, "count": len(rows), "campaigns": rows}


@mcp.tool()
def get_ad_insights(
    account_id: str = "",
    level: str = "campaign",
    date_preset: str = "last_7d",
    since: str = "",
    until: str = "",
    limit: int = 200,
    breakdowns: str = "",
) -> dict:
    """查询只读成效数据。level 可选 account/campaign/adset/ad；日期可用 date_preset，或同时填写 since/until（YYYY-MM-DD）；breakdowns 可填 country、publisher_platform 等。"""
    if level not in ("account", "campaign", "adset", "ad"):
        raise ValueError("level 必须是 account、campaign、adset 或 ad")
    rows = insight_rows(
        account_id, level, date_preset, since, until, limit, breakdowns
    )
    return {
        "account_id": account_id or DEFAULT_ACCOUNT,
        "level": level,
        "date_preset": date_preset if not (since and until) else None,
        "since": since or None,
        "until": until or None,
        "count": len(rows),
        "summary": summary(rows),
        "rows": rows,
    }


@mcp.tool()
def get_creative_performance(
    account_id: str = "",
    date_preset: str = "last_7d",
    since: str = "",
    until: str = "",
    min_installs: int = 1,
    sort_by: str = "cpi",
    limit: int = 200,
) -> dict:
    """查询素材（广告名称）表现并聚合重名素材。适合找最低 CPI、最高 ROAS 或高花费低转化素材。"""
    rows = insight_rows(
        account_id, "ad", date_preset, since, until, limit
    )
    groups = defaultdict(list)
    for row in rows:
        groups[row.get("ad_name") or row.get("ad_id") or "未命名素材"].append(row)

    materials = []
    for name, items in groups.items():
        item = summary(items)
        item["material_name"] = name
        item["ad_ids"] = sorted(
            {row.get("ad_id") for row in items if row.get("ad_id")}
        )
        if item["installs"] >= min_installs:
            materials.append(item)

    if sort_by not in ("cpi", "roas", "spend", "installs", "purchases"):
        raise ValueError("sort_by 必须是 cpi、roas、spend、installs 或 purchases")
    reverse = sort_by != "cpi"
    materials.sort(
        key=lambda item: (
            item.get(sort_by) is None,
            item.get(sort_by) if item.get(sort_by) is not None else 10**30,
        ),
        reverse=reverse,
    )
    return {
        "account_id": account_id or DEFAULT_ACCOUNT,
        "count": len(materials),
        "sort_by": sort_by,
        "materials": materials,
    }


@mcp.tool()
def compare_periods(
    account_id: str = "",
    first_since: str = "",
    first_until: str = "",
    second_since: str = "",
    second_until: str = "",
    level: str = "account",
) -> dict:
    """对比两个明确日期区间的 Meta Ads 表现，返回汇总和变化百分比。日期格式 YYYY-MM-DD。"""
    if not all((first_since, first_until, second_since, second_until)):
        raise ValueError("必须填写两个周期的开始和结束日期")
    first_rows = insight_rows(
        account_id, level, "", first_since, first_until, 500
    )
    second_rows = insight_rows(
        account_id, level, "", second_since, second_until, 500
    )
    first = summary(first_rows)
    second = summary(second_rows)
    changes = {}
    for key in ("spend", "installs", "cpi", "purchases", "revenue", "roas", "ctr"):
        old = first.get(key)
        new = second.get(key)
        changes[key + "_pct"] = (
            round((new - old) / old * 100, 2)
            if old not in (None, 0) and new is not None
            else None
        )
    return {
        "account_id": account_id or DEFAULT_ACCOUNT,
        "first_period": {
            "since": first_since,
            "until": first_until,
            "summary": first,
        },
        "second_period": {
            "since": second_since,
            "until": second_until,
            "summary": second,
        },
        "change_second_vs_first": changes,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
