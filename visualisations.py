import os

# Set the matplotlib config directory before importing matplotlib to avoid permission errors
os.environ.setdefault("MPLCONFIGDIR", os.path.join(os.getcwd(), ".matplotlib_cache"))

import matplotlib

# Use the non-interactive Agg backend so charts can be saved to files without a display
matplotlib.use("Agg")

import matplotlib.pyplot as plt

# Directory where this file lives — used to build absolute paths to the static folder
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _save_static(filename):
    """Return the absolute path to save a file in the static folder, creating it if needed."""
    static_dir = os.path.join(_BASE_DIR, "static")
    os.makedirs(static_dir, exist_ok=True)
    return os.path.join(static_dir, filename)


def _resolve_labels(uniprot_ids):
    """Convert a list of UniProt IDs to gene names using KINEPIK.
    Only IDs that look like UniProt accessions are looked up — others are passed through as-is.
    Returns a dict {uniprot_id: gene_name}."""
    from utils import batch_uniprot_to_gene, looks_like_uniprot_id
    ids_to_resolve = [uid for uid in uniprot_ids if looks_like_uniprot_id(uid)]
    if not ids_to_resolve:
        return {}
    return batch_uniprot_to_gene(ids_to_resolve)


def _style_ax(ax):
    """Apply consistent styling to a matplotlib axes: dashed grid lines, no top/right border."""
    ax.yaxis.grid(True, linestyle="--", linewidth=0.5, color="#d1d5db", zorder=0)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="x", labelsize=9)
    ax.tick_params(axis="y", labelsize=9)


def _add_value_labels(ax, bars, scores):
    """Print the z-score value above (positive) or below (negative) each bar in a chart."""
    for bar, score in zip(bars, scores):
        offset = 0.05 if score >= 0 else -0.05
        va = "bottom" if score >= 0 else "top"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            score + offset,
            f"{score:.2f}",
            ha="center",
            va=va,
            fontsize=7.5,
            color="#111827",
        )


def plot_ksea(data, perturbation=None, direction=None):
    """Draw a bar chart of KSEA z-scores for the top kinases under a perturbation.
    Bars are ordered by strength of effect in the requested direction: strongest
    activation first when direction='positive', strongest inhibition first when
    direction='negative', otherwise sorted from most positive to most negative.
    direction: 'positive', 'negative', or None."""
    if direction == "negative":
        rows = sorted(data[:10], key=lambda r: r["z_score"])
    else:
        rows = sorted(data[:10], key=lambda r: r["z_score"], reverse=True)
    raw_ids = [row["kinase"] for row in rows]
    label_map = _resolve_labels(raw_ids)
    kinases = [label_map.get(uid, uid) for uid in raw_ids]
    scores = [row["z_score"] for row in rows]
    perturbation = perturbation or (rows[0].get("perturbation", "selected condition") if rows else "selected condition")
    colors = ["#2563eb" if score >= 0 else "#dc2626" for score in scores]

    direction_label = {
        "positive": "Most Activated",
        "negative": "Most Inhibited",
    }.get(direction, "Top Activity Changes")

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(kinases, scores, color=colors, edgecolor="white", linewidth=0.6, zorder=3)
    ax.axhline(0, color="#374151", linewidth=0.9, zorder=4)
    _style_ax(ax)
    _add_value_labels(ax, bars, scores)

    ax.set_xlabel("Kinase", fontsize=11)
    ax.set_ylabel("Z-score", fontsize=11)
    ax.set_title(f"KSEA {direction_label} — {perturbation}", fontsize=12, fontweight="bold", pad=10)
    plt.xticks(rotation=40, ha="right")
    fig.tight_layout()

    abs_path = _save_static("ksea.png")
    fig.savefig(abs_path, dpi=150)
    plt.close(fig)
    return "static/ksea.png"


def plot_top_connected_kinases(kinase_names, connection_counts):
    """Draw a horizontal bar chart of the top connected kinases ranked by number of connections.

    Parameters
    ----------
    kinase_names : list[str]
        Gene names or IDs of the top kinases, ordered from most to least connected.
    connection_counts : list[int]
        Connection counts corresponding to each kinase.

    Returns
    -------
    str
        Relative path to the saved image.
    """
    # Reverse so highest is at the top
    names = kinase_names[::-1]
    counts = connection_counts[::-1]
    colors = ["#2563eb"] * len(names)

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.barh(names, counts, color=colors, edgecolor="white", linewidth=0.6, zorder=3)

    for bar, count in zip(bars, counts):
        ax.text(
            bar.get_width() + 5,
            bar.get_y() + bar.get_height() / 2,
            str(count),
            va="center",
            fontsize=9,
            color="#111827",
        )

    ax.set_xlabel("Number of Connections", fontsize=11)
    ax.set_title(
        "Most Connected Kinases — KINEPIK Signalling Network",
        fontsize=12, fontweight="bold", pad=12,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.xaxis.grid(True, linestyle="--", linewidth=0.5, color="#d1d5db", zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()

    abs_path = _save_static("top_connected.png")
    fig.savefig(abs_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "static/top_connected.png"


def plot_network(records, central_label, kinase_id=None):
    """Draw a force-directed subnetwork for a queried kinase.

    Shows edges between neighbour nodes (not just centre↔neighbour), uses a
    spring layout so clusters emerge naturally, and colours nodes by their
    degree in the full graph so hub kinases stand out.
    """
    try:
        import networkx as nx
    except ImportError:
        return None

    from utils import batch_uniprot_to_gene, looks_like_uniprot_id

    G = nx.MultiDiGraph()

    SOURCE_KEYS = ["source", "Source", "from", "From", "Kinase", "kinase"]
    TARGET_KEYS = ["target", "Target", "to", "To", "Substrate", "substrate"]

    for item in records:
        if isinstance(item, str):
            parts = item.strip().split()
            if len(parts) >= 3:
                source, target = parts[0], parts[2]
            elif len(parts) == 2:
                source, target = parts[0], parts[1]
            else:
                continue
        elif isinstance(item, dict):
            source = next((item[k] for k in SOURCE_KEYS if item.get(k)), None)
            target = next((item[k] for k in TARGET_KEYS if item.get(k)), None)
            if not source or not target:
                continue
        else:
            continue
        G.add_edge(source, target)

    if G.number_of_edges() == 0:
        return None

    central_id = kinase_id if kinase_id else central_label

    # Build subgraph of direct neighbours + all edges between them
    # Select the top 20 by degree in the full graph so the most connected are always shown
    neighbours = set(G.predecessors(central_id)) | set(G.successors(central_id))
    selected = sorted(neighbours, key=lambda n: G.degree(n), reverse=True)[:20]
    subG = G.subgraph(selected + [central_id]).copy()
    subG.remove_edges_from(nx.selfloop_edges(subG))

    # Resolve UniProt IDs to gene names
    unknown_ids = [n for n in subG.nodes if looks_like_uniprot_id(n)]
    label_map = batch_uniprot_to_gene(unknown_ids) if unknown_ids else {}
    subG = nx.relabel_nodes(subG, label_map)
    central_name = label_map.get(central_id, central_label)

    # Degree calculated only over subgraph nodes so outlier nodes from the
    # second API call don't inflate max_deg and make all neighbours look equal
    subgraph_nodes = set(subG.nodes)
    sub_degrees = {label_map.get(n, n): G.degree(n) for n in selected}
    max_deg = max(sub_degrees.values()) if sub_degrees else 1

    # Force-directed layout — clusters emerge naturally
    simple = nx.DiGraph(subG)
    pos = nx.spring_layout(simple, k=2.5, seed=42, iterations=100)

    # Node sizes and colours by degree in full graph
    MIN_SZ, MAX_SZ = 600, 3000
    node_sizes = []
    node_colors = []
    for n in subG.nodes:
        if n == central_name:
            node_sizes.append(3500)
            node_colors.append("#1d4ed8")
        else:
            deg = sub_degrees.get(n, 1)
            size = MIN_SZ + (MAX_SZ - MIN_SZ) * (deg - 1) / max(max_deg - 1, 1)
            node_sizes.append(int(size))
            intensity = (deg - 1) / max(max_deg - 1, 1)
            r = int(147 - intensity * 80)
            g_ch = int(197 - intensity * 100)
            b = int(253 - intensity * 50)
            node_colors.append(f"#{r:02x}{g_ch:02x}{b:02x}")

    # Edge widths — bidirectional edges drawn thicker
    edge_widths = []
    for u, v in subG.edges():
        edge_widths.append(2.0 if subG.has_edge(v, u) else 0.8)

    fig, ax = plt.subplots(figsize=(14, 11))
    ax.set_facecolor("#f8fafc")
    fig.patch.set_facecolor("#f8fafc")

    nx.draw_networkx_nodes(subG, pos, node_color=node_colors, node_size=node_sizes, ax=ax, alpha=0.93)
    nx.draw_networkx_labels(subG, pos, font_size=7.5, font_color="white", font_weight="bold", ax=ax)
    nx.draw_networkx_edges(
        subG, pos,
        edge_color="#94a3b8",
        arrows=True,
        arrowsize=12,
        arrowstyle="-|>",
        connectionstyle="arc3,rad=0.12",
        ax=ax,
        node_size=node_sizes,
        width=edge_widths,
        alpha=0.65,
    )

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#1d4ed8", markersize=13, label=f"{central_name} (queried)"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#93c5fd", markersize=9,  label="Low connectivity"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#4365c3", markersize=11, label="High connectivity"),
        Line2D([0], [0], color="#94a3b8", linewidth=2.0, label="Bidirectional interaction"),
    ]
    ax.legend(handles=legend_elements, loc="lower left", fontsize=8, framealpha=0.85, edgecolor="#e2e8f0")

    ax.set_title(
        f"Kinase Interaction Network — {central_name}\n"
        f"Node size = network connectivity · Edges shown between all connected kinases",
        fontsize=12, fontweight="bold", pad=14,
    )
    ax.axis("off")
    fig.tight_layout()

    abs_path = _save_static("network.png")
    fig.savefig(abs_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return "static/network.png"


def plot_ksea_heatmap(matrix, kinase_labels, perturbation_labels):
    """Draw a heatmap of KSEA z-scores with kinases on the Y-axis and perturbations on the X-axis.

    Each cell is coloured on a red–white–blue diverging scale:
    blue = activated (positive z-score), red = inhibited (negative z-score),
    light grey = no data available for that combination.

    The numeric z-score is printed inside each cell for readability.

    Parameters
    ----------
    matrix : dict
        Mapping of ``(kinase_label, perturbation_label)`` tuples to z-score floats.
    kinase_labels : list[str]
        Ordered list of kinase names (row labels).
    perturbation_labels : list[str]
        Ordered list of perturbation names (column labels).

    Returns
    -------
    str
        Relative path to the saved image (``"static/ksea_heatmap.png"``).
    """
    import numpy as np

    import numpy as np
    import matplotlib.cm as cm
    import matplotlib.colors as mcolors

    # Drop perturbations that are entirely NaN across all kinases (all-grey columns)
    perturbation_labels = [
        p for p in perturbation_labels
        if any(not np.isnan(matrix.get((k, p), np.nan)) for k in kinase_labels)
    ]
    # Drop kinases that are entirely NaN across all perturbations (all-grey rows)
    kinase_labels = [
        k for k in kinase_labels
        if any(not np.isnan(matrix.get((k, p), np.nan)) for p in perturbation_labels)
    ]

    if not perturbation_labels or not kinase_labels:
        # Nothing survived filtering — return a minimal placeholder image
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.text(0.5, 0.5, "No KSEA data available for the requested combinations.",
                ha="center", va="center", fontsize=11, color="#6b7280", transform=ax.transAxes)
        ax.axis("off")
        abs_path = _save_static("ksea_heatmap.png")
        fig.savefig(abs_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return "static/ksea_heatmap.png"

    # Build a 2-D float array; NaN where no data exists
    grid = np.array(
        [
            [matrix.get((k, p), np.nan) for p in perturbation_labels]
            for k in kinase_labels
        ],
        dtype=float,
    )

    n_rows = len(kinase_labels)
    n_cols = len(perturbation_labels)
    fig_w = max(10, n_cols * 1.6)
    fig_h = max(5, n_rows * 1.2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    # Diverging colour scale centred at 0
    finite_vals = grid[~np.isnan(grid)]
    vmax = float(np.max(np.abs(finite_vals))) if finite_vals.size else 3.0
    cmap = cm.RdBu
    norm = mcolors.Normalize(vmin=-vmax, vmax=vmax)

    # Draw every cell as a coloured Rectangle — avoids imshow dtype constraints
    for i in range(n_rows):
        for j in range(n_cols):
            val = grid[i, j]
            if np.isnan(val):
                ax.add_patch(
                    plt.Rectangle(
                        (j - 0.5, i - 0.5), 1, 1,
                        facecolor="#e5e7eb", edgecolor="white", lw=0.6,
                    )
                )
                ax.text(j, i, "n/a", ha="center", va="center",
                        fontsize=7, color="#9ca3af")
            else:
                colour = cmap(norm(val))
                ax.add_patch(
                    plt.Rectangle(
                        (j - 0.5, i - 0.5), 1, 1,
                        facecolor=colour, edgecolor="white", lw=0.6,
                    )
                )
                text_colour = "white" if abs(val) > vmax * 0.55 else "#111827"
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=7.5, color=text_colour, fontweight="bold")

    # Axis limits, labels, ticks
    ax.set_xlim(-0.5, n_cols - 0.5)
    ax.set_ylim(-0.5, n_rows - 0.5)
    ax.invert_yaxis()   # first kinase at the top
    ax.set_xticks(range(n_cols))
    ax.set_xticklabels(perturbation_labels, rotation=40, ha="right", fontsize=9)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(kinase_labels, fontsize=9)
    ax.tick_params(length=0)

    # Colourbar via ScalarMappable (no imshow required)
    sm = cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, label="KSEA Z-score", shrink=0.75, pad=0.02)
    cbar.ax.tick_params(labelsize=8)

    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.set_title(
        "Kinase Activity Heatmap — KSEA Z-scores\n"
        "Blue = activated · Red = inhibited · Grey = no data",
        fontsize=11, fontweight="bold", pad=12,
    )

    fig.tight_layout()
    abs_path = _save_static("ksea_heatmap.png")
    fig.savefig(abs_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return "static/ksea_heatmap.png"


def plot_ksea_conditions(data, protein_label):
    """Draw a bar chart comparing a single kinase's KSEA z-score across multiple perturbations.
    Bars are sorted from most activated to most inhibited.
    P-values are shown on the bars where KINEPIK provides them."""
    rows = sorted(data, key=lambda r: r["z_score"], reverse=True)

    # Resolve protein label if it's a UniProt ID
    label_map = _resolve_labels([protein_label])
    protein_label = label_map.get(protein_label, protein_label)

    perturbations = [row["perturbation"] for row in rows]
    scores = [row["z_score"] for row in rows]
    colors = ["#2563eb" if score >= 0 else "#dc2626" for score in scores]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(perturbations, scores, color=colors, edgecolor="white", linewidth=0.6, zorder=3)
    ax.axhline(0, color="#374151", linewidth=0.9, zorder=4)
    _style_ax(ax)

    # Scale label offset and y-limit padding to the data range so two-line
    # labels (z-score + p-value) never collide with the bar or the x-axis
    span = max(scores) - min(scores) if len(scores) > 1 else abs(scores[0]) or 1
    offset_mag = max(span * 0.08, 0.05)

    for bar, row in zip(bars, rows):
        z = row["z_score"]
        p_value = row.get("p_value")
        label = f"{z:.2f}"
        if p_value is not None:
            label += f"\np={p_value:.2g}"
        va = "bottom" if z >= 0 else "top"
        offset = offset_mag if z >= 0 else -offset_mag
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            z + offset,
            label,
            ha="center",
            va=va,
            fontsize=7.5,
            color="#111827",
        )

    ymin, ymax = min(scores), max(scores)
    pad = max(span * 0.35, 0.3)
    ax.set_ylim(ymin - pad, ymax + pad)

    ax.set_xlabel("Perturbation", fontsize=11)
    ax.set_ylabel("KSEA Z-score", fontsize=11)
    ax.set_title(f"{protein_label} KSEA Activity Across Perturbations", fontsize=12, fontweight="bold", pad=10)
    plt.xticks(rotation=40, ha="right")
    fig.tight_layout()

    abs_path = _save_static("ksea_conditions.png")
    fig.savefig(abs_path, dpi=150)
    plt.close(fig)
    return "static/ksea_conditions.png"


def plot_ksea_cell_lines(data, protein_label, perturbation):
    """Draw a bar chart comparing a single kinase's KSEA z-score under one
    perturbation across multiple cell lines (e.g. MCF7, HL60, NTERA2).

    Bars are sorted from most activated to most inhibited, and p-values are
    shown where KINEPIK provides them — same visual language as
    plot_ksea_conditions but comparing cell lines instead of drugs."""
    rows = sorted(data, key=lambda r: r["z_score"], reverse=True)

    label_map = _resolve_labels([protein_label])
    protein_label = label_map.get(protein_label, protein_label)

    cell_lines = [row["cell_line"] for row in rows]
    scores = [row["z_score"] for row in rows]
    colors = ["#2563eb" if score >= 0 else "#dc2626" for score in scores]

    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(cell_lines, scores, color=colors, edgecolor="white", linewidth=0.6, zorder=3)
    ax.axhline(0, color="#374151", linewidth=0.9, zorder=4)
    _style_ax(ax)

    # Scale label offset and y-limit padding to the data range so the two-line
    # labels (z-score + p-value) never collide with the bar or the x-axis
    span = max(scores) - min(scores) if len(scores) > 1 else abs(scores[0]) or 1
    offset_mag = max(span * 0.08, 0.05)

    for bar, row in zip(bars, rows):
        z = row["z_score"]
        p_value = row.get("p_value")
        label = f"{z:.2f}"
        if p_value is not None:
            label += f"\np={p_value:.2g}"
        va = "bottom" if z >= 0 else "top"
        offset = offset_mag if z >= 0 else -offset_mag
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            z + offset,
            label,
            ha="center",
            va=va,
            fontsize=8,
            color="#111827",
        )

    ymin, ymax = min(scores), max(scores)
    pad = max(span * 0.35, 0.3)
    ax.set_ylim(ymin - pad, ymax + pad)

    ax.set_xlabel("Cell Line", fontsize=11)
    ax.set_ylabel("KSEA Z-score", fontsize=11)
    ax.set_title(
        f"{protein_label} KSEA Activity Under {perturbation} — Across Cell Lines",
        fontsize=12, fontweight="bold", pad=10,
    )
    fig.tight_layout()

    abs_path = _save_static("ksea_cell_lines.png")
    fig.savefig(abs_path, dpi=150)
    plt.close(fig)
    return "static/ksea_cell_lines.png"


def plot_fold_change(data, kinase_label, substrate_label, perturbation, cell_line):
    """Draw a bar chart of raw substrate fold-change values — one bar per
    phosphosite — for a specific kinase's effect on a specific substrate
    under one drug. This is the underlying experimental evidence behind a
    KSEA z-score, rather than the summary statistic itself.

    data: list of dicts with keys 'site' (phosphosite label, e.g. 'S1189')
    and 'fc' (float fold-change value)."""
    rows = sorted(data, key=lambda r: r["fc"], reverse=True)

    sites = [row["site"] for row in rows]
    values = [row["fc"] for row in rows]
    colors = ["#2563eb" if v >= 0 else "#dc2626" for v in values]

    fig, ax = plt.subplots(figsize=(max(8, len(sites) * 0.9), 5))
    bars = ax.bar(sites, values, color=colors, edgecolor="white", linewidth=0.6, zorder=3)
    ax.axhline(0, color="#374151", linewidth=0.9, zorder=4)
    _style_ax(ax)

    span = max(values) - min(values) if len(values) > 1 else abs(values[0]) or 1
    offset_mag = max(span * 0.08, 0.05)
    for bar, v in zip(bars, values):
        va = "bottom" if v >= 0 else "top"
        offset = offset_mag if v >= 0 else -offset_mag
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            v + offset,
            f"{v:.2f}",
            ha="center",
            va=va,
            fontsize=8,
            color="#111827",
        )

    ymin, ymax = min(values), max(values)
    pad = max(span * 0.3, 0.3)
    ax.set_ylim(ymin - pad, ymax + pad)

    ax.set_xlabel("Phosphosite", fontsize=11)
    ax.set_ylabel("Fold-change (log2)", fontsize=11)
    ax.set_title(
        f"{kinase_label} → {substrate_label} — Raw Fold-Change Under {perturbation} ({cell_line})",
        fontsize=12, fontweight="bold", pad=10,
    )
    fig.tight_layout()

    abs_path = _save_static("fold_change.png")
    fig.savefig(abs_path, dpi=150)
    plt.close(fig)
    return "static/fold_change.png"

