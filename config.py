import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "")
CSRF_TOKEN = os.getenv("CSRF_TOKEN", "")
SESSION_TOKEN = os.getenv("SESSION_TOKEN", "")
WEB_DID = os.getenv("WEB_DID", "")

PORT = int(os.getenv("PORT", "8085"))
HOST = os.getenv("HOST", "0.0.0.0")

# 风控与配额
MAX_ORDERS_PER_PERSON_DAILY = int(os.getenv("MAX_ORDERS_PER_PERSON_DAILY", "2"))
ALLOW_ORDERING = os.getenv("ALLOW_ORDERING", "true").lower() in ("true", "1", "yes")

# 测试/演练模式 (默认 False 正常下单营业扣豆；设为 True 则全链路 Mock 模拟)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

# 飞书多维表格通知 Webhook
FEISHU_NOTIFY_WEBHOOK = os.getenv("FEISHU_NOTIFY_WEBHOOK", "")

# 飞书多维表格分享表单链接
FEISHU_FORM_URL = os.getenv("FEISHU_FORM_URL", "")
