import json
from pathlib import Path

class KeggIdMap:
    def __init__(self, cache_path: Path, resolver=None):
        self.cache_path = Path(cache_path)
        self._cache = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self._resolver = resolver or _default_resolver

    def kegg_for_inchikey(self, inchikey: str) -> str | None:
        if inchikey in self._cache:
            return self._cache[inchikey]
        kegg = self._resolver(inchikey)
        self._cache[inchikey] = kegg
        self.cache_path.write_text(json.dumps(self._cache))
        return kegg

def _default_resolver(inchikey: str) -> str | None:
    # Real path: UniChem (EBI) InChIKey -> KEGG. Network; rate-limited.
    import requests
    url = "https://www.ebi.ac.uk/unichem/api/v1/compounds"
    try:
        r = requests.post(url, json={"type": "inchikey", "compound": inchikey}, timeout=10)
        for s in r.json().get("compounds", [{}])[0].get("sources", []):
            if s.get("shortName") == "kegg_ligand":
                return s.get("compoundId")
    except Exception:
        return None
    return None
