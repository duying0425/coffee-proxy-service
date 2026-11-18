"""
R Cafe 开放点单服务主程序 (FastAPI)
支持：
1. 局域网/公网响应式 Web 点单界面
2. 飞书多维表格 / 自定义 Webhook 自动化点单
"""
import os
import json
from typing import Optional, Any, Dict
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

import config
from coffee_client import RCafeClient, PRESET_PRODUCTS

app = FastAPI(title="R Cafe 开放点单服务")

# 初始化客户端
client = RCafeClient(
    csrf_token=config.CSRF_TOKEN,
    session_token=config.SESSION_TOKEN,
    base_url=config.BASE_URL,
    web_did=config.WEB_DID
)

# 内存单日点单计数
daily_orders_cache: Dict[str, int] = {}

class OrderSubmitReq(BaseModel):
    user_name: str
    product: str          # 'americano' | 'latte' | '美式' | '拿铁'
    temp: str             # '热饮' | '冰饮'
    remark: Optional[str] = None


@app.get("/api/info")
def get_service_info():
    """获取当前服务状态、余额与支持的菜单"""
    try:
        balance = client.get_balance()
        return {
            "status": "online",
            "owner": balance.get("userName"),
            "rBalance": balance.get("rBalance"),
            "allowOrdering": config.ALLOW_ORDERING,
            "maxDailyPerUser": config.MAX_ORDERS_PER_PERSON_DAILY,
            "products": {
                k: {"name": v["name"], "price": v["price"]}
                for k, v in PRESET_PRODUCTS.items()
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/orders/my")
def get_my_orders():
    """获取最新点单列表与取餐状态"""
    try:
        return client.get_my_orders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/order")
def submit_order_web(order: OrderSubmitReq):
    """Web 界面下单接口"""
    if not config.ALLOW_ORDERING:
        raise HTTPException(status_code=400, detail="服务当前已暂停接单")

    user_name = order.user_name.strip()
    if not user_name:
        raise HTTPException(status_code=400, detail="请输入您的姓名或花名")

    # 风控限制
    user_count = daily_orders_cache.get(user_name, 0)
    if user_count >= config.MAX_ORDERS_PER_PERSON_DAILY:
        raise HTTPException(status_code=400, detail=f"您今日点单已达上限（{config.MAX_ORDERS_PER_PERSON_DAILY}杯）")

    try:
        res = client.order_coffee_for_colleague(
            user_name=user_name,
            product_str=order.product,
            temp_str=order.temp,
            extra_remark=order.remark
        )
        daily_orders_cache[user_name] = user_count + 1
        return {
            "status": "success",
            "message": "下单成功！",
            **res
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"咖啡接口调用失败: {str(e)}")


@app.post("/api/feishu/webhook")
@app.post("/webhook")
async def handle_feishu_webhook(request: Request):
    """
    飞书多维表格表单 / 自动化 Webhook 专用接口
    支持常见的字段映射：
    - user_name / 姓名 / 点单人 / 同事
    - product / 饮品 / 品类 / 咖啡 / 商品
    - temp / 冷热 / 温度 / 规格
    - remark / 备注 / 说明
    """
    if not config.ALLOW_ORDERING:
        return JSONResponse(status_code=400, content={"error": "服务当前已暂停接单"})

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须为合法 JSON")

    # 兼容飞书多维表格记录结构 (可能嵌套在 fields 字典内)
    data = body.get("fields", body) if isinstance(body, dict) else {}

    # 提取姓名
    user_name = (
        data.get("user_name") or data.get("姓名") or data.get("点单人") or
        data.get("同事") or data.get("name") or ""
    )
    if isinstance(user_name, list) and user_name:  # 飞书人员字段可能为列表
        user_name = user_name[0].get("name", "") if isinstance(user_name[0], dict) else str(user_name[0])

    user_name = str(user_name).strip()
    if not user_name:
        return JSONResponse(status_code=400, content={"error": "缺少点单人姓名字段（如 '姓名' 或 'user_name'）"})

    # 提取饮品
    product = (
        data.get("product") or data.get("饮品") or data.get("品类") or
        data.get("咖啡") or data.get("商品") or "拿铁"
    )
    if isinstance(product, list) and product:
        product = product[0]
    product = str(product).strip()

    # 提取温度
    temp = (
        data.get("temp") or data.get("冷热") or data.get("温度") or
        data.get("规格") or "热饮"
    )
    if isinstance(temp, list) and temp:
        temp = temp[0]
    temp = str(temp).strip()

    # 提取备注
    remark = data.get("remark") or data.get("备注") or data.get("说明") or None
    if remark:
        remark = str(remark).strip()

    # 风控限制
    user_count = daily_orders_cache.get(user_name, 0)
    if user_count >= config.MAX_ORDERS_PER_PERSON_DAILY:
        return JSONResponse(
            status_code=400,
            content={"error": f"该用户今日点单已达上限（{config.MAX_ORDERS_PER_PERSON_DAILY}杯）"}
        )

    try:
        res = client.order_coffee_for_colleague(
            user_name=user_name,
            product_str=product,
            temp_str=temp,
            extra_remark=remark
        )
        daily_orders_cache[user_name] = user_count + 1
        
        # 返回取餐码等详细信息，方便飞书自动化工作流提取并回写表格
        return {
            "status": "success",
            "code": 0,
            "message": "下单成功",
            "data": res
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"点单失败: {str(e)}"}
        )


@app.get("/", response_class=HTMLResponse)
def index_html():
    """现代化响应式点单前台"""
    return """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>R Café 点单通道</title>
        <style>
            :root {
                --primary: #00704A;
                --primary-light: #E8F5E9;
                --bg: #F4F6F8;
                --card: #FFFFFF;
                --text-main: #1F2329;
                --text-sub: #646A73;
                --border: #DEE0E3;
            }
            * { box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
                background-color: var(--bg);
                color: var(--text-main);
                margin: 0;
                padding: 20px 16px;
                display: flex;
                justify-content: center;
            }
            .container {
                max-width: 460px;
                width: 100%;
            }
            .brand-header {
                text-align: center;
                margin-bottom: 20px;
            }
            .brand-title {
                font-size: 24px;
                font-weight: 700;
                color: var(--primary);
                margin: 0 0 6px 0;
            }
            .brand-sub {
                font-size: 13px;
                color: var(--text-sub);
            }
            .badge-status {
                display: inline-block;
                margin-top: 8px;
                background: var(--primary-light);
                color: #2E7D32;
                font-size: 13px;
                font-weight: 500;
                padding: 4px 12px;
                border-radius: 20px;
            }
            .card {
                background: var(--card);
                border-radius: 16px;
                padding: 24px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
                margin-bottom: 16px;
            }
            .form-item {
                margin-bottom: 18px;
            }
            .form-label {
                display: block;
                font-size: 14px;
                font-weight: 600;
                margin-bottom: 8px;
            }
            input[type="text"] {
                width: 100%;
                padding: 13px 14px;
                border: 1.5px solid var(--border);
                border-radius: 10px;
                font-size: 15px;
                outline: none;
                transition: border-color 0.2s;
            }
            input[type="text"]:focus {
                border-color: var(--primary);
            }
            .grid-options {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 10px;
            }
            .opt-btn {
                padding: 14px;
                border: 1.5px solid var(--border);
                background: #FFF;
                border-radius: 10px;
                text-align: center;
                cursor: pointer;
                transition: all 0.2s;
            }
            .opt-btn .title {
                font-size: 15px;
                font-weight: 600;
            }
            .opt-btn .sub {
                font-size: 12px;
                color: var(--text-sub);
                margin-top: 3px;
            }
            .opt-btn.active {
                border-color: var(--primary);
                background-color: var(--primary-light);
                color: var(--primary);
            }
            .opt-btn.active .sub {
                color: var(--primary);
            }
            .submit-btn {
                width: 100%;
                padding: 15px;
                background: var(--primary);
                color: #FFF;
                border: none;
                border-radius: 12px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                box-shadow: 0 4px 12px rgba(0, 112, 74, 0.25);
                margin-top: 10px;
            }
            .submit-btn:disabled {
                background: #99C7B6;
                cursor: not-allowed;
                box-shadow: none;
            }
            .result-card {
                display: none;
                background: #F0FDF4;
                border: 1.5px solid #BBF7D0;
                border-radius: 14px;
                padding: 20px;
                margin-top: 16px;
                text-align: center;
            }
            .pickup-label {
                font-size: 13px;
                color: #166534;
                font-weight: 500;
            }
            .pickup-num {
                font-size: 38px;
                font-weight: 800;
                color: var(--primary);
                letter-spacing: 1px;
                margin: 8px 0;
            }
            .pickup-desc {
                font-size: 13px;
                color: #4B5563;
                line-height: 1.5;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="brand-header">
                <div class="brand-title">☕ R Café 点单通道</div>
                <div class="brand-sub">欢迎同事点单 · 自动写入备注</div>
                <div class="badge-status" id="statusBadge">服务在线 · 余额充足</div>
            </div>

            <div class="card">
                <div class="form-item">
                    <label class="form-label">您的姓名 / 花名 <span style="color:#E5484D">*</span></label>
                    <input type="text" id="userName" placeholder="姓名将直接打印在杯贴备注上" required>
                </div>

                <div class="form-item">
                    <label class="form-label">选择咖啡</label>
                    <div class="grid-options">
                        <div class="opt-btn active" onclick="setProduct('latte', this)">
                            <div class="title">拿铁</div>
                            <div class="sub">6.9 R豆</div>
                        </div>
                        <div class="opt-btn" onclick="setProduct('americano', this)">
                            <div class="title">美式咖啡</div>
                            <div class="sub">4.9 R豆</div>
                        </div>
                    </div>
                </div>

                <div class="form-item">
                    <label class="form-label">温度规格</label>
                    <div class="grid-options">
                        <div class="opt-btn active" onclick="setTemp('热饮', this)">
                            <div class="title">热饮</div>
                            <div class="sub">中杯 / 无糖</div>
                        </div>
                        <div class="opt-btn" onclick="setTemp('冰饮', this)">
                            <div class="title">冰饮</div>
                            <div class="sub">中杯 / 无糖</div>
                        </div>
                    </div>
                </div>

                <div class="form-item">
                    <label class="form-label">额外备注（选填）</label>
                    <input type="text" id="remark" placeholder="例如：少冰、稍后做等">
                </div>

                <button class="submit-btn" id="submitBtn" onclick="doSubmit()">立即下单</button>

                <div class="result-card" id="resultCard">
                    <div class="pickup-label">🎉 点单成功！请凭此取餐号取餐：</div>
                    <div class="pickup-num" id="pickupNo">----</div>
                    <div class="pickup-desc" id="pickupDetail"></div>
                </div>
            </div>
        </div>

        <script>
            let selectedProduct = 'latte';
            let selectedTemp = '热饮';

            function setProduct(p, el) {
                selectedProduct = p;
                el.parentElement.querySelectorAll('.opt-btn').forEach(btn => btn.classList.remove('active'));
                el.classList.add('active');
            }

            function setTemp(t, el) {
                selectedTemp = t;
                el.parentElement.querySelectorAll('.opt-btn').forEach(btn => btn.classList.remove('active'));
                el.classList.add('active');
            }

            async function doSubmit() {
                const name = document.getElementById('userName').value.trim();
                const remark = document.getElementById('remark').value.trim();
                const btn = document.getElementById('submitBtn');
                const card = document.getElementById('resultCard');

                if (!name) {
                    alert('请输入您的姓名或花名！');
                    document.getElementById('userName').focus();
                    return;
                }

                btn.disabled = true;
                btn.innerText = '正在提交订单...';
                card.style.display = 'none';

                try {
                    const res = await fetch('/api/order', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            user_name: name,
                            product: selectedProduct,
                            temp: selectedTemp,
                            remark: remark
                        })
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || data.error || '下单失败');

                    card.style.display = 'block';
                    document.getElementById('pickupNo').innerText = data.pickupNo;
                    document.getElementById('pickupDetail').innerHTML = `
                        <b>${data.colleague}</b> 点的 <b>${data.productName} (${data.temp})</b><br>
                        备注：${data.remark} · 扣除 ${data.cost} R豆
                    `;
                } catch (e) {
                    alert('下单失败: ' + e.message);
                } finally {
                    btn.disabled = false;
                    btn.innerText = '立即下单';
                }
            }
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)
