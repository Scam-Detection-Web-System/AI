import boto3
import json
import PyPDF2
import re
import os
from dotenv import load_dotenv

# CONFIG 
load_dotenv()
REGION = os.getenv("REGION")
VECTOR_BUCKET = os.getenv("VECTOR_BUCKET")
INDEX_NAME = os.getenv("INDEX_NAME")
EMBED_MODEL_ID = os.getenv("EMBEDDING_MODEL")
FILE_PATH = os.getenv("PDF_PATH")

bedrock = boto3.client('bedrock-runtime', region_name=REGION)
s3v = boto3.client('s3vectors', region_name=REGION)

# get embedding vector 
def get_embedding(text):
    if len(text) > 2000:
        text = text[:2000]
        
    body = json.dumps({
        "texts": [text],
        "input_type": "search_document"
    })
    
    response = bedrock.invoke_model(
        body=body,
        modelId=EMBED_MODEL_ID,
        contentType="application/json",
        accept="application/json"
    )
    
    response_body = json.loads(response.get('body').read())
    return response_body['embeddings'][0]

# Process PDF, chunk text, get embedding and upload to S3 Vector
def process_and_upload():
    reader = PyPDF2.PdfReader(FILE_PATH)
    full_content = ""
    for page in reader.pages:
        t = page.extract_text()
        if t: full_content += t + "\n"

    articles = re.split(r'(?=Điều\s+\d+\.)', full_content)
    current_chapter = "Chương I"
    processed_chunks = []

    for art in articles:
        chapter_match = re.search(r'(Chương\s+[IVXLCDM]+)', art)
        if chapter_match:
            current_chapter = chapter_match.group(1)
        
        clean_art = re.sub(r'--- PAGE \d+ ---', '', art).strip()
        
        # Nếu sau khi chia theo Khoản vẫn dài > 2000, sẽ cắt cứng (Truncate)
        if len(clean_art) > 1800:
            sub_sections = re.split(r'(?=\n\d+\.\s)', clean_art)
            header_info = clean_art.split('\n')[0] 
            for sub in sub_sections:
                if len(sub.strip()) < 10: continue
                chunk_text = f"Luật ANM 2025 - {current_chapter} - {header_info}\n{sub.strip()}"
                # max 2000 chars
                processed_chunks.append(chunk_text[:2000])
        else:
            if len(clean_art) >= 20:
                chunk_text = f"Luật ANM 2025 - {current_chapter}\n{clean_art}"
                processed_chunks.append(chunk_text[:2000])

    print(f"Tổng số chunks: {len(processed_chunks)}.")

    batch = []
    for i, text in enumerate(processed_chunks):
        print(f"Chunk {i+1}/{len(processed_chunks)} (Độ dài: {len(text)})")
        
        # get vector
        vector = get_embedding(text)
        
        # Metadata for S3 vector index
        safe_metadata = text.encode('utf-8')[:2000].decode('utf-8', 'ignore')

        batch.append({
            "key": f"anm-2025-v{i}",
            "data": {"float32": vector},
            "metadata": {"text": safe_metadata}
        })

        if len(batch) >= 10:
            s3v.put_vectors(vectorBucketName=VECTOR_BUCKET, indexName=INDEX_NAME, vectors=batch)
            batch = []

    if batch:
        s3v.put_vectors(vectorBucketName=VECTOR_BUCKET, indexName=INDEX_NAME, vectors=batch)

    print("Done")

if __name__ == "__main__":
    process_and_upload()