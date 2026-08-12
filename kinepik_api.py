import requests


BASE_URL = "http://127.0.0.1:5002/api/0"

# Cosmetic only — shown in the "Data source" citation instead of the real
# internal address, since pikapi isn't reachable from outside this server.
# Requests still always go through BASE_URL above.
DISPLAY_BASE_URL = "https://kinepik.org/api/0"

TIMEOUT = 20        # seconds before a normal request gives up
KSEA_TIMEOUT = 120  # KSEA requests are slower so get a longer timeout


def call_kinepik(endpoint, params=None, timeout=TIMEOUT):
    """Send a GET request to the KINEPIK API and return the response.
    If anything goes wrong (network error, bad status, timeout) it returns
    a dict with an 'error' key instead of crashing."""
    url = f"{BASE_URL}/{endpoint}"
    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            # Some endpoints return plain text (e.g. SIF network format)
            return response.text
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return {"error": f"HTTP {status}: {exc}"}
    except requests.exceptions.ConnectionError:
        return {"error": "Could not connect to KINEPIK API"}
    except requests.exceptions.Timeout:
        return {"error": "KINEPIK API timed out"}
    except Exception as exc:
        return {"error": str(exc)}


def lookup_protein(protein_id, fields=None):
    """Look up general information about a protein (or comma-separated list of proteins).
    Returns description, gene name, UniProt name, and kinase family info."""
    params = {"protein_ids": protein_id}
    if fields:
        params["fields"] = fields
    return call_kinepik("proteins/results", params)


def get_all_kinases(phosphosites=False):
    """Get a list of all kinases in the KINEPIK database.
    Set phosphosites=True to also include phosphosite data for each kinase."""
    params = {}
    if phosphosites:
        params["phosphosites"] = 1
    return call_kinepik("kinases/all", params)


def get_kinase(uniprot_id, phosphosites=None, confidence=False):
    """Get detailed information about a specific kinase by its UniProt ID.
    Optionally include phosphosite targets and confidence scores."""
    params = {"kinase_ids": uniprot_id}
    if phosphosites:
        params["phosphosites"] = phosphosites
    if confidence:
        params["confidence"] = 1
    return call_kinepik("kinases/specific", params)


def get_all_perturbations(type_filter=None):
    """Get a list of all perturbations (drugs/treatments) in KINEPIK.
    Optionally filter by type (e.g. inhibitor, activator)."""
    params = {}
    if type_filter:
        params["type"] = type_filter
    return call_kinepik("perturbation/all", params)


def get_available_perturbations(name, confidence=None):
    """Check which perturbations are available for a given drug or treatment name."""
    params = {"name": name}
    if confidence is not None:
        params["confidence"] = confidence
    return call_kinepik("perturbation/available", params)


def get_fold_change(entity_id, relation_type, cell_line=None, confidence=False):
    """Get fold change data for a protein under different perturbations.
    relation_type is either 'kinase' or 'substrate'.
    Optionally filter by cell line (e.g. MCF7)."""
    params = {"id": entity_id, "type": relation_type}
    if cell_line:
        params["cell_line"] = cell_line
    if confidence:
        params["confidence"] = 1
    return call_kinepik("perturbation/fc", params)


def get_ksea(
    uniprot_id,
    perturbation,
    cell_line="MCF7",
    weighted=False,
    autophosphorylation=None,
    phosphosite_confidence=False,
    sid=None,
):
    """Run Kinase Substrate Enrichment Analysis (KSEA) for one or more kinases.
    Returns a z-score showing whether the kinase is more activated or inhibited
    under the given perturbation in the given cell line.
    uniprot_id can be a single ID or a list of IDs."""
    if isinstance(uniprot_id, list):
        uniprot_id = ",".join(uniprot_id)

    params = {
        "kinase_ids": uniprot_id,
        "perturbations": perturbation,
        "cell_line": cell_line,
    }
    if weighted:
        params["weighted"] = "true"
    if autophosphorylation:
        params["autophosphorylation"] = autophosphorylation
    if phosphosite_confidence:
        params["phosphosite_confidence"] = 1
    if sid is not None:
        params["sid"] = sid
    return call_kinepik("perturbation/KSEA", params, timeout=KSEA_TIMEOUT)


def get_available_cell_lines():
    """Discover which cell lines KINEPIK has experimental data for.
    Works by sending an intentionally invalid cell line name to the KSEA endpoint —
    the API responds with a plain-text error that lists the valid options, e.g.:
    'Cell line not available in experimental data. Available cell lines: HL60, MCF7 and NTERA2'
    Returns that plain-text string for the caller to parse."""
    return call_kinepik(
        "perturbation/KSEA",
        {
            "kinase_ids": "P31749",
            "perturbations": "AZD3759",
            "cell_line": "__probe__",
        },
        timeout=KSEA_TIMEOUT,
    )


def get_network(resolution="kinases"):
    """Get the full kinase signalling network from KINEPIK.
    Returns edges in SIF format (source interaction target)."""
    return call_kinepik("sif/all", {"resolution": resolution})


def get_network_specific(uniprot_ids, resolution="kinases"):
    """Get the signalling network for a specific list of kinases.
    Returns edges showing which proteins they phosphorylate."""
    return call_kinepik(
        "sif/specific",
        {
            "kinase_ids": ",".join(uniprot_ids),
            "resolution": resolution,
        },
    )


def get_network_attributes(kinase_ids, resolution="kinases", attr_type="IDs"):
    """Get node attributes (e.g. IDs, names) for kinases in the network."""
    if isinstance(kinase_ids, list):
        kinase_ids = ",".join(kinase_ids)

    params = {
        "kinases": kinase_ids,
        "resolution": resolution,
        "type": attr_type,
    }
    return call_kinepik("sif/attributes", params)
