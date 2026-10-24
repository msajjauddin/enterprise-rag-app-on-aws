"""
Lambda function to initialize the Aurora PostgreSQL database: pgvector extension,
schema (documents, chunks, document_acl), HNSW + full-text indexes, and the
least-privileged application role. Idempotent; CI invokes it after every apply.
"""
import os
import json
import boto3
import logging
import psycopg2
import time
import socket
from psycopg2 import sql

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
secretsmanager = boto3.client('secretsmanager')

# Get environment variables
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_MASTER_SECRET_ARN = os.environ.get('DB_MASTER_SECRET_ARN')
DB_APP_SECRET_ARN = os.environ.get('DB_APP_SECRET_ARN')
STAGE = os.environ.get('STAGE')
EMBEDDING_DIMENSIONS = int(os.environ.get('EMBEDDING_DIMENSIONS', '1024'))
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', '5'))
RETRY_DELAY = int(os.environ.get('RETRY_DELAY', '10'))  # seconds


def get_secret(secret_arn):
    """
    Get a JSON secret from Secrets Manager.
    """
    try:
        secret_response = secretsmanager.get_secret_value(SecretId=secret_arn)
        return json.loads(secret_response['SecretString'])
    except Exception as e:
        logger.error(f"Error getting secret: {str(e)}")
        raise e


def check_dns_resolution(host):
    """
    Check if hostname can be resolved to an IP address.

    Args:
        host (str): Hostname to resolve

    Returns:
        bool: True if hostname can be resolved, False otherwise
    """
    try:
        socket.gethostbyname(host)
        return True
    except socket.gaierror:
        return False


def connect_with_retry(credentials, retry_count=0):
    """
    Connect to the writer as the master user. Retries cover a Serverless v2
    instance resuming from auto-pause and DNS for a freshly created cluster.
    """
    if not check_dns_resolution(DB_HOST):
        if retry_count < MAX_RETRIES:
            logger.warning(f"Could not resolve hostname '{DB_HOST}'. Retrying in {RETRY_DELAY}s ({retry_count + 1}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)
            return connect_with_retry(credentials, retry_count + 1)
        raise RuntimeError(f"Could not resolve hostname '{DB_HOST}' after {MAX_RETRIES} attempts.")

    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=5432,
            user=credentials['username'],
            password=credentials['password'],
            dbname=DB_NAME,
            sslmode='require',
            connect_timeout=30
        )
        conn.autocommit = True
        return conn
    except psycopg2.OperationalError as e:
        if retry_count < MAX_RETRIES:
            logger.warning(f"Database connection error: {str(e)}. Retrying in {RETRY_DELAY}s ({retry_count + 1}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)
            return connect_with_retry(credentials, retry_count + 1)
        raise


def create_schema(cursor, dimensions):
    """
    Create the pgvector extension, tables and indexes.
    """
    logger.info("Creating pgvector extension...")
    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")

    logger.info("Creating documents table...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        document_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        file_name TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        status TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 's3',
        bucket TEXT NOT NULL,
        key TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    # Who may read a document: 'user:<sub>' or 'group:<name>' principals
    logger.info("Creating document_acl table...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS document_acl (
        document_id TEXT NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
        principal TEXT NOT NULL,
        PRIMARY KEY (document_id, principal)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_document_acl_principal ON document_acl (principal)")

    logger.info("Creating chunks table...")
    cursor.execute(sql.SQL("""
    CREATE TABLE IF NOT EXISTS chunks (
        id BIGSERIAL PRIMARY KEY,
        chunk_id TEXT NOT NULL UNIQUE,
        document_id TEXT NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
        chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL,
        metadata JSONB,
        embedding VECTOR({dims}) NOT NULL,
        content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """).format(dims=sql.Literal(dimensions)))

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks (document_id)")

    # Full-text side of hybrid search
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_content_tsv ON chunks USING gin (content_tsv)")

    # Vector side of hybrid search. HNSW needs no training data, unlike IVFFlat.
    logger.info("Creating HNSW index on embeddings...")
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    """)

    # Guard against an embedding model change without re-ingestion
    cursor.execute("""
    SELECT atttypmod FROM pg_attribute
    WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'
    """)
    row = cursor.fetchone()
    if row and row[0] != dimensions:
        raise RuntimeError(
            f"chunks.embedding has {row[0]} dimensions but EMBEDDING_DIMENSIONS is {dimensions}. "
            "Drop and re-create the chunks table, then re-ingest all documents."
        )


def ensure_app_role(cursor, app_credentials):
    """
    Create or update the application role and grant it data access only.
    """
    username = app_credentials['username']
    password = app_credentials['password']

    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (username,))
    if cursor.fetchone():
        logger.info(f"Updating role '{username}'")
        cursor.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(username), sql.Literal(password)))
    else:
        logger.info(f"Creating role '{username}'")
        cursor.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(username), sql.Literal(password)))

    role = sql.Identifier(username)
    cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(DB_NAME), role))
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    cursor.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(role))
    cursor.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role))
    cursor.execute(sql.SQL(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(role))
    cursor.execute(sql.SQL(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(role))

    # HNSW search settings as role defaults: ACL filtering discards candidates, so search
    # wider and let pgvector (0.8+) keep scanning until enough rows pass the filter.
    # Role defaults avoid per-session SET statements, which would pin RDS Proxy connections.
    for setting, value in (("hnsw.ef_search", "100"), ("hnsw.iterative_scan", "relaxed_order")):
        try:
            cursor.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(
                role, sql.SQL(setting), sql.Literal(value)))
        except psycopg2.Error as e:
            logger.warning(f"Could not set {setting} (pgvector version too old?): {e}")


def handler(event, context):
    """
    Lambda function handler to initialize the database.
    """
    logger.info(f"Starting database initialization for stage: {STAGE}")

    if isinstance(event, dict) and event.get('action') == 'healthcheck':
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'DB initialization function is healthy',
                'stage': STAGE
            })
        }

    conn = None
    try:
        master_credentials = get_secret(DB_MASTER_SECRET_ARN)
        app_credentials = get_secret(DB_APP_SECRET_ARN)

        logger.info(f"Connecting to database '{DB_NAME}' at {DB_HOST}")
        conn = connect_with_retry(master_credentials)
        cursor = conn.cursor()

        create_schema(cursor, EMBEDDING_DIMENSIONS)
        ensure_app_role(cursor, app_credentials)
        cursor.close()

        logger.info("Database initialization completed successfully")
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Database initialization completed successfully'
            })
        }

    except Exception as e:
        logger.error(f"Error in database initialization: {str(e)}")
        # Raise so the invocation reports FunctionError and CI fails loudly
        raise
    finally:
        if conn is not None:
            conn.close()
