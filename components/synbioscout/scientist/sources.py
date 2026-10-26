"""Actual public-data connectors. Retrieved associations are not causal conclusions."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import quote
from xml.etree import ElementTree as ET

from .annotation_index import query_receipt
from .http import DataSourceError, HTTPClient
from .models import SourceRecord
from .store import digest, utc_now

OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"
EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"


class IdentityResolutionError(DataSourceError):
    """Unresolved identity with a bounded, public candidate summary, never a selection."""

    def __init__(self, message, *, status, candidates=None):
        super().__init__(message)
        self.status = self.identity_status = status
        self.candidates = list(candidates or [])[:10]


def _gene_names(entry):
    names = []
    genes = entry.get("genes", [])
    if not isinstance(genes, list):
        raise DataSourceError("Malformed UniProt gene collection")
    for gene in genes:
        if not isinstance(gene, dict):
            raise DataSourceError("Malformed UniProt gene record")
        primary = gene.get("geneName", {})
        if isinstance(primary, dict) and isinstance(primary.get("value"), str):
            names.append(primary["value"])
        aliases = gene.get("synonyms", [])
        if not isinstance(aliases, list):
            raise DataSourceError("Malformed UniProt gene synonym collection")
        for alias in aliases:
            if isinstance(alias, dict) and isinstance(alias.get("value"), str):
                names.append(alias["value"])
    return list(dict.fromkeys(names))


def _protein_candidate(entry):
    organism = entry.get("organism", {})
    if not isinstance(organism, dict):
        raise DataSourceError("Malformed UniProt organism")
    accession, species = entry.get("primaryAccession", ""), organism.get("scientificName", "")
    return {"provider": "uniprot", "entity_type": "protein",
            "accession": accession[:80] if isinstance(accession, str) else "",
            "species": species[:160] if isinstance(species, str) else "",
            "gene_names": [name[:80] for name in _gene_names(entry)[:20]],
            "reviewed": entry.get("entryType") == "UniProtKB reviewed (Swiss-Prot)"}


def _molecule_candidate(row):
    # Raw provider rows may include unrelated fields; candidate disclosure is a
    # whitelist rather than a pass-through of the provider response.
    cid = row.get("CID", "")
    key = row.get("InChIKey", "")
    smiles = row.get("SMILES") or row.get("IsomericSMILES") or row.get("CanonicalSMILES") or ""
    return {"provider": "pubchem", "entity_type": "molecule",
            "cid": str(cid)[:40] if isinstance(cid, (int, str)) else "",
            "inchikey": key[:40] if isinstance(key, str) else "",
            "smiles": smiles[:500] if isinstance(smiles, str) else ""}


def _pubchem_not_found(exc):
    response = getattr(exc.__cause__, "response", None)
    if response is None or response.status_code != 404:
        return False
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return False
    return (isinstance(payload, dict) and isinstance(payload.get("Fault"), dict) and
            payload["Fault"].get("Code") == "PUGREST.NotFound")


class _SourceTextParser(HTMLParser):
    """Strip actual markup without consuming statistical inequalities as tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        self.parts.append(" ")

    def handle_endtag(self, tag):
        self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.parts.append(" ")


def clean_text(text):
    # An angle-bracket regex also matches `p < .001 ... <h4>` and can
    # silently erase results and the following null-result clauses. Parse tags
    # before decoding entities; escaped mathematical text is data, not markup.
    parser = _SourceTextParser()
    parser.feed(text or "")
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


class BiomedicalSources:
    def __init__(self, http: HTTPClient):
        self.http = http

    def _graphql(self, query, variables):
        event_start = len(getattr(self.http, "request_events", []))
        payload = self.http.post_json(OT_URL, {"query": query, "variables": variables})
        if payload.get("errors"):
            message = "; ".join(str(e.get("message", "GraphQL error"))
                                for e in payload["errors"])
            raise DataSourceError("Open Targets: " + message)
        operation = re.search(r"\bquery\s+(\w+)", query)
        self.last_annotation_receipt = query_receipt(self.http, "open_targets",
            operation.group(1) if operation else "fixed_graphql_query",
            {key: value for key, value in variables.items() if key in {"q", "entities", "id", "size"}},
            payload, event_start)
        return payload.get("data", {})

    def search_entity(self, text, entity):
        query = """query Search($q:String!,$entities:[String!]) {
          search(queryString:$q,entityNames:$entities,page:{index:0,size:5}) {
            hits { id entity name description }
          }
        }"""
        return self._graphql(query, {"q": text, "entities": [entity]}).get("search", {}).get("hits", [])

    def disease_targets(self, disease_id, limit=10):
        query = """query Disease($id:String!,$size:Int!) {
          disease(efoId:$id) { id name description
            associatedTargets(page:{index:0,size:$size}) { rows { score
              datatypeScores { id score }
              target { id approvedSymbol approvedName proteinIds { id source } }
            } }
          }
        }"""
        return self._graphql(query, {"id": disease_id, "size": limit}).get("disease") or {}

    def target_details(self, ensembl_id):
        query = """query Target($id:String!) { target(ensemblId:$id) {
          id approvedSymbol approvedName proteinIds { id source }
          tractability { label modality value }
        } }"""
        return self._graphql(query, {"id": ensembl_id}).get("target") or {}

    def database_source(self, key, title, value, url, provider="open_targets"):
        return SourceRecord(source_id=key, provider=provider, title=title, url=url,
                            retrieved_at=utc_now(), text=json.dumps(value, ensure_ascii=False),
                            evidence_scope="database", metadata=value)

    def resolve_disease(self, name):
        """A search hit is a lead; only a unique exact, checked ontology name resolves."""
        hits = self.search_entity(name, "disease")
        search_receipt = getattr(self, "last_annotation_receipt", {})
        if not isinstance(hits, list) or any(not isinstance(h, dict) for h in hits):
            raise DataSourceError("Malformed Open Targets disease search")
        candidates = [{"id": str(h.get("id", ""))[:80], "name": str(h.get("name", ""))[:250]}
                      for h in hits[:5]]
        norm = lambda s: re.sub(r"\s+", " ", str(s).casefold()).strip()
        exact = [h for h in hits if norm(h.get("name", "")) == norm(name)]
        if len(hits) >= 5 or len(exact) != 1:
            status = "truncated" if len(hits) >= 5 else ("ambiguous" if exact else "unresolved")
            raise IdentityResolutionError("Disease ontology identity requires one exact untruncated match",
                                          status=status, candidates=candidates)
        hit = exact[0]
        if not isinstance(hit.get("id"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+", hit["id"]):
            raise IdentityResolutionError("Disease ontology identifier is invalid", status="mismatch", candidates=candidates)
        query = """query DiseaseIdentity($id:String!) { disease(efoId:$id) { id name } }"""
        checked = self._graphql(query, {"id": hit["id"]}).get("disease")
        if not isinstance(checked, dict) or checked.get("id") != hit["id"] or norm(checked.get("name", "")) != norm(name):
            raise IdentityResolutionError("Disease ontology record does not confirm searched identity",
                                          status="mismatch", candidates=candidates)
        return {"id": checked["id"], "name": checked["name"], "identity_status": "resolved",
                "query_receipts": [r for r in (search_receipt, getattr(self, "last_annotation_receipt", {})) if r]}

    def search_literature(self, query, limit=8, *, sort="relevance", cursor_mark="*", facet="", query_origin="caller_query"):
        """One bounded page. Relevance is the default; recency is explicit, never implicit."""
        if sort not in {"relevance", "date"} or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Unsupported bounded literature search options")
        if not isinstance(cursor_mark, str) or not cursor_mark or len(cursor_mark) > 1000:
            raise ValueError("Invalid literature cursor")
        params = {"query": query, "format": "json", "resultType": "core", "pageSize": limit,
                  "cursorMark": cursor_mark}
        if sort == "date":
            params["sort_date"] = "y"
        event_start = len(getattr(self.http, "request_events", []))
        data = self.http.get_json(EPMC_URL + "/search", params=params)
        if not isinstance(data, dict) or not isinstance(data.get("resultList"), dict) or not isinstance(data["resultList"].get("result"), list):
            raise DataSourceError("Malformed Europe PMC result collection")
        total = data.get("hitCount")
        total = total if type(total) is int and total >= 0 else None
        returned = len(data["resultList"]["result"][:limit])
        first = cursor_mark == "*"
        next_cursor = data.get("nextCursorMark")
        has_more = (returned < total if first and total is not None else
                    next_cursor != cursor_mark if isinstance(next_cursor, str) and next_cursor else None)
        self.last_literature_scope = {"total_hits": total, "query": query, "page_size": limit,
            "pagination": "first_bounded_page" if first else "bounded_cursor_page",
            "sort": "sort_date=y" if sort == "date" else "relevance", "facet": facet, "query_origin": query_origin,
            "retrieved_count": returned, "hit_range_start": 1 if first and returned else None,
            "hit_range_end": returned if first and returned else None, "has_more": has_more,
            "coverage": "bounded_subset" if has_more else ("returned_query_batch" if total is not None else "total_unknown")}
        receipt = query_receipt(self.http, "europe_pmc", "literature_search", params, data, event_start)
        records = []
        for paper in data.get("resultList", {}).get("result", [])[:limit]:
            if not isinstance(paper, dict):
                raise DataSourceError("Malformed Europe PMC paper record")
            pmid = str(paper.get("pmid") or "")
            doi = str(paper.get("doi") or "")
            pmcid = str(paper.get("pmcid") or "")
            key = "pmid:" + pmid if pmid else "epmc:" + str(paper.get("source")) + ":" + str(paper.get("id"))
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else (
                "https://doi.org/" + doi if doi else "https://europepmc.org/article/" +
                str(paper.get("source")) + "/" + str(paper.get("id")))
            author_list = paper.get("authorList", {})
            author_rows = author_list.get("author", []) if isinstance(author_list, dict) else []
            authors = [a["fullName"][:200] for a in author_rows[:100]
                       if isinstance(a, dict) and isinstance(a.get("fullName"), str)] if isinstance(author_rows, list) else []
            if not authors and isinstance(paper.get("authorString"), str):
                authors = [paper["authorString"][:2000]]
            journal_info = paper.get("journalInfo", {})
            journal = journal_info.get("journal", {}) if isinstance(journal_info, dict) else {}
            journal_title = journal.get("title", "") if isinstance(journal, dict) else ""
            records.append(SourceRecord(source_id=key, provider="europe_pmc",
                title=clean_text(paper.get("title", "")), url=url, retrieved_at=utc_now(),
                text=clean_text(paper.get("abstractText", "")), pmid=pmid, doi=doi,
                pmcid=pmcid, evidence_scope="abstract", metadata={
                    "year": paper.get("pubYear"), "publication_types": paper.get("pubTypeList", {}),
                    "is_open_access": paper.get("isOpenAccess"), "query": query,
                    "authors": authors, "journal": journal_title if isinstance(journal_title, str) else "",
                    "retrieval_facet": facet, "search_scope": dict(self.last_literature_scope),
                    "query_receipt": receipt}))
        return records

    def full_text(self, source: SourceRecord):
        if not source.pmcid or source.metadata.get("is_open_access") != "Y":
            return None
        raw = self.http.get_text(EPMC_URL + "/" + quote(source.pmcid) + "/fullTextXML")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise DataSourceError("Invalid Europe PMC full-text XML") from exc
        sections = []
        for element in root.findall(".//body//sec"):
            title = clean_text("".join(element.find("title").itertext())) if element.find("title") is not None else "Section"
            paragraphs = [clean_text("".join(p.itertext())) for p in element.findall("./p")]
            if paragraphs:
                sections.append(title + "\n" + "\n".join(paragraphs))
        if not sections:
            body = root.find(".//body")
            if body is not None:
                sections = [clean_text("".join(body.itertext()))]
        if not sections:
            return None
        metadata = {**source.metadata, "full_text_source_url": EPMC_URL + "/" + source.pmcid + "/fullTextXML"}
        return source.model_copy(update={"text": source.text + "\n\n" + "\n\n".join(sections),
                                          "evidence_scope": "full_text", "metadata": metadata})

    def resolve_target(self, gene_symbol, species="Homo sapiens"):
        if not isinstance(gene_symbol, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", gene_symbol):
            raise ValueError("A valid protein/gene symbol is required")
        if (not isinstance(species, str) or not species.strip() or len(species) > 200 or
                any(c in species for c in '\"\\') or any(ord(c) < 32 for c in species)):
            raise ValueError("A valid scientific species name is required")
        query = f"(gene_exact:{gene_symbol}) AND (organism_name:\"{species}\")"
        parameters = {"query": query, "format": "json", "size": 5}
        event_start = len(getattr(self.http, "request_events", []))
        data = self.http.get_json("https://rest.uniprot.org/uniprotkb/search", params=parameters)
        if not isinstance(data, dict) or "results" not in data:
            raise DataSourceError("Malformed UniProt identity response")
        records = data.get("results", [])
        if not isinstance(records, list) or any(not isinstance(entry, dict) for entry in records):
            raise DataSourceError("Malformed UniProt identity record collection")
        exact = []
        for entry in records:
            if any(name.upper() == gene_symbol.upper() for name in _gene_names(entry)):
                exact.append(entry)
        candidates = [_protein_candidate(entry) for entry in exact or records]
        if any(_protein_candidate(entry)["species"].casefold() != species.casefold()
               for entry in exact):
            raise IdentityResolutionError("Protein identity species mismatch", status="mismatch",
                                          candidates=candidates)
        # A full first batch can hide additional exact matches. The endpoint's
        # next page is deliberately not fetched merely to force a selection.
        if len(records) >= 5:
            raise IdentityResolutionError("Protein identity ambiguous/unresolved: bounded search "
                                          "may hide additional matches", status="truncated",
                                          candidates=candidates)
        if len(exact) != 1:
            raise IdentityResolutionError(
                f"Protein identity ambiguous/unresolved for {gene_symbol} ({species})",
                status="ambiguous" if exact else "empty", candidates=candidates)
        entry = exact[0]
        accession = entry.get("primaryAccession")
        if not isinstance(accession, str) or not accession:
            raise IdentityResolutionError("Protein identity missing accession", status="mismatch",
                                          candidates=candidates)
        source = self.database_source("uniprot:" + accession, f"UniProt {gene_symbol} {accession}",
            entry, "https://www.uniprot.org/uniprotkb/" + accession, "uniprot")
        source.metadata["query_receipt"] = query_receipt(self.http, "uniprot", "protein_identity",
            {**parameters, "gene_symbol": gene_symbol, "species": species}, data, event_start)
        return accession, source

    def resolve_molecule(self, name):
        from rdkit import Chem
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A molecule name is required")
        try:
            properties = self.http.get_json("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/" +
                quote(name, safe="") + "/property/IsomericSMILES,InChIKey/JSON")
        except DataSourceError as exc:
            if _pubchem_not_found(exc):
                raise IdentityResolutionError("Molecule name maps to 0 entities; explicit identity "
                                              "needed", status="empty") from exc
            raise
        if (not isinstance(properties, dict) or
                not isinstance(properties.get("PropertyTable"), dict) or
                "Properties" not in properties["PropertyTable"]):
            raise DataSourceError("Malformed PubChem identity response")
        rows = properties.get("PropertyTable", {}).get("Properties", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DataSourceError("Malformed PubChem identity record collection")
        candidates = [_molecule_candidate(row) for row in rows[:10]]
        if len(rows) != 1:
            status = "truncated" if len(rows) > 10 else ("ambiguous" if rows else "empty")
            raise IdentityResolutionError(
                f"Molecule name maps to {len(rows)} entities; explicit identity needed",
                status=status, candidates=candidates)
        row = rows[0]
        smiles = row.get("SMILES") or row.get("IsomericSMILES") or row.get("CanonicalSMILES")
        molecule = Chem.MolFromSmiles(smiles or "")
        if molecule is None or not molecule.GetNumAtoms():
            raise IdentityResolutionError("PubChem returned an invalid structure", status="mismatch",
                                          candidates=candidates)
        canonical = Chem.MolToSmiles(molecule, isomericSmiles=True)
        key = Chem.MolToInchiKey(molecule)
        if row.get("InChIKey") != key:
            raise IdentityResolutionError("Retrieved molecule SMILES/InChIKey disagree",
                                          status="mismatch", candidates=candidates)
        cid = str(row.get("CID", ""))
        if not cid.isdigit() or int(cid) <= 0:
            raise IdentityResolutionError("PubChem identity missing a valid CID", status="mismatch",
                                          candidates=candidates)
        source = self.database_source("pubchem:" + cid, f"PubChem identity for {name}",
            {"name_query": name, "cid": cid, "smiles": canonical, "inchikey": key},
            "https://pubchem.ncbi.nlm.nih.gov/compound/" + cid, "pubchem")
        return {"candidate_id": "mol_" + digest([canonical, key])[:16],
                "smiles": canonical, "inchikey": key, "pubchem_cid": cid}, source

