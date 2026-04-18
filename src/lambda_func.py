import json, boto3, os, base64, urllib3
from concurrent.futures import ThreadPoolExecutor
import time
# --- Configuration ---
REGION = os.environ.get("REGION", "ap-southeast-1")
KNOWLEDGE_BASE_ID = os.environ.get("KB_ID")
DYNAMODB_TABLE = os.environ.get("DYNAMO_TABLE")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_IMAGE")
DOMAIN = os.environ.get("DOMAIN")
PHONE =os.environ.get("PHONE")
prompt = os.environ.get("SYSTEM_PROMPT")

# --- Clients ---
# Bedrock Runtime ở us-east-1 để đảm bảo hỗ trợ Claude 3 Haiku ổn định nhất
bedrock_runtime = boto3.client('bedrock-runtime', region_name="us-east-1") 
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime', region_name=REGION)
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb', region_name=REGION)
phone = dynamodb.Table(PHONE)
domain = dynamodb.Table(DOMAIN)
http = urllib3.PoolManager()
rate_limit_table = dynamodb.Table("check_spam")
# --- Helper Functions ---
LIMIT_COUNT = 5   # Số lượt chat tối đa
WINDOW_SECONDS = 60 # Trong vòng 1 phút

def is_rate_limited(user_id):
    if not user_id: return False
    now = int(time.time())

    try:
        res = rate_limit_table.get_item(Key={'UserId': str(user_id)})
        item = res.get('Item')
        print(item)

        # Reset nếu chưa có hoặc hết thời gian
        if not item or now > item.get('ExpireTime', 0):
            rate_limit_table.put_item(
                Item={
                    'UserId': str(user_id),
                    'RequestCount': 1,
                    'ExpireTime': now + WINDOW_SECONDS
                }
            )
            return False

        # Update có điều kiện (atomic, chống spam chuẩn)
        rate_limit_table.update_item(
            Key={'UserId': str(user_id)},
            UpdateExpression="SET RequestCount = RequestCount + :one",
            ConditionExpression="RequestCount < :limit",
            ExpressionAttributeValues={
                ':one': 1,
                ':limit': LIMIT_COUNT
            }
        )
        return False

    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        # Vượt quá limit
        return True

    except Exception as e:
        print(f"Rate limit error: {e}")
        return False
def get_image_from_s3(key):
    """Lấy file ảnh từ S3 và encode sang base64"""
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        image_data = response['Body'].read()
        return base64.b64encode(image_data).decode('utf-8')
    except Exception as e:
        print(f"Error fetching image from S3 ({key}): {str(e)}")
        return None

def extract_entities_with_ai(user_text):
    """Sử dụng Claude để trích xuất SĐT và Domain thay cho Regex"""
    if not user_text: return [], []
    
    prompt = (
        f"Extract all phone numbers and domains/URLs from this text. "
        f"Return ONLY a JSON object: {{\"phones\": [], \"domains\": []}}. "
        f"Text: {user_text}"
    )
    
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 200,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}]
    })
    
    try:
        resp = bedrock_runtime.invoke_model(body=body, modelId="us.anthropic.claude-3-haiku-20240307-v1:0")
        res_json = json.loads(json.loads(resp.get('body').read())['content'][0]['text'])
        return res_json.get('phones', []), res_json.get('domains', [])
    except Exception as e:
        print(f"Error extracting entities: {e}")
        return [], []

def clean_phone(text):
    num_only = "".join(filter(str.isdigit, str(text)))
    if num_only.startswith('84'): num_only = '0' + num_only[2:]
    return num_only

def check_blacklist_multi(phones, domains):
    results = []
    
    # Check Phones
    for p in phones:
        cp = clean_phone(p)
        if not cp: continue
        try:
            res = phone.get_item(Key={'phone_number': str(cp)})
            if 'Item' in res:
                it = res['Item']
                results.append(f"[SĐT {p}]: Đánh giá: {it.get('Label')}")
        except: continue

    # Check Domains 
    print(domains)
    for d in domains:
        try:
            res = domain.get_item(Key={'domain': d.lower()})
            if 'Item' in res:
                it = res['Item']
                results.append(f"Domain {d} không phải lừa đảo")
        except: continue
        pass
        
    return "\n".join(results) if results else "Không tìm thấy trong danh sách."

def search_rag_law(query_text):
    """Truy vấn trực tiếp từ Bedrock Knowledge Base (RAG)"""
    if not query_text or not KNOWLEDGE_BASE_ID:
        return "N/A"
    
    try:
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=KNOWLEDGE_BASE_ID,
            retrievalQuery={'text': query_text},
            retrievalConfiguration={
                'vectorSearchConfiguration': {
                    'numberOfResults': 3, 
                    'overrideSearchType': 'SEMANTIC'
                }
            }
        )
        
        results = response.get('retrievalResults', [])
        texts = [r.get('content', {}).get('text', '') for r in results]
        return "\n---\n".join(texts) if texts else "N/A"
        
    except Exception as e:
        print(f"Error querying KB: {str(e)}")
        return "N/A"

def send_telegram(chat_id, text):
    if not TELEGRAM_TOKEN or not chat_id: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown"}
    try:
        encoded_data = json.dumps(payload).encode('utf-8')
        http.request('POST', url, body=encoded_data, headers={'Content-Type': 'application/json'})
    except Exception as e:
        print(f"Error sending Telegram: {e}")

# --- 3. Main Handler ---
def lambda_handler(event, context):
    # Khai báo mặc định
    platform = event.get('platform')
    chat_id = event.get('chatId')
    
    # Định danh user thống nhất
    user_identifier = f"{platform}_{chat_id}"

    # Kiểm tra Rate Limit
    if is_rate_limited(user_identifier):
        msg_limit = "⚠️ Bạn đang chat quá nhanh. Vui lòng đợi 1 phút nhé!"
        if platform == 'telegram':
            send_telegram(chat_id, msg_limit)
        return {
            "statusCode": 429,
            "body": json.dumps({"answer": msg_limit, "status": "rate_limited"})
        }
    is_api_gateway = 'body' in event
    platform = event.get('platform')
    if platform == 'telegram':
        # Xử lý Request từ API Gateway/Webhook
        platform = event.get('platform')
        chat_id = event.get('chatId')
        user_text = event.get('text', '').strip()
        image_input = event.get('image_base64') # Telegram gửi base64
        print(f"chatid: {chat_id}, user text: {user_text}, img: {image_input}, platform: {platform}")
    else:
        # Xử lý Request gọi trực tiếp từ Spring Boot (bằng AWS SDK Invoke)
        platform = event.get('platform')
        chat_id = event.get('chatId')
        user_text = event.get('text', '').strip()
        urls = event.get('imageUrls', [])
        image_input = event.get('imageUrls')
        image_input = urls[0] if urls else None
        print(f"chatid: {chat_id}, user text: {user_text}, img: {image_input}, platform: {platform}")

    try:
        # --- Xử lý hình ảnh (S3 Key vs Base64) ---
        final_image_base64 = None
        
        if image_input:
            # Nếu là Web và input là s3_key (nhận diện qua độ dài hoặc format)
            if platform == "web" and len(image_input) < 1024 and not image_input.startswith('data:'):
                final_image_base64 = get_image_from_s3(image_input)
            else:
                final_image_base64 = image_input

        # Chuẩn hóa Base64 (bỏ phần header nếu có)
        if final_image_base64 and "," in final_image_base64:
            final_image_base64 = final_image_base64.split(",")[1]

        # --- Logic xử lý AI & DB ---
        phones, domains = extract_entities_with_ai(user_text)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f_db = executor.submit(check_blacklist_multi, phones, domains)
            f_rag = executor.submit(search_rag_law, user_text)
            
            db_info = f_db.result()
            law_context = f_rag.result()

        # Build Claude Content
        messages_content = []
        if final_image_base64:
            messages_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": final_image_base64}
            })
        
        messages_content.append({
            "type": "text", 
            "text": (
                f"NGỮ CẢNH HỆ THỐNG:\n"
                f"- Dữ liệu thông tin: {db_info}\n"
                f"- Luật ATTT (RAG): {law_context}\n\n"
                f"CÂU HỎI: {user_text if user_text else 'Phân tích ảnh này'}"
            )
        })
        print(db_info)

        # --- System Prompt (Giữ nguyên của bạn) ---
        system_prompt = prompt

        final_body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 600,
            "temperature": 0.2,
            "system": system_prompt,
            "messages": [{"role": "user", "content": messages_content}]
        })
        
        resp = bedrock_runtime.invoke_model(body=final_body, modelId="us.anthropic.claude-3-haiku-20240307-v1:0")
        final_reply = json.loads(resp.get('body').read())['content'][0]['text']
        # --- Kết quả trả về ---
        response_data = {
            "answer": final_reply,
        }

        if platform == "telegram":
            send_telegram(chat_id, final_reply)
            return {"statusCode": 200, "body": json.dumps({"status": "success"})} if is_api_gateway else {"status": "success"}
        
        # Trả về cho Web/Backend
        if is_api_gateway:
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(response_data)
            }
        return response_data

    except Exception as e:
        print(f"Error: {str(e)}")
        err_msg = "AnTiScaQ đang bận, vui lòng thử lại sau!"
        if platform == "telegram": send_telegram(chat_id, err_msg)
        error_res = {"error": str(e), "status": "failed"}
        return {"statusCode": 500, "body": json.dumps(error_res)} if is_api_gateway else error_res