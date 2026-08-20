from formatters import (
    format_all_kinases,
    format_available_cell_lines,
    format_kinase_targets,
    format_ksea_result,
    format_network_attributes,
    format_perturbation_result,
    format_phosphosite_viewer,
    format_protein_result,
)
from kinepik_api import (
    get_all_kinases,
    get_all_perturbations,
    get_available_cell_lines,
    get_available_perturbations,
    get_fold_change,
    get_kinase,
    get_ksea,
    get_network,
    get_network_attributes,
    get_network_specific,
    lookup_protein,
)
from ksea_analysis import get_kinome_ksea, extract_ksea_data, extract_ksea_condition_data
from utils import _is_error_payload, extract_first_record, normalise_to_uniprot, _normalise_records
from visualisations import plot_fold_change, plot_ksea, plot_ksea_cell_lines, plot_ksea_conditions, plot_ksea_heatmap, plot_network, plot_top_connected_kinases, _resolve_labels
from table_viewer import build_phosphosite_table


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalise_or_error(protein):
    """Convert a protein name or UniProt ID to a UniProt ID.
    Returns (protein_id, None) on success, or (None, error_dict) if the protein
    cannot be identified.  The error_dict matches the standard reply format
    used by all tool handlers: {'reply': str, 'image': None}."""
    protein_id = normalise_to_uniprot(protein)
    if protein_id:
        return protein_id, None
    return None, {
        "reply": f"I could not identify {protein}. Try MTOR, AKT, ERK, EGFR, or a UniProt ID.",
        "image": None,
    }


def _with_source(reply_text, endpoint):
    """Append a footer noting which KINEPIK API endpoint produced this reply,
    for transparency/reproducibility. Shows DISPLAY_BASE_URL (a presentable
    public address) rather than the real internal BASE_URL requests actually
    go to, since that's not reachable from outside this server."""
    from kinepik_api import DISPLAY_BASE_URL
    return f"{reply_text}\n\nData source: {DISPLAY_BASE_URL}/{endpoint}"


# ---------------------------------------------------------------------------
# AI tool handlers
# Each run_* function is called by ai_router._dispatch_tool when GPT picks
# the matching tool.  They all return {"reply": str, "image": str | None}.
# ---------------------------------------------------------------------------

def run_ksea_single(protein, perturbation="AZD3759", cell_line="MCF7"):
    """Get the KSEA z-score for a single kinase under a perturbation and return it as text."""
    protein_id, error = _normalise_or_error(protein)
    if error:
        return error

    result = get_ksea(protein_id, perturbation=perturbation, cell_line=cell_line)
    if _is_error_payload(result):
        return {"reply": f"KINEPIK error: {result['error']}", "image": None}

    text = format_ksea_result(protein, result)
    return {
        "reply": _with_source(
            f"KSEA result for {protein} under {perturbation} in {cell_line}.\n"
            "A positive z-score suggests activation; a negative z-score suggests inhibition.\n\n"
            f"{text}",
            "perturbation/KSEA",
        ),
        "image": None,
    }


def _get_drugs_with_data():
    """Query KINEPIK dynamically to find which drugs actually have experimental KSEA data.
    Returns a sorted list of drug names that have AvailableTargetKinases in the database."""
    all_perts = get_all_perturbations()
    records = _normalise_records(all_perts)
    drugs_with_data = []
    for item in records:
        if not isinstance(item, dict):
            continue
        name = (
            item.get("PerturbationName")
            or item.get("perturbation_name")
            or item.get("name")
        )
        if not name:
            continue
        available = get_available_perturbations(name)
        if _is_error_payload(available):
            continue
        available_records = _normalise_records(available)
        for rec in available_records:
            if isinstance(rec, dict) and rec.get("AvailableTargetKinases"):
                drugs_with_data.append(name)
                break
    return sorted(drugs_with_data, key=lambda x: x.lower())


def run_ksea_top_visualisation(perturbation="AZD3759", cell_line="MCF7", direction=None):
    """Run KSEA across all kinases and produce a bar chart of the top results.
    direction can be 'positive' (most activated), 'negative' (most inhibited), or None (both).
    If no data is found for the requested perturbation, dynamically fetches the list of
    drugs that do have data and suggests them to the user."""
    result, meta = get_kinome_ksea([], perturbation=perturbation, cell_line=cell_line)
    if _is_error_payload(result):
        drugs = _get_drugs_with_data()
        suggestion = ", ".join(drugs) if drugs else "No drugs with data found."
        return {
            "reply": (
                f"No KSEA data is available for {perturbation} in {cell_line}.\n\n"
                f"The following drugs have experimental data in KINEPIK:\n{suggestion}"
            ),
            "image": None,
        }

    data = extract_ksea_data(result, direction=direction)
    if not data:
        label = {"positive": "activated", "negative": "inhibited"}.get(direction, "affected")
        drugs = _get_drugs_with_data()
        suggestion = ", ".join(drugs) if drugs else "No drugs with data found."
        return {
            "reply": (
                f"No {label} kinases found for {perturbation} in {cell_line}.\n\n"
                f"The following drugs have experimental data in KINEPIK:\n{suggestion}"
            ),
            "image": None,
        }

    image_path = plot_ksea(data, perturbation=perturbation, direction=direction)
    direction_text = {
        "positive": "most activated",
        "negative": "most inhibited",
    }.get(direction, "most changed")

    # Mirrors plot_ksea's own sort/label logic exactly, including its
    # direction-aware order, so the text below can never drift out of sync
    # with what the chart actually shows.
    if direction == "negative":
        top_rows = sorted(data[:10], key=lambda r: r["z_score"])
    else:
        top_rows = sorted(data[:10], key=lambda r: r["z_score"], reverse=True)
    raw_ids = [row["kinase"] for row in top_rows]
    label_map = _resolve_labels(raw_ids)
    values_line = "; ".join(
        f"{label_map.get(row['kinase'], row['kinase'])}: z={row['z_score']:.2f}"
        for row in top_rows
    )

    return {
        "reply": _with_source(
            f"Top {direction_text} kinases under {perturbation} in {cell_line}.\n"
            "Positive z-scores suggest activation; negative z-scores suggest inhibition.\n"
            f"Scanned {meta['kinases_requested']} KINEPIK kinases in {meta['batches']} batches. "
            f"Failed batches: {meta['failed_batches']}.\n"
            f"Values: {values_line}",
            "perturbation/KSEA",
        ),
        "image": image_path,
    }


def run_ksea_condition_comparison(protein, perturbations, cell_line="MCF7"):
    """Compare a single kinase's KSEA z-score across multiple perturbations and
    produce a bar chart showing how its activity changes under each condition."""
    protein_id, error = _normalise_or_error(protein)
    if error:
        return error

    perturbations = [item for item in perturbations if item]
    if len(perturbations) < 2:
        return {"reply": "Please provide at least two perturbations to compare.", "image": None}

    result = get_ksea(protein_id, perturbation=",".join(perturbations), cell_line=cell_line)
    if _is_error_payload(result):
        return {"reply": f"KINEPIK error: {result['error']}", "image": None}

    data = extract_ksea_condition_data(result)
    if not data:
        return {"reply": f"No KSEA condition data found for {protein} in {cell_line}.", "image": None}

    image_path = plot_ksea_conditions(data, protein)
    sorted_data = sorted(data, key=lambda r: r["z_score"], reverse=True)
    values_line = "; ".join(
        f"{row['perturbation']}: z={row['z_score']:.2f}"
        + (f" (p={row['p_value']:.2g})" if row.get("p_value") is not None else "")
        for row in sorted_data
    )
    return {
        "reply": _with_source(
            f"KSEA activity for {protein} across {len(data)} perturbations in {cell_line}.\n"
            "Positive z-scores suggest activation; negative z-scores suggest inhibition. "
            "P-values are annotated where KINEPIK provides them.\n"
            f"Values: {values_line}",
            "perturbation/KSEA",
        ),
        "image": image_path,
    }


DEFAULT_CELL_LINES = ["MCF7", "HL60", "NTERA2"]


def run_ksea_cell_line_comparison(protein, perturbation, cell_lines=None):
    """Compare a single kinase's KSEA z-score under one perturbation across
    multiple cell lines, showing whether the drug's effect is cell-type specific.
    Runs one KSEA query per cell line and combines the results into one chart."""
    protein_id, error = _normalise_or_error(protein)
    if error:
        return error

    cell_lines = [c for c in (cell_lines or DEFAULT_CELL_LINES) if c]
    if len(cell_lines) < 2:
        return {"reply": "Please provide at least two cell lines to compare.", "image": None}

    data = []
    failed = []
    for cell_line in cell_lines:
        result = get_ksea(protein_id, perturbation=perturbation, cell_line=cell_line)
        if _is_error_payload(result):
            failed.append(cell_line)
            continue
        rows = extract_ksea_condition_data(result)
        if not rows:
            failed.append(cell_line)
            continue
        # Only one perturbation was requested, so take the matching row
        row = rows[0]
        data.append({
            "cell_line": cell_line,
            "z_score": row["z_score"],
            "p_value": row.get("p_value"),
        })

    if not data:
        return {
            "reply": (
                f"No KSEA data found for {protein} under {perturbation} in any of: "
                f"{', '.join(cell_lines)}."
            ),
            "image": None,
        }

    image_path = plot_ksea_cell_lines(data, protein, perturbation)
    footnote = f"\nNo data available in: {', '.join(failed)}." if failed else ""
    return {
        "reply": _with_source(
            f"KSEA activity for {protein} under {perturbation} across "
            f"{len(data)} cell line(s): {', '.join(row['cell_line'] for row in data)}.\n"
            "Positive z-scores suggest activation; negative z-scores suggest inhibition. "
            "Differences between cell lines can indicate cell-type specific drug response."
            f"{footnote}",
            "perturbation/KSEA",
        ),
        "image": image_path,
    }


def run_ksea_heatmap(kinases, perturbations, cell_line="MCF7"):
    """Fetch KSEA z-scores for every kinase × perturbation combination and draw a heatmap.
    Calls get_ksea once per kinase (all perturbations comma-separated) to minimise API calls.
    Columns and rows that are entirely missing data are automatically dropped."""
    if not kinases or not perturbations:
        return {"reply": "Please provide at least one kinase and one perturbation.", "image": None}

    matrix = {}           # {(kinase_label, perturbation): z_score}
    resolved_labels = []  # kinase display labels in resolution order
    failed = []

    for kinase_name in kinases:
        protein_id = normalise_to_uniprot(kinase_name)
        if not protein_id:
            failed.append(kinase_name)
            continue

        result = get_ksea(protein_id, perturbation=",".join(perturbations), cell_line=cell_line)
        if _is_error_payload(result):
            failed.append(kinase_name)
            continue

        rows = extract_ksea_condition_data(result)
        label = kinase_name.upper()
        resolved_labels.append(label)
        for row in rows:
            matrix[(label, row["perturbation"])] = row["z_score"]

    if not matrix:
        return {
            "reply": (
                "No KSEA data was returned for the requested kinases and perturbations. "
                "Check the names are valid KINEPIK entries."
            ),
            "image": None,
        }

    image_path = plot_ksea_heatmap(matrix, resolved_labels, perturbations)
    footnote = f"\nCould not resolve: {', '.join(failed)}." if failed else ""
    note = (
        "\nNote: CRISPR knockdowns (e.g. EZH1_sg01) often lack phosphoproteomic data — "
        "try drug perturbations like Dasatinib or Tofacitinib instead."
        if failed or len(perturbations) > 3 else ""
    )
    return {
        "reply": _with_source(
            f"KSEA z-score heatmap for {len(resolved_labels)} kinase(s) across "
            f"{len(perturbations)} perturbation(s) in {cell_line}.\n"
            "Blue = activated · Red = inhibited · Columns/rows with no data are hidden."
            f"{footnote}{note}",
            "perturbation/KSEA",
        ),
        "image": image_path,
    }


def run_protein_lookup(protein):
    """Look up general information about a protein (description, gene name, kinase family)."""
    protein_id, error = _normalise_or_error(protein)
    if error:
        return error
    result = lookup_protein(protein_id)
    return {"reply": _with_source(format_protein_result(protein_id, result), "proteins/results"), "image": None}


def run_kinase_info(protein):
    """Look up kinase-specific classification info: family, group, description,
    and how many phosphosites are on record for it. Family/group data only exists
    in KINEPIK's full kinases/all list (kinases/specific doesn't return it), so
    this fetches that list once and finds the matching entry client-side."""
    protein_id, error = _normalise_or_error(protein)
    if error:
        return error

    all_result = get_all_kinases()
    if _is_error_payload(all_result):
        return {"reply": f"KINEPIK error: {all_result['error']}", "image": None}

    match = None
    for item in all_result or []:
        entry = item if isinstance(item, dict) else (item[0] if isinstance(item, list) and item else None)
        if isinstance(entry, dict) and entry.get("UniprotID") == protein_id:
            match = entry
            break

    site_result = get_kinase(protein_id)
    target_sites = kinase_sites = []
    site_item = extract_first_record(site_result) if not _is_error_payload(site_result) else None
    if isinstance(site_item, dict):
        target_sites = site_item.get("TargetPhosphosites", [])
        kinase_sites = site_item.get("PhosphositesOnKinase", [])

    if not match:
        return {
            "reply": (
                f"Could not find classification info for {protein} ({protein_id}) in KINEPIK's kinase list.\n"
                f"Target phosphosites listed: {len(target_sites) if isinstance(target_sites, list) else 'Unknown'}\n"
                f"Autophosphorylation sites on this kinase: {len(kinase_sites) if isinstance(kinase_sites, list) else 'Unknown'}"
            ),
            "image": None,
        }

    kinase_info = match.get("KinaseInfo", {})
    name = match.get("GeneInfo", {}).get("MappedGene", protein)
    description = match.get("Description", "Unknown")
    family = kinase_info.get("KinaseFamily", "Unknown")
    group = kinase_info.get("KinaseGroup", "Unknown")
    subfamily = kinase_info.get("KinaseSubfamily")

    subfamily_line = f"\n- Subfamily: {subfamily}" if subfamily else ""
    return {
        "reply": _with_source(
            f"Kinase lookup for {name} ({protein_id})\n"
            f"- Description: {description}\n"
            f"- Family: {family}\n"
            f"- Group: {group}"
            f"{subfamily_line}\n"
            f"- Target phosphosites listed: {len(target_sites) if isinstance(target_sites, list) else 'Unknown'}\n"
            f"- Autophosphorylation sites on this kinase: {len(kinase_sites) if isinstance(kinase_sites, list) else 'Unknown'}",
            "kinases/all, kinases/specific",
        ),
        "image": None,
    }


def run_list_all_kinases():
    """List the kinases available in KINEPIK (preview of the first 10 of ~504)."""
    result = get_all_kinases()
    return {"reply": _with_source(format_all_kinases(result), "kinases/all"), "image": None}


def run_network_attributes_lookup(proteins):
    """Look up network node attributes (metadata such as IDs/names) for one or more kinases."""
    if isinstance(proteins, str):
        proteins = [proteins]
    kinase_ids = []
    unresolved = []
    for name in proteins:
        protein_id = normalise_to_uniprot(name)
        if protein_id:
            kinase_ids.append(protein_id)
        else:
            unresolved.append(name)
    if not kinase_ids:
        return {"reply": f"I could not identify: {', '.join(unresolved)}.", "image": None}
    result = get_network_attributes(kinase_ids)
    reply = format_network_attributes(kinase_ids, result)
    if unresolved:
        reply += f"\n\nCould not identify: {', '.join(unresolved)}."
    return {"reply": _with_source(reply, "sif/attributes"), "image": None}


def run_top_connected_kinases(top_n=10):
    """Fetch the full KINEPIK signalling network and rank kinases by number of connections.
    Returns the top N most connected kinases with their connection counts."""
    result = get_network()
    if _is_error_payload(result):
        return {"reply": f"KINEPIK error: {result['error']}", "image": None}

    # Count connections per kinase from the SIF network
    connection_counts = {}
    records = result if isinstance(result, list) else _normalise_records(result)

    for item in records:
        if isinstance(item, str):
            parts = item.strip().split()
            if len(parts) >= 3:
                source, target = parts[0], parts[2]
            elif len(parts) == 2:
                source, target = parts[0], parts[1]
            else:
                continue
            connection_counts[source] = connection_counts.get(source, 0) + 1
            connection_counts[target] = connection_counts.get(target, 0) + 1
        elif isinstance(item, dict):
            source = item.get("source") or item.get("Source")
            target = item.get("target") or item.get("Target")
            if source:
                connection_counts[source] = connection_counts.get(source, 0) + 1
            if target:
                connection_counts[target] = connection_counts.get(target, 0) + 1

    if not connection_counts:
        return {"reply": "Could not extract network data from KINEPIK.", "image": None}

    # Sort by connection count and take top N
    top = sorted(connection_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

    # Resolve UniProt IDs to gene names
    from utils import batch_uniprot_to_gene, looks_like_uniprot_id
    ids = [k for k, _ in top if looks_like_uniprot_id(k)]
    label_map = batch_uniprot_to_gene(ids) if ids else {}

    names = [label_map.get(kinase_id, kinase_id) for kinase_id, _ in top]
    counts = [count for _, count in top]

    lines = [f"{i}. {name} — {count} connections" for i, (name, count) in enumerate(zip(names, counts), 1)]

    image_path = plot_top_connected_kinases(names, counts)

    return {
        "reply": _with_source(
            f"Top {top_n} most connected kinases in the KINEPIK signalling network:\n\n"
            + "\n".join(lines),
            "sif/all",
        ),
        "image": image_path,
    }


def run_kinase_targets(protein):
    """Get the list of phosphosite targets that a kinase phosphorylates."""
    protein_id, error = _normalise_or_error(protein)
    if error:
        return error
    result = get_kinase(protein_id, phosphosites="targets")
    return {"reply": _with_source(format_kinase_targets(protein, protein_id, result), "kinases/specific"), "image": None}


def run_phosphosite_viewer(protein, mode="targets"):
    """Show a paginated, searchable table of a kinase's phosphosites.

    mode="targets" (default) shows the phosphosites this kinase acts on, on
    other proteins. mode="own" shows the kinase's own autophosphorylation
    sites instead — genuinely different KINEPIK data, not a filtered view
    of the same list, since a kinase's own sites and the sites it targets
    on other proteins are two separate things."""
    protein_id, error = _normalise_or_error(protein)
    if error:
        return error

    api_mode = "sites" if mode == "own" else "targets"
    all_result = get_kinase(protein_id, phosphosites=api_mode)

    table_path = build_phosphosite_table(protein, protein_id, all_result, mode=mode)
    if not table_path:
        return {
            "reply": _with_source(
                format_phosphosite_viewer(protein, protein_id, all_result, mode=mode), "kinases/specific"
            ),
            "image": None,
        }

    scope_text = f"on {protein}" if mode == "own" else f"targeted by {protein}"
    return {
        "reply": _with_source(
            f"Phosphosites {scope_text} ({protein_id}) — browse the table below.",
            "kinases/specific",
        ),
        "image": None,
        "table": table_path,
    }


def run_kinase_substrate_check(kinase, substrate):
    """Answer a direct yes/no question: does this kinase target this specific
    substrate? Searches the kinase's full phosphosite target list for a
    case-insensitive match on the substrate gene name."""
    kinase_id, error = _normalise_or_error(kinase)
    if error:
        return error

    result = get_kinase(kinase_id, phosphosites="targets")
    if _is_error_payload(result):
        return {"reply": f"KINEPIK error: {result['error']}", "image": None}

    all_sites = []
    for item in result or []:
        if not isinstance(item, dict):
            continue
        sites = item.get("TargetPhosphosites", item.get("target_phosphosites", []))
        if isinstance(sites, list):
            all_sites.extend(str(s) for s in sites if s)

    if not all_sites:
        return {"reply": f"No phosphosite target data found for {kinase}.", "image": None}

    substrate_upper = substrate.strip().upper()
    matches = [
        site for site in all_sites
        if site.split("(")[0].strip().upper() == substrate_upper
    ]

    if not matches:
        return {
            "reply": _with_source(
                f"No, {kinase} does not appear to target {substrate} in KINEPIK's data "
                f"({len(all_sites)} total targets checked).",
                "kinases/specific",
            ),
            "image": None,
        }

    site_list = ", ".join(sorted(dict.fromkeys(matches)))
    return {
        "reply": _with_source(f"Yes — {kinase} targets {substrate} at: {site_list}", "kinases/specific"),
        "image": None,
    }


def _collect_fold_change(full_sites, perturbation, cell_line):
    """Query fold-change for each phosphosite string and filter to one perturbation.
    Shared by run_fold_change_visualisation for both the kinase's own sites
    (type=target_kinase) and a named substrate's sites (type=target_phosphosite)."""
    perturbation_lower = perturbation.strip().lower()
    data = []
    for full_site in full_sites:
        fc_result = get_fold_change(full_site, "target_phosphosite", cell_line=cell_line)
        if _is_error_payload(fc_result) or not fc_result:
            continue
        for entry in fc_result:
            if not isinstance(entry, dict):
                continue
            record = entry.get(full_site)
            if not isinstance(record, dict):
                continue
            if str(record.get("Perturbation", "")).strip().lower() != perturbation_lower:
                continue
            try:
                fc_value = float(record.get("FC"))
            except (TypeError, ValueError):
                continue
            residue = full_site.split("(", 1)[1].rstrip(")") if "(" in full_site else full_site
            data.append({"site": residue, "fc": fc_value})
    return data


def run_fold_change_visualisation(kinase, substrate=None, perturbation=None, cell_line="MCF7"):
    """Show raw fold-change values (the experimental evidence behind a KSEA z-score)
    under one drug — either for a kinase's effect on a specific substrate, or,
    if no substrate is given, for the kinase's own autophosphorylation sites.

    Scoped to a single drug since the fold-change endpoint only accepts one
    phosphosite per call — querying every site across every drug would mean
    hundreds or thousands of API calls."""
    kinase_id, error = _normalise_or_error(kinase)
    if error:
        return error

    if not perturbation:
        return {"reply": "Please specify a drug/perturbation to show fold-change for.", "image": None}

    if not substrate:
        # No substrate named — show the kinase's own autophosphorylation sites
        own_result = get_kinase(kinase_id, phosphosites="sites")
        if _is_error_payload(own_result):
            return {"reply": f"KINEPIK error: {own_result['error']}", "image": None}

        own_sites = []
        for item in own_result or []:
            if not isinstance(item, dict):
                continue
            sites = item.get("PhosphositesOnKinase", item.get("phosphosites_on_kinase", []))
            if isinstance(sites, list):
                own_sites.extend(str(s) for s in sites if s)
        own_sites = sorted(dict.fromkeys(own_sites))

        if not own_sites:
            return {"reply": f"No autophosphorylation site data found for {kinase}.", "image": None}

        data = _collect_fold_change(own_sites, perturbation, cell_line)
        label = kinase
    else:
        target_result = get_kinase(kinase_id, phosphosites="targets")
        if _is_error_payload(target_result):
            return {"reply": f"KINEPIK error: {target_result['error']}", "image": None}

        all_sites = []
        for item in target_result or []:
            if not isinstance(item, dict):
                continue
            sites = item.get("TargetPhosphosites", item.get("target_phosphosites", []))
            if isinstance(sites, list):
                all_sites.extend(str(s) for s in sites if s)

        substrate_upper = substrate.strip().upper()
        matching_sites = sorted(dict.fromkeys(
            site for site in all_sites
            if site.split("(")[0].strip().upper() == substrate_upper
        ))

        if not matching_sites:
            return {
                "reply": f"{kinase} does not appear to target {substrate} in KINEPIK's data, so no fold-change evidence is available.",
                "image": None,
            }

        data = _collect_fold_change(matching_sites, perturbation, cell_line)
        label = substrate

    if not data:
        subject = f"{kinase}'s effect on {substrate}" if substrate else f"{kinase}'s own autophosphorylation"
        return {
            "reply": f"No fold-change data found for {subject} under {perturbation} in {cell_line}.",
            "image": None,
        }

    image_path = plot_fold_change(data, kinase, label, perturbation, cell_line)
    subject = f"{kinase}'s effect on {substrate}" if substrate else f"{kinase}'s own autophosphorylation"
    return {
        "reply": _with_source(
            f"Raw fold-change for {subject} under {perturbation} in {cell_line}, "
            f"across {len(data)} phosphosite(s).\n"
            "This is the underlying experimental measurement behind the KSEA z-score — "
            "positive values suggest increased phosphorylation, negative suggest decreased.",
            "perturbation/fc",
        ),
        "image": image_path,
    }


def run_network_visualisation(protein, kinase_only=True):
    """Fetch the signalling network for a kinase from KINEPIK and draw a relationship
    diagram showing connected proteins, sized by interaction strength.
    kinase_only restricts the network to kinase-to-kinase edges only, excluding
    non-kinase substrates such as transcription factors or structural proteins."""
    protein_id, error = _normalise_or_error(protein)
    if error:
        return error

    result = get_network_specific([protein_id])
    if _is_error_payload(result):
        return {"reply": f"KINEPIK error: {result['error']}", "image": None}

    records = _normalise_records(result)

    # Extract neighbour IDs from first call and fetch their inter-connections
    SOURCE_KEYS = ["source", "Source", "from", "From", "Kinase", "kinase"]
    TARGET_KEYS = ["target", "Target", "to", "To", "Substrate", "substrate"]
    neighbour_ids = set()
    for item in records:
        if isinstance(item, str):
            parts = item.strip().split()
            if len(parts) >= 3:
                neighbour_ids.add(parts[2])
            elif len(parts) == 2:
                neighbour_ids.add(parts[1])
        elif isinstance(item, dict):
            t = next((item[k] for k in TARGET_KEYS if item.get(k)), None)
            if t:
                neighbour_ids.add(t)
    neighbour_ids.discard(protein_id)
    if neighbour_ids:
        neighbour_result = get_network_specific(list(neighbour_ids)[:20])
        if not _is_error_payload(neighbour_result):
            records = records + _normalise_records(neighbour_result)

    image_path = plot_network(records, protein, kinase_id=protein_id, kinase_only=kinase_only)

    if not image_path:
        return {
            "reply": f"No network connections found for {protein} in KINEPIK.",
            "image": None,
        }

    scope_text = (
        "showing kinase-to-kinase connections only"
        if kinase_only
        else "showing all interactions, including non-kinase substrates"
    )
    return {
        "reply": _with_source(
            f"Network diagram for {protein}, {scope_text}.\n"
            "Node size reflects connectivity in the full network. "
            "Arrows show the direction of signalling. "
            "Thicker edges indicate bidirectional relationships.",
            "sif/specific",
        ),
        "image": image_path,
    }


def run_explain_chart(image_path):
    """Send the last generated chart to GPT with vision and return a plain English interpretation."""
    import base64
    import os
    from openai import OpenAI

    if not image_path:
        return {"reply": "No chart has been generated yet. Ask for a visualisation first.", "image": None}

    # Build absolute path from the static relative path
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    filename = os.path.basename(image_path)
    abs_path = os.path.join(static_dir, filename)

    if not os.path.exists(abs_path):
        return {"reply": "The chart image could not be found. Try generating it again.", "image": None}

    with open(abs_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-5.1")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "This is a kinase signalling visualisation from the KINEPIK database. "
                                "Please explain in plain English what this chart shows, what the key patterns are, "
                                "and what a researcher should take away from it. "
                                "Keep it concise and avoid jargon where possible."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        )
        explanation = response.choices[0].message.content
    except Exception as exc:
        return {"reply": f"Could not interpret the chart: {exc}", "image": None}

    return {"reply": explanation, "image": None}


def run_drug_targets(drug):
    """Look up the known kinase targets of a drug using the KINEPIK perturbation/available endpoint.
    Passes confidence=1.0 to retrieve TargetKinases (known binding targets) from the
    Known_perturbations table rather than AvailableTargetKinases (experimental data only)."""
    result = get_available_perturbations(drug, confidence=1.0)
    if _is_error_payload(result):
        return {"reply": f"KINEPIK error: {result['error']}", "image": None}

    records = _normalise_records(result)
    target_kinases = []
    for item in records:
        if isinstance(item, dict):
            targets = item.get("TargetKinases") or item.get("AvailableTargetKinases") or []
            if isinstance(targets, list):
                target_kinases.extend(targets)

    target_kinases = list(dict.fromkeys(target_kinases))

    if not target_kinases:
        return {
            "reply": (
                f"No known kinase targets found for {drug} in KINEPIK. "
                "The drug may not be in the database or may not have known binding targets listed."
            ),
            "image": None,
        }

    # Resolve UniProt IDs to gene names using the shared cached lookup
    from utils import batch_uniprot_to_gene
    id_map = batch_uniprot_to_gene(target_kinases)
    gene_names = [id_map.get(uid, uid) for uid in target_kinases]

    return {
        "reply": _with_source(
            f"Known kinase targets of {drug} ({len(gene_names)} found):\n"
            + ", ".join(gene_names),
            "perturbation/available",
        ),
        "image": None,
    }


def run_perturbation_list():
    """Fetch and return a list of all perturbations available in KINEPIK."""
    result = get_all_perturbations()
    return {"reply": _with_source(format_perturbation_result(result), "perturbation/all"), "image": None}


def run_cell_line_list():
    """Discover which cell lines KINEPIK has experimental data for by probing the KSEA
    endpoint with an invalid cell line name, which causes the API to list the valid ones."""
    result = get_available_cell_lines()
    return {"reply": _with_source(format_available_cell_lines(result), "perturbation/KSEA"), "image": None}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def chatbot_reply(user_message, session_id=None):
    """Main entry point for all chat messages — always routed through the AI.

    The import of ai_chatbot_reply is deferred to inside this function to avoid
    a circular import — ai_router imports the run_* handlers from this module.

    session_id is used by the AI router to remember conversation history.
    """
    if not user_message or not user_message.strip():
        return "Please enter a message."

    from ai_router import ai_chatbot_reply  # deferred to avoid circular import
    return ai_chatbot_reply(user_message, session_id=session_id)
