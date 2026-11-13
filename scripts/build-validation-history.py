"""Render reviewed, non-sensitive validation summaries without embedding executable data."""

import html
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "docs/product-validation"
RESULTS = {"passed": "通过", "failed": "失败", "not_run": "未执行", "blocked": "受阻", "unverified": "未验证"}


def render(records):
    rows = []
    seen = set()
    for record in records:
        required = ("id", "date", "commit", "module", "scenario", "phase", "result", "record", "fix_of")
        if not isinstance(record, dict) or any(not isinstance(record.get(k), str) for k in required):
            raise ValueError("运行摘要字段不完整或不是字符串")
        if (
            record["id"] in seen
            or record["result"] not in RESULTS
            or record["phase"] not in {"stability", "quality"}
        ):
            raise ValueError("重复ID或无效阶段、结果")
        seen.add(record["id"])
        target = PurePosixPath(record["record"])
        if (
            target.is_absolute()
            or ".." in target.parts
            or ":" in str(target)
            or "\\" in str(target)
            or target.suffix != ".md"
        ):
            raise ValueError("记录链接必须为目录内Markdown相对路径")
        values = [record[k] for k in ("id", "date", "commit", "module", "scenario", "phase")]
        cells = "".join("<td>" + html.escape(v) + "</td>" for v in values)
        cells += "<td>" + RESULTS[record["result"]] + "</td>"
        cells += '<td><a href="' + html.escape(str(target), quote=True) + '">运行记录</a></td>'
        cells += "<td>" + html.escape(record["fix_of"]) + "</td>"
        rows.append("<tr>" + cells + "</tr>")
    body = "".join(rows) or '<tr><td colspan="9">本轮尚无运行记录；未执行不等于通过。</td></tr>'
    return (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ainovel 全项目验证历史</title><style>
body{font:16px/1.7 system-ui;background:#f6f7f4;color:#233a33;margin:24px}main{max-width:1400px;margin:auto}
input,select{font:inherit;padding:8px;border:1px solid #73867a;border-radius:6px}label{display:inline-block;margin:8px}
.scroll{overflow:auto}table{border-collapse:collapse;width:100%;background:#fffefa}td,th{border:1px solid #c8d1c9;padding:10px;text-align:left;overflow-wrap:anywhere}
a{color:#245e4d}:focus-visible{outline:3px solid #a25b16;outline-offset:3px}p{max-width:80ch}
</style><main><h1>ainovel 全项目验证历史</h1><p>覆盖整个产品。稳定性与产出质量分别记录；真实稿件及诊断证据保存在仓库外。</p>
<label>搜索日期、模块、场景、版本或关联记录 <input id="query" type="search"></label>
<label>验证阶段 <select id="phase"><option value="">全部</option><option value="stability">功能稳定性</option><option value="quality">产出质量</option></select></label>
<label>结果 <select id="result"><option value="">全部</option><option>通过</option><option>失败</option><option>未执行</option><option>受阻</option><option>未验证</option></select></label>
<p id="status" role="status"></p><div class="scroll"><table><thead><tr><th>ID</th><th>日期</th><th>源码版本</th><th>模块</th><th>场景</th><th>阶段</th><th>结果</th><th>详情</th><th>修复关联ID</th></tr></thead><tbody>"""
        + body
        + """</tbody></table></div><p><a href="README.md">记录说明</a> · <a href="需求与场景矩阵.md">全项目场景矩阵</a></p></main><script>
const query=document.querySelector('#query'),phase=document.querySelector('#phase'),result=document.querySelector('#result');
const rows=[...document.querySelectorAll('tbody tr')].filter(row=>row.cells.length===9);
function filter(){let count=0;for(const row of rows){const show=row.textContent.toLowerCase().includes(query.value.toLowerCase())&&(!phase.value||row.cells[5].textContent===phase.value)&&(!result.value||row.cells[6].textContent===result.value);row.hidden=!show;if(show)count++;}document.querySelector('#status').textContent=`显示 ${count} 条运行记录`;}
query.addEventListener('input',event=>{if(!event.isComposing)filter()});query.addEventListener('compositionend',filter);phase.addEventListener('change',filter);result.addEventListener('change',filter);filter();
</script></html>
"""
    )


if __name__ == "__main__":
    records = json.loads((DIRECTORY / "runs.json").read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("运行记录必须是列表")
    for record in records:
        # Validate before resolving any supplied path.
        render([record])
        if not (DIRECTORY / record["record"]).is_file():
            raise ValueError("运行记录链接不存在")
    (DIRECTORY / "index.html").write_text(render(records), encoding="utf-8", newline="\n")
