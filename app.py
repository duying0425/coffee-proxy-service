"""
R Cafe 开放点单服务主程序 (FastAPI)
包含：
1. 双通道点单支持：
   - 通道一：飞书实名表单（推荐，支持飞书出餐私信强提醒）
   - 通道二：网页花名点单（支持花名/免登，网页自查制作进度）
2. 响应式 Web 前台 + 实时进度轮询（排队 -> 制作中 -> 待取餐 -> 已完成）
3. 飞书多维表格 / 自定义 Webhook 自动化点单
4. 后台自动巡检与飞书取餐就绪 Webhook 推送
"""
import os
import json
import asyncio
from typing import Optional, Any, Dict, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
import requests

import config
from coffee_client import RCafeClient, PRESET_PRODUCTS

app = FastAPI(title="R Cafe 开放点单服务")

client = RCafeClient(
    csrf_token=config.CSRF_TOKEN,
    session_token=config.SESSION_TOKEN,
    base_url=config.BASE_URL,
    web_did=config.WEB_DID
)

daily_orders_cache: Dict[str, int] = {}
STATUS_MAP = {
    "pending": {"text": "待制作", "color": "#E67E22", "desc": "订单已提交，吧台排队中..."},
    "making": {"text": "制作中", "color": "#2F6FED", "desc": "咖啡师正在制作您的饮品..."},
    "ready": {"text": "待取餐（餐好）", "color": "#27AE60", "desc": "🎉 咖啡已做好，请前往吧台领取！"},
    "completed": {"text": "已完成", "color": "#646A73", "desc": "已核销取餐"},
    "cancelled": {"text": "已取消", "color": "#E5484D", "desc": "订单已取消，R豆已原路退回"}
}

notified_ready_orders = set()


@app.on_event("startup")
async def startup_event():
    """服务启动时，初始化后台监听任务"""
    asyncio.create_task(order_status_monitor_loop())


async def order_status_monitor_loop():
    """后台任务：每 15 秒轮询一次，发现订单餐好自动推送飞书 Webhook"""
    print("[Monitor] 启动后台订单状态监听巡检...")
    try:
        init_data = client.get_my_orders()
        for item in init_data.get("items", []):
            if item.get("status") in ("ready", "completed"):
                notified_ready_orders.add(item.get("orderNo"))
        print(f"[Monitor] 已初始化历史订单缓存: {len(notified_ready_orders)} 笔")
    except Exception as e:
        print(f"[Monitor] 初始化订单缓存异常: {e}")

    while True:
        await asyncio.sleep(15)
        if not config.FEISHU_NOTIFY_WEBHOOK:
            continue
        try:
            data = client.get_my_orders()
            items = data.get("items", [])
            for item in items:
                order_no = item.get("orderNo")
                status = item.get("status")
                
                if status in ("ready", "completed") and order_no not in notified_ready_orders:
                    remark = item.get("remark") or ""
                    colleague = "同事"
                    if "点单人:" in remark:
                        colleague = remark.split("点单人:")[1].split("(")[0].strip()
                    elif remark:
                        colleague = remark.strip()

                    product_name = "咖啡"
                    spec_info = ""
                    if item.get("items") and len(item["items"]) > 0:
                        first_prod = item["items"][0]
                        product_name = first_prod.get("productName", "咖啡")
                        spec_info = first_prod.get("specInfo", "")

                    pickup_no = item.get("pickupNo", "")

                    payload = {
                        "pickup_no": pickup_no,
                        "order_no": order_no,
                        "colleague": colleague,
                        "product": product_name,
                        "spec": spec_info,
                        "status": "待取餐",
                        "message": f"☕ 您的 {product_name} 已做好！取餐码为 {pickup_no}，请尽快前往咖啡厅吧台领取！"
                    }
                    try:
                        resp = requests.post(config.FEISHU_NOTIFY_WEBHOOK, json=payload, timeout=8)
                        print(f"[Monitor] 触发飞书通知: {order_no} ({pickup_no}), 响应: {resp.text}")
                        notified_ready_orders.add(order_no)
                    except Exception as post_err:
                        print(f"[Monitor] 推送飞书通知失败: {post_err}")
        except Exception as poll_err:
            print(f"[Monitor] 轮询订单异常: {poll_err}")


class OrderSubmitReq(BaseModel):
    user_name: str
    product: str
    temp: str
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
            "feishuFormUrl": config.FEISHU_FORM_URL,
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
        data = client.get_my_orders()
        for item in data.get("items", []):
            st = item.get("status", "unknown")
            info = STATUS_MAP.get(st, {"text": st, "color": "#888", "desc": ""})
            item["statusText"] = info["text"]
            item["statusColor"] = info["color"]
            item["statusDesc"] = info["desc"]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/order/status")
def get_order_status(order_no: Optional[str] = None, pickup_no: Optional[str] = None, user_name: Optional[str] = None):
    """单单查询或按姓名查询最新订单进度"""
    try:
        data = client.get_my_orders()
        items = data.get("items", [])
        
        target_order = None
        for item in items:
            if order_no and item.get("orderNo") == order_no:
                target_order = item
                break
            if pickup_no and item.get("pickupNo") == pickup_no:
                target_order = item
                break
            if user_name and user_name in (item.get("remark") or ""):
                target_order = item
                break

        if not target_order:
            return {"found": False, "message": "未查询到对应订单"}

        st = target_order.get("status", "unknown")
        info = STATUS_MAP.get(st, {"text": st, "color": "#888", "desc": ""})
        return {
            "found": True,
            "orderNo": target_order.get("orderNo"),
            "pickupNo": target_order.get("pickupNo"),
            "status": st,
            "statusText": info["text"],
            "statusColor": info["color"],
            "statusDesc": info["desc"],
            "isReady": st in ("ready", "completed"),
            "remark": target_order.get("remark"),
            "items": target_order.get("items", [])
        }
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
    """飞书多维表格表单 / 自动化 Webhook 专用接口"""
    if not config.ALLOW_ORDERING:
        return JSONResponse(status_code=400, content={"error": "服务当前已暂停接单"})

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须为合法 JSON")

    data = body.get("fields", body) if isinstance(body, dict) else {}

    user_name = (
        data.get("user_name") or data.get("姓名") or data.get("点单人") or
        data.get("同事") or data.get("name") or ""
    )
    if isinstance(user_name, list) and user_name:
        user_name = user_name[0].get("name", "") if isinstance(user_name[0], dict) else str(user_name[0])

    user_name = str(user_name).strip()
    if not user_name:
        return JSONResponse(status_code=400, content={"error": "缺少点单人姓名字段（如 '姓名' 或 'user_name'）"})

    product = (
        data.get("product") or data.get("饮品") or data.get("品类") or
        data.get("咖啡") or data.get("商品") or "拿铁"
    )
    if isinstance(product, list) and product:
        product = product[0]
    product = str(product).strip()

    temp = (
        data.get("temp") or data.get("冷热") or data.get("温度") or
        data.get("规格") or "热饮"
    )
    if isinstance(temp, list) and temp:
        temp = temp[0]
    temp = str(temp).strip()

    remark = data.get("remark") or data.get("备注") or data.get("说明") or None
    if remark:
        remark = str(remark).strip()

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
    """现代化响应式点单前台（双通道对比 + 实时制作进度轮询与历史订单卡片）"""
    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>R Café 点单通道</title>
        <style>
            :root {{
                --primary: #00704A;
                --primary-light: #E8F5E9;
                --feishu-blue: #3370FF;
                --feishu-light: #EFF4FF;
                --bg: #F4F6F8;
                --card: #FFFFFF;
                --text-main: #1F2329;
                --text-sub: #646A73;
                --border: #DEE0E3;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
                background-color: var(--bg);
                color: var(--text-main);
                margin: 0;
                padding: 16px;
                display: flex;
                justify-content: center;
            }}
            .container {{
                max-width: 500px;
                width: 100%;
            }}
            .brand-header {{
                text-align: center;
                margin-bottom: 16px;
            }}
            .brand-title {{
                font-size: 24px;
                font-weight: 700;
                color: var(--primary);
                margin: 0 0 4px 0;
            }}
            .brand-sub {{
                font-size: 13px;
                color: var(--text-sub);
            }}

            /* 双通道引导区 */
            .channel-container {{
                display: flex;
                flex-direction: column;
                gap: 12px;
                margin-bottom: 16px;
            }}
            .channel-card-feishu {{
                background: linear-gradient(135deg, #F0F5FF 0%, #E6EFFF 100%);
                border: 1.5px solid #ADC8FF;
                border-radius: 14px;
                padding: 16px;
                position: relative;
            }}
            .channel-badge-top {{
                display: inline-block;
                background: var(--feishu-blue);
                color: #FFF;
                font-size: 11px;
                font-weight: 700;
                padding: 2px 8px;
                border-radius: 6px;
                margin-bottom: 8px;
            }}
            .channel-header-row {{
                display: flex;
                align-items: center;
                gap: 10px;
                margin-bottom: 6px;
            }}
            .channel-header-row .icon {{
                font-size: 24px;
            }}
            .channel-header-row .title {{
                font-size: 16px;
                font-weight: 700;
                color: #1D39C4;
            }}
            .channel-desc {{
                font-size: 13px;
                color: #4B5563;
                line-height: 1.45;
                margin-bottom: 12px;
            }}
            .feishu-jump-btn {{
                display: block;
                text-align: center;
                background: var(--feishu-blue);
                color: #FFF;
                font-size: 14px;
                font-weight: 600;
                padding: 11px 16px;
                border-radius: 10px;
                text-decoration: none;
                box-shadow: 0 3px 10px rgba(51, 112, 255, 0.25);
                transition: opacity 0.2s;
            }}
            .feishu-jump-btn:active {{
                opacity: 0.85;
            }}

            .channel-divider {{
                display: flex;
                align-items: center;
                text-align: center;
                color: var(--text-sub);
                font-size: 12px;
                margin: 4px 0;
            }}
            .channel-divider::before, .channel-divider::after {{
                content: '';
                flex: 1;
                border-bottom: 1px dashed #D0D3D6;
            }}
            .channel-divider:not(:empty)::before {{
                margin-right: .75em;
            }}
            .channel-divider:not(:empty)::after {{
                margin-left: .75em;
            }}

            .nav-tabs {{
                display: flex;
                background: #E5E9ED;
                border-radius: 10px;
                padding: 3px;
                margin-bottom: 16px;
            }}
            .nav-tab {{
                flex: 1;
                text-align: center;
                padding: 8px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                border-radius: 8px;
                color: var(--text-sub);
                transition: all 0.2s;
            }}
            .nav-tab.active {{
                background: #FFF;
                color: var(--primary);
                box-shadow: 0 2px 6px rgba(0,0,0,0.08);
            }}
            .card {{
                background: var(--card);
                border-radius: 16px;
                padding: 20px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
                margin-bottom: 16px;
            }}
            .form-item {{
                margin-bottom: 16px;
            }}
            .form-label {{
                display: block;
                font-size: 14px;
                font-weight: 600;
                margin-bottom: 6px;
            }}
            .form-tip {{
                font-size: 12px;
                color: #8F959E;
                margin-top: 4px;
            }}
            input[type="text"] {{
                width: 100%;
                padding: 12px 14px;
                border: 1.5px solid var(--border);
                border-radius: 10px;
                font-size: 15px;
                outline: none;
            }}
            input[type="text"]:focus {{
                border-color: var(--primary);
            }}
            .grid-options {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 10px;
            }}
            .opt-btn {{
                padding: 12px;
                border: 1.5px solid var(--border);
                background: #FFF;
                border-radius: 10px;
                text-align: center;
                cursor: pointer;
            }}
            .opt-btn .title {{
                font-size: 15px;
                font-weight: 600;
            }}
            .opt-btn .sub {{
                font-size: 12px;
                color: var(--text-sub);
                margin-top: 3px;
            }}
            .opt-btn.active {{
                border-color: var(--primary);
                background-color: var(--primary-light);
                color: var(--primary);
            }}
            .opt-btn.active .sub {{
                color: var(--primary);
            }}
            .submit-btn {{
                width: 100%;
                padding: 14px;
                background: var(--primary);
                color: #FFF;
                border: none;
                border-radius: 12px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                box-shadow: 0 4px 12px rgba(0, 112, 74, 0.25);
            }}
            .submit-btn:disabled {{
                background: #99C7B6;
                cursor: not-allowed;
                box-shadow: none;
            }}
            .status-banner {{
                border-radius: 14px;
                padding: 16px;
                margin-top: 16px;
                text-align: center;
                display: none;
            }}
            .status-banner.making {{
                background: #EFF6FF;
                border: 1.5px solid #BFDBFE;
            }}
            .status-banner.ready {{
                background: #F0FDF4;
                border: 2px solid #22C55E;
                animation: pulse 1.8s infinite;
            }}
            @keyframes pulse {{
                0% {{ box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.4); }}
                70% {{ box-shadow: 0 0 0 10px rgba(34, 197, 94, 0); }}
                100% {{ box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }}
            }}
            .pickup-badge-title {{
                font-size: 13px;
                font-weight: 600;
            }}
            .pickup-code-lg {{
                font-size: 42px;
                font-weight: 800;
                margin: 6px 0;
                letter-spacing: 2px;
            }}
            .stepper {{
                display: flex;
                justify-content: space-between;
                margin: 16px 0 8px 0;
                position: relative;
            }}
            .step-item {{
                flex: 1;
                text-align: center;
                font-size: 12px;
                color: #999;
                font-weight: 500;
            }}
            .step-item.active {{
                color: var(--primary);
                font-weight: 700;
            }}
            .step-circle {{
                width: 22px;
                height: 22px;
                border-radius: 50%;
                background: #E5E7EB;
                color: #666;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 4px auto;
                font-size: 11px;
            }}
            .step-item.active .step-circle {{
                background: var(--primary);
                color: #FFF;
            }}
            .order-item-card {{
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 14px;
                margin-bottom: 12px;
                background: #FFF;
            }}
            .order-top {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 6px;
            }}
            .status-tag {{
                padding: 3px 8px;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="brand-header">
                <div class="brand-title">☕ R Café 点单通道</div>
                <div class="brand-sub">欢迎同事点单 · 自动扣额度制作</div>
            </div>

            <!-- 通道一：飞书实名表单推荐卡片 -->
            <div class="channel-card-feishu">
                <span class="channel-badge-top">⭐ 推荐 · 飞书私信通知</span>
                <div class="channel-header-row">
                    <span class="icon">📱</span>
                    <span class="title">飞书实名问卷点单</span>
                </div>
                <div class="channel-desc">
                    自动关联飞书账号实名点单，咖啡出餐后<b>直接通过飞书私信推送取餐码</b>，无需反复盯着网页！
                </div>
                <a href="{config.FEISHU_FORM_URL}" target="_blank" class="feishu-jump-btn">
                    打开飞书表单点单 &rarr;
                </a>
            </div>

            <div class="channel-divider">或者选择网页点单</div>

            <!-- Tab 切换 -->
            <div class="nav-tabs">
                <div class="nav-tab active" id="tabOrder" onclick="switchTab('order')">网页花名点单</div>
                <div class="nav-tab" id="tabStatus" onclick="switchTab('status')">点单进度 / 记录</div>
            </div>

            <!-- Tab 1: 网页花名点单面板 -->
            <div id="panelOrder">
                <div class="card">
                    <div style="background: #FFFBE6; border: 1px solid #FFE58F; border-radius: 8px; padding: 10px 12px; font-size: 12px; color: #D48806; margin-bottom: 16px;">
                        💡 <b>提示</b>：网页点单适合使用花名或匿名，<b>不支持飞书私信推送</b>。下单后请留在网页或切换至「点单进度」查看制作状态。
                    </div>

                    <div class="form-item">
                        <label class="form-label">您的花名 / 昵称 <span style="color:#E5484D">*</span></label>
                        <input type="text" id="userName" placeholder="将打印在咖啡杯贴备注上" required>
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
                        <input type="text" id="remark" placeholder="如：少冰、稍后做等">
                    </div>

                    <button class="submit-btn" id="submitBtn" onclick="doSubmit()">立即下单</button>
                </div>
            </div>

            <!-- Tab 2: 进度 / 记录面板 -->
            <div id="panelStatus" style="display: none;">
                <div class="card" style="margin-bottom: 12px; padding: 16px;">
                    <div style="font-size: 14px; font-weight: 600; margin-bottom: 8px;">快速查询我的点单</div>
                    <div style="display: flex; gap: 8px;">
                        <input type="text" id="queryName" placeholder="输入姓名、花名或取餐码" style="flex: 1;">
                        <button onclick="queryMyOrders()" style="padding: 0 16px; background: var(--primary); color: #FFF; border: none; border-radius: 8px; font-weight: 600; cursor: pointer;">查询</button>
                    </div>
                </div>

                <div id="ordersList">
                    <div style="text-align: center; color: #888; font-size: 13px; padding: 20px;">正在加载订单进度...</div>
                </div>
            </div>

            <!-- 动态弹出的即时状态卡片 -->
            <div class="status-banner" id="liveStatusCard">
                <div class="pickup-badge-title" id="bannerTitle">订单提交中...</div>
                <div class="pickup-code-lg" id="bannerPickupNo">----</div>
                <div id="bannerStatusText" style="font-size: 16px; font-weight: 700; margin-bottom: 6px;"></div>
                <div id="bannerDesc" style="font-size: 13px; color: #555;"></div>

                <div class="stepper">
                    <div class="step-item" id="step1"><div class="step-circle">1</div>待制作</div>
                    <div class="step-item" id="step2"><div class="step-circle">2</div>制作中</div>
                    <div class="step-item" id="step3"><div class="step-circle">3</div>餐好待取</div>
                    <div class="step-item" id="step4"><div class="step-circle">4</div>已取餐</div>
                </div>
                <div style="font-size: 12px; color: #888; margin-top: 10px;">⚡ 页面每 8 秒自动刷新状态，做好了将变绿提醒</div>
            </div>
        </div>

        <script>
            let selectedProduct = 'latte';
            let selectedTemp = '热饮';
            let activeOrderNo = localStorage.getItem('last_order_no') || null;
            let activePickupNo = localStorage.getItem('last_pickup_no') || null;
            let pollingTimer = null;

            const savedName = localStorage.getItem('coffee_user_name');
            if (savedName) {{
                document.getElementById('userName').value = savedName;
                document.getElementById('queryName').value = savedName;
            }}

            if (activeOrderNo) {{
                startPollingStatus(activeOrderNo, activePickupNo);
            }}

            function switchTab(tab) {{
                if (tab === 'order') {{
                    document.getElementById('tabOrder').classList.add('active');
                    document.getElementById('tabStatus').classList.remove('active');
                    document.getElementById('panelOrder').style.display = 'block';
                    document.getElementById('panelStatus').style.display = 'none';
                }} else {{
                    document.getElementById('tabStatus').classList.add('active');
                    document.getElementById('tabOrder').classList.remove('active');
                    document.getElementById('panelOrder').style.display = 'none';
                    document.getElementById('panelStatus').style.display = 'block';
                    queryMyOrders();
                }}
            }}

            function setProduct(p, el) {{
                selectedProduct = p;
                el.parentElement.querySelectorAll('.opt-btn').forEach(btn => btn.classList.remove('active'));
                el.classList.add('active');
            }}

            function setTemp(t, el) {{
                selectedTemp = t;
                el.parentElement.querySelectorAll('.opt-btn').forEach(btn => btn.classList.remove('active'));
                el.classList.add('active');
            }}

            async function doSubmit() {{
                const name = document.getElementById('userName').value.trim();
                const remark = document.getElementById('remark').value.trim();
                const btn = document.getElementById('submitBtn');

                if (!name) {{
                    alert('请输入您的花名或昵称！');
                    document.getElementById('userName').focus();
                    return;
                }}

                localStorage.setItem('coffee_user_name', name);
                btn.disabled = true;
                btn.innerText = '正在提交订单...';

                try {{
                    const res = await fetch('/api/order', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            user_name: name,
                            product: selectedProduct,
                            temp: selectedTemp,
                            remark: remark
                        }})
                    }});
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || data.error || '下单失败');

                    activeOrderNo = data.orderNo;
                    activePickupNo = data.pickupNo;
                    localStorage.setItem('last_order_no', activeOrderNo);
                    localStorage.setItem('last_pickup_no', activePickupNo);

                    startPollingStatus(activeOrderNo, activePickupNo);
                }} catch (e) {{
                    alert('下单失败: ' + e.message);
                }} finally {{
                    btn.disabled = false;
                    btn.innerText = '立即下单';
                }}
            }}

            function startPollingStatus(orderNo, pickupNo) {{
                const banner = document.getElementById('liveStatusCard');
                banner.style.display = 'block';
                document.getElementById('bannerPickupNo').innerText = pickupNo || '----';

                if (pollingTimer) clearInterval(pollingTimer);
                checkStatus(orderNo, pickupNo);
                pollingTimer = setInterval(() => checkStatus(orderNo, pickupNo), 8000);
            }}

            async function checkStatus(orderNo, pickupNo) {{
                try {{
                    const res = await fetch(`/api/order/status?order_no=${{encodeURIComponent(orderNo)}}&pickup_no=${{encodeURIComponent(pickupNo)}}`);
                    const data = await res.json();
                    if (!data.found) return;

                    const banner = document.getElementById('liveStatusCard');
                    const textEl = document.getElementById('bannerStatusText');
                    const descEl = document.getElementById('bannerDesc');
                    const titleEl = document.getElementById('bannerTitle');

                    textEl.innerText = data.statusText;
                    textEl.style.color = data.statusColor;
                    descEl.innerText = data.statusDesc;

                    ['step1', 'step2', 'step3', 'step4'].forEach(id => document.getElementById(id).classList.remove('active'));

                    if (data.status === 'pending') {{
                        banner.className = 'status-banner making';
                        titleEl.innerText = '⏳ 订单已提交，排队中...';
                        document.getElementById('step1').classList.add('active');
                    }} else if (data.status === 'making') {{
                        banner.className = 'status-banner making';
                        titleEl.innerText = '👨‍🍳 咖啡师正在制作中...';
                        document.getElementById('step1').classList.add('active');
                        document.getElementById('step2').classList.add('active');
                    }} else if (data.status === 'ready') {{
                        banner.className = 'status-banner ready';
                        titleEl.innerText = '🔔 咖啡做好了！请前往吧台领取';
                        document.getElementById('step1').classList.add('active');
                        document.getElementById('step2').classList.add('active');
                        document.getElementById('step3').classList.add('active');
                    }} else if (data.status === 'completed') {{
                        banner.className = 'status-banner';
                        titleEl.innerText = '✅ 订单已完成取餐';
                        document.getElementById('step1').classList.add('active');
                        document.getElementById('step2').classList.add('active');
                        document.getElementById('step3').classList.add('active');
                        document.getElementById('step4').classList.add('active');
                        clearInterval(pollingTimer);
                    }}
                }} catch(e) {{
                    console.log('轮询状态异常', e);
                }}
            }}

            async function queryMyOrders() {{
                const keyword = document.getElementById('queryName').value.trim();
                const listEl = document.getElementById('ordersList');
                listEl.innerHTML = '<div style="text-align:center; color:#888; padding:20px;">正在获取订单列表...</div>';

                try {{
                    const res = await fetch('/api/orders/my');
                    const data = await res.json();
                    const items = data.items || [];

                    let filtered = items;
                    if (keyword) {{
                        filtered = items.filter(it => 
                            (it.remark && it.remark.includes(keyword)) ||
                            it.pickupNo === keyword ||
                            it.orderNo === keyword
                        );
                    }}

                    if (filtered.length === 0) {{
                        listEl.innerHTML = `<div style="text-align:center; color:#888; padding:30px;">未找到与 "${{keyword}}" 相关的订单</div>`;
                        return;
                    }}

                    let html = '';
                    filtered.slice(0, 15).forEach(it => {{
                        const productInfo = it.items && it.items[0] ? `${{it.items[0].productName}} (${{it.items[0].specInfo || ''}})` : '咖啡';
                        html += `
                        <div class="order-item-card">
                            <div class="order-top">
                                <span style="font-size: 20px; font-weight: 800; color: var(--primary);">取餐号: ${{it.pickupNo}}</span>
                                <span class="status-tag" style="background: ${{it.statusColor}}20; color: ${{it.statusColor}};">${{it.statusText}}</span>
                            </div>
                            <div style="font-size: 14px; font-weight: 600; margin-bottom: 4px;">${{productInfo}}</div>
                            <div style="font-size: 12px; color: #555;">${{it.remark || '无备注'}}</div>
                            <div style="font-size: 11px; color: #999; margin-top: 6px;">下单时间: ${{new Date(it.createdAt).toLocaleTimeString()}} · 状态: ${{it.statusDesc}}</div>
                        </div>
                        `;
                    }});
                    listEl.innerHTML = html;
                }} catch (e) {{
                    listEl.innerHTML = `<div style="color:red; text-align:center; padding:20px;">加载失败: ${{e.message}}</div>`;
                }}
            }}
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)
