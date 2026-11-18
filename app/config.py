"""Central configuration. Every secret comes from environment variables — never hardcode keys."""
import os


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def supabase_url() -> str:
    u = env("SUPABASE_URL").rstrip("/")
    for sfx in ("/rest/v1", "/storage/v1"):
        if u.endswith(sfx):
            u = u[: -len(sfx)]
    return u


def supabase_key() -> str:
    return env("SUPABASE_SERVICE_KEY")


def anthropic_key() -> str:
    return env("ANTHROPIC_API_KEY")


def openai_key() -> str:
    return env("OPENAI_API_KEY")


def api_key() -> str:
    """Shared secret n8n sends in the x-api-key header. If empty, auth is disabled (local dev only)."""
    return env("PLJ_API_KEY")


BUCKET = env("STORAGE_BUCKET", "documents")
LEADS_TABLE = env("LEADS_TABLE", "Leads")

# Two AI providers. Claude = OCR of scanned PDFs/photos (hard). ChatGPT = everything else (easy).
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o")
OPENAI_MAX_OUTPUT = int(env("OPENAI_MAX_OUTPUT", "16000"))   # gpt-4o allows 16,384 output tokens


def _provider(key: str, default: str) -> str:
    v = env(key, default).lower()
    return "claude" if v in ("claude", "anthropic") else "openai"


# Which provider does each job (openai | claude). PDFs always go to Claude.
LLM_EXTRACT_IMAGE = _provider("LLM_EXTRACT_IMAGE", "claude")   # photos/JPG of documents (OCR)
LLM_EXTRACT_TEXT = _provider("LLM_EXTRACT_TEXT", "openai")     # DOCX text
LLM_IGR = _provider("LLM_IGR", "openai")                       # IGR row matching
LLM_DRAFT = _provider("LLM_DRAFT", "openai")                   # title report drafting
LLM_DEFAULT = _provider("LLM_DEFAULT", "openai")
LLM_FALLBACK = env("LLM_FALLBACK", "true").lower() in ("1", "true", "yes")

# File names written next to the lead folder: {year}/{month}/{lead_id}/
LEGALINFO_FILE = "legalinfo_ai.json"          # portal schema (unchanged keys)
ANALYSIS_FILE = "legal_analysis_ai.json"      # full structured analysis used for reports
STATUS_FILE = "ai_status.json"                # job status (survives restarts)
REPORTS_DIR = "reports"

# Firm letterhead (change here, not in code)
FIRM_NAME = env("FIRM_NAME", "PROFICIENT LAW JUNCTION")
FIRM_TAGLINE = env("FIRM_TAGLINE", "ADVOCATES & LEGAL CONSULTANTS")
FIRM_ADDRESS = env(
    "FIRM_ADDRESS",
    "Office No: 4, Bhatt/Kadam Building, Old Nagardas Road, Behind Bhatia Medical, "
    "Andheri - (East), Mumbai-400 069. Office : 022-28347177, Mobile : 9869507557",
)
FIRM_EMAIL = env("FIRM_EMAIL", "plawjunction@gmail.com")
FIRM_SIGNATORY = env("FIRM_SIGNATORY", "For Proficient Law Junction LLP")
FIRM_PLACE = env("FIRM_PLACE", "Mumbai")

# Limits
PDF_CHUNK_MAX_PAGES = int(env("PDF_CHUNK_MAX_PAGES", "20"))
# output tokens for reading one PDF part; a part whose answer still does not fit is split in half automatically
EXTRACT_MAX_TOKENS = int(env("EXTRACT_MAX_TOKENS", "32000"))
PDF_CHUNK_MAX_MB = float(env("PDF_CHUNK_MAX_MB", "15"))
IGR_MAX_CANDIDATES = int(env("IGR_MAX_CANDIDATES", "250"))
IGR_BATCH_SIZE = int(env("IGR_BATCH_SIZE", "30"))
DEFAULT_SEARCH_YEARS = int(env("DEFAULT_SEARCH_YEARS", "30"))
JOB_STALE_MINUTES = int(env("JOB_STALE_MINUTES", "60"))
