"""
R Cafe 开放点单服务主程序 (FastAPI)
包含：
1. 正常营业模式 (DRY_RUN=false)：真实咖啡接口下单
2. 出餐通知精准推送：仅在【待取餐（ready）】时推送1次，【取餐完成（completed）】绝对不二次打扰
3. 紧凑型双通道引导（精简顶部通知条，释放首屏空间）
4. 0ms 即时触觉反馈 + 按钮加载动效 + 实时出单骨架卡片
5. 制作进度 / 记录页面：输入花名/取餐码实时即时筛选（无需手动点查询）
6. 飞书多维表格 / 自定义 Webhook 自动化点单
7. 后台自动巡检与飞书取餐就绪 Webhook 推送
"""
import os
import json
import random
import asyncio
from datetime import datetime
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
mock_orders_cache: Dict[str, dict] = {}
notified_ready_orders = set()

STATUS_MAP = {
    "pending": {"text": "待制作", "color": "#E67E22", "desc": "订单已提交，吧台排队中..."},
    "making": {"text": "制作中", "color": "#2F6FED", "desc": "咖啡师正在制作您的饮品..."},
    "ready": {"text": "待取餐（餐好）", "color": "#27AE60", "desc": "🎉 咖啡已做好，请前往吧台领取！"},
    "completed": {"text": "已完成", "color": "#646A73", "desc": "已核销取餐"},
    "cancelled": {"text": "已取消", "color": "#E5484D", "desc": "订单已取消，R豆已原路退回"}
}


@app.on_event("startup")
async def startup_event():
    """服务启动时，初始化后台监听任务"""
    asyncio.create_task(order_status_monitor_loop())


async def order_status_monitor_loop():
    """后台任务：每 15 秒轮询一次，仅在订单餐好时推送1次飞书 Webhook（取餐完成不二次提醒）"""
    print(f"[Monitor] 启动后台订单状态监听巡检 (当前测试模式: {config.DRY_RUN})...")
    try:
        init_data = client.get_my_orders()
        for item in init_data.get("items", []):
            if item.get("status") in ("ready", "completed"):
                notified_ready_orders.add(item.get("orderNo"))
        print(f"[Monitor] 已初始化真实历史订单缓存: {len(notified_ready_orders)} 笔")
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
                
                # 仅在待取餐（ready）时发送提醒，避免在取餐完成（completed）时二次打扰
                if status == "ready" and order_no not in notified_ready_orders:
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
                        print(f"[Monitor] 触发出餐提醒: {order_no} ({pickup_no}), 响应: {resp.text}")
                        notified_ready_orders.add(order_no)
                    except Exception as post_err:
                        print(f"[Monitor] 推送飞书出餐提醒失败: {post_err}")
                elif status == "completed":
                    # 取餐完成（completed），直接记录为已通知，绝不发送二次打扰通知
                    notified_ready_orders.add(order_no)
        except Exception as poll_err:
            print(f"[Monitor] 轮询订单异常: {poll_err}")


async def simulate_mock_order_ready(order_no: str, pickup_no: str, colleague: str, product_name: str, temp: str):
    """测试模式专享：10秒后自动触发完成通知"""
    await asyncio.sleep(10)
    print(f"[Mock] 测试订单 {order_no} 模拟制作完成，准备触发飞书通知...")
    
    if order_no in mock_orders_cache:
        mock_orders_cache[order_no]["status"] = "ready"
        mock_orders_cache[order_no]["statusText"] = "待取餐（餐好）"
        mock_orders_cache[order_no]["statusColor"] = "#27AE60"
        mock_orders_cache[order_no]["statusDesc"] = "🎉【测试演练】咖啡已做好，请前往吧台领取！"
        mock_orders_cache[order_no]["isReady"] = True

    if config.FEISHU_NOTIFY_WEBHOOK:
        payload = {
            "pickup_no": pickup_no,
            "order_no": order_no,
            "colleague": colleague,
            "product": product_name,
            "spec": f"中杯 / {temp} / 无糖",
            "status": "待取餐",
            "message": f"☕【测试演练通知】您的 {product_name} 已做好！取餐码为 {pickup_no}，请尽快前往吧台领取！"
        }
        try:
            resp = requests.post(config.FEISHU_NOTIFY_WEBHOOK, json=payload, timeout=8)
            print(f"[Mock] 测试订单飞书通知已发送: {order_no}, 飞书响应: {resp.text}")
        except Exception as e:
            print(f"[Mock] 发送测试通知异常: {e}")


def handle_dry_run_order(user_name: str, product_str: str, temp_str: str, remark_str: Optional[str] = None) -> dict:
    """生成测试模拟订单"""
    pickup_no = f"T{random.randint(101, 899)}"
    order_no = f"TEST{datetime.now().strftime('%m%d%H%M%S')}"
    
    p_name = "拿铁" if "拿铁" in product_str or "latte" in product_str.lower() else "美式咖啡"
    t_name = "冰饮" if any(x in temp_str for x in ["冰", "冷", "ice", "cold"]) else "热饮"

    full_remark = f"点单人: {user_name} (测试演练)"
    if remark_str and remark_str.strip():
        full_remark += f" | {remark_str.strip()}"

    res_data = {
        "pickupNo": pickup_no,
        "orderNo": order_no,
        "productName": p_name,
        "temp": t_name,
        "cost": 0.0,
        "colleague": user_name,
        "remark": full_remark,
        "status": "making",
        "statusText": "制作中 (测试模拟)",
        "statusColor": "#2F6FED",
        "statusDesc": "【测试模式】系统正在模拟制作，10秒后自动触发完成通知...",
        "isReady": False,
        "createdAt": datetime.now().isoformat()
    }

    mock_orders_cache[order_no] = res_data
    return res_data


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
            "dryRun": config.DRY_RUN,
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
    """获取最新点单列表与取餐状态（整合测试模拟单）"""
    try:
        data = client.get_my_orders()
        items = data.get("items", [])
        
        for mock_item in list(mock_orders_cache.values())[::-1]:
            items.insert(0, {
                "orderNo": mock_item["orderNo"],
                "pickupNo": mock_item["pickupNo"],
                "status": mock_item["status"],
                "statusText": mock_item["statusText"],
                "statusColor": mock_item["statusColor"],
                "statusDesc": mock_item["statusDesc"],
                "remark": mock_item["remark"],
                "createdAt": mock_item["createdAt"],
                "items": [{
                    "productName": mock_item["productName"],
                    "specInfo": f"中杯 / {mock_item['temp']} / 无糖"
                }]
            })

        for item in items:
            st = item.get("status", "unknown")
            if "statusText" not in item:
                info = STATUS_MAP.get(st, {"text": st, "color": "#888", "desc": ""})
                item["statusText"] = info["text"]
                item["statusColor"] = info["color"]
                item["statusDesc"] = info["desc"]
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/order/status")
def get_order_status(order_no: Optional[str] = None, pickup_no: Optional[str] = None, user_name: Optional[str] = None):
    """单单查询或按姓名查询最新订单进度（支持模拟测试单）"""
    if order_no and order_no in mock_orders_cache:
        m = mock_orders_cache[order_no]
        return {
            "found": True,
            "orderNo": m["orderNo"],
            "pickupNo": m["pickupNo"],
            "status": m["status"],
            "statusText": m["statusText"],
            "statusColor": m["statusColor"],
            "statusDesc": m["statusDesc"],
            "isReady": m.get("isReady", False),
            "remark": m["remark"],
            "items": [{"productName": m["productName"]}]
        }

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
async def submit_order_web(order: OrderSubmitReq):
    """Web 界面下单接口"""
    if not config.ALLOW_ORDERING:
        raise HTTPException(status_code=400, detail="服务当前已暂停接单")

    user_name = order.user_name.strip()
    if not user_name:
        raise HTTPException(status_code=400, detail="请输入您的姓名或花名")

    # 显式测试前缀才走 mock，正常用户走真实下单
    is_mock = config.DRY_RUN or any(x in user_name for x in ["测试", "test", "Test"])
    if is_mock:
        res = handle_dry_run_order(
            user_name=user_name,
            product_str=order.product,
            temp_str=order.temp,
            remark_str=order.remark
        )
        asyncio.create_task(simulate_mock_order_ready(res["orderNo"], res["pickupNo"], user_name, res["productName"], res["temp"]))
        return {
            "status": "success",
            "message": "【测试演练成功】已生成模拟取餐号，10秒后将自动模拟出餐通知",
            **res
        }

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

    is_mock = config.DRY_RUN or data.get("dry_run") is True or any(x in user_name for x in ["测试", "test", "Test"])
    if is_mock:
        print(f"[Webhook] 收到飞书测试演练点单: {user_name} - {product} ({temp})")
        res = handle_dry_run_order(
            user_name=user_name,
            product_str=product,
            temp_str=temp,
            remark_str=remark
        )
        asyncio.create_task(simulate_mock_order_ready(res["orderNo"], res["pickupNo"], user_name, res["productName"], res["temp"]))
        return {
            "status": "success",
            "code": 0,
            "message": "【演练模式】测试下单成功（未真实扣豆），10秒后将触发完成通知",
            "data": res
        }

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
    """紧凑现代化响应式前台"""
    status_tag_html = '<div class="status-pill" style="color:#D97706; background:#FEF3C7;">● 测试演练模式 (不扣豆)</div>' if config.DRY_RUN else '<div class="status-pill">● 营业中</div>'

    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>R Café</title>
        <style>
            :root {{
                --primary: #00704A;
                --primary-dark: #005538;
                --primary-light: #EBF7EE;
                --feishu-blue: #3370FF;
                --bg: #F5F7FA;
                --card: #FFFFFF;
                --text-main: #1F2329;
                --text-sub: #646A73;
                --text-tip: #8F959E;
                --border: #DEE0E3;
            }}
            * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
                background-color: var(--bg);
                color: var(--text-main);
                margin: 0;
                padding: 12px 14px;
                display: flex;
                justify-content: center;
            }}
            .container {{
                max-width: 480px;
                width: 100%;
            }}

            .feishu-hero-card {{
                background: linear-gradient(135deg, #F0F6FF 0%, #E8F2FF 100%);
                border: 1.5px solid #BFDBFE;
                border-radius: 14px;
                padding: 16px 16px 14px 16px;
                margin-bottom: 14px;
                box-shadow: 0 4px 14px rgba(51, 112, 255, 0.08);
                position: relative;
                overflow: hidden;
            }}
            .feishu-hero-card::after {{
                content: "☕";
                position: absolute;
                right: -8px;
                bottom: -10px;
                font-size: 72px;
                opacity: 0.12;
                pointer-events: none;
            }}
            .hero-badge-row {{
                display: flex;
                align-items: center;
                gap: 6px;
                margin-bottom: 6px;
            }}
            .hero-badge {{
                background: var(--feishu-blue);
                color: #FFF;
                font-size: 11px;
                font-weight: 700;
                padding: 2px 7px;
                border-radius: 4px;
                letter-spacing: 0.5px;
            }}
            .hero-sub-tip {{
                font-size: 12px;
                font-weight: 600;
                color: #2563EB;
            }}
            .hero-title {{
                font-size: 15.5px;
                font-weight: 800;
                color: #1E293B;
                margin-bottom: 4px;
            }}
            .hero-desc {{
                font-size: 12.5px;
                color: #475569;
                line-height: 1.5;
                margin-bottom: 12px;
            }}
            .hero-btn {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
                background: var(--feishu-blue);
                color: #FFF;
                text-decoration: none;
                padding: 11px 16px;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 700;
                box-shadow: 0 3px 8px rgba(51, 112, 255, 0.28);
                transition: all 0.15s ease;
            }}
            .hero-btn:active {{
                background: #255DD9;
                transform: scale(0.985);
            }}

            .channel-divider {{
                display: flex;
                align-items: center;
                margin: 14px 4px 12px 4px;
                color: #94A3B8;
                font-size: 12px;
                font-weight: 500;
            }}
            .channel-divider::before,
            .channel-divider::after {{
                content: "";
                flex: 1;
                height: 1px;
                background: #E2E8F0;
            }}
            .channel-divider span {{
                padding: 0 10px;
            }}

            .brand-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 10px;
                padding: 0 4px;
            }}
            .brand-title {{
                font-size: 20px;
                font-weight: 800;
                color: var(--primary);
                display: flex;
                align-items: center;
                gap: 6px;
            }}
            .status-pill {{
                font-size: 12px;
                color: #2E7D32;
                background: var(--primary-light);
                padding: 3px 8px;
                border-radius: 12px;
                font-weight: 600;
            }}

            .nav-tabs {{
                display: flex;
                background: #E8ECEF;
                border-radius: 8px;
                padding: 2.5px;
                margin-bottom: 12px;
            }}
            .nav-tab {{
                flex: 1;
                text-align: center;
                padding: 7px;
                font-size: 13.5px;
                font-weight: 600;
                cursor: pointer;
                border-radius: 6px;
                color: var(--text-sub);
                transition: all 0.15s ease;
            }}
            .nav-tab.active {{
                background: #FFF;
                color: var(--primary);
                box-shadow: 0 1px 4px rgba(0,0,0,0.06);
            }}

            .card {{
                background: var(--card);
                border-radius: 14px;
                padding: 18px;
                box-shadow: 0 2px 12px rgba(0, 0, 0, 0.04);
                margin-bottom: 12px;
            }}
            .form-item {{
                margin-bottom: 14px;
            }}
            .form-label {{
                display: block;
                font-size: 13.5px;
                font-weight: 600;
                margin-bottom: 6px;
            }}
            .form-tip {{
                font-size: 11.5px;
                color: var(--text-tip);
                margin-top: 4px;
            }}
            input[type="text"] {{
                width: 100%;
                padding: 11px 12px;
                border: 1.5px solid var(--border);
                border-radius: 8px;
                font-size: 14.5px;
                outline: none;
                transition: border-color 0.2s;
            }}
            input[type="text"]:focus {{
                border-color: var(--primary);
            }}

            .grid-options {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }}
            .opt-btn {{
                padding: 11px;
                border: 1.5px solid var(--border);
                background: #FFF;
                border-radius: 8px;
                text-align: center;
                cursor: pointer;
                transition: all 0.15s ease;
            }}
            .opt-btn:active {{
                transform: scale(0.97);
            }}
            .opt-btn .title {{
                font-size: 14.5px;
                font-weight: 700;
            }}
            .opt-btn .sub {{
                font-size: 11.5px;
                color: var(--text-sub);
                margin-top: 2px;
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
                padding: 13px;
                background: var(--primary);
                color: #FFF;
                border: none;
                border-radius: 10px;
                font-size: 15.5px;
                font-weight: 700;
                cursor: pointer;
                box-shadow: 0 4px 10px rgba(0, 112, 74, 0.2);
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
                transition: transform 0.08s ease, background 0.2s;
            }}
            .submit-btn:active {{
                transform: scale(0.98);
                background: var(--primary-dark);
            }}
            .submit-btn:disabled {{
                background: #99C7B6;
                cursor: not-allowed;
                transform: none;
                box-shadow: none;
            }}

            .spinner {{
                width: 16px;
                height: 16px;
                border: 2px solid rgba(255,255,255,0.4);
                border-top-color: #FFF;
                border-radius: 50%;
                animation: spin 0.7s linear infinite;
                display: none;
            }}
            @keyframes spin {{
                to {{ transform: rotate(360deg); }}
            }}

            .status-banner {{
                position: relative;
                border-radius: 12px;
                padding: 16px;
                margin-top: 12px;
                text-align: center;
                display: none;
                transition: all 0.3s ease;
            }}
            .status-banner .card-close {{
                position: absolute;
                top: 8px;
                right: 12px;
                font-size: 20px;
                color: #94A3B8;
                cursor: pointer;
                line-height: 1;
                padding: 2px 6px;
                border-radius: 4px;
            }}
            .status-banner .card-close:hover {{
                color: #475569;
                background: rgba(0, 0, 0, 0.05);
            }}
            .status-banner.submitting {{
                background: #F8FAFC;
                border: 1.5px dashed #CBD5E1;
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
                70% {{ box-shadow: 0 0 0 8px rgba(34, 197, 94, 0); }}
                100% {{ box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }}
            }}
            .pickup-badge-title {{
                font-size: 13px;
                font-weight: 600;
            }}
            .pickup-code-lg {{
                font-size: 38px;
                font-weight: 800;
                margin: 4px 0;
                letter-spacing: 2px;
            }}
            .stepper {{
                display: flex;
                justify-content: space-between;
                margin: 14px 0 6px 0;
                position: relative;
            }}
            .step-item {{
                flex: 1;
                text-align: center;
                font-size: 11.5px;
                color: #999;
                font-weight: 500;
            }}
            .step-item.active {{
                color: var(--primary);
                font-weight: 700;
            }}
            .step-circle {{
                width: 20px;
                height: 20px;
                border-radius: 50%;
                background: #E5E7EB;
                color: #666;
                display: flex;
                align-items: center;
                justify-content: center;
                margin: 0 auto 3px auto;
                font-size: 10.5px;
            }}
            .step-item.active .step-circle {{
                background: var(--primary);
                color: #FFF;
            }}
            .order-item-card {{
                border: 1px solid var(--border);
                border-radius: 10px;
                padding: 12px;
                margin-bottom: 10px;
                background: #FFF;
                transition: transform 0.15s ease;
            }}
            .order-top {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 4px;
            }}
            .status-tag {{
                padding: 2px 7px;
                border-radius: 4px;
                font-size: 11.5px;
                font-weight: 600;
            }}

            .order-group-header {{
                font-size: 13px;
                font-weight: 700;
                color: #475569;
                margin: 6px 0 8px 2px;
                display: flex;
                align-items: center;
                gap: 6px;
            }}
            .history-collapse-bar {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                background: #F1F5F9;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                padding: 10px 14px;
                margin: 14px 0 10px 0;
                cursor: pointer;
                transition: background 0.15s ease;
                user-select: none;
            }}
            .history-collapse-bar:hover {{
                background: #E2E8F0;
            }}
            .history-bar-title {{
                font-size: 13px;
                font-weight: 700;
                color: #334155;
            }}
            .history-bar-count {{
                font-size: 12px;
                color: #64748B;
            }}
            .history-toggle-action {{
                font-size: 12px;
                color: #2563EB;
                font-weight: 600;
                display: flex;
                align-items: center;
                gap: 4px;
            }}

            .search-box-wrapper {{
                display: flex;
                gap: 8px;
                align-items: center;
            }}
            .search-input-rel {{
                position: relative;
                flex: 1;
            }}
            .clear-icon {{
                position: absolute;
                right: 10px;
                top: 50%;
                transform: translateY(-50%);
                cursor: pointer;
                color: #999;
                font-size: 17px;
                display: none;
                width: 20px;
                height: 20px;
                line-height: 18px;
                text-align: center;
                border-radius: 50%;
                background: #EAECEF;
            }}
            .btn-refresh {{
                padding: 10px 12px;
                background: #FFF;
                color: var(--text-main);
                border: 1.5px solid var(--border);
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 4px;
                white-space: nowrap;
                transition: all 0.15s ease;
            }}
            .btn-refresh:active {{
                background: #E8ECEF;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="brand-header">
                <div class="brand-title">☕ R Café 自助点单</div>
                {status_tag_html}
            </div>

            <div class="nav-tabs">
                <div class="nav-tab active" id="tabOrder" onclick="switchTab('order')">自助点单</div>
                <div class="nav-tab" id="tabStatus" onclick="switchTab('status')">制作进度 / 记录</div>
            </div>

            <div id="panelOrder">
                <!-- 飞书通知优先推荐卡片 -->
                <div class="feishu-hero-card">
                    <div class="hero-badge-row">
                        <span class="hero-badge">🔔 首选推荐</span>
                        <span class="hero-sub-tip">咖啡做好 飞书弹窗私聊提醒</span>
                    </div>
                    <div class="hero-title">用飞书问卷点单</div>
                    <div class="hero-desc">
                        制作完成自动发送飞书私信通知，附带专属取餐码，免盯网页不漏取
                    </div>
                    <a href="{config.FEISHU_FORM_URL}" target="_blank" class="hero-btn">
                        <span>👉 前往飞书问卷点单 (享出餐私信通知)</span>
                        <span>&rarr;</span>
                    </a>
                </div>

                <div class="channel-divider">
                    <span>或在下方网页直接点单（仅网页查看进度）</span>
                </div>

                <div class="card">
                    <div class="form-item">
                        <label class="form-label">点单署名 / 昵称 <span style="color:#E5484D">*</span></label>
                        <input type="text" id="userName" placeholder="将打印在咖啡杯贴备注上" required autocomplete="off">
                        <div class="form-tip">网页点单需在网页「制作进度」查看出餐状态，无飞书弹窗提醒</div>
                    </div>

                    <div class="form-item">
                        <label class="form-label">选择咖啡</label>
                        <div class="grid-options">
                            <div class="opt-btn active" onclick="setProduct('latte', this)">
                                <div class="title">拿铁</div>
                                <div class="sub">6.9 R豆 · 中杯/无糖</div>
                            </div>
                            <div class="opt-btn" onclick="setProduct('americano', this)">
                                <div class="title">美式咖啡</div>
                                <div class="sub">4.9 R豆 · 中杯/无糖</div>
                            </div>
                        </div>
                    </div>

                    <div class="form-item">
                        <label class="form-label">温度规格</label>
                        <div class="grid-options">
                            <div class="opt-btn active" onclick="setTemp('热饮', this)">
                                <div class="title">热饮</div>
                                <div class="sub">标准温度</div>
                            </div>
                            <div class="opt-btn" onclick="setTemp('冰饮', this)">
                                <div class="title">冰饮</div>
                                <div class="sub">加冰清凉</div>
                            </div>
                        </div>
                    </div>

                    <div class="form-item">
                        <label class="form-label">额外备注（选填）</label>
                        <input type="text" id="remark" placeholder="如：少冰、稍后做等">
                    </div>

                    <button class="submit-btn" id="submitBtn" onclick="doSubmit()">
                        <div class="spinner" id="btnSpinner"></div>
                        <span id="btnText">立即下单</span>
                    </button>

                    <!-- 出单进度卡片：仅在当前点单 Tab 内展示 -->
                    <div class="status-banner" id="liveStatusCard">
                        <span class="card-close" onclick="dismissLiveCard()" title="关闭进度卡片">&times;</span>
                        <div class="pickup-badge-title" id="bannerTitle">订单提交中...</div>
                        <div class="pickup-code-lg" id="bannerPickupNo">----</div>
                        <div id="bannerStatusText" style="font-size: 15px; font-weight: 700; margin-bottom: 4px;"></div>
                        <div id="bannerDesc" style="font-size: 12.5px; color: #555;"></div>

                        <div class="stepper">
                            <div class="step-item" id="step1"><div class="step-circle">1</div>待制作</div>
                            <div class="step-item" id="step2"><div class="step-circle">2</div>制作中</div>
                            <div class="step-item" id="step3"><div class="step-circle">3</div>餐好待取</div>
                            <div class="step-item" id="step4"><div class="step-circle">4</div>已取餐</div>
                        </div>
                        <div style="font-size: 11.5px; color: #8F959E; margin-top: 8px;">⚡ 页面每 8 秒自动轮询，咖啡做好将变绿提醒</div>
                    </div>
                </div>
            </div>

            <div id="panelStatus" style="display: none;">
                <div class="card" style="margin-bottom: 10px; padding: 12px 14px;">
                    <div class="search-box-wrapper">
                        <div class="search-input-rel">
                            <input type="text" id="queryName" placeholder="输入花名、取餐号实时筛选..." oninput="onSearchInput()" autocomplete="off">
                            <span class="clear-icon" id="clearSearchBtn" onclick="clearSearch()">&times;</span>
                        </div>
                        <button class="btn-refresh" onclick="fetchOrders(true)">
                            <span id="refreshIcon">🔄</span> 刷新
                        </button>
                    </div>
                </div>

                <div id="ordersList">
                    <div style="text-align: center; color: #888; font-size: 13px; padding: 20px;">正在加载订单进度...</div>
                </div>
            </div>
        </div>

        <script>
            let selectedProduct = 'latte';
            let selectedTemp = '热饮';
            let activeOrderNo = localStorage.getItem('last_order_no') || null;
            let activePickupNo = localStorage.getItem('last_pickup_no') || null;
            let pollingTimer = null;
            let allOrdersCache = [];

            let savedName = localStorage.getItem('coffee_user_name') || '';
            // 若缓存包含测试关键词，立即抹除，避免每次打开误入测试分支
            if (/测试|test|mock/i.test(savedName)) {{
                localStorage.removeItem('coffee_user_name');
                savedName = '';
            }}
            if (savedName) {{
                document.getElementById('userName').value = savedName;
            }}
            // 筛选框默认保持为空，进入点单记录时展示完整出单队列
            document.getElementById('queryName').value = '';

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
                    fetchOrders(true);
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
                const btnSpinner = document.getElementById('btnSpinner');
                const btnText = document.getElementById('btnText');
                const banner = document.getElementById('liveStatusCard');

                if (!name) {{
                    alert('请输入您的花名或昵称！');
                    document.getElementById('userName').focus();
                    return;
                }}

                if (!/测试|test|mock/i.test(name)) {{
                    localStorage.setItem('coffee_user_name', name);
                }} else {{
                    localStorage.removeItem('coffee_user_name');
                }}

                btn.disabled = true;
                btnSpinner.style.display = 'block';
                btnText.innerText = '正在向咖啡厅下单...';

                banner.className = 'status-banner submitting';
                banner.style.display = 'block';
                document.getElementById('bannerTitle').innerText = '☕ 正在连接咖啡厅出餐系统...';
                document.getElementById('bannerPickupNo').innerText = '...';
                document.getElementById('bannerStatusText').innerText = '提交中';
                document.getElementById('bannerStatusText').style.color = '#646A73';
                document.getElementById('bannerDesc').innerText = '正在为您分配取餐码...';

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
                    
                    let data;
                    const resText = await res.text();
                    try {{
                        data = JSON.parse(resText);
                    }} catch(e) {{
                        throw new Error(resText || '服务器内部错误');
                    }}

                    if (!res.ok) throw new Error(data.detail || data.error || data.message || '下单失败');

                    activeOrderNo = data.orderNo;
                    activePickupNo = data.pickupNo;
                    localStorage.setItem('last_order_no', activeOrderNo);
                    localStorage.setItem('last_pickup_no', activePickupNo);

                    startPollingStatus(activeOrderNo, activePickupNo);
                }} catch (e) {{
                    banner.style.display = 'none';
                    alert('下单失败: ' + e.message);
                }} finally {{
                    btn.disabled = false;
                    btnSpinner.style.display = 'none';
                    btnText.innerText = '立即下单';
                }}
            }}

            function dismissLiveCard() {{
                const banner = document.getElementById('liveStatusCard');
                if (banner) banner.style.display = 'none';
                if (pollingTimer) clearInterval(pollingTimer);
                localStorage.removeItem('last_order_no');
                localStorage.removeItem('last_pickup_no');
            }}

            function startPollingStatus(orderNo, pickupNo) {{
                const banner = document.getElementById('liveStatusCard');
                banner.style.display = 'block';
                document.getElementById('bannerPickupNo').innerText = pickupNo || '----';
                document.getElementById('bannerTitle').innerText = '☕ 正在同步制作进度...';

                if (pollingTimer) clearInterval(pollingTimer);
                checkStatus(orderNo, pickupNo);
                pollingTimer = setInterval(() => checkStatus(orderNo, pickupNo), 8000);
            }}

            async function checkStatus(orderNo, pickupNo) {{
                try {{
                    const res = await fetch(`/api/order/status?order_no=${{encodeURIComponent(orderNo)}}&pickup_no=${{encodeURIComponent(pickupNo)}}`);
                    const data = await res.json();
                    const banner = document.getElementById('liveStatusCard');

                    if (!data || !data.found) {{
                        // 未查询到有效订单（如旧测试单或已失效单）-> 立即清空缓存并隐藏
                        dismissLiveCard();
                        return;
                    }}

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

            async function fetchOrders(showLoading = false) {{
                const listEl = document.getElementById('ordersList');
                if (showLoading && allOrdersCache.length === 0) {{
                    listEl.innerHTML = '<div style="text-align:center; color:#888; padding:20px;">正在获取订单列表...</div>';
                }}

                try {{
                    const res = await fetch('/api/orders/my');
                    const data = await res.json();
                    allOrdersCache = data.items || [];
                    renderFilteredOrders();
                }} catch (e) {{
                    if (allOrdersCache.length === 0) {{
                        listEl.innerHTML = `<div style="color:red; text-align:center; padding:20px;">加载失败: ${{e.message}}</div>`;
                    }}
                }}
            }}

            function onSearchInput() {{
                const val = document.getElementById('queryName').value.trim();
                document.getElementById('clearSearchBtn').style.display = val ? 'block' : 'none';
                renderFilteredOrders();
            }}

            function clearSearch() {{
                document.getElementById('queryName').value = '';
                document.getElementById('clearSearchBtn').style.display = 'none';
                renderFilteredOrders();
                document.getElementById('queryName').focus();
            }}

            let historyExpanded = false;

            function isOrderToday(dateStr) {{
                if (!dateStr) return false;
                const d = new Date(dateStr);
                if (isNaN(d.getTime())) return false;
                return d.toDateString() === (new Date()).toDateString();
            }}

            function formatOrderTime(dateStr) {{
                if (!dateStr) return '';
                const d = new Date(dateStr);
                if (isNaN(d.getTime())) return dateStr;
                const now = new Date();
                const isCurrent = d.toDateString() === now.toDateString();
                const timeStr = d.toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
                if (isCurrent) {{
                    return `今天 ${{timeStr}}`;
                }}
                const yesterday = new Date(now);
                yesterday.setDate(yesterday.getDate() - 1);
                if (d.toDateString() === yesterday.toDateString()) {{
                    return `昨天 ${{timeStr}}`;
                }}
                const month = (d.getMonth() + 1).toString().padStart(2, '0');
                const day = d.getDate().toString().padStart(2, '0');
                return `${{month}}-${{day}} ${{timeStr}}`;
            }}

            function toggleHistoryOrders() {{
                historyExpanded = !historyExpanded;
                const box = document.getElementById('historyOrdersBox');
                const icon = document.getElementById('historyToggleIcon');
                const text = document.getElementById('historyToggleText');
                if (box) box.style.display = historyExpanded ? 'block' : 'none';
                if (icon) icon.innerText = historyExpanded ? '▲' : '▼';
                if (text) text.innerText = historyExpanded ? '收起' : '展开查看';
            }}

            function renderOrderCard(it) {{
                const productInfo = it.items && it.items[0] ? `${{it.items[0].productName}} (${{it.items[0].specInfo || ''}})` : '咖啡';
                const timeFormatted = formatOrderTime(it.createdAt);
                return `
                <div class="order-item-card">
                    <div class="order-top">
                        <span style="font-size: 18px; font-weight: 800; color: var(--primary);">取餐号: ${{it.pickupNo}}</span>
                        <span class="status-tag" style="background: ${{it.statusColor}}20; color: ${{it.statusColor}};">${{it.statusText}}</span>
                    </div>
                    <div style="font-size: 13.5px; font-weight: 600; margin-bottom: 3px;">${{productInfo}}</div>
                    <div style="font-size: 12px; color: #555;">${{it.remark || '无备注'}}</div>
                    <div style="font-size: 11px; color: #999; margin-top: 5px;">下单时间: ${{timeFormatted}} · 状态: ${{it.statusDesc}}</div>
                </div>
                `;
            }}

            function renderFilteredOrders() {{
                const keyword = document.getElementById('queryName').value.trim().toLowerCase();
                const listEl = document.getElementById('ordersList');
                const clearBtn = document.getElementById('clearSearchBtn');
                if (clearBtn) clearBtn.style.display = keyword ? 'block' : 'none';

                let filtered = allOrdersCache;
                if (keyword) {{
                    filtered = allOrdersCache.filter(it => {{
                        const remark = (it.remark || '').toLowerCase();
                        const pickupNo = (it.pickupNo || '').toLowerCase();
                        const orderNo = (it.orderNo || '').toLowerCase();
                        const prodName = it.items && it.items[0] ? (it.items[0].productName || '').toLowerCase() : '';
                        const statusText = (it.statusText || '').toLowerCase();
                        return remark.includes(keyword) || pickupNo.includes(keyword) || orderNo.includes(keyword) || prodName.includes(keyword) || statusText.includes(keyword);
                    }});
                }}

                if (filtered.length === 0) {{
                    listEl.innerHTML = `<div style="text-align:center; color:#888; padding:30px;">未找到匹配 "${{keyword}}" 的订单</div>`;
                    return;
                }}

                const todayOrders = [];
                const historyOrders = [];

                filtered.forEach(it => {{
                    if (isOrderToday(it.createdAt)) {{
                        todayOrders.push(it);
                    }} else {{
                        historyOrders.push(it);
                    }}
                }});

                let html = '';

                // 今日订单区
                if (todayOrders.length > 0) {{
                    html += `<div class="order-group-header"><span>☕ 今日订单</span><span style="font-size: 11.5px; color: #64748B; font-weight: 500;">（共 ${{todayOrders.length}} 单）</span></div>`;
                    todayOrders.forEach(it => {{
                        html += renderOrderCard(it);
                    }});
                }} else if (!keyword) {{
                    html += `<div style="text-align: center; color: #94A3B8; font-size: 13px; padding: 22px 0; background: #FFF; border-radius: 10px; border: 1.5px dashed var(--border); margin-bottom: 12px;">☕ 今日吧台暂无新点单</div>`;
                }}

                // 往日历史订单区 (默认折叠，若正在搜索则自动展开)
                if (historyOrders.length > 0) {{
                    const shouldShowHistory = keyword ? true : historyExpanded;
                    html += `
                    <div class="history-collapse-bar" onclick="toggleHistoryOrders()">
                        <div>
                            <span class="history-bar-title">📦 往日历史订单</span>
                            <span class="history-bar-count">（共 ${{historyOrders.length}} 单）</span>
                        </div>
                        <span class="history-toggle-action">
                            <span id="historyToggleText">${{shouldShowHistory ? '收起' : '展开查看'}}</span>
                            <span id="historyToggleIcon" style="font-size: 10px;">${{shouldShowHistory ? '▲' : '▼'}}</span>
                        </span>
                    </div>
                    <div id="historyOrdersBox" style="display: ${{shouldShowHistory ? 'block' : 'none'}};">
                        ${{historyOrders.slice(0, 30).map(it => renderOrderCard(it)).join('')}}
                    </div>
                    `;
                }}

                listEl.innerHTML = html;
            }}
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)
