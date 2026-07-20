import csv
import io
import json
import os
from collections import defaultdict
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, render_template_string, request

app = Flask(__name__)
TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
ACCOUNT = os.environ.get("META_AD_ACCOUNT_ID", "act_2027145677891682")
VERSION = os.environ.get("META_API_VERSION", "v23.0")
FIELDS = ",".join([
    "account_name", "campaign_name", "adset_name", "ad_name", "spend",
    "impressions", "clicks", "actions", "action_values"
])


def graph_get(path, params):
    if not TOKEN:
        raise RuntimeError("服务器尚未配置 META_ACCESS_TOKEN")
    query = dict(params)
    query["access_token"] = TOKEN
    url = "https://graph.facebook.com/{}/{}?{}".format(VERSION, path, urlencode(query))
    try:
        with urlopen(Request(url), timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(body)["error"]["message"]
        except Exception:
            message = body
        raise RuntimeError("Meta API 错误：" + message) from exc


def action(items, names):
    values = {x.get("action_type"): float(x.get("value", 0) or 0) for x in (items or [])}
    return sum(values.get(name, 0) for name in names)


def parsed(row, level):
    installs = action(row.get("actions"), ["mobile_app_install", "omni_app_install"])
    purchases = action(row.get("actions"), ["purchase", "omni_purchase", "app_custom_event.fb_mobile_purchase"])
    revenue = action(row.get("action_values"), ["purchase", "omni_purchase", "app_custom_event.fb_mobile_purchase"])
    spend = float(row.get("spend", 0) or 0)
    impressions = float(row.get("impressions", 0) or 0)
    clicks = float(row.get("clicks", 0) or 0)
    names = {
        "campaign": ("campaign_name", "广告系列"),
        "adset": ("adset_name", "广告组"),
        "material": ("ad_name", "素材"),
        "account": ("account_name", "账户"),
    }
    field, fallback = names[level]
    return {
        "name": row.get(field) or fallback,
        "spend": spend,
        "installs": installs,
        "cpi": spend / installs if installs else None,
        "purchases": purchases,
        "cpp": spend / purchases if purchases else None,
        "revenue": revenue,
        "roas": revenue / spend if spend else None,
        "impressions": impressions,
        "clicks": clicks,
        "ctr": clicks / impressions * 100 if impressions else 0,
    }


def insights(since, until, level):
    api_level = "ad" if level == "material" else level
    params = {
        "fields": FIELDS,
        "level": api_level,
        "time_range": json.dumps({"since": since, "until": until}),
        "limit": 500,
    }
    payload = graph_get(ACCOUNT + "/insights", params)
    source = []
    while True:
        source.extend(payload.get("data", []))
        next_url = payload.get("paging", {}).get("next")
        if not next_url:
            break
        with urlopen(Request(next_url), timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))

    rows = [parsed(row, level) for row in source]
    if level != "material":
        return rows

    groups = defaultdict(lambda: {
        "name": "", "spend": 0, "installs": 0, "purchases": 0,
        "revenue": 0, "impressions": 0, "clicks": 0
    })
    for row in rows:
        item = groups[row["name"]]
        item["name"] = row["name"]
        for key in ("spend", "installs", "purchases", "revenue", "impressions", "clicks"):
            item[key] += row[key]

    result = []
    for item in groups.values():
        item["cpi"] = item["spend"] / item["installs"] if item["installs"] else None
        item["cpp"] = item["spend"] / item["purchases"] if item["purchases"] else None
        item["roas"] = item["revenue"] / item["spend"] if item["spend"] else None
        item["ctr"] = item["clicks"] / item["impressions"] * 100 if item["impressions"] else 0
        result.append(dict(item))
    return result


def selected_dates():
    preset = request.args.get("preset", "last_7d")
    today = date.today()
    if preset == "yesterday":
        since = until = today - timedelta(days=1)
    elif preset == "last_30d":
        since, until = today - timedelta(days=30), today - timedelta(days=1)
    elif preset == "this_month":
        since, until = today.replace(day=1), today
    elif preset == "custom":
        since = date.fromisoformat(request.args.get("since", str(today - timedelta(days=7))))
        until = date.fromisoformat(request.args.get("until", str(today - timedelta(days=1))))
    else:
        since, until = today - timedelta(days=7), today - timedelta(days=1)
    return str(since), str(until)


def view_data():
    since, until = selected_dates()
    level = request.args.get("level", "material")
    rows = insights(since, until, level)
    minimum = float(request.args.get("min_installs", 1) or 0)
    rows = [row for row in rows if row["installs"] >= minimum]
    sort_key = request.args.get("sort", "spend")
    rows.sort(
        key=lambda row: (row.get(sort_key) is not None, row.get(sort_key) or 0),
        reverse=sort_key != "cpi",
    )
    summary = {
        key: sum(row[key] for row in rows)
        for key in ("spend", "installs", "purchases", "revenue", "impressions", "clicks")
    }
    summary["cpi"] = summary["spend"] / summary["installs"] if summary["installs"] else None
    summary["roas"] = summary["revenue"] / summary["spend"] if summary["spend"] else None
    summary["ctr"] = summary["clicks"] / summary["impressions"] * 100 if summary["impressions"] else 0
    return since, until, level, rows, summary


def money(value):
    return "-" if value is None else "$" + "{:,.2f}".format(value)


def number(value):
    return "{:,.0f}".format(value)


PAGE = """
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meta Ads 团队看板</title>
<style>
:root{--ink:#17212b;--muted:#66727f;--line:#dfe5ea;--blue:#1368ce;--green:#147d64;--bg:#f5f7f9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Arial,"Microsoft YaHei",sans-serif}
header{background:#fff;border-bottom:1px solid var(--line);padding:20px 28px}.wrap{max-width:1440px;margin:auto}
h1{margin:0;font-size:22px;letter-spacing:0}.sub{color:var(--muted);margin-top:4px}
.filters{display:flex;gap:12px;align-items:end;flex-wrap:wrap;padding:18px 28px;background:#fff;border-bottom:1px solid var(--line)}
label{display:grid;gap:5px;font-size:12px;color:var(--muted)}select,input,button,a.button{height:38px;border:1px solid #bfc9d3;border-radius:6px;background:#fff;padding:0 11px;color:var(--ink)}
button,a.button{display:inline-flex;align-items:center;text-decoration:none;cursor:pointer;font-weight:600}button{background:var(--blue);border-color:var(--blue);color:#fff}
main{padding:22px 28px}.metrics{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:12px;margin-bottom:18px}
.metric{background:#fff;border:1px solid var(--line);border-radius:7px;padding:15px}.metric b{display:block;font-size:21px;margin-top:5px}.metric span{color:var(--muted)}
.panel{background:#fff;border:1px solid var(--line);border-radius:7px;overflow:hidden}.panel-head{padding:14px 16px;border-bottom:1px solid var(--line);font-weight:700}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1080px}th,td{padding:11px 14px;border-bottom:1px solid #edf0f2;text-align:right;white-space:nowrap}
th{background:#fafbfc;color:#52606d;font-size:12px}th:first-child,td:first-child{text-align:left;max-width:360px;overflow:hidden;text-overflow:ellipsis}
.good{color:var(--green);font-weight:700}.error{background:#fff3f1;border:1px solid #efb3a8;padding:16px;border-radius:7px;color:#9b2c1f}
@media(max-width:900px){.metrics{grid-template-columns:repeat(2,1fr)}header,.filters,main{padding-left:16px;padding-right:16px}}
</style></head>
<body>
<header><div class="wrap"><h1>Meta Ads 团队看板</h1><div class="sub">{{ account }} · {{ since }} 至 {{ until }}</div></div></header>
<form class="filters" method="get">
<label>时间范围<select name="preset">{% for value,label in presets %}<option value="{{ value }}" {% if preset==value %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
<label>开始日期<input type="date" name="since" value="{{ since }}"></label>
<label>结束日期<input type="date" name="until" value="{{ until }}"></label>
<label>查看层级<select name="level">{% for value,label in levels %}<option value="{{ value }}" {% if level==value %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
<label>最少安装数<input type="number" min="0" name="min_installs" value="{{ min_installs }}"></label>
<label>排序<select name="sort">{% for value,label in sorts %}<option value="{{ value }}" {% if sort==value %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
<button type="submit">查询</button><a class="button" href="/export.csv?{{ query }}">导出 CSV</a>
</form>
<main class="wrap">
{% if error %}<div class="error"><b>读取失败</b><br>{{ error }}</div>{% else %}
<section class="metrics">
<div class="metric"><span>花费</span><b>{{ money(summary.spend) }}</b></div>
<div class="metric"><span>安装</span><b>{{ number(summary.installs) }}</b></div>
<div class="metric"><span>CPI</span><b>{{ money(summary.cpi) }}</b></div>
<div class="metric"><span>购买</span><b>{{ number(summary.purchases) }}</b></div>
<div class="metric"><span>收入</span><b>{{ money(summary.revenue) }}</b></div>
<div class="metric"><span>ROAS</span><b>{{ "%.2f"|format(summary.roas or 0) }}</b></div>
</section>
<section class="panel"><div class="panel-head">明细 · {{ rows|length }} 条</div><div class="table-wrap"><table>
<thead><tr><th>名称</th><th>花费</th><th>安装</th><th>CPI</th><th>购买</th><th>购买成本</th><th>收入</th><th>ROAS</th><th>展示</th><th>点击</th><th>CTR</th></tr></thead>
<tbody>{% for row in rows %}<tr>
<td title="{{ row.name }}">{{ row.name }}</td><td>{{ money(row.spend) }}</td><td>{{ number(row.installs) }}</td>
<td class="{% if row.cpi and summary.cpi and row.cpi < summary.cpi %}good{% endif %}">{{ money(row.cpi) }}</td>
<td>{{ number(row.purchases) }}</td><td>{{ money(row.cpp) }}</td><td>{{ money(row.revenue) }}</td>
<td>{{ "%.2f"|format(row.roas or 0) }}</td><td>{{ number(row.impressions) }}</td><td>{{ number(row.clicks) }}</td><td>{{ "%.2f"|format(row.ctr) }}%</td>
</tr>{% else %}<tr><td colspan="11">当前筛选条件没有数据</td></tr>{% endfor %}</tbody>
</table></div></section>{% endif %}
</main></body></html>
"""


@app.route("/")
def dashboard():
    since, until = selected_dates()
    level = request.args.get("level", "material")
    rows, summary, error = [], {}, None
    try:
        since, until, level, rows, summary = view_data()
    except Exception as exc:
        error = str(exc)
    return render_template_string(
        PAGE, account=ACCOUNT, since=since, until=until, level=level,
        rows=rows, summary=summary, error=error,
        preset=request.args.get("preset", "last_7d"),
        min_installs=request.args.get("min_installs", "1"),
        sort=request.args.get("sort", "spend"),
        query=urlencode(request.args), money=money, number=number,
        presets=[("last_7d", "过去 7 天"), ("yesterday", "昨天"), ("last_30d", "过去 30 天"), ("this_month", "本月"), ("custom", "自定义")],
        levels=[("material", "素材"), ("campaign", "广告系列"), ("adset", "广告组"), ("account", "账户")],
        sorts=[("spend", "花费从高到低"), ("installs", "安装从高到低"), ("cpi", "CPI 从低到高"), ("roas", "ROAS 从高到低")],
    )


@app.route("/export.csv")
def export_csv():
    since, until, level, rows, _ = view_data()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["名称", "花费", "安装", "CPI", "购买", "购买成本", "收入", "ROAS", "展示", "点击", "CTR"])
    for row in rows:
        writer.writerow([row["name"], row["spend"], row["installs"], row["cpi"], row["purchases"], row["cpp"], row["revenue"], row["roas"], row["impressions"], row["clicks"], row["ctr"]])
    filename = "meta-ads-{}-{}-{}.csv".format(level, since, until)
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="' + filename + '"'})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "account": ACCOUNT})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
