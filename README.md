# Serverless AI Chatbot with Amazon Bedrock & RAG

Dự án triển khai Chatbot AI thông minh sử dụng kiến trúc **Serverless** trên AWS. Hệ thống tích hợp **Amazon Bedrock** và **Knowledge Bases** để thực hiện kỹ thuật **RAG (Retrieval-Augmented Generation)**, giúp chatbot trả lời dựa trên dữ liệu tùy chỉnh được lưu trữ riêng biệt.

---

## 🏗 Kiến trúc hệ thống (Architecture)

Hệ thống được thiết kế hoàn toàn trên nền tảng Serverless để tối ưu chi phí và khả năng mở rộng:

* **Data Source:** Tài liệu (PDF, TXT, Markdown) lưu trữ tại **Amazon S3**.
* **Vector Database:** **Knowledge Bases for Amazon Bedrock** tự động quản lý việc chunking, embedding và lưu trữ vector.
* **Orchestration:** **AWS Lambda** đóng vai trò xử lý logic, tiếp nhận yêu cầu và truy vấn Knowledge Base.
* **LLM:** Sử dụng các mô hình tiên tiến như **Anthropic Claude 3** thông qua Amazon Bedrock.

---

## 🛠 Công nghệ sử dụng

* **Ngôn ngữ:** Python 3.10
* **AWS Services:**
    * Amazon Bedrock (Model: Claude 3 / Haiku / Sonnet)
    * Knowledge Bases for Amazon Bedrock
    * AWS Lambda
    * Amazon S3
* **SDK:** `boto3` (AWS SDK for Python)

---

## 🚀 Hướng dẫn triển khai

### 1. Chuẩn bị trên AWS
1.  **Request Model Access:** Đảm bảo bạn đã kích hoạt quyền truy cập cho mô hình Claude trong console của Amazon Bedrock.
2.  **S3 Bucket:** Tạo một bucket và upload tài liệu hướng dẫn của bạn lên đó.
3.  **Knowledge Base:** * Tạo Knowledge Base trong Bedrock Console.
    * Kết nối với S3 Bucket vừa tạo.
    * Thực hiện **Sync** để hoàn tất quá trình nhúng dữ liệu (Embedding).

### 2. Cấu hình Lambda Function
1.  Tạo Lambda Function với Python 3.10 trở lên.
2.  Thêm quyền (IAM Policy) cho Lambda Role:
    * `bedrock:RetrieveAndGenerate`
    * `bedrock:Retrieve`
    * `bedrock:InvokeModel`
3.  Cấu hình **Environment Variables**:
    * `KNOWLEDGE_BASE_ID`: ID của Knowledge Base bạn đã tạo.
    * `MODEL_ARN`: ARN của model (ví dụ: Claude 3 Sonnet).
