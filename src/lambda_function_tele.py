import json
import boto3
import urllib3
import os
import re
import io

# Environment Variables
REGION = os.environ.get("REGION", "ap-southeast-1")
VECTOR_BUCKET = os.environ.get("VECTOR_BUCKET")
INDEX_NAME = os.environ.get("INDEX_NAME")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM")
DYNAMODB_TABLE = os.environ.get("DYNAMO_TABLE") 
MODEL_ID = os.environ.get("MODEL_ID")
PROMPT = os.environ.get("SYSTEM_PROMPT")

# AWS Clients
bedrock_runtime = boto3.client('bedrock-runtime', region_name="us-east-1")
s3v = boto3.client('s3vectors', region_name=REGION)
dynamodb = boto3.resource('dynamodb', region_name=REGION)
http = urllib3.PoolManager()

def send_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        http.request('POST', url, body=json.dumps(payload), headers={'Content-Type': 'application/json'})
    except Exception as e:
        print(f"Telegram Error: {e}")

def clean_phone(text):
    if not text: return ""
    num_only = re.sub(r'\D', '', str(text))
    if num_only.startswith('84'): 
        num_only = '0' + num_only[2:]
    return num_only

def check_blacklist_dynamo(targets):
    """Query DynamoDB for target phone numbers"""
    table = dynamodb.Table(DYNAMODB_TABLE)
    for target in targets:
        target_clean = clean_phone(target)
        try:
            response = table.get_item(Key={'phone_number': int(target_clean)})
            if 'Item' in response:
                item = response['Item']
                print(f"Found in DynamoDB: {item}")
                return {
                    "target": target, 
                    "tag": item.get('Label', 'N/A'), 
                    "comment": item.get('feedback', 'N/A')
                }
        except Exception as e:
            print(f"DynamoDB Query Error: {e}")
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
    except: return None

# get relevant data from s3 vector
def search_s3_vector(query_text):
    try:
        query_vector = get_embedding(query_text)
        if not query_vector: return ""
        response = s3v.query_vectors(
            bucketName=VECTOR_BUCKET,
            indexName=INDEX_NAME,
            vector={"float32": query_vector},
            topK=3 
        )
        return "\n".join([hit.get('metadata', {}).get('text', '') for hit in response.get('hits', [])])
    except: 
        return ""

def get_intent_with_haiku(user_text):
    prompt = (
        "Phân tích câu hỏi người dùng và trả lời DUY NHẤT một từ khóa, không trả lời gì khác:\n"
        "- 'CHECK_PHONE': Kiểm tra lừa đảo, số điện thoại, link lạ.\n"
        "- 'LAW_INFO': Hỏi về luật, quy định, hình phạt.\n"
        "- 'GENERAL': Chào hỏi, nội dung khác.\n\n"
        f"Câu hỏi: {user_text}"
    )
    try:
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 15,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]
        })

        resp = bedrock_runtime.invoke_model(body=body, modelId="us.anthropic.claude-3-haiku-20240307-v1:0")
        return json.loads(resp.get('body').read())['content'][0]['text'].strip().upper()
    except: return "GENERAL"

def lambda_handler(event, context):
    chat_id = None
    try:
        # Parse request from telegram
        body = json.loads(event.get('body', '{}'))
        if 'message' not in body or 'text' not in body['message']: 
            return {'statusCode': 200}
        
        chat_id = body['message']['chat']['id']
        user_text = body['message']['text'].strip()

        http.request('POST', f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction", 
                     body=json.dumps({"chat_id": chat_id, "action": "typing"}),
                     headers={'Content-Type': 'application/json'})

        # get intent
        intent = get_intent_with_haiku(user_text)
        
        blacklist_info = ""
        law_context = ""

        # intent-based processing
        if "CHECK_PHONE" in intent:
            # find phone numbers in text
            phone_entities = re.findall(r'(?:\+84|84|0)(?:\d{9,10})\b', user_text)
            print(phone_entities)
            if phone_entities:
                match = check_blacklist_dynamo(phone_entities)
                if match:
                    blacklist_info = f"THÔNG TIN: Số {match['target']} nằm trong danh sách. Loại: {match['tag']}. Nhận xét: {match['comment']}."
                else:
                    blacklist_info = "Số điện thoại này hiện chưa có trong cơ sở dữ liệu."
            else:
                blacklist_info = "Bạn vui lòng cung cấp số điện thoại cụ thể để tôi kiểm tra."

        elif "LAW_INFO" in intent:
            law_context = search_s3_vector(user_text)

        # System prompt for model
        system_prompt = (PROMPT)
        
        user_content = (
            f"NGỮ CẢNH HỆ THỐNG:\n"
            f"- Ý định: {intent}\n"
            f"- Dữ liệu : {blacklist_info if blacklist_info else 'Không phát hiện'}\n"
            f"- Dữ liệu Luật: {law_context if law_context else 'N/A'}\n\n"
            f"CÂU HỎI NGƯỜI DÙNG: {user_text}"
        )

        final_body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 400,
            "temperature": 0.2,
            "system": system_prompt,
            "messages": [{"role": "user", "content": [{"type": "text", "text": user_content}]}]
        })
        
        # call model
        resp = bedrock_runtime.invoke_model(body=final_body, modelId=MODEL_ID)
        final_text = json.loads(resp.get('body').read())['content'][0]['text']
        
        # respond to telegram
        send_telegram(chat_id, final_text)

    except Exception as e:
        print(f"Main Error: {str(e)}")
        if chat_id:
            send_telegram(chat_id, " Có lỗi xảy ra trong quá trình xử lý thông tin. Vui lòng thử lại sau.")
        
    return {'statusCode': 200}