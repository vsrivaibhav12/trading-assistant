"""Supabase (Postgres) through its plain REST API, plus an in-memory twin used for tests and dry runs.
Only needs `requests`, so the dashboard can import it too."""
from __future__ import annotations
import datetime as _dt
import math
import requests


def jsonable(o):
    """Make numpy / pandas / datetime values JSON-safe."""
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            o = o.item()
        except Exception:
            pass
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


def _fmt(v):
    v = jsonable(v)
    return "null" if v is None else str(v).lower() if isinstance(v, bool) else str(v)


class SupabaseDB:
    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):            # legacy JWT-style service_role key; new sb_secret_ keys use apikey only
            self.h["Authorization"] = f"Bearer {key}"

    def _req(self, method, table, params=None, body=None, prefer=None):
        h = dict(self.h)
        if prefer:
            h["Prefer"] = prefer
        r = requests.request(method, f"{self.base}/{table}", params=params, json=jsonable(body) if body is not None else None,
                             headers=h, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"supabase {method} {table} -> {r.status_code}: {r.text[:200]}")
        return r.json() if r.text else []

    def insert(self, table, row):
        rows = self._req("POST", table, body=row if isinstance(row, list) else [row], prefer="return=representation")
        return rows if isinstance(row, list) else rows[0]

    def select(self, table, filters=None, order=None, limit=None):
        """Pages through results (Supabase caps one response at 1000 rows)."""
        base = [("select", "*")]
        for col, op, val in (filters or []):
            if op == "in":
                base.append((col, f"in.({','.join(str(jsonable(x)) for x in val)})"))
            else:
                base.append((col, f"{op}.{_fmt(val)}"))
        if not order and table != "kv":
            order = "id.asc"
        if order:
            base.append(("order", order))
        out, offset = [], 0
        while True:
            page = min(1000, (limit - len(out)) if limit else 1000)
            rows = self._req("GET", table, params=base + [("limit", page), ("offset", offset)])
            out += rows
            if len(rows) < page or (limit and len(out) >= limit):
                return out
            offset += page

    def update(self, table, id_, values):
        rows = self._req("PATCH", table, params={"id": f"eq.{id_}"}, body=values, prefer="return=representation")
        return rows[0] if rows else None

    def upsert(self, table, row, on_conflict):
        return self._req("POST", table, params={"on_conflict": on_conflict}, body=[row],
                         prefer="resolution=merge-duplicates,return=representation")

    def delete(self, table, filters):
        params = [(c, f"{op}.{_fmt(v)}") for c, op, v in filters]
        return self._req("DELETE", table, params=params)

    def kv_get(self, key, default=None):
        rows = self.select("kv", [("key", "eq", key)], limit=1)
        return rows[0]["value"] if rows else default

    def kv_set(self, key, value):
        self.upsert("kv", {"key": key, "value": value, "updated_at": _dt.datetime.now(_dt.timezone.utc)}, "key")


class MemoryDB:
    """Same interface, no network. Used by the self-test and DRY_RUN."""

    def __init__(self):
        self.t: dict[str, list[dict]] = {}
        self._id = 0

    def insert(self, table, row):
        if isinstance(row, list):
            return [self.insert(table, r) for r in row]
        self._id += 1
        r = jsonable(dict(row))
        r.setdefault("id", self._id)
        self.t.setdefault(table, []).append(r)
        return dict(r)

    @staticmethod
    def _ok(r, filters):
        for col, op, val in (filters or []):
            v, val = r.get(col), jsonable(val)
            if op == "eq" and not v == val: return False
            if op == "neq" and v == val: return False
            if op == "gte" and not (v is not None and v >= val): return False
            if op == "lte" and not (v is not None and v <= val): return False
            if op == "gt" and not (v is not None and v > val): return False
            if op == "lt" and not (v is not None and v < val): return False
            if op == "in" and v not in [jsonable(x) for x in val]: return False
        return True

    def select(self, table, filters=None, order=None, limit=None):
        rows = [dict(r) for r in self.t.get(table, []) if self._ok(r, filters)]
        if order:
            col, _, d = order.partition(".")
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=(d == "desc"))
        return rows[:limit] if limit else rows

    def update(self, table, id_, values):
        for r in self.t.get(table, []):
            if r.get("id") == id_:
                r.update(jsonable(values))
                return dict(r)

    def upsert(self, table, row, on_conflict):
        for r in self.t.setdefault(table, []):
            if r.get(on_conflict) == row.get(on_conflict):
                r.update(jsonable(row))
                return [dict(r)]
        return [self.insert(table, row)]

    def delete(self, table, filters):
        self.t[table] = [r for r in self.t.get(table, []) if not self._ok(r, filters)]

    def kv_get(self, key, default=None):
        rows = self.select("kv", [("key", "eq", key)])
        return rows[0]["value"] if rows else default

    def kv_set(self, key, value):
        self.upsert("kv", {"key": key, "value": value}, "key")
