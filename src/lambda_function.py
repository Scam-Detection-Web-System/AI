import json
import boto3
import urllib3
import os

# --- CẤU HÌNH ---
REGION = os.getenv("REGION")
VECTOR_BUCKET = os.getenv("VECTOR_BUCKET")
INDEX_NAME = os.getenv("INDEX_NAME")
EMBED_MODEL_ID = os.getenv("EMBEDDING_MODEL")
FILE_PATH = os.getenv("PDF_PATH")

# Khởi tạo client (Nên để ngoài lambda_handler để tận dụng Warm Start)
bedrock = boto3.client('bedrock-runtime', region_name=REGION)
s3v = boto3.client('s3vectors', region_name=REGION)
http = urllib3.PoolManager()

def get_query_embedding(text):
    """Biến câu hỏi người dùng thành vector dùng Cohere Multilingual v3"""
    body = json.dumps({
        "texts": [text],
        "input_type": "search_query"
    })
    response = bedrock.invoke_model(
        body=body,
        modelId="cohere.embed-multilingual-v3",
        contentType="application/json"
    )
    # Lấy list vector số thực
    return json.loads(response.get('body').read())['embeddings'][0]

def search_law(vector):
    """Truy vấn 3 đoạn luật khớp nhất từ S3 Vector Bucket"""
    try:
        # Đổi từ search_vectors sang search_index theo tài liệu S3 Vector Buckets
        response = s3v.search_index(
            bucketName=VECTOR_BUCKET,
            indexName=INDEX_NAME,
            vector=vector,  # Một số phiên bản yêu cầu {"float32": vector} nếu lỗi định dạng
            topK=3
        )
        
        context = ""
        hits = response.get('hits', [])
        if not hits:
            return "Không tìm thấy tài liệu liên quan."
            
        for hit in hits:
            # Lấy text từ metadata bạn đã đánh chỉ mục (Index)
            text_part = hit.get('metadata', {}).get('text', 'Nội dung trống')
            context += f"{text_part}\n---\n"
        return context
    except Exception as e:
        print(f"Lỗi truy vấn S3 Vector: {str(e)}")
        return ""

def ask_claude(query, context):
    """Dùng Claude 3 Haiku để trả lời dựa trên luật đã tìm được"""
    # Cấu trúc Prompt tối ưu cho RAG
    prompt = f"Dựa vào các điều luật dưới đây, hãy trả lời câu hỏi của người dùng một cách chính xác.\n\nNGỮ CẢNH LUẬT:\n{context}\n\nCÂU HỎI: {query}"
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "system": "Bạn là trợ lý ảo chuyên về Luật An ninh mạng Việt Nam 2025. Nếu không thấy thông tin trong luật, hãy nói 'Tôi không tìm thấy thông tin này trong bộ luật hiện có'.",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
            }
        ]
    })
    
    response = bedrock.invoke_model(
        body=body,
        modelId="anthropic.claude-3-haiku-20240307-v1:0"
    )
    result = json.loads(response.get('body').read())
    return result['content'][0]['text']

def lambda_handler(event, context):
    try:
        # 1. Giải mã dữ liệu từ Telegram Webhook
        body_str = event.get('body', '{}')
        body = json.loads(body_str)
        
        if 'message' not in body or 'text' not in body['message']:
            return {'statusCode': 200}
            
        chat_id = body['message']['chat']['id']
        user_text = body['message']['text']

        # 2. Xử lý RAG (Embedding -> Search -> Claude)
        # Bước này có thể tốn 5-10s, hãy đảm bảo Timeout Lambda > 30s
        query_vector = get_query_embedding(user_text)
        law_context = search_law(query_vector)
        answer = ask_claude(user_text, law_context)

        # 3. Gửi kết quả về Telegram
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id, 
            "text": answer,
            "parse_mode": "Markdown" # Giúp hiển thị đẹp hơn nếu AI trả về định dạng luật
        }
        
        http.request(
            'POST', 
            url, 
            body=json.dumps(payload), 
            headers={'Content-Type': 'application/json'}
        )

    except Exception as e:
        # In lỗi ra CloudWatch để debug
        print(f"Lỗi hệ thống: {str(e)}")
        
    # Luôn trả về 200 để Telegram không gửi lại tin nhắn (retry)
    return {'statusCode': 200}