import json
import boto3
import os
import re
import base64

# --- Configuration ---
REGION = os.environ.get("REGION", "ap-southeast-1")
VECTOR_BUCKET = os.environ.get("VECTOR_BUCKET")
INDEX_NAME = os.environ.get("LAW_INDEX")
DYNAMODB_TABLE = os.environ.get("DYNAMO_TABLE")
model_id = os.environ.get("MODEL_ID")
PROMPT = os.environ.get("SYSTEM_PROMPT")

# --- Clients (Warm Start) ---
bedrock_runtime = boto3.client('bedrock-runtime', region_name="ap-southeast-1") # Haiku chạy nhanh nhất ở us-east-1
s3v = boto3.client('s3vectors', region_name="ap-southeast-1")
dynamodb = boto3.resource('dynamodb', region_name=REGION)
table = dynamodb.Table(DYNAMODB_TABLE)

def clean_phone(text):
    if not text: return ""
    num_only = re.sub(r'\D', '', str(text))
    if num_only.startswith('84'): num_only = '0' + num_only[2:]
    return num_only

def check_blacklist_dynamo(targets):
    for target in targets:
        target_clean = clean_phone(target)
        try:
            response = table.get_item(Key={'phone_number': int(target_clean)})
            if 'Item' in response:
                item = response['Item']
                return {
                    "target": target, 
                    "tag": item.get('Label', 'N/A'), 
                    "comment": item.get('feedback', 'N/A')
                }
        except: continue
    return None

def get_embedding(text):
    try:
        body = json.dumps({"texts": [text], "input_type": "search_query"})
        response = bedrock_runtime.invoke_model(
            modelId="cohere.embed-multilingual-v3",
            contentType="application/json",
            body=body
        )
        return json.loads(response.get('body').read()).get('embeddings')[0]
    except: 
        return None

def search_s3_vector(query_text):
    results = []
    
    try:
        # embedding user text
        query_vector = get_embedding(query_text)
        if not query_vector: 
            return "N/A"

        # call s3 vector search
        response = s3v.query_vectors(
            vectorBucketName=VECTOR_BUCKET,
            indexName=INDEX_NAME,
            queryVector={"float32": query_vector},
            topK=5,
            returnMetadata=True
        )
        
        # get vectors list from response
        vectors_list = response.get('vectors', [])
        
        if not vectors_list:
            if os.environ.get("DEBUG_MODE") == "true":
                print("DEBUG: Không tìm thấy kết quả nào trong mảng 'vectors'")
                return "N/A"
            
        # check vector
        for item in vectors_list:
            # get metadata
            meta = item.get('metadata', {})
            
            # get content from metadata
            content = meta.get('text')
            
            if content:
                # clean text
                clean_content = content.replace('\n', ' ').strip()
                results.append(clean_content)
        
        # final result
        if results:
            return "\n\n---\n\n".join(results)
        else:
            return "N/A"

    except Exception as e:
        if os.environ.get("DEBUG_MODE") == "true":
            print(f"RAG Error: {str(e)}")
            return "N/A"
def lambda_handler(event, context):
    try:
        # get text and image from request
        raw_body = event.get('body', '{}')
        req_body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
        
        user_text = req_body.get('message', '').strip()
        image_base64 = req_body.get('image_base64')

        blacklist_info = "Không có thông tin."
        law_context = "N/A"
        
        # find phone number in text and check database
        phone_entities = re.findall(r'(?:\+84|84|0)(?:\d{9,10})\b', user_text)
        
        if phone_entities:
            # find phone 
            match = check_blacklist_dynamo(phone_entities)
            if match:
                blacklist_info = f"DỮ LIỆU: Số {match['target']} thuộc nhãn {match['tag']}. Ghi chú: {match['comment']}."
            else:
                blacklist_info = "Số điện thoại này hiện chưa có trong danh sách."
        else:
            # Get RAG context 
            law_context = search_s3_vector(user_text) if user_text else "N/A"
            print("rule: ",law_context)

        # system prompt and messages content for model
        messages_content = []
        if image_base64:
            if "," in image_base64: image_base64 = image_base64.split(",")[1]
            messages_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}
            })
        
        messages_content.append({
            "type": "text", 
            "text": (
                f"NGỮ CẢNH HỆ THỐNG:\n"
                f"- Dữ liệu thông tin: {blacklist_info}\n"
                f"- Dữ liệu Luật : {law_context}\n\n"
                f"CÂU HỎI NGƯỜI DÙNG: {user_text if user_text else 'Phân tích hình ảnh này'}"
            )
        })

        system_prompt = (PROMPT)

        # call model
        final_body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "temperature": 0.1,
            "system": system_prompt,
            "messages": [{"role": "user", "content": messages_content}]
        })
        
        resp = bedrock_runtime.invoke_model(
            body=final_body, 
            modelId=model_id
        )
        final_text = json.loads(resp.get('body').read())['content'][0]['text']

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'answer': final_text}, ensure_ascii=False)
        }

    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'answer': "Hệ thống đang bận, vui lòng thử lại sau."}, ensure_ascii=False)
        }