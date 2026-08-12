from utils import _is_error_payload, extract_first_record, parse_synonyms, _normalise_records


def _first_present(item, keys, default="Unknown"):
    """Return the first non-empty value found in a dict for the given list of keys.
    Used to handle API responses where the same field may have different names."""
    for key in keys:
        value = item.get(key)
        if value not in (None, "", []):
            return value
    return default



def _format_record_summary(item, preferred_keys=None, max_fields=6):
    """Convert a dict record into a readable pipe-separated string.
    preferred_keys are shown first; remaining fields fill up to max_fields total."""
    if not isinstance(item, dict):
        return str(item)

    preferred_keys = preferred_keys or []
    parts = []
    used = set()

    for key in preferred_keys:
        if key in item and item[key] not in (None, "", []):
            parts.append(f"{key}: {item[key]}")
            used.add(key)

    for key, value in item.items():
        if key in used or value in (None, "", []):
            continue
        if isinstance(value, (list, dict)):
            value = f"{len(value)} entries"
        parts.append(f"{key}: {value}")
        if len(parts) >= max_fields:
            break

    return " | ".join(parts) if parts else "No readable fields"


def _nested_value(item, container_key, value_key, default=None):
    """Safely read a value from a nested dict, e.g. item['GeneInfo']['MappedGene'].
    Returns default if either key is missing or the container isn't a dict."""
    container = item.get(container_key)
    if isinstance(container, dict):
        return container.get(value_key, default)
    return default


def _is_plain_message(result):
    """Return True if the result is a short plain-text string (not a multi-line or SIF response).
    Used to pass simple API messages straight through without further parsing."""
    return isinstance(result, str) and "\n" not in result and " phosphorylates " not in result


def format_protein_result(protein_id, result):
    """Format a protein lookup response into a readable text summary.
    Shows description, gene name, UniProt name, synonyms, and kinase classification."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"

    item = extract_first_record(result)
    if not isinstance(item, dict):
        return f"No protein information found for {protein_id}."

    gene_info = item.get("GeneInfo", item.get("mappedgene", {}))
    kinase_info = item.get("KinaseInfo", item.get("kinase", {}))

    gene_name = "Unknown"
    synonyms = []
    if isinstance(gene_info, dict):
        gene_name = gene_info.get("MappedGene", gene_info.get("name", "Unknown"))
        synonyms = parse_synonyms(gene_info.get("GeneSynonyms", gene_info.get("synonyms", [])))

    description = item.get("Description", item.get("description", "No description available."))
    uniprot_name = item.get("UniprotName", item.get("uniprot_name", item.get("name", "Unknown")))

    is_kinase = "Unknown"
    kinase_family = "Unknown"
    kinase_group = "Unknown"
    if isinstance(kinase_info, dict):
        is_kinase = kinase_info.get("IsKinase", kinase_info.get("is_kinase", "Unknown"))
        kinase_family = kinase_info.get("KinaseFamily", kinase_info.get("family", "Unknown"))
        kinase_group = kinase_info.get("KinaseGroup", kinase_info.get("group", "Unknown"))

    is_kinase_text = "Yes" if is_kinase == 1 else "No" if is_kinase == 0 else str(is_kinase)
    synonym_text = ", ".join(synonyms[:5]) if synonyms else "None listed"

    return (
        f"Protein lookup for {protein_id}\n"
        f"- Description: {description}\n"
        f"- Gene name: {gene_name}\n"
        f"- UniProt entry name: {uniprot_name}\n"
        f"- Synonyms: {synonym_text}\n"
        f"- Is kinase: {is_kinase_text}\n"
        f"- Kinase family: {kinase_family}\n"
        f"- Kinase group: {kinase_group}"
    )


def format_all_kinases(result):
    """Format the full kinase list into a readable summary showing up to 10 kinases
    with their UniProt ID, gene name, family, and group."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"

    kinases = _normalise_records(result)
    if not kinases:
        return "No kinases found."

    lines = []
    for item in kinases[:10]:
        if not isinstance(item, dict):
            lines.append(f"- {item}")
            continue

        kinase_id = _first_present(item, ["UniProtID", "UniprotID", "uniprot_id", "id", "KinaseID"])
        name = (
            _nested_value(item, "GeneInfo", "MappedGene")
            or _first_present(item, ["GeneName", "MappedGene", "gene", "name", "Kinase"])
        )
        family = _nested_value(item, "KinaseInfo", "KinaseFamily") or _first_present(
            item, ["KinaseFamily", "family"], default=None
        )
        group = _nested_value(item, "KinaseInfo", "KinaseGroup") or _first_present(
            item, ["KinaseGroup", "group"], default=None
        )

        details = []
        if family:
            details.append(f"family: {family}")
        if group:
            details.append(f"group: {group}")

        suffix = f" | {' | '.join(details)}" if details else ""
        lines.append(f"- {name} ({kinase_id}){suffix}")

    total = len(kinases)
    shown = min(total, 10)
    return f"Kinases found: {total}. Showing {shown}:\n" + "\n".join(lines)


def format_kinase_result(protein_name, protein_id, result):
    """Format a single kinase lookup into a readable summary. KINEPIK's
    kinases/specific endpoint does not return a family/group classification —
    only phosphosite counts and the UniProt display name — so only those
    are shown here rather than displaying a placeholder."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"

    item = extract_first_record(result)
    if not isinstance(item, dict):
        return f"No kinase data found for {protein_name} ({protein_id})."

    name = _first_present(item, ["UniprotName", "GeneName", "gene", "name", "Kinase"], protein_name)
    target_sites = item.get("TargetPhosphosites") or item.get("target_phosphosites") or []
    kinase_sites = item.get("PhosphositesOnKinase") or item.get("phosphosites_on_kinase") or []

    return (
        f"Kinase lookup for {name} ({protein_id})\n"
        f"- Target phosphosites listed: {len(target_sites) if isinstance(target_sites, list) else 'Unknown'}\n"
        f"- Autophosphorylation sites on this kinase: {len(kinase_sites) if isinstance(kinase_sites, list) else 'Unknown'}"
    )


def format_phosphosite_viewer(protein_name, protein_id, all_result, limit=50):
    """Format a plain-text phosphosite list for a kinase: substrate and site.
    Fallback used only if the HTML table (table_viewer.py) can't be built."""
    if _is_error_payload(all_result):
        return f"KINEPIK error: {all_result['error']}"

    if not all_result:
        return f"No phosphosite data found for {protein_name}."

    try:
        all_sites = []
        for item in all_result:
            if not isinstance(item, dict):
                continue
            sites = item.get("TargetPhosphosites", item.get("target_phosphosites", []))
            if isinstance(sites, list):
                all_sites.extend(str(s) for s in sites if s)
        all_sites = list(dict.fromkeys(all_sites))

        if not all_sites:
            return f"No phosphosite targets found for {protein_name}."

        all_sites.sort()

        total = len(all_sites)
        shown = all_sites[:limit]

        # Use " | " as a column separator rather than space-padding — the chat
        # UI collapses repeated whitespace, which would otherwise destroy alignment
        rows = []
        for site in shown:
            if "(" in site and site.endswith(")"):
                substrate, residue = site.rsplit("(", 1)
                residue = residue.rstrip(")")
            else:
                substrate, residue = site, ""
            rows.append(f"{substrate} | {residue}")

        header = "Substrate | Site"
        table = "\n".join([header, "-" * len(header)] + rows)

        footnote = ""
        if total > limit:
            footnote = f"\n\n...and {total - limit} more (showing top {limit} of {total})."

        return (
            f"Phosphosites targeted by {protein_name} ({protein_id}):\n\n"
            f"{table}"
            f"{footnote}"
        )
    except Exception:
        return f"Could not parse phosphosite data for {protein_name}."


def format_kinase_targets(protein_name, protein_id, result):
    """Format the list of phosphosite targets that a kinase phosphorylates.
    Returns up to 10 unique target sites as a readable list."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"

    if not result:
        return f"No kinase target data found for {protein_name}."

    try:
        targets = []
        for item in result:
            if not isinstance(item, dict):
                continue
            phosphosites = item.get("TargetPhosphosites", item.get("target_phosphosites", []))
            if isinstance(phosphosites, list):
                targets.extend(str(site) for site in phosphosites if site)

        if not targets:
            return f"No targets found for {protein_name}."

        targets = list(dict.fromkeys(targets))[:10]
        return (
            f"{protein_name} ({protein_id}) phosphorylates:\n"
            + "\n".join(f"- {target}" for target in targets)
        )

    except Exception:
        return f"Could not parse kinase target data for {protein_name}."


def format_ksea_result(protein_name, result):
    """Format a KSEA result for a single kinase into readable text.
    Shows z-score, p-value, and n for up to 5 records."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"

    if isinstance(result, dict):
        data = result.get("results") or result.get("data") or []
    else:
        data = result

    if not isinstance(data, list) or not data:
        return f"No KSEA results found for {protein_name}."

    lines = []

    for item in data:
        if not isinstance(item, dict):
            continue

        # Handle nested format: {kinase_id: {perturbation: {z_score: ...}}}
        nested_rows = []
        for kinase_id, perturbations in item.items():
            if not isinstance(perturbations, dict):
                continue
            for perturbation, values in perturbations.items():
                if isinstance(values, dict):
                    nested_rows.append((kinase_id, perturbation, values))

        if nested_rows:
            for kinase_id, perturbation, values in nested_rows:
                zscore = values.get("z_score", values.get("ZScore"))
                pvalue = values.get("p_value", values.get("PValue"))
                n = values.get("n")
                details = f" | n: {n}" if n is not None else ""
                lines.append(
                    f"- {protein_name} ({kinase_id}) under {perturbation} | "
                    f"Z-score: {zscore} | p-value: {pvalue}{details}"
                )
            continue

        # Handle flat format: {kinase: ..., z_score: ..., p_value: ...}
        kinase = item.get("kinase") or item.get("Kinase") or item.get("name") or protein_name
        zscore = item.get("zscore") or item.get("z_score") or item.get("ZScore") or item.get("score")
        pvalue = item.get("pvalue") or item.get("p_value") or item.get("PValue")
        lines.append(f"- {kinase} | Z-score: {zscore} | p-value: {pvalue}")

        if len(lines) >= 5:
            break

    if not lines:
        return f"Could not extract KSEA values for {protein_name}."

    return f"KSEA results for {protein_name}:\n" + "\n".join(lines[:5])


def format_perturbation_result(result):
    """Format the full perturbation list into a readable summary showing
    up to 10 entries with name, gene target, action, and type."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"

    records = _normalise_records(result)
    if not records:
        return "No perturbation data found."

    try:
        lines = []
        for item in records:
            if not isinstance(item, dict):
                lines.append(f"- {item}")
                continue

            name = _first_present(item, ["PerturbationName", "perturbation_name", "name", "Name"])
            gene = _first_present(item, ["Gene", "gene"], default=None)
            action = _first_present(item, ["Action", "action"], default=None)
            perturbation_type = _first_present(item, ["Type", "type"], default=None)

            details = []
            if gene:
                details.append(f"gene: {gene}")
            if action:
                details.append(f"action: {action}")
            if perturbation_type:
                details.append(f"type: {perturbation_type}")

            suffix = f" | {' | '.join(details)}" if details else ""
            lines.append(f"- {name}{suffix}")

        return f"Perturbations found: {len(records)}. Showing all {len(records)}:\n" + "\n".join(lines)

    except Exception:
        return "Could not parse perturbation data."


def format_available_perturbations(name, result):
    """Format the available perturbations for a given drug or treatment name.
    Shows up to 10 entries with cell line and confidence where available."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"
    if _is_plain_message(result):
        return result

    records = _normalise_records(result)
    if not records:
        return f"No available perturbation data found for {name}."

    lines = []
    for item in records[:10]:
        if isinstance(item, dict):
            perturbation = _first_present(
                item,
                ["PerturbationName", "perturbation_name", "Perturbation", "perturbation", "Name", "name"],
                name,
            )
            cell_line = _first_present(item, ["CellLine", "cell_line", "cellline"], default=None)
            confidence = _first_present(item, ["Confidence", "confidence"], default=None)
            details = []
            if cell_line:
                details.append(f"cell line: {cell_line}")
            if confidence:
                details.append(f"confidence: {confidence}")
            suffix = f" | {' | '.join(details)}" if details else ""
            lines.append(f"- {perturbation}{suffix}")
        else:
            lines.append(f"- {item}")

    return f"Available perturbation entries for {name}: {len(records)}. Showing {min(len(records), 10)}:\n" + "\n".join(lines)


def format_available_cell_lines(result):
    """Parse the KINEPIK response and return the list of supported cell lines.

    KINEPIK surfaces valid cell line names inside a plain-text error message when
    an invalid cell line is sent to the KSEA endpoint, e.g.:
        'Cell line not available in experimental data.
         Available cell lines: HL60, MCF7 and NTERA2'
    This function extracts those names and presents them clearly.

    Falls back to a known-good list (HL60, MCF7, NTERA2) if parsing fails.
    """
    import re

    # The API returns a plain-text string, not JSON, when the cell line is invalid
    raw = result if isinstance(result, str) else (result.get("error", "") if isinstance(result, dict) else "")

    # Try to extract everything after "Available cell lines: "
    match = re.search(r"Available cell lines[:\s]+(.+)", raw, re.IGNORECASE)
    if match:
        # Split on commas and "and"
        names_raw = match.group(1).strip().rstrip(".")
        names = [n.strip() for n in re.split(r",\s*|\s+and\s+", names_raw) if n.strip()]
        if names:
            return (
                f"KINEPIK has experimental data for {len(names)} cell line(s):\n"
                + "\n".join(f"  • {n}" for n in sorted(names))
                + "\n\nUse one of these exact names when specifying a cell line, "
                "e.g. 'show KSEA for AKT under AZD3759 in HL60'."
            )

    # Fallback: known values as of the current KINEPIK dataset
    fallback = ["HL60", "MCF7", "NTERA2"]
    return (
        "KINEPIK has experimental data for 3 cell lines:\n"
        + "\n".join(f"  • {n}" for n in fallback)
        + "\n\nUse one of these exact names when specifying a cell line."
    )


def format_fold_change(entity_id, relation_type, result):
    """Format fold-change data for a protein under different perturbations.
    Shows up to 10 entries with fold change value, cell line, and p-value."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"
    if _is_plain_message(result):
        return result

    records = _normalise_records(result)
    if not records:
        return f"No fold-change data found for {entity_id} ({relation_type})."

    lines = []
    for item in records[:10]:
        if isinstance(item, dict):
            label = _first_present(item, ["Perturbation", "perturbation", "Name", "name", "id"], entity_id)
            fold_change = _first_present(item, ["FoldChange", "fold_change", "FC", "fc", "MeanFC", "mean_fc"], default=None)
            cell_line = _first_present(item, ["CellLine", "cell_line", "cellline"], default=None)
            pvalue = _first_present(item, ["p_value", "PValue", "pvalue"], default=None)

            details = []
            if fold_change is not None:
                details.append(f"fold change: {fold_change}")
            if cell_line:
                details.append(f"cell line: {cell_line}")
            if pvalue is not None:
                details.append(f"p-value: {pvalue}")

            if details:
                lines.append(f"- {label} | " + " | ".join(details))
            else:
                lines.append(f"- {_format_record_summary(item)}")
        else:
            lines.append(f"- {item}")

    return f"Fold-change results for {entity_id} ({relation_type}):\n" + "\n".join(lines)


def format_network(result):
    """Format the full kinase network into a readable list of edges.
    Each entry shows source → target with the interaction type where available."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"

    records = _normalise_records(result)
    if not records:
        return "No network data found."

    lines = []
    for item in records[:10]:
        if isinstance(item, dict):
            source = _first_present(item, ["source", "Source", "from", "Kinase", "kinase"], default=None)
            target = _first_present(item, ["target", "Target", "to", "Substrate", "substrate"], default=None)
            relation = _first_present(item, ["interaction", "Interaction", "relation", "type"], default=None)
            if source and target:
                suffix = f" | {relation}" if relation else ""
                lines.append(f"- {source} -> {target}{suffix}")
            else:
                lines.append(f"- {_format_record_summary(item)}")
        else:
            lines.append(f"- {item}")

    return f"Network entries: {len(records)}. Showing {min(len(records), 10)}:\n" + "\n".join(lines)


def format_network_specific(kinase_ids, result):
    """Format the network edges for a specific set of kinases into readable text.
    Shows source, target, and interaction type for up to 10 edges."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"

    ids_text = ", ".join(kinase_ids) if isinstance(kinase_ids, list) else str(kinase_ids)
    records = _normalise_records(result)
    if not records:
        return f"No network data found for {ids_text}."

    return f"Network for {ids_text}:\n" + "\n".join(
        f"- {_format_record_summary(item, ['source', 'target', 'interaction', 'Source', 'Target'])}"
        for item in records[:10]
    )


def format_network_attributes(kinase_ids, result):
    """Format node attribute data (e.g. IDs, names) for a set of kinases into readable text."""
    if _is_error_payload(result):
        return f"KINEPIK error: {result['error']}"
    if _is_plain_message(result):
        return result

    ids_text = ", ".join(kinase_ids) if isinstance(kinase_ids, list) else str(kinase_ids)
    records = _normalise_records(result)
    if not records:
        return f"No network attributes found for {ids_text}."

    return f"Network attributes for {ids_text}:\n" + "\n".join(
        f"- {_format_record_summary(item, ['id', 'ID', 'name', 'Name', 'attribute', 'Attribute'])}"
        for item in records[:10]
    )
