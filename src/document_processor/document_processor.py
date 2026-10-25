"""
Lambda function that ingests documents from the SQS ingestion queue.
Each message is an S3 object-created event (uploads) or a connector message
(SharePoint / Confluence sync jobs). PDFs go through layout-aware analysis
(pdf_analysis.py: tables via Textract, charts described by a vision model); other
files are read as text. The result is chunked, embedded in batches with Amazon
Bedrock and upserted into Aurora PostgreSQL together with its access-control list. Failed messages are reported individually so SQS retries
only those and moves them to the DLQ after the max receive count.
"""
import os
import json
import boto3
import logging
import tempfile
import psycopg2
import uuid
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional

from psycopg2.extras import execute_values

# Import LangChain components
from langchain_community.document_loaders import TextLoader, CSVLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document

try:  # package import (tests) / flat module (Lambda)
    from .pdf_analysis import analyze_pdf, split_table
except ImportError:
    from pdf_analysis import analyze_pdf, split_table

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
secretsmanager = boto3.client('secretsmanager')
bedrock_runtime = boto3.client('bedrock-runtime')
textract = boto3.client('textract')

# Get environment variables
DOCUMENTS_BUCKET = os.environ.get('DOCUMENTS_BUCKET')
METADATA_TABLE = os.environ.get('METADATA_TABLE')
STAGE = os.environ.get('STAGE')
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
DB_SECRET_ARN = os.environ.get('DB_SECRET_ARN')
EMBEDDING_MODEL_ID = os.environ.get('EMBEDDING_MODEL_ID', 'amazon.titan-embed-text-v2:0')
EMBEDDING_DIMENSIONS = int(os.environ.get('EMBEDDING_DIMENSIONS', '1024'))
EMBEDDING_BATCH_SIZE = int(os.environ.get('EMBEDDING_BATCH_SIZE', '16'))
CHUNK_SIZE = int(os.environ.get('CHUNK_SIZE', '1000'))
CHUNK_OVERLAP = int(os.environ.get('CHUNK_OVERLAP', '200'))
# PDF analysis: "triage" (only pages with tables, figures or scans), "full" (Textract on every page), "off"
DOCUMENT_ANALYSIS = os.environ.get('DOCUMENT_ANALYSIS', 'triage')
VISION_MODEL_ID = os.environ.get('VISION_MODEL_ID', 'us.openai.gpt-6-luna')
MAX_FIGURES_PER_DOCUMENT = int(os.environ.get('MAX_FIGURES_PER_DOCUMENT', '30'))
TABLE_CHUNK_MAX_CHARS = int(os.environ.get('TABLE_CHUNK_MAX_CHARS', '4000'))

MIME_TYPES = {
    'pdf': 'application/pdf',
    'txt': 'text/plain',
    'md': 'text/markdown',
    'csv': 'text/csv',
}

_db_credentials = None


def get_postgres_credentials():
    """
    Get the application database credentials from Secrets Manager (cached per container).
    """
    global _db_credentials
    if _db_credentials is None:
        try:
            secret_response = secretsmanager.get_secret_value(SecretId=DB_SECRET_ARN)
            _db_credentials = json.loads(secret_response['SecretString'])
        except Exception as e:
            logger.error(f"Error getting PostgreSQL credentials: {str(e)}")
            raise e
    return _db_credentials


def get_postgres_connection(credentials):
    """
    Get a connection to PostgreSQL (through RDS Proxy in prod).
    """
    return psycopg2.connect(
        host=DB_HOST,
        port=5432,
        user=credentials['username'],
        password=credentials['password'],
        dbname=DB_NAME,
        sslmode='require',
        connect_timeout=30
    )


# =========================
# Embeddings (Amazon Bedrock)
# =========================

def _embed_titan(text: str) -> List[float]:
    response = bedrock_runtime.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        contentType='application/json',
        accept='application/json',
        body=json.dumps({
            'inputText': text,
            'dimensions': EMBEDDING_DIMENSIONS,
            'normalize': True
        })
    )
    return json.loads(response['body'].read())['embedding']


def _embed_cohere_batch(texts: List[str]) -> List[List[float]]:
    response = bedrock_runtime.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        contentType='application/json',
        accept='application/json',
        body=json.dumps({
            'texts': texts,
            'input_type': 'search_document',
            'embedding_types': ['float'],
            'output_dimension': EMBEDDING_DIMENSIONS
        })
    )
    return json.loads(response['body'].read())['embeddings']['float']


def embed_documents(texts: List[str]) -> List[List[float]]:
    """
    Embed chunk texts in batches. Cohere models accept a batch per request; Titan
    takes one text per request, so a batch is sent as concurrent requests.
    Errors propagate: storing placeholder vectors would silently break retrieval.
    """
    embeddings: List[List[float]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[start:start + EMBEDDING_BATCH_SIZE]
        if EMBEDDING_MODEL_ID.startswith('cohere.'):
            embeddings.extend(_embed_cohere_batch(batch))
        else:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                embeddings.extend(pool.map(_embed_titan, batch))
    return embeddings


def to_vector_literal(embedding: List[float]) -> str:
    return '[' + ','.join(str(x) for x in embedding) + ']'


# =========================
# Loading and chunking
# =========================

def get_document_loader(file_path, mime_type):
    """
    Get the LangChain loader for non-PDF files (PDFs go through analyze_pdf).

    Args:
        file_path (str): Path to the file
        mime_type (str): MIME type of the file

    Returns:
        LangChain document loader
    """
    if mime_type in ['text/csv', 'application/csv']:
        return CSVLoader(file_path)
    # Plain text, markdown and unknown types
    return TextLoader(file_path, autodetect_encoding=True)


def chunk_documents(documents: List[Document]) -> List[Document]:
    """
    Split text documents into chunks.

    Args:
        documents (List[Document]): List of LangChain documents

    Returns:
        List[Document]: List of chunked documents
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=["\n\n", "\n", " ", ""]
    )
    return [c for c in text_splitter.split_documents(documents) if c.page_content.strip()]


def build_chunks(file_path: str, file_name: str, mime_type: str) -> List[Document]:
    """
    Turn a file into chunks. Each chunk's metadata carries its page and element
    type (text / table / figure). Tables are kept whole (or split by rows with the
    header repeated) so their structure survives embedding.
    """
    if mime_type != 'application/pdf':
        documents = get_document_loader(file_path, mime_type).load()
        for document in documents:
            document.metadata["element_type"] = "text"
        return chunk_documents(documents)

    elements = analyze_pdf(
        file_path, file_name,
        textract=textract,
        bedrock_runtime=bedrock_runtime,
        vision_model_id=VISION_MODEL_ID,
        mode=DOCUMENT_ANALYSIS,
        max_figures=MAX_FIGURES_PER_DOCUMENT,
    )

    chunks: List[Document] = []
    text_documents = []
    for element in elements:
        metadata = {"page": element.page, "element_type": element.kind}
        if element.kind == "text":
            text_documents.append(Document(page_content=element.content, metadata=metadata))
        elif element.kind == "table":
            for part in split_table(element.content, TABLE_CHUNK_MAX_CHARS):
                chunks.append(Document(
                    page_content=f"Table from {file_name}, page {element.page + 1}:\n{part}",
                    metadata=metadata))
        else:
            chunks.append(Document(
                page_content=f"Figure from {file_name}, page {element.page + 1}: {element.content}",
                metadata=metadata))

    counts = {kind: sum(1 for e in elements if e.kind == kind) for kind in ("text", "table", "figure")}
    logger.info(f"{file_name}: analysed {counts} (mode={DOCUMENT_ANALYSIS})")

    # Keep page order: text chunks of a page come before its tables and figures
    all_chunks = chunk_documents(text_documents) + chunks
    return sorted(all_chunks, key=lambda c: (c.metadata["page"], c.metadata["element_type"] != "text"))


def get_mime_type(file_name: str) -> str:
    return MIME_TYPES.get(file_name.rsplit('.', 1)[-1].lower(), 'application/octet-stream')


# =========================
# Message parsing
# =========================

def parse_acl(owner_id: str, acl_value: Optional[str]) -> List[str]:
    """
    Build the principals that may read a document. The owner can always read it;
    extra principals come as comma-separated 'group:<name>' / 'user:<sub>' values.
    """
    principals = {f"user:{owner_id}"}
    for item in (acl_value or '').split(','):
        item = item.strip()
        if item.startswith('group:') or item.startswith('user:'):
            principals.add(item)
    return sorted(principals)


def jobs_from_message(body: Dict) -> List[Dict]:
    """
    Turn one SQS message body into ingestion jobs.

    S3 event:          {"Records": [{"s3": {"bucket": {...}, "object": {"key": ...}}}]}
    Connector message: {"bucket", "key", "document_id", "owner_id", "acl": [...], "source"}
    """
    if body.get('Event') == 's3:TestEvent':
        return []

    jobs = []
    if 'Records' in body:
        for record in body['Records']:
            bucket = record['s3']['bucket']['name']
            # S3 event keys are URL-encoded
            key = urllib.parse.unquote_plus(record['s3']['object']['key'])

            # Format: uploads/{user_id}/{document_id}/{file_name}
            parts = key.split('/')
            if len(parts) < 4:
                logger.warning(f"Skipping object with unexpected key format: {key}")
                continue
            jobs.append({
                'bucket': bucket,
                'key': key,
                'owner_id': parts[1],
                'document_id': parts[2],
                'file_name': parts[-1],
                'acl': None,  # read from object metadata
                'source': 's3',
            })
    elif 'key' in body:
        file_name = body['key'].split('/')[-1]
        jobs.append({
            'bucket': body.get('bucket', DOCUMENTS_BUCKET),
            'key': body['key'],
            'owner_id': body.get('owner_id', 'system'),
            'document_id': body.get('document_id') or str(uuid.uuid5(uuid.NAMESPACE_URL, body['key'])),
            'file_name': body.get('file_name', file_name),
            'acl': ','.join(body.get('acl', [])),
            'source': body.get('source', 'connector'),
        })
    else:
        raise ValueError(f"Unrecognised ingestion message: {json.dumps(body)[:500]}")
    return jobs


# =========================
# Processing
# =========================

def process_document(job: Dict) -> int:
    """
    Load, chunk, embed and upsert one document. Re-processing the same document
    replaces its chunks and ACL atomically, so retries are idempotent.

    Returns:
        int: Number of chunks stored
    """
    bucket, key = job['bucket'], job['key']
    head = s3_client.head_object(Bucket=bucket, Key=key)
    acl_value = job['acl'] if job['acl'] is not None else head.get('Metadata', {}).get('acl')
    principals = parse_acl(job['owner_id'], acl_value)
    mime_type = get_mime_type(job['file_name'])

    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        file_path = temp_file.name
    try:
        logger.info(f"Downloading s3://{bucket}/{key}")
        s3_client.download_file(bucket, key, file_path)

        chunks = build_chunks(file_path, job['file_name'], mime_type)
        logger.info(f"Created {len(chunks)} chunks for {job['document_id']}")

        embeddings = embed_documents([c.page_content for c in chunks])
    finally:
        try:
            os.unlink(file_path)
        except OSError as e:
            logger.warning(f"Error cleaning up temporary file {file_path}: {str(e)}")

    now = datetime.now(timezone.utc)
    conn = get_postgres_connection(get_postgres_credentials())
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                INSERT INTO documents (document_id, owner_id, file_name, mime_type, status, source, bucket, key, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'processed', %s, %s, %s, %s, %s)
                ON CONFLICT (document_id) DO UPDATE SET
                    file_name = EXCLUDED.file_name,
                    mime_type = EXCLUDED.mime_type,
                    status = 'processed',
                    key = EXCLUDED.key,
                    updated_at = EXCLUDED.updated_at
                """, (job['document_id'], job['owner_id'], job['file_name'], mime_type,
                      job['source'], bucket, key, now, now))

                cursor.execute("DELETE FROM chunks WHERE document_id = %s", (job['document_id'],))
                cursor.execute("DELETE FROM document_acl WHERE document_id = %s", (job['document_id'],))

                execute_values(
                    cursor,
                    "INSERT INTO document_acl (document_id, principal) VALUES %s",
                    [(job['document_id'], p) for p in principals]
                )

                rows = [
                    (
                        str(uuid.uuid4()),
                        job['document_id'],
                        index,
                        chunk.page_content,
                        json.dumps({
                            "source": key,
                            "page": chunk.metadata.get("page", 0),
                            "element_type": chunk.metadata.get("element_type", "text"),
                        }),
                        to_vector_literal(embedding),
                    )
                    for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))
                ]
                if rows:
                    execute_values(
                        cursor,
                        """
                        INSERT INTO chunks (chunk_id, document_id, chunk_index, content, metadata, embedding)
                        VALUES %s
                        """,
                        rows,
                        template="(%s, %s, %s, %s, %s, %s::vector)",
                        page_size=200
                    )
    finally:
        conn.close()

    dynamodb.Table(METADATA_TABLE).put_item(
        Item={
            'id': f"doc#{job['document_id']}",
            'document_id': job['document_id'],
            'user_id': job['owner_id'],
            'file_name': job['file_name'],
            'status': 'processed',
            'bucket': bucket,
            'key': key,
            'acl': principals,
            'num_chunks': len(chunks),
            'num_tables': sum(1 for c in chunks if c.metadata.get('element_type') == 'table'),
            'num_figures': sum(1 for c in chunks if c.metadata.get('element_type') == 'figure'),
            'embedding_model': EMBEDDING_MODEL_ID,
            'updated_at': int(now.timestamp() * 1000)
        }
    )
    return len(chunks)


def handler(event, context):
    """
    SQS-triggered handler. Returns partial batch failures so only failed
    messages are retried (and dead-lettered after the max receive count).
    """
    if isinstance(event, dict) and event.get('action') == 'healthcheck':
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Document processor is healthy',
                'stage': STAGE
            })
        }

    failures = []
    for record in event.get('Records', []):
        message_id = record.get('messageId')
        try:
            body = json.loads(record['body'])
            for job in jobs_from_message(body):
                num_chunks = process_document(job)
                logger.info(json.dumps({
                    'event': 'document_ingested',
                    'document_id': job['document_id'],
                    'num_chunks': num_chunks,
                    'source': job['source']
                }))
        except Exception as e:
            logger.error(f"Failed to process message {message_id}: {str(e)}", exc_info=True)
            failures.append({'itemIdentifier': message_id})

    return {'batchItemFailures': failures}
