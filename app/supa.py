"""Supabase REST + Storage helpers (service key from env)."""
import json
import urllib.parse
from datetime import datetime
from typing import List, Optional

import httpx

from . import config


class SupabaseError(RuntimeError):
    pass


def _headers(extra: Optional[dict] = None) -> dict:
    k = config.supabase_key()
    if not k or not config.supabase_url():
        raise SupabaseError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
    h = {"apikey": k}
    if k.startswith("eyJ"):            # legacy service_role JWT; new sb_secret_ keys go in apikey only
        h["Authorization"] = f"Bearer {k}"
    if extra:
        h.update(extra)
    return h


def _obj_url(path: str) -> str:
    return f"{config.supabase_url()}/storage/v1/object/{config.BUCKET}/{urllib.parse.quote(path, safe='/')}"


def get_lead(lead_id: int) -> dict:
    url = f"{config.supabase_url()}/rest/v1/{config.LEADS_TABLE}"
    r = httpx.get(url, headers=_headers(), timeout=20,
                  params={"leadid": f"eq.{lead_id}", "select": "leadid,custname,createdt,leadtype,typeofwork"})
    if r.status_code != 200:
        raise SupabaseError(f"Lead query failed ({r.status_code}): {r.text[:300]}")
    rows = r.json()
    if not rows:
        raise SupabaseError(f"Lead {lead_id} not found")
    return rows[0]


def lead_prefix(lead: dict, year: str = "", month: str = "") -> str:
    """{year}/{month}/{lead_id}. Explicit year/month from the portal win over created date."""
    if year and month:
        return f"{year}/{month.lower()[:3]}/{lead['leadid']}"
    try:
        dt = datetime.fromisoformat(str(lead.get("createdt", "")).replace("Z", "+00:00"))
    except Exception:
        dt = datetime.utcnow()
    return f"{dt.year}/{dt.strftime('%b').lower()}/{lead['leadid']}"


def list_folder(prefix: str) -> List[dict]:
    url = f"{config.supabase_url()}/storage/v1/object/list/{config.BUCKET}"
    out, offset = [], 0
    while True:
        r = httpx.post(url, headers=_headers({"Content-Type": "application/json"}), timeout=30,
                       json={"prefix": prefix, "limit": 1000, "offset": offset,
                             "sortBy": {"column": "name", "order": "asc"}})
        if r.status_code != 200:
            raise SupabaseError(f"List failed for {prefix}: {r.text[:300]}")
        batch = r.json()
        # folders come back with id = null
        out += [f for f in batch if f.get("name") and f.get("id")]
        if len(batch) < 1000:
            return out
        offset += 1000


def download(path: str) -> bytes:
    r = httpx.get(_obj_url(path), headers=_headers(), timeout=180)
    if r.status_code != 200:
        raise SupabaseError(f"Download failed for {path} ({r.status_code})")
    return r.content


def upload(path: str, data: bytes, content_type: str) -> str:
    r = httpx.post(_obj_url(path), content=data, timeout=120,
                   headers=_headers({"Content-Type": content_type, "x-upsert": "true"}))
    if r.status_code not in (200, 201):
        raise SupabaseError(f"Upload failed for {path} ({r.status_code}): {r.text[:300]}")
    return f"{config.BUCKET}/{path}"


def delete(path: str) -> bool:
    url = f"{config.supabase_url()}/storage/v1/object/{config.BUCKET}"
    r = httpx.request("DELETE", url, headers=_headers({"Content-Type": "application/json"}),
                      json={"prefixes": [path]}, timeout=30)
    return r.status_code in (200, 204)


def upload_json(path: str, obj) -> str:
    return upload(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")


def read_json(path: str):
    r = httpx.get(_obj_url(path), headers=_headers(), timeout=30)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def save_legalinfo_to_lead(lead_id: int, legalinfo: dict) -> bool:
    url = f"{config.supabase_url()}/rest/v1/{config.LEADS_TABLE}"
    r = httpx.patch(url, params={"leadid": f"eq.{lead_id}"}, timeout=30,
                    headers=_headers({"Content-Type": "application/json", "Prefer": "return=minimal"}),
                    json={"legalinfo": legalinfo})
    return r.status_code in (200, 204)
