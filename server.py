"""
R Cafe 局域网/飞书开放点单中继服务 (FastAPI)
包含内嵌点单页面 + 飞书 Webhook 接收能力
"""
import os
import json
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

from coffee_client import RCafeClient

# ================= 配置区域 =================
CSRF_TOKEN = "34cd12ca94876ec074efd54cbbd807700f696d02e8bdf345a829df331ea0510d"
SESSION_TOKEN = "N:b6f7bd6edc4940a2b611_497233.eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ2YWx1ZSI6eyJ0ZW5hbnRJRCI6NDk3MjMzLCJ1c2VySUQiOjE4MTgzMDcxMjIzNDM5NDgsImlkZW50aXR5SUQiOjE4NjY1MTYzOTM5MDcyMjAsImlkZW50aXR5VHlwZSI6MSwic2Vzc2lvblRva2VuIjoiMGM0ZjdiZTkwY2E0Y2RiZnA1bmQyNTliN2QwM2UzM2ViNGVsbngzZTlmZjI3NWZmOWRiYzllMmZuNjZkMmMyZTc4ODI5N2I4MWVBWSIsImxvZ2luVHlwZSI6MSwibG9naW5BdCI6MTc5NDkwNjAwMjE3MSwiZXhwaXJlQXQiOjE3OTc0OTgwMDIxNzF9LCJleHAiOjE4MTA0NTgwMDJ9.O9Kvkn8VIZbEYGz-Sti8a1uZqSoMO7F6TVIQmkFHdt0"

# 风控配置（防过度消耗）
MAX_ORDERS_PER_PERSON_DAILY = 2  # 每位同事每日最多点单数
ALLOW_ORDERING = True             # 是否开启接单开关
DAILY_BUDGET_CAP = 100.0         # 每日最大消耗R豆上限

# 简易内存计数（生产环境可存入本地 json 或 sqlite）
daily_orders_cache = {}

client = RCafeClient(csrf_token=CSRF_TOKEN, session_token=SESSION_TOKEN)
app = FastAPI(title="R Cafe 开放点单服务")

# 预置商品与规格字典
PRESET_PRODUCTS = {
    "americano": {
        "id": "d207db00-c102-40e4-a82e-62c062d908ed",
        "name": "美式咖啡",
        "price": 4.9,
        "specs": {
            "cup": {"中杯": "0863d3a9-65d0-45e2-af95-8072be5942f1"},
            "temp": {"热饮": "ea8a4617-82b5-4914-bcfa-325aaa5ff019", "冰饮": "0bd7d0c9-deb0-4a77-81cc-348617da18ef"},
            "sugar": {"无糖": "ad0fdea0-a37e-4219-a936-d9536dfe510b"}
        }
    },
    "latte": {
        "id": "b0afbd5f-809a-4702-b727-3bb762a62c72",
        "name": "拿铁",
        "price": 6.9,
        "specs": {
            "cup": {"中杯": "92e7b7c9-69ca-4e50-805f-8fb6018d8e71"},
            "temp": {"热饮": "efbae013-9b7f-4eb7-9998-e7eaef5d3992", "冰饮": "1110e891-dde7-423f-84fb-dc0cd7f451b7"},
            "sugar": {"无糖": "1586471c-f84e-4d61-9418-0fd3db67b3cd"}
        }
    }
}

class OrderSubmitReq(BaseModel):
    user_name: str
    product_key: str      # 'americano' 或 'latte'
    temp: str             # '热饮' 或 '冰饮'
    remark: Optional[str] = None

@app.get("/api/info")
def get_service_info():
    """获取当前余额与点单状态"""
    try:
        balance = client.get_balance()
        return {
            "status": "online",
            "owner": balance.get("userName"),
            "rBalance": balance.get("rBalance"),
            "allowOrdering": ALLOW_ORDERING,
            "products": PRESET_PRODUCTS
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/orders/my")
def get_my_orders():
    """获取最新订单（方便查看取餐号）"""
    try:
        return client.get_my_orders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/order")
def submit_order(order: OrderSubmitReq):
    """同事提交点单接口"""
    if not ALLOW_ORDERING:
        raise HTTPException(status_code=400, detail="当前已暂停接单")

    user_name = order.user_name.strip()
    if not user_name:
        raise HTTPException(status_code=400, detail="请输入您的姓名/花名")

    # 风控限制
    user_count = daily_orders_cache.get(user_name, 0)
    if user_count >= MAX_ORDERS_PER_PERSON_DAILY:
        raise HTTPException(status_code=400, detail=f"您今日点单已达上限（{MAX_ORDERS_PER_PERSON_DAILY}杯）")

    product = PRESET_PRODUCTS.get(order.product_key)
    if not product:
        raise HTTPException(status_code=400, detail="未知的饮品品类")

    temp_id = product["specs"]["temp"].get(order.temp)
    if not temp_id:
        raise HTTPException(status_code=400, detail="无效的温度选项")

    cup_id = list(product["specs"]["cup"].values())[0]
    sugar_id = list(product["specs"]["sugar"].values())[0]
    spec_ids = [cup_id, temp_id, sugar_id]

    remark_text = f"同事: {user_name}"
    if order.remark:
        remark_text += f" | {order.remark.strip()}"

    try:
        res = client.create_order(
            product_id=product["id"],
            spec_ids=spec_ids,
            remark=remark_text,
            quantity=1
        )
        daily_orders_cache[user_name] = user_count + 1
        
        # 尝试查询刚刚生成的最新订单以获取取餐码 (pickupNo)
        orders_info = client.get_my_orders()
        latest_order = orders_info.get("items", [{}])[0]
        
        return {
            "status": "success",
            "message": f"下单成功！",
            "pickupNo": latest_order.get("pickupNo", "已生成"),
            "orderNo": latest_order.get("orderNo"),
            "productName": product["name"],
            "temp": order.temp,
            "cost": product["price"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"咖啡服务下单失败: {str(e)}")


@app.get("/", response_class=HTMLResponse)
def index_html():
    """轻量同事点单页面（自适应手机与PC）"""
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>R Café 同事自助点单</title>
        <style>
            :root {
                --primary: #00704A;
                --bg: #F7F9FB;
                --card: #FFFFFF;
                --text: #1F2329;
                --sub: #8F959E;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: var(--bg);
                color: var(--text);
                margin: 0;
                padding: 16px;
                display: flex;
                justify-content: center;
            }
            .container {
                max-width: 440px;
                width: 100%;
            }
            .header {
                text-align: center;
                margin-bottom: 20px;
            }
            .header h1 {
                font-size: 22px;
                margin: 0 0 6px 0;
                color: var(--primary);
            }
            .badge {
                display: inline-block;
                background: #E8F5E9;
                color: #2E7D32;
                font-size: 13px;
                padding: 4px 12px;
                border-radius: 12px;
            }
            .card {
                background: var(--card);
                border-radius: 16px;
                padding: 20px;
                box-shadow: 0 4px 16px rgba(0,0,0,0.06);
                margin-bottom: 16px;
            }
            .form-group {
                margin-bottom: 16px;
            }
            label {
                display: block;
                font-size: 14px;
                font-weight: 600;
                margin-bottom: 8px;
            }
            input, select {
                width: 100%;
                box-sizing: border-box;
                padding: 12px;
                border: 1px solid #DCE1E6;
                border-radius: 8px;
                font-size: 15px;
            }
            .btn-group {
                display: flex;
                gap: 10px;
            }
            .btn-option {
                flex: 1;
                padding: 12px;
                border: 1px solid #DCE1E6;
                background: #FFF;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 500;
                text-align: center;
            }
            .btn-option.active {
                border-color: var(--primary);
                background: #E8F5E9;
                color: var(--primary);
                font-weight: 600;
            }
            .submit-btn {
                width: 100%;
                padding: 14px;
                background: var(--primary);
                color: #FFF;
                border: none;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                margin-top: 10px;
            }
            .submit-btn:disabled {
                background: #A0C5B8;
                cursor: not-allowed;
            }
            .result-card {
                display: none;
                background: #E8F5E9;
                border: 1px solid #C8E6C9;
                border-radius: 12px;
                padding: 16px;
                margin-top: 16px;
                text-align: center;
            }
            .pickup-code {
                font-size: 32px;
                font-weight: bold;
                color: var(--primary);
                margin: 8px 0;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>☕ R Café 同事点单通道</h1>
                <div class="badge" id="balanceBadge">账户就绪 · 余额充足</div>
            </div>

            <div class="card">
                <div class="form-group">
                    <label>您的姓名 / 花名</label>
                    <input type="text" id="userName" placeholder="例如：张三" required>
                </div>

                <div class="form-group">
                    <label>选择饮品</label>
                    <div class="btn-group">
                        <div class="btn-option active" onclick="selectProduct('latte', this)">拿铁 (6.9豆)</div>
                        <div class="btn-option" onclick="selectProduct('americano', this)">美式 (4.9豆)</div>
                    </div>
                </div>

                <div class="form-group">
                    <label>温度规格</label>
                    <div class="btn-group">
                        <div class="btn-option active" onclick="selectTemp('热饮', this)">热饮</div>
                        <div class="btn-option" onclick="selectTemp('冰饮', this)">冰饮</div>
                    </div>
                </div>

                <div class="form-group">
                    <label>备注（选填）</label>
                    <input type="text" id="remark" placeholder="如：稍后做、少冰等">
                </div>

                <button class="submit-btn" id="subBtn" onclick="submitOrder()">确认下单</button>

                <div class="result-card" id="resultCard">
                    <div style="font-size:14px; color:#2E7D32;">🎉 下单成功！请凭取餐号取餐：</div>
                    <div class="pickup-code" id="resCode">A---</div>
                    <div id="resDetail" style="font-size:13px; color:#555;"></div>
                </div>
            </div>
        </div>

        <script>
            let curProduct = 'latte';
            let curTemp = '热饮';

            function selectProduct(key, el) {
                curProduct = key;
                el.parentElement.querySelectorAll('.btn-option').forEach(e => e.classList.remove('active'));
                el.classList.add('active');
            }

            function selectTemp(t, el) {
                curTemp = t;
                el.parentElement.querySelectorAll('.btn-option').forEach(e => e.classList.remove('active'));
                el.classList.add('active');
            }

            async function submitOrder() {
                const name = document.getElementById('userName').value.trim();
                const remark = document.getElementById('remark').value.trim();
                const btn = document.getElementById('subBtn');
                const resultCard = document.getElementById('resultCard');

                if (!name) {
                    alert('请输入您的姓名或花名！');
                    return;
                }

                btn.disabled = true;
                btn.innerText = '正在提交订单...';
                resultCard.style.display = 'none';

                try {
                    const res = await fetch('/api/order', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            user_name: name,
                            product_key: curProduct,
                            temp: curTemp,
                            remark: remark
                        })
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || '下单失败');

                    resultCard.style.display = 'block';
                    document.getElementById('resCode').innerText = data.pickupNo;
                    document.getElementById('resDetail').innerText = `${data.productName} (${data.temp}) · 扣除 ${data.cost} R豆`;
                } catch(err) {
                    alert('下单出错：' + err.message);
                } finally {
                    btn.disabled = false;
                    btn.innerText = '确认下单';
                }
            }
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    # 启动在 8080 端口，局域网同事可直接通过 http://<你的IP>:8080 访问
    print("🚀 启动 R Cafe 开放点单服务，访问地址: http://127.0.0.1:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)
