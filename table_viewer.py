"""Self-contained paginated HTML tables for tabular KINEPIK results.

Unlike the KSEA charts (Matplotlib PNGs), a phosphosite list is naturally
tabular and can run into hundreds/thousands of rows — a plain text reply
can't paginate. This module renders a small standalone HTML file with
plain JavaScript page controls, shown in the chat inside an iframe.
"""

import html
import json
import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 20


def _save_static(filename):
    static_dir = os.path.join(_BASE_DIR, "static")
    os.makedirs(static_dir, exist_ok=True)
    return os.path.join(static_dir, filename)


def _extract_sites(result, key="TargetPhosphosites", alt_key="target_phosphosites"):
    """Pull the flat list of 'SUBSTRATE(SITE)' strings out of a KINEPIK
    kinases/specific response, de-duplicated and in original order.

    key/alt_key select which field to read — TargetPhosphosites for the
    sites a kinase acts on, or PhosphositesOnKinase for its own sites."""
    sites = []
    if not result or not isinstance(result, list):
        return sites
    for item in result:
        if not isinstance(item, dict):
            continue
        raw = item.get(key, item.get(alt_key, []))
        if isinstance(raw, list):
            sites.extend(str(s) for s in raw if s)
    return list(dict.fromkeys(sites))


def build_phosphosite_table(protein_name, protein_id, all_result, mode="targets"):
    """Build a paginated HTML table of a kinase's phosphosites.

    mode="targets" (default) shows the phosphosites this kinase acts on, on
    other proteins. mode="own" shows the kinase's own autophosphorylation
    sites instead — a different KINEPIK field entirely, not a filtered view
    of the same data.

    Only shows substrate and site — KINEPIK's confidence field could not be
    reliably explained (its underlying meaning isn't documented), so no
    confidence label is shown rather than presenting an unverified claim.

    Returns the relative static path to the HTML file, or None if there is
    no phosphosite data to show.
    """
    if mode == "own":
        all_sites = _extract_sites(all_result, key="PhosphositesOnKinase", alt_key="phosphosites_on_kinase")
    else:
        all_sites = _extract_sites(all_result)
    if not all_sites:
        return None

    rows = []
    for site in all_sites:
        if "(" in site and site.endswith(")"):
            substrate, residue = site.rsplit("(", 1)
            residue = residue.rstrip(")")
        else:
            substrate, residue = site, ""
        rows.append({"substrate": substrate, "site": residue})

    rows.sort(key=lambda r: r["substrate"])

    rows_json = json.dumps(rows)
    safe_name = html.escape(protein_name)
    safe_id = html.escape(protein_id)

    page_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    * {{ box-sizing: border-box; }}
    body {{
        margin: 0;
        font-family: -apple-system, 'Segoe UI', sans-serif;
        background: #ffffff;
        color: #0f172a;
        font-size: 13px;
    }}
    .table-wrap {{ padding: 14px 16px; }}
    .table-header {{
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 10px;
        flex-wrap: wrap;
        gap: 6px;
    }}
    .table-header h3 {{ margin: 0; font-size: 15px; color: #0d2137; }}
    .table-header .meta {{ font-size: 12px; color: #64748b; }}
    .search-row {{ margin-bottom: 10px; }}
    .search-row input {{
        width: 100%;
        padding: 8px 12px;
        border: 1.5px solid #e2e8f0;
        border-radius: 8px;
        font-size: 13px;
        color: #0f172a;
        background: #f8fafc;
        outline: none;
        transition: border-color 0.2s, box-shadow 0.2s;
    }}
    .search-row input:focus {{
        border-color: #0891b2;
        box-shadow: 0 0 0 3px rgba(8, 145, 178, 0.12);
        background: #fff;
    }}
    .no-results {{
        padding: 20px 8px;
        text-align: center;
        color: #94a3b8;
        font-size: 13px;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{
        text-align: left;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.4px;
        color: #64748b;
        border-bottom: 2px solid #e2e8f0;
        padding: 6px 8px;
    }}
    tbody td {{
        padding: 7px 8px;
        border-bottom: 1px solid #f1f5f9;
    }}
    tbody tr:hover {{ background: #f8fafc; }}
    .pagination {{
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 10px;
        margin-top: 12px;
        padding-top: 10px;
        border-top: 1px solid #f1f5f9;
    }}
    .pagination button {{
        padding: 6px 12px;
        border: 1px solid #cbd5e1;
        background: white;
        border-radius: 6px;
        font-size: 12px;
        cursor: pointer;
        color: #0d2137;
    }}
    .pagination button:hover:not(:disabled) {{ background: #f1f5f9; border-color: #0891b2; }}
    .pagination button:disabled {{ opacity: 0.4; cursor: default; }}
    .pagination span {{ font-size: 12px; color: #475569; }}
    .note {{
        margin-top: 10px;
        font-size: 11px;
        color: #94a3b8;
        font-style: italic;
    }}
</style>
</head>
<body>
<div class="table-wrap">
    <div class="table-header">
        <h3>Phosphosites {"on" if mode == "own" else "targeted by"} {safe_name} ({safe_id})</h3>
        <span class="meta" id="count-label"></span>
    </div>
    <div class="search-row">
        <input type="text" id="search-input" placeholder="Search by substrate name, e.g. BRCA1..." autocomplete="off">
    </div>
    <table>
        <thead>
            <tr><th>Substrate</th><th>Site</th></tr>
        </thead>
        <tbody id="table-body"></tbody>
    </table>
    <div class="pagination">
        <button id="prev-btn">&larr; Previous</button>
        <span id="page-label"></span>
        <button id="next-btn">Next &rarr;</button>
    </div>
    <div class="note">
        This list combines validated and predicted kinase-substrate relationships
        from KINEPIK's underlying data sources — not every entry has direct
        experimental confirmation.
    </div>
</div>
<script>
    const allRows = {rows_json};
    const pageSize = {PAGE_SIZE};
    let filteredRows = allRows;
    let page = 0;

    const tbody = document.getElementById("table-body");
    const pageLabel = document.getElementById("page-label");
    const countLabel = document.getElementById("count-label");
    const prevBtn = document.getElementById("prev-btn");
    const nextBtn = document.getElementById("next-btn");
    const searchInput = document.getElementById("search-input");

    function render() {{
        const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
        const start = page * pageSize;
        const pageRows = filteredRows.slice(start, start + pageSize);

        if (filteredRows.length === 0) {{
            tbody.innerHTML = `<tr><td colspan="2"><div class="no-results">No phosphosites match your search.</div></td></tr>`;
        }} else {{
            tbody.innerHTML = pageRows.map(r =>
                `<tr><td>${{r.substrate}}</td><td>${{r.site}}</td></tr>`
            ).join("");
        }}

        pageLabel.textContent = `Page ${{page + 1}} of ${{totalPages}}`;
        countLabel.textContent = filteredRows.length === allRows.length
            ? `${{allRows.length}} sites total`
            : `${{filteredRows.length}} of ${{allRows.length}} sites match`;
        prevBtn.disabled = page === 0;
        nextBtn.disabled = page >= totalPages - 1;
    }}

    prevBtn.addEventListener("click", () => {{ if (page > 0) {{ page--; render(); }} }});
    nextBtn.addEventListener("click", () => {{
        const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
        if (page < totalPages - 1) {{ page++; render(); }}
    }});

    searchInput.addEventListener("input", () => {{
        const query = searchInput.value.trim().toLowerCase();
        filteredRows = query
            ? allRows.filter(r => r.substrate.toLowerCase().includes(query))
            : allRows;
        page = 0;
        render();
    }});

    render();
</script>
</body>
</html>"""

    filename = "phosphosite_table_own.html" if mode == "own" else "phosphosite_table.html"
    abs_path = _save_static(filename)
    with open(abs_path, "w") as f:
        f.write(page_html)

    return f"static/{filename}"
