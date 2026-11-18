"""
R Cafe (飞书秒搭咖啡应用) 客户端核心模块
"""
import requests
import json
from typing import Dict, List, Optional, Tuple

# 预置商品与规格字典
PRESET_PRODUCTS = {
    "americano": {
        "id": "d207db00-c102-40e4-a82e-62c062d908ed",
        "name": "美式咖啡",
        "aliases": ["美式", "美式咖啡", "americano", "coffee"],
        "price": 4.9,
        "specs": {
            "cup": {"中杯": "0863d3a9-65d0-45e2-af95-8072be5942f1"},
            "temp": {
                "热饮": "ea8a4617-82b5-4914-bcfa-325aaa5ff019",
                "冰饮": "0bd7d0c9-deb0-4a77-81cc-348617da18ef"
            },
            "sugar": {"无糖": "ad0fdea0-a37e-4219-a936-d9536dfe510b"}
        }
    },
    "latte": {
        "id": "b0afbd5f-809a-4702-b727-3bb762a62c72",
        "name": "拿铁",
        "aliases": ["拿铁", "经典拿铁", "latte"],
        "price": 6.9,
        "specs": {
            "cup": {"中杯": "92e7b7c9-69ca-4e50-805f-8fb6018d8e71"},
            "temp": {
                "热饮": "efbae013-9b7f-4eb7-9998-e7eaef5d3992",
                "冰饮": "1110e891-dde7-423f-84fb-dc0cd7f451b7"
            },
            "sugar": {"无糖": "1586471c-f84e-4d61-9418-0fd3db67b3cd"}
        }
    }
}

class RCafeClient:
    def __init__(self, csrf_token: str, session_token: str, base_url: str = "https://reachauto.feishuapp.com/app/app_17fmvspzbtf", web_did: str = "1882094366978228"):
        self.base_url = base_url.rstrip("/")
        self.csrf_token = csrf_token
        self.session_token = session_token
        self.web_did = web_did

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "referer": self.base_url,
            "x-page-route": "/app/app_17fmvspzbtf",
            "x-suda-csrf-token": self.csrf_token,
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "cookie": f"suda-csrf-token={self.csrf_token}; X-Force-Runtime-Session={self.session_token}; suda_web_did={self.web_did}"
        }

    def get_menu(self) -> dict:
        """获取咖啡菜单及规格"""
        url = f"{self.base_url}/api/coffee/menu?type=coffee"
        resp = requests.get(url, headers=self.headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_balance(self) -> dict:
        """获取个人当前余额/R豆"""
        url = f"{self.base_url}/api/coffee/points/summary"
        resp = requests.get(url, headers=self.headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_my_orders(self) -> dict:
        """获取我的历史/最新订单列表（包含取餐码）"""
        url = f"{self.base_url}/api/coffee/orders/my"
        resp = requests.get(url, headers=self.headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def create_order(self, product_id: str, spec_ids: List[str], remark: Optional[str] = None, quantity: int = 1) -> dict:
        """
        底层下单接口
        """
        url = f"{self.base_url}/api/coffee/orders"
        payload = {
            "items": [
                {
                    "productId": product_id,
                    "quantity": quantity,
                    "specIds": spec_ids
                }
            ],
            "remark": remark,
            "payType": "points",
            "pointsBeanType": "r",
            "couponInstanceId": None
        }
        resp = requests.post(url, headers=self.headers, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def order_coffee_for_colleague(
        self,
        user_name: str,
        product_str: str,
        temp_str: str,
        extra_remark: Optional[str] = None
    ) -> dict:
        """
        高阶下单函数：自动识别商品、规格，并将同事姓名规范写入备注
        """
        # 1. 匹配商品
        matched_product = None
        p_clean = product_str.strip().lower()
        for key, p_data in PRESET_PRODUCTS.items():
            if p_clean == key or any(alias in p_clean for alias in p_data["aliases"]):
                matched_product = p_data
                break

        if not matched_product:
            raise ValueError(f"未找到对应饮品: '{product_str}'，当前支持：美式、拿铁")

        # 2. 匹配温度
        t_clean = temp_str.strip().lower()
        if any(w in t_clean for w in ["冰", "冷", "ice", "cold"]):
            temp_key = "冰饮"
        elif any(w in t_clean for w in ["热", "温", "hot", "warm"]):
            temp_key = "热饮"
        else:
            raise ValueError(f"未知的温度类型: '{temp_str}'，请选择'热饮'或'冰饮'")

        temp_id = matched_product["specs"]["temp"][temp_key]
        cup_id = list(matched_product["specs"]["cup"].values())[0]
        sugar_id = list(matched_product["specs"]["sugar"].values())[0]
        spec_ids = [cup_id, temp_id, sugar_id]

        # 3. 构造备注（核心：同事姓名明确写入备注）
        user_name = user_name.strip()
        if extra_remark and extra_remark.strip():
            final_remark = f"点单人: {user_name} ({extra_remark.strip()})"
        else:
            final_remark = f"点单人: {user_name}"

        # 4. 执行下单
        self.create_order(
            product_id=matched_product["id"],
            spec_ids=spec_ids,
            remark=final_remark,
            quantity=1
        )

        # 5. 立即查询最新订单获取取餐号
        orders_info = self.get_my_orders()
        items = orders_info.get("items", [])
        latest_order = items[0] if items else {}

        return {
            "pickupNo": latest_order.get("pickupNo", "已生成"),
            "orderNo": latest_order.get("orderNo", ""),
            "productName": matched_product["name"],
            "temp": temp_key,
            "cost": matched_product["price"],
            "colleague": user_name,
            "remark": final_remark
        }
