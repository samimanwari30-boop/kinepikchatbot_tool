import ast
import re

import requests


UNIPROT_TIMEOUT = 10  # seconds before a UniProt API request gives up

# Common aliases that users might type which differ from the official gene name
ALIAS_TO_GENE = {
    "ERK": "MAPK1",
    "ERK1": "MAPK3",
    "ERK2": "MAPK1",
    "AKT": "AKT1",
    "PI3K": "PIK3CA",
    "MTORC1": "MTOR",
}

# Cache for gene name → UniProt ID lookups so we don't repeat API calls
_uniprot_cache = {}


def looks_like_uniprot_id(text):
    """Return True if the text looks like a UniProt accession number (e.g. P42345, Q9H4A3).
    Used to decide whether to look up a name or treat it as an ID directly."""
    text = text.strip().upper()
    patterns = [
        r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$",
        r"^[A-NR-Z][0-9][A-Z][A-Z0-9]{2}[0-9]$",
    ]
    return any(re.match(pattern, text) for pattern in patterns)


def gene_to_uniprot_live(gene_name):
    """Convert a gene name (e.g. 'EGFR') to its UniProt ID (e.g. 'P00533').
    Checks the local cache first, then queries the UniProt REST API.
    Returns None if the gene cannot be found."""
    key = gene_name.strip().upper()

    # Return cached result if we've looked this up before
    if key in _uniprot_cache:
        return _uniprot_cache[key]

    # Handle common aliases (e.g. ERK → MAPK1)
    resolved = ALIAS_TO_GENE.get(key, key)

    url = "https://rest.uniprot.org/uniprotkb/search"
    params = {
        "query": f"gene_exact:{resolved} AND organism_id:9606 AND reviewed:true",
        "fields": "accession",
        "format": "json",
        "size": 1,
    }
    try:
        response = requests.get(url, params=params, timeout=UNIPROT_TIMEOUT)
        response.raise_for_status()
        results = response.json().get("results", [])
        if results:
            accession = results[0].get("primaryAccession")
            _uniprot_cache[key] = accession
            return accession
    except Exception:
        pass

    # Cache the miss so we don't retry it
    _uniprot_cache[key] = None
    return None


def normalise_to_uniprot(user_input):
    """Take whatever the user typed (gene name or UniProt ID) and return a UniProt ID.
    If it already looks like a UniProt ID it is returned as-is.
    Otherwise it is looked up via the UniProt API."""
    cleaned = user_input.strip().upper()
    if looks_like_uniprot_id(cleaned):
        return cleaned
    return gene_to_uniprot_live(cleaned)


# Cache for UniProt ID → gene name lookups
_gene_name_cache = {}


def batch_uniprot_to_gene(uniprot_ids):
    """Convert a list of UniProt IDs to gene names using KINEPIK's lookup_protein endpoint.
    Sends all IDs in a single API call and caches the results.
    Returns a dict {uniprot_id: gene_name} — IDs that can't be resolved are left out."""
    if not uniprot_ids:
        return {}

    # Only fetch IDs we haven't seen before
    to_fetch = [uid for uid in uniprot_ids if uid not in _gene_name_cache]

    if to_fetch:
        try:
            from kinepik_api import lookup_protein
            # KINEPIK accepts a comma-separated list of IDs in one request
            result = lookup_protein(",".join(to_fetch))
            if isinstance(result, list):
                for item in result:
                    # KINEPIK returns nested lists — unwrap until we reach a dict
                    while isinstance(item, list):
                        item = item[0] if item else None
                    if not isinstance(item, dict):
                        continue
                    uid = item.get("UniprotID") or item.get("UniProtID")
                    gene_info = item.get("GeneInfo", {})
                    gene_name = gene_info.get("MappedGene") if isinstance(gene_info, dict) else None
                    if uid and gene_name:
                        _gene_name_cache[uid] = gene_name
        except Exception:
            pass

        # Mark any IDs that couldn't be resolved so we don't retry them
        for uid in to_fetch:
            if uid not in _gene_name_cache:
                _gene_name_cache[uid] = None

    return {uid: _gene_name_cache[uid] for uid in uniprot_ids if _gene_name_cache.get(uid)}


def extract_first_record(data):
    """Dig into a nested list structure and return the first dict found.
    KINEPIK sometimes returns data as [[{...}]] so this unwraps it."""
    current = data
    while isinstance(current, list):
        if not current:
            return None
        current = current[0]
    return current if isinstance(current, dict) else None


def parse_synonyms(raw_value):
    """Parse a list of gene synonyms that may come back as a list, a string,
    or a string representation of a list (e.g. \"['KIAA0731', 'LARP']\")."""
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value if item is not None]

    if isinstance(raw_value, str):
        value = raw_value.strip()
        if not value:
            return []
        # Try to parse it as a Python list literal
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item is not None]
        except Exception:
            pass
        # Fall back to comma-separated splitting
        if "," in value:
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value]

    return []


def _is_error_payload(payload):
    """Return True if the payload is an error response from KINEPIK (i.e. has an 'error' key)."""
    return isinstance(payload, dict) and "error" in payload


def _normalise_records(result):
    """Flatten a raw KINEPIK API response into a plain list of records.
    Handles three shapes the API can return:
    - plain text  → split by newline (e.g. SIF network format)
    - dict        → unwrap a known list key (results, data, items, nodes, edges)
    - list        → returned as-is
    Used wherever raw API output needs to be iterated over uniformly."""
    if isinstance(result, str):
        return [line.strip() for line in result.splitlines() if line.strip()]

    if isinstance(result, dict):
        for key in ("results", "data", "items", "nodes", "edges"):
            value = result.get(key)
            if isinstance(value, list):
                return value
        return [result]

    if isinstance(result, list):
        return result

    return []


def estimate_tokens(text, model=None):
    """Estimate how many tokens a piece of text will use when sent to OpenAI.
    Uses tiktoken if installed, otherwise falls back to a rough character-based estimate
    (1 token ≈ 4 characters).  Accepts an optional model name to pick the right encoding."""
    import os
    try:
        import tiktoken
    except ImportError:
        return max(1, len(text) // 4)

    model = model or os.getenv("OPENAI_MODEL", "gpt-4.1")
    try:
        encoding = tiktoken.encoding_for_model(model)
    except Exception:
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return max(1, len(text) // 4)
    try:
        return len(encoding.encode(text))
    except Exception:
        return max(1, len(text) // 4)
