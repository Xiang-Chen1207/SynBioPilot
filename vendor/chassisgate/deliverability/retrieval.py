import json
import re
import time
from pathlib import Path

import requests

from .schema import Candidate, Chassis

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_PMID_RE = re.compile(r"\bPMID\s+(\d+)\b", re.I)


def _cache_path(cache_dir, key):
    return Path(cache_dir) / f"{key}.json" if cache_dir else None


def fetch_evidence(
    candidate: Candidate,
    chassis: Chassis | None = None,
    *,
    offline: bool = False,
    cache_dir: Path | None = None,
    max_records: int = 5,
    timeout: int = 30,
    raise_on_error: bool = False,
    blocked_pmids: set[str] | None = None,
) -> list[str]:
    key = f"{candidate.id}__{getattr(chassis, 'id', '') if chassis is not None else ''}"
    cp = _cache_path(cache_dir, key)
    if cp and cp.exists():
        try:
            return _filter_blocked_pmids(
                json.loads(cp.read_text()),
                blocked_pmids,
            )
        except Exception:
            pass
    if offline:
        return []

    try:
        ids: list[str] = []
        search_errors: list[Exception] = []
        for term in _pubmed_query_terms(candidate, chassis):
            try:
                search_response = _get_with_retries(
                    ESEARCH,
                    params={
                        "db": "pubmed",
                        "term": term,
                        "retmax": max_records,
                        "retmode": "json",
                    },
                    timeout=timeout,
                )
            except Exception as exc:
                search_errors.append(exc)
                continue
            for pmid in (
                search_response.json()
                .get("esearchresult", {})
                .get("idlist", [])
            ):
                if pmid not in ids:
                    ids.append(pmid)
                if len(ids) >= max_records:
                    break
            if len(ids) >= max_records:
                break
        if not ids:
            if search_errors and raise_on_error:
                raise search_errors[-1]
            return []
        fetch_response = _get_with_retries(
            EFETCH,
            params={
                "db": "pubmed",
                "id": ",".join(ids),
                "rettype": "abstract",
                "retmode": "xml",
            },
            timeout=timeout,
        )
        fetch_response.raise_for_status()
        xml = fetch_response.text
        snippets = _parse_pubmed_xml(xml, max_records)
        if not snippets:
            return []
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(snippets))
        return _filter_blocked_pmids(snippets, blocked_pmids)
    except Exception:
        if raise_on_error:
            raise
        return []


def _get_with_retries(
    url: str,
    *,
    params: dict,
    timeout: int,
    max_retries: int = 3,
    backoff_seconds: float = 0.75,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries or not _is_retryable_request_error(exc):
                raise
            time.sleep(backoff_seconds * (2 ** attempt))
    raise last_error


def _is_retryable_request_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in {408, 409, 429}:
        return True
    if isinstance(status, int) and 500 <= status <= 599:
        return True
    return isinstance(exc, (
        requests.Timeout,
        requests.ConnectionError,
    ))


def _pubmed_query_terms(
    candidate: Candidate,
    chassis: Chassis | None,
) -> list[str]:
    molecule = candidate.name.strip()
    if not molecule:
        return []
    if chassis is None:
        return [
            f'"{molecule}" AND (biosynthesis OR biotransformation OR pathway OR enzyme)',
        ]

    chassis_terms = _chassis_terms(chassis.name)
    chassis_or = " OR ".join(f'"{term}"' for term in chassis_terms)
    molecule_q = f'"{molecule}"'
    specific = [
        f"{molecule_q} AND ({chassis_or}) AND (biosynthesis OR production OR produces OR fermentation)",
        f"{molecule_q} AND ({chassis_or}) AND (biotransformation OR conversion OR converts OR precursor)",
        f"{molecule_q} AND ({chassis_or}) AND (engineered OR heterologous OR recombinant OR pathway OR enzyme)",
        f"{molecule_q} AND ({chassis_or})",
    ]
    broad_route = (
        f"{molecule_q} AND (biosynthesis OR biotransformation OR pathway OR enzyme OR synthase)"
    )
    return [*specific, broad_route]


def _chassis_terms(name: str) -> list[str]:
    clean = " ".join(name.split())
    terms = [clean] if clean else []
    parts = clean.split()
    if len(parts) >= 2:
        genus_species = " ".join(parts[:2])
        terms.append(genus_species)
        terms.append(f"{parts[0][0]}. {parts[1]}")
    return list(dict.fromkeys(terms))


def _parse_pubmed_xml(xml: str, max_records: int) -> list[str]:
    out = []
    for art in re.findall(r"<PubmedArticle>.*?</PubmedArticle>", xml, re.S)[:max_records]:
        pmid_m = re.search(r"<PMID[^>]*>(\d+)</PMID>", art)
        title_m = re.search(r"<ArticleTitle>(.*?)</ArticleTitle>", art, re.S)
        pmid = pmid_m.group(1) if pmid_m else "?"
        title = re.sub(r"<[^>]+>", "", title_m.group(1)) if title_m else ""
        abstract = " ".join(
            re.sub(r"<[^>]+>", " ", m)
            for m in re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", art, re.S)
        )
        text = " ".join(f"PMID {pmid}: {title} {abstract}".split())
        if text.strip():
            out.append(text[:700])
    return out


def _filter_blocked_pmids(
    snippets: list[str],
    blocked_pmids: set[str] | None,
) -> list[str]:
    if not blocked_pmids:
        return snippets
    blocked = {str(pmid) for pmid in blocked_pmids}
    return [
        snippet for snippet in snippets
        if not (_PMID_RE.search(snippet) and _PMID_RE.search(snippet).group(1) in blocked)
    ]
