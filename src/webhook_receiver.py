import json, boto3, os, urllib3, base64
print('test')
lambda_client = boto3.client('lambda')
http = urllib3.PoolManager()
token = os.environ.get("TELEGRAM")
CORE_NAME = os.environ.get("FUNCTION_NAME")

def get_telegram_file(file_id):
    if not token: return None
    try:
        # 1. Lấy file_path từ Telegram
        url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
        resp = http.request('GET', url)
        data = json.loads(resp.data.decode('utf-8'))
        if not data.get('ok'): return None
        
        file_path = data['result']['file_path']
        # 2. Tải file về
        file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        file_resp = http.request('GET', file_url)
        return file_resp.data
    except Exception as e:
        print(f"Error getting file: {e}")
        return None

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        msg = body.get('message', {})
        chat_id = msg.get('chat', {}).get('id')
        
        if not chat_id: return {'statusCode': 200}

        # Khởi tạo payload mặc định (Không có ảnh)
        # caption thường đi kèm với ảnh thay vì text
        user_text = msg.get('text') or msg.get('caption') or ""
        
        payload = {
            "platform": "telegram",
            "chatId": str(chat_id),
            "text": user_text,
            "image_base64": None,
            "image_url": None
        }

        # XỬ LÝ ẢNH (Nếu có thì mới làm, không thì thôi)
        if 'photo' in msg:
            # Telegram gửi nhiều size, lấy cái cuối cùng là size lớn nhất
            file_id = msg['photo'][-1]['file_id']
            img_bytes = get_telegram_file(file_id)
            
            if img_bytes:
                # Chuyển sang Base64 để gửi sang Core
                payload["image_base64"] = base64.b64encode(img_bytes).decode('utf-8')
                print(f"Đã xử lý ảnh cho chat_id: {chat_id}")
            else:
                print(f"Có ảnh nhưng không tải được file_id: {file_id}")

        # GỌI CORE (BẤT ĐỒNG BỘ)
        if CORE_NAME:
            lambda_client.invoke(
                FunctionName=CORE_NAME,
                InvocationType='Event',
                Payload=json.dumps(payload)
            )
            print(f"Tele invoke core success: {chat_id}")
        else:
            print("LỖI: Chưa cấu hình FUNCTION_NAME trong Environment Variables")

        return {'statusCode': 200}

    except Exception as e:
        print(f"Tele Error: {e}")
        return {'statusCode': 200} # Vẫn trả về 200 để Telegram không gửi lại webhook liên tục