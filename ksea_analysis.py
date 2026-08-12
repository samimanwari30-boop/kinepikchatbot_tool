import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from kinepik_api import get_all_kinases, get_ksea, get_available_perturbations
from utils import _is_error_payload, _normalise_records


# Default settings — can be overridden with environment variables
DEFAULT_BATCH_SIZE = 20   # how many kinases to send per KSEA request
# Batches run in parallel, up to this many at once. pikapi's database is
# SQLite, which handles concurrent connections poorly (file-level locking) —
# keep this low against local pikapi to avoid intermittent batch failures.
# Keep it low (1-2) against the shared live kinepik.org API too, to avoid
# overloading it with concurrent requests.
DEFAULT_MAX_WORKERS = 2

# TEMPORARY: set to True to force every KSEA scan to skip the cache and hit
# the live API fresh, for manual testing. Set back to False when done.
DISABLE_CACHE = True

# File path for persistent cache — survives app restarts
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ksea_cache.json")

# In-memory cache for kinome KSEA results
# Key: "perturbation||cell_line" — Value: [results, meta]
_ksea_cache: dict = {}

# Cache for the full kinase ID list — never changes between queries
_kinase_ids_cache: list = []

# Live progress messages for in-flight KSEA scans
# Key: (perturbation, cell_line) — Value: progress string
_ksea_progress: dict = {}


def _load_cache():
    """Load persisted KSEA results from disk into memory on startup."""
    global _ksea_cache
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, "r") as f:
                _ksea_cache = json.load(f)
        except Exception:
            _ksea_cache = {}


def _save_cache():
    """Write the in-memory KSEA cache to disk so it survives restarts."""
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(_ksea_cache, f)
    except Exception:
        pass


# Load cache from disk when the module is first imported
_load_cache()


def extract_ksea_data(result, direction=None):
    """Extract KSEA rows from a raw API result and return the top 10 sorted by z-score.

    direction controls which kinases are returned:
    - None       → top 10 by absolute z-score (mix of activated and inhibited)
    - 'positive' → top 10 most activated (highest positive z-score)
    - 'negative' → top 10 most inhibited (lowest negative z-score)

    Rows with NaN or infinite z-scores are silently dropped.
    """
    data = _normalise_records(result)
    rows = []

    for item in data:
        if not isinstance(item, dict):
            continue
        for kinase_id, perturbations in item.items():
            if not isinstance(perturbations, dict):
                continue
            for perturbation, values in perturbations.items():
                if not isinstance(values, dict):
                    continue
                z = values.get("z_score")
                if z is None or not math.isfinite(z):
                    continue
                rows.append({
                    "kinase": kinase_id,
                    "z_score": z,
                    "perturbation": perturbation,
                })

    if direction == "positive":
        rows = [r for r in rows if r["z_score"] > 0]
        rows = sorted(rows, key=lambda r: r["z_score"], reverse=True)
    elif direction == "negative":
        rows = [r for r in rows if r["z_score"] < 0]
        rows = sorted(rows, key=lambda r: r["z_score"])
    else:
        rows = sorted(rows, key=lambda r: abs(r["z_score"]), reverse=True)

    return rows[:10]


def extract_ksea_condition_data(result):
    """Extract KSEA rows from a raw API result for a condition comparison chart.

    Returns one row per perturbation with z-score, p-value, and n (substrate count).
    Rows with NaN or infinite z-scores are silently dropped.
    Used by the bar chart handlers to compare one kinase across drugs or cell lines.
    """
    data = _normalise_records(result)
    rows = []

    for item in data:
        if not isinstance(item, dict):
            continue
        for kinase_id, perturbations in item.items():
            if not isinstance(perturbations, dict):
                continue
            for perturbation, values in perturbations.items():
                if not isinstance(values, dict):
                    continue
                z = values.get("z_score")
                if z is None or not math.isfinite(z):
                    continue
                rows.append({
                    "kinase": kinase_id,
                    "z_score": z,
                    "p_value": values.get("p_value"),
                    "n": values.get("n"),
                    "perturbation": perturbation,
                })

    return rows


def extract_kinase_ids(kinases_result):
    """Pull out the UniProt IDs from the list of kinases returned by KINEPIK.
    Handles different response shapes the API might return."""
    if _is_error_payload(kinases_result):
        return []

    if isinstance(kinases_result, dict):
        records = kinases_result.get("results") or kinases_result.get("data") or []
    else:
        records = kinases_result

    ids = []
    for item in records:
        if not isinstance(item, dict):
            continue
        # Try several possible field names for the UniProt ID
        kinase_id = (
            item.get("UniprotID")
            or item.get("UniProtID")
            or item.get("uniprot_id")
            or item.get("id")
        )
        if kinase_id:
            ids.append(kinase_id)

    # Remove duplicates while preserving order
    return list(dict.fromkeys(ids))


def _chunks(items, size):
    """Split a list into smaller chunks of a given size.
    Used to break the kinase list into batches for parallel API calls."""
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _env_int(name, default):
    """Read an integer from an environment variable, falling back to a default value."""
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _get_available_kinases(perturbation):
    """Ask KINEPIK which kinases actually have data for this perturbation.
    Returns a set of UniProt IDs, or an empty set if the call fails.
    This avoids querying kinases that would return NaN z-scores."""
    try:
        result = get_available_perturbations(perturbation)
        if _is_error_payload(result) or not isinstance(result, list):
            return set()
        for item in result:
            if isinstance(item, dict):
                targets = item.get("AvailableTargetKinases", [])
                if isinstance(targets, list):
                    return set(targets)
    except Exception:
        pass
    return set()


def get_kinome_ksea(seed_kinase_ids, perturbation="AZD3759", cell_line="MCF7"):
    """Run KSEA across only the kinases that have data for a given perturbation.

    Uses perturbation/available to find which kinases actually have data,
    then intersects with KINEPIK's kinase list to avoid wasting API calls
    on kinases that would return NaN z-scores.

    seed_kinase_ids: list of UniProt IDs to prioritise (put at the front)
    perturbation: drug or treatment name (e.g. 'AZD3759')
    cell_line: cell line to use (e.g. 'MCF7')

    Results are cached — repeated queries are returned instantly.

    Returns (results, meta) where:
    - results is a list of KSEA records or an error dict
    - meta contains stats about how many kinases were queried
    """
    # Return cached result if already queried (check file-safe string key)
    cache_key = f"{perturbation.lower()}||{cell_line.lower()}"
    if cache_key in _ksea_cache and not DISABLE_CACHE and not os.getenv("DISABLE_CACHE"):
        entry = _ksea_cache[cache_key]
        return entry[0], entry[1]

    # Step 1: get all KINEPIK kinase IDs (cached after first call)
    global _kinase_ids_cache
    if not _kinase_ids_cache:
        kinases_result = get_all_kinases()
        if _is_error_payload(kinases_result):
            return kinases_result, {"kinases_requested": 0, "batches": 0, "failed_batches": 0}
        _kinase_ids_cache = extract_kinase_ids(kinases_result)

    if not _kinase_ids_cache:
        return {"error": "Could not extract kinase IDs from KINEPIK."}, {
            "kinases_requested": 0, "batches": 0, "failed_batches": 0
        }

    # Step 2: use all kinases — no filter applied
    kinase_ids = list(_kinase_ids_cache)

    # Step 3: put seed kinases first, remove duplicates
    seed_kinase_ids = [k for k in seed_kinase_ids if k]
    kinase_ids = list(dict.fromkeys(seed_kinase_ids + kinase_ids))

    # Step 4: split into batches and run in parallel
    batch_size = max(1, _env_int("KSEA_BATCH_SIZE", DEFAULT_BATCH_SIZE))
    max_workers = max(1, _env_int("KSEA_MAX_WORKERS", DEFAULT_MAX_WORKERS))
    batches = list(_chunks(kinase_ids, batch_size))

    results = []
    failed_batches = 0
    completed = 0
    total_batches = len(batches)
    progress_key = (perturbation.lower(), cell_line.lower())

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(get_ksea, batch, perturbation=perturbation, cell_line=cell_line)
            for batch in batches
        ]
        for future in as_completed(futures):
            completed += 1
            _ksea_progress[progress_key] = f"Scanning kinases... {completed} of {total_batches} batches complete"
            batch_result = future.result()
            if _is_error_payload(batch_result):
                failed_batches += 1
                continue
            if isinstance(batch_result, list):
                results.extend(batch_result)
            else:
                results.append(batch_result)

    _ksea_progress.pop(progress_key, None)

    meta = {
        "kinases_requested": len(kinase_ids),
        "batches": len(batches),
        "failed_batches": failed_batches,
        "total_kinepik_kinases": len(_kinase_ids_cache),
    }

    if not results:
        return {"error": "KINEPIK returned no KSEA results for the kinase batches."}, meta

    # Save to memory and disk — instant repeat queries even after restart
    _ksea_cache[cache_key] = [results, meta]
    _save_cache()
    return results, meta
