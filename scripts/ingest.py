#!/usr/bin/env python3
"""
ingest.py — Hybrid-search ingestion pipeline for IBM Db2 12.1
=============================================================================
Takes one PDF and builds a hybrid-search corpus end to end:

  1. Extract   PDF -> Markdown with Docling (markdown persisted to disk).
  2. Chunk     Docling HybridChunker, capped to the embedding model's token limit.
  3. Load      CREATE TABLE chunks(chunk_id, chunk_text); insert every chunk.
  4. Lexical   db2ts text-search index over chunk_text (OpenSearch-backed, BM25).
  5. Model     CREATE EXTERNAL MODEL — register a watsonx.ai embedding model.
  6. Vector    ALTER TABLE ADD VECTOR column; populate with TO_EMBEDDING(chunk_text).

End state: one row per chunk holding its text, a stable chunk_id, a text-search
index over the text, and its dense vector — one chunk, two representations.

Run order:  cleanup.sh   (clean slate)   ->   ingest.py

-----------------------------------------------------------------------------
WHY db2ts FOR THE TEXT INDEX (not pure SQL):
  Db2 Text Search admin operations are exposed as the SYSPROC.SYSTS_* stored
  procedures, but on this platform they fail over a remote connection with
  SQL0444N ("*ADMIN_BG" external library). The db2ts CLP is the reliable path,
  so step 4 shells out to db2ts as the instance owner (directly if you already
  are it, otherwise via `sudo -niu <owner>`). Everything else is ibm_db SQL.

PARAMETERIZED SQL:
  All row data (chunk inserts) uses parameter markers. DDL and db2ts commands
  cannot use parameter markers for identifiers, so schema/table/index/model/
  column names are validated against a strict identifier allow-list, and the
  watsonx string constants in CREATE EXTERNAL MODEL are single-quote-escaped.

CONFIG: every setting comes from a CLI flag or an env var (see build_config()).
-----------------------------------------------------------------------------
"""

import argparse
import getpass
import os
import re
import subprocess
import sys
from dataclasses import dataclass

import ibm_db
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from transformers import AutoTokenizer


# --- Known watsonx.ai embedding models -> output vector dimension ------------
# Used to DERIVE the default vector dimension from the chosen model. Override
# with --vector-dim for any model not listed here.
WATSONX_EMBED_DIMS = {
    "sentence-transformers/all-minilm-l6-v2": 384,
    "ibm/slate-30m-english-rtrvr-v2": 384,
    "ibm/slate-125m-english-rtrvr-v2": 768,
    "ibm/granite-embedding-107m-multilingual": 384,
    "ibm/granite-embedding-278m-multilingual": 768,
    "intfloat/multilingual-e5-large": 1024,
}

# DEFAULTS (noted per the spec). The pipeline aligns the chunker's token budget
# to all-MiniLM-L6-v2 (256 tokens, 384-dim), which is also a watsonx.ai model —
# so chunk sizing and the embedding model agree out of the box.
DEFAULT_WX_MODEL_ID     = "sentence-transformers/all-minilm-l6-v2"  # watsonx model id
DEFAULT_TOKENIZER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # HF tokenizer id
DEFAULT_MAX_TOKENS      = 256
DEFAULT_WX_URL          = "https://us-south.ml.cloud.ibm.com/ml/v1/text/embeddings?version=2024-05-01"
DEFAULT_COORD_TYPE      = "FLOAT32"   # VECTOR coordinate type: REAL | FLOAT32 | INT8


@dataclass
class Config:
    pdf: str
    markdown_out: str
    db_host: str
    db_port: str
    db_name: str
    db_user: str
    db_password: str
    schema: str
    table: str
    index_name: str
    vector_column: str
    model_name: str            # Db2 external-model name (schema-qualified)
    tokenizer_model: str
    max_tokens: int
    vector_dim: int
    coord_type: str
    wx_url: str
    wx_apikey: str
    wx_project_id: str
    wx_model_id: str
    opensearch_port: str
    instance_owner: str
    skip_embedding: bool


# --- identifier safety (DDL can't be parameterized) -------------------------
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def ident(name: str, what: str) -> str:
    if not _IDENT.match(name or ""):
        sys.exit(f"ERROR: invalid {what} {name!r} (allowed: letters, digits, underscore)")
    return name

def qualified(schema: str, name: str) -> str:
    return f"{ident(schema, 'schema')}.{ident(name, 'name')}"

def sql_str(value: str) -> str:
    """Single-quote a string constant for DDL, escaping embedded quotes."""
    return "'" + (value or "").replace("'", "''") + "'"

def log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


# --- configuration ----------------------------------------------------------
def env(name, default=None):
    return os.environ.get(name, default)

def load_dotenv():
    """Load KEY=VALUE pairs from .env into the environment (real env vars win).
    Looks in the current directory and at the repo root (this file's parent's
    parent), so it works whether you run from the repo root or from scripts/.
    Db2 + watsonx.ai credentials are expected here."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (".env", os.path.join(repo_root, ".env")):
        if not os.path.exists(path):
            continue
        for line in open(path):
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

def build_config(argv=None) -> Config:
    load_dotenv()  # so .env (Db2 + watsonx credentials) populates the defaults below
    p = argparse.ArgumentParser(description="Hybrid-search ingestion pipeline for Db2 12.1")
    # input / output
    p.add_argument("--pdf", default=env("PDF_PATH"), help="source PDF path (env PDF_PATH)")
    p.add_argument("--markdown-out", default=env("MARKDOWN_PATH"),
                   help="markdown output path (env MARKDOWN_PATH; default: <pdf>.md)")
    # Db2 connection
    p.add_argument("--db-host", default=env("DB2_HOST", "localhost"), help="env DB2_HOST")
    p.add_argument("--db-port", default=env("DB2_PORT", "50000"), help="env DB2_PORT")
    p.add_argument("--db-name", default=env("DB2_DATABASE", "sample"), help="env DB2_DATABASE")
    p.add_argument("--db-user", default=env("DB2_USER", "db2inst1"), help="env DB2_USER")
    p.add_argument("--db-password", default=env("DB2_PASSWORD"), help="env DB2_PASSWORD")
    # schema / object names
    p.add_argument("--schema", default=env("DB2_SCHEMA", "myschema"), help="env DB2_SCHEMA")
    p.add_argument("--table", default=env("DB2_TABLE", "chunks"), help="env DB2_TABLE")
    p.add_argument("--index-name", default=env("DB2_INDEX_NAME"),
                   help="text index name (env DB2_INDEX_NAME; default: <table>_text_idx)")
    p.add_argument("--vector-column", default=env("DB2_VECTOR_COLUMN", "embedding"),
                   help="env DB2_VECTOR_COLUMN")
    p.add_argument("--model-name", default=env("DB2_MODEL_NAME"),
                   help="Db2 external-model name (env DB2_MODEL_NAME; default: <schema>.<table>_embed)")
    # chunking / vector
    p.add_argument("--tokenizer-model", default=env("TOKENIZER_MODEL", DEFAULT_TOKENIZER_MODEL),
                   help="HF tokenizer id for chunk sizing (env TOKENIZER_MODEL)")
    p.add_argument("--max-tokens", type=int, default=int(env("MAX_TOKENS", DEFAULT_MAX_TOKENS)),
                   help="HybridChunker token cap (env MAX_TOKENS)")
    p.add_argument("--vector-dim", type=int, default=env("VECTOR_DIM"),
                   help="vector dimension (env VECTOR_DIM; default: derived from --wx-model-id)")
    p.add_argument("--coord-type", default=env("VECTOR_COORD_TYPE", DEFAULT_COORD_TYPE),
                   help="VECTOR coordinate type REAL|FLOAT32|INT8 (env VECTOR_COORD_TYPE)")
    # watsonx.ai
    p.add_argument("--wx-url", default=env("WATSONX_URL", DEFAULT_WX_URL), help="env WATSONX_URL")
    p.add_argument("--wx-apikey", default=env("WATSONX_APIKEY"), help="env WATSONX_APIKEY")
    p.add_argument("--wx-project-id", default=env("WATSONX_PROJECT_ID"), help="env WATSONX_PROJECT_ID")
    p.add_argument("--wx-model-id", default=env("WATSONX_MODEL_ID", DEFAULT_WX_MODEL_ID),
                   help="watsonx embedding model id (env WATSONX_MODEL_ID)")
    # misc
    p.add_argument("--opensearch-port", default=env("OPENSEARCH_PORT", "9200"),
                   help="port used to find the registered OpenSearch server (env OPENSEARCH_PORT)")
    p.add_argument("--instance-owner", default=env("DB2_INSTANCE_OWNER", "db2inst1"),
                   help="OS user that owns the Db2 instance, for db2ts (env DB2_INSTANCE_OWNER)")
    p.add_argument("--skip-embedding", action="store_true",
                   default=env("SKIP_EMBEDDING", "").lower() in ("1", "true", "yes"),
                   help="skip steps 5-6 (watsonx model + vectors); lexical-only ingest")
    a = p.parse_args(argv)

    if not a.pdf:
        p.error("a source PDF is required (--pdf or PDF_PATH)")
    if not a.db_password:
        p.error("Db2 password is required (--db-password or DB2_PASSWORD)")

    markdown_out = a.markdown_out or (os.path.splitext(a.pdf)[0] + ".md")
    index_name = a.index_name or f"{a.table}_text_idx"
    model_name = a.model_name or f"{a.schema}.{a.table}_embed"
    vector_dim = int(a.vector_dim) if a.vector_dim else WATSONX_EMBED_DIMS.get(a.wx_model_id)
    if not vector_dim:
        p.error(f"could not derive vector dimension for model {a.wx_model_id!r}; pass --vector-dim")

    # validate identifiers up front
    ident(a.schema, "schema"); ident(a.table, "table")
    ident(index_name, "index name"); ident(a.vector_column, "vector column")
    for part in model_name.split("."):
        ident(part, "model name part")

    return Config(
        pdf=a.pdf, markdown_out=markdown_out,
        db_host=a.db_host, db_port=a.db_port, db_name=a.db_name,
        db_user=a.db_user, db_password=a.db_password,
        schema=a.schema, table=a.table, index_name=index_name,
        vector_column=a.vector_column, model_name=model_name,
        tokenizer_model=a.tokenizer_model, max_tokens=a.max_tokens,
        vector_dim=vector_dim, coord_type=a.coord_type,
        wx_url=a.wx_url, wx_apikey=a.wx_apikey, wx_project_id=a.wx_project_id,
        wx_model_id=a.wx_model_id, opensearch_port=a.opensearch_port,
        instance_owner=a.instance_owner, skip_embedding=a.skip_embedding,
    )


# --- Db2 helpers ------------------------------------------------------------
def connect(cfg: Config):
    dsn = (f"DATABASE={cfg.db_name};HOSTNAME={cfg.db_host};PORT={cfg.db_port};"
           f"PROTOCOL=TCPIP;UID={cfg.db_user};PWD={cfg.db_password};")
    return ibm_db.connect(dsn, "", "")

def exec_sql(conn, sql):
    return ibm_db.exec_immediate(conn, sql)

def run_db2ts(cfg: Config, ts_cmd: str, ignore=()):
    """Run a single db2ts command as the instance owner. Returns combined output;
    raises unless the output contains one of `ignore` (for idempotent no-ops)."""
    inner = f'export DB2DBDFT={cfg.db_name}; db2ts "{ts_cmd}"'
    if getpass.getuser() == cfg.instance_owner:
        argv = ["bash", "-lc", inner]
    else:
        argv = ["sudo", "-niu", cfg.instance_owner, "bash", "-lc", inner]
    r = subprocess.run(argv, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 and not any(s in out for s in ignore):
        raise RuntimeError(f"db2ts failed: {ts_cmd}\n{out.strip()}")
    return out


# --- pipeline steps ---------------------------------------------------------
def step_extract(cfg: Config) -> str:
    log("1/extract", f"Docling: {cfg.pdf} -> {cfg.markdown_out}")
    if not os.path.exists(cfg.pdf):
        sys.exit(f"ERROR: PDF not found: {cfg.pdf}")
    document = DocumentConverter().convert(cfg.pdf).document
    markdown = document.export_to_markdown()
    with open(cfg.markdown_out, "w") as f:
        f.write(markdown)
    log("1/extract", f"wrote {len(markdown):,} characters")
    return cfg.markdown_out

def step_chunk(cfg: Config, markdown_path: str):
    log("2/chunk", f"HybridChunker (max_tokens={cfg.max_tokens}, tokenizer={cfg.tokenizer_model})")
    document = DocumentConverter().convert(markdown_path).document
    chunker = HybridChunker(tokenizer=HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(cfg.tokenizer_model),
        max_tokens=cfg.max_tokens))
    chunks = [chunker.contextualize(chunk=c) for c in chunker.chunk(dl_doc=document)]
    log("2/chunk", f"produced {len(chunks)} chunks")
    return chunks

def step_load(cfg: Config, conn, chunks):
    tbl = qualified(cfg.schema, cfg.table)
    log("3/load", f"CREATE TABLE {tbl} + insert {len(chunks)} chunks")
    try:
        exec_sql(conn, f"""
            CREATE TABLE {tbl} (
                chunk_id   INTEGER NOT NULL PRIMARY KEY,
                chunk_text CLOB(1M) NOT NULL
            )""")
    except Exception as e:
        sys.exit(f"ERROR creating {tbl}: {e}\nRun cleanup.sh first for a clean slate.")
    # Parameterized insert — row data never built into SQL text.
    stmt = ibm_db.prepare(conn, f"INSERT INTO {tbl} (chunk_id, chunk_text) VALUES (?, ?)")
    for chunk_id, text in enumerate(chunks, start=1):
        ibm_db.bind_param(stmt, 1, chunk_id)
        ibm_db.bind_param(stmt, 2, text)
        ibm_db.execute(stmt)
    log("3/load", f"inserted {len(chunks)} rows (chunk_id 1..{len(chunks)})")

def step_text_index(cfg: Config, conn):
    tbl = qualified(cfg.schema, cfg.table)
    idx = qualified(cfg.schema, cfg.index_name)
    log("4/lexical", f"text-search index {idx} over {tbl}({cfg.vector_column!r} excluded)")

    # Find the registered OpenSearch server (parameterized lookup).
    stmt = ibm_db.prepare(conn,
        "SELECT SERVERID FROM SYSIBMTS.SYSTSSERVERS "
        "WHERE SERVERPORT = ? AND ENGINETYPE = 'OPENSEARCH' FETCH FIRST 1 ROW ONLY")
    ibm_db.bind_param(stmt, 1, int(cfg.opensearch_port))
    ibm_db.execute(stmt)
    row = ibm_db.fetch_assoc(stmt)
    if not row:
        sys.exit(f"ERROR: no OpenSearch server registered on port {cfg.opensearch_port}. "
                 "Register it once (Db2 Text Search + OpenSearch) before ingesting.")
    server_id = int(row["SERVERID"])
    log("4/lexical", f"using OpenSearch server id {server_id}")

    # db2ts: enable (idempotent) -> create INACTIVE -> activate -> populate.
    run_db2ts(cfg, f"ENABLE DATABASE FOR TEXT CONNECT TO {cfg.db_name}",
              ignore=("CIE00322", "already enabled"))
    run_db2ts(cfg, f"CREATE INDEX {idx} FOR TEXT ON {tbl}(chunk_text) "
                   f"SERVERID {server_id} INACTIVE")
    run_db2ts(cfg, f"ALTER INDEX {idx} FOR TEXT SET ACTIVE")
    run_db2ts(cfg, f"UPDATE INDEX {idx} FOR TEXT")
    log("4/lexical", "index created, activated, and populated")

def step_register_model(cfg: Config, conn):
    log("5/model", f"CREATE EXTERNAL MODEL {cfg.model_name} (watsonx {cfg.wx_model_id})")
    if not (cfg.wx_apikey and cfg.wx_project_id):
        sys.exit("ERROR: watsonx credentials missing (--wx-apikey / --wx-project-id). "
                 "Use --skip-embedding for a lexical-only ingest.")
    # DDL: identifiers validated; string constants escaped (markers not allowed in DDL).
    ddl = (f"CREATE EXTERNAL MODEL {cfg.model_name} PROVIDER WATSONX "
           f"ID {sql_str(cfg.wx_model_id)} "
           f"URL {sql_str(cfg.wx_url)} "
           f"TYPE TEXT_EMBEDDING RETURNING VECTOR({cfg.vector_dim}, {cfg.coord_type}) "
           f"KEY {sql_str(cfg.wx_apikey)} "
           f"PROJECT_ID {sql_str(cfg.wx_project_id)}")
    # Drop any prior registration first, so config changes (URL, key, model id,
    # dimension) always take effect on a re-run.
    try:
        exec_sql(conn, f"DROP EXTERNAL MODEL {cfg.model_name}")
    except Exception:
        pass
    exec_sql(conn, ddl)
    log("5/model", "model registered")

def step_vector_embed(cfg: Config, conn):
    tbl = qualified(cfg.schema, cfg.table)
    col = ident(cfg.vector_column, "vector column")
    log("6/vector", f"ADD COLUMN {col} VECTOR({cfg.vector_dim}, {cfg.coord_type}) + embed")
    try:
        exec_sql(conn, f"ALTER TABLE {tbl} ADD COLUMN {col} VECTOR({cfg.vector_dim}, {cfg.coord_type})")
    except Exception as e:
        if "SQLSTATE=42711" in str(e):  # column already exists
            log("6/vector", "vector column already present — reusing")
        else:
            raise
    # In-database embedding: Db2 calls watsonx via the registered model, set-based.
    log("6/vector", "running TO_EMBEDDING over chunk_text (in-database)…")
    exec_sql(conn, f"UPDATE {tbl} SET {col} = TO_EMBEDDING(chunk_text USING {cfg.model_name})")
    log("6/vector", "vectors stored")


def main():
    cfg = build_config()
    print(f"== ingest: {cfg.pdf} -> {cfg.schema}.{cfg.table} "
          f"(dim {cfg.vector_dim}, {'lexical-only' if cfg.skip_embedding else 'lexical + vector'}) ==")

    md = step_extract(cfg)
    chunks = step_chunk(cfg, md)

    conn = connect(cfg)
    try:
        step_load(cfg, conn, chunks)
        step_text_index(cfg, conn)            # MUST precede the vector column
        if cfg.skip_embedding:
            log("5-6", "skipped (watsonx model + vectors) — lexical-only ingest")
        else:
            step_register_model(cfg, conn)
            step_vector_embed(cfg, conn)
    finally:
        ibm_db.close(conn)

    print("== done ==")


if __name__ == "__main__":
    main()
