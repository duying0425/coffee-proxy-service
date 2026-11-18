import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "https://reachauto.feishuapp.com/app/app_17fmvspzbtf")
CSRF_TOKEN = os.getenv("CSRF_TOKEN", "34cd12ca94876ec074efd54cbbd807700f696d02e8bdf345a829df331ea0510d")
SESSION_TOKEN = os.getenv("SESSION_TOKEN", "N:b6f7bd6edc4940a2b611_497233.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ2YWx1ZSI6eyJ0ZW5hbnRJRCI6NDk3MjMzLCJ1c2VySUQiOjE4MTgzMDcxMjIzNDM5NDgsImlkZW50aXR5SUQiOjE4NjY1MTYzOTM5MDcyMjAsImlkZW50aXR5VHlwZSI6MSwic2Vzc2lvblRva2VuIjoiMGM0ZjdiZTkwY2E0Y2RiZnA1bmQyNTliN2QwM2UzM2ViNGVsbngzZTlmZjI3NWZmOWRiYzllMmZuNjZkMmMyZTc4ODI5N2I4MWVBWSIsImxvZ2luVHlwZSI6MSwibG9naW5BdCI6MTc5NDkwNjAwMjE3MSwiZXhwaXJlQXQiOjE3OTc0OTgwMDIxNzF9LCJleHAiOjE4MTA0NTgwMDJ9.O9Kvkn8VIZbEYGz-Sti8a1uZqSoMO7F6TVIQmkFHdt0")
WEB_DID = os.getenv("WEB_DID", "1882094366978228")

PORT = int(os.getenv("PORT", "8085"))
HOST = os.getenv("HOST", "0.0.0.0")

# 风控与配额
MAX_ORDERS_PER_PERSON_DAILY = int(os.getenv("MAX_ORDERS_PER_PERSON_DAILY", "2"))
ALLOW_ORDERING = os.getenv("ALLOW_ORDERING", "true").lower() in ("true", "1", "yes")

# 飞书多维表格取餐就绪通知 Webhook
FEISHU_NOTIFY_WEBHOOK = os.getenv("FEISHU_NOTIFY_WEBHOOK", "https://reachauto.feishu.cn/base/workflow/webhook/event/ZaoIa3r7gwGqqthZRmcclb0cn1e")
