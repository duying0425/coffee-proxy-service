# R Café 咖啡点单开放服务 (Feishu Coffee Proxy)

这是一个为公司内部飞书企业应用（ReachAuto R Café）量身打造的点单中继代理服务。
通过反向代理与协议封装，允许其他同事在不直接拥有余额账户的情况下，通过网页前端或飞书多维表格自助点单，并**自动将点单人姓名打印在杯贴备注上**。

---

## 🌟 核心特性

1. **多端支持**：
   - **响应式 Web 前端**：手机/PC 浏览器打开 `https://cafe.tmhcorps.cn` 即可秒级点单并实时获得取餐码（如 `A096`）。
   - **飞书多维表格 Webhook**：原生支持飞书 Base 表单自动化提交，下单后自动返回取餐号。
2. **规范备注（姓名上贴）**：
   - 严格将同事姓名写入订单备注（格式：`点单人: 张三` 或 `点单人: 张三 (少冰)`），吧台咖啡师做餐和打印杯贴时一目了然。
3. **风控与安全控制**：
   - 默认配置单人单日最大点单限额（默认 2 杯）。
   - 服务全局启停开关，余额不足时可随时暂停接单。
4. **轻量与高可用**：
   - 纯 Python 异步框架（FastAPI + Uvicorn），基于 Systemd 守护进程常驻后台，Nginx 反向代理。

---

## 📋 接口与服务规范

### 1. 网页前端
- **访问地址**：`https://cafe.tmhcorps.cn/`
- **功能**：同事输入姓名、选择品类（拿铁 6.9豆 / 美式 4.9豆）、规格（热饮 / 冰饮）、选填备注，提交后即刻展示取餐码。

### 2. 飞书多维表格 Webhook
- **Webhook 地址**：`https://cafe.tmhcorps.cn/api/feishu/webhook`
- **请求方式**：`POST`
- **请求头**：`Content-Type: application/json`
- **支持字段**（智能模糊兼容中英文字段）：
  ```json
  {
    "姓名": "张三",
    "饮品": "拿铁",
    "冷热": "热饮",
    "备注": "少冰"
  }
  ```
  或者标准的英文 JSON：
  ```json
  {
    "user_name": "张三",
    "product": "latte",
    "temp": "热饮",
    "remark": "少冰"
  }
  ```
- **成功返回示例**：
  ```json
  {
    "status": "success",
    "code": 0,
    "message": "下单成功",
    "data": {
      "pickupNo": "A096",
      "orderNo": "CO202611170096",
      "productName": "拿铁",
      "temp": "热饮",
      "cost": 6.9,
      "colleague": "张三",
      "remark": "点单人: 张三 (少冰)"
    }
  }
  ```

---

## 🔑 维护和更新凭证的方法

飞书秒搭的登录凭证 `X-Force-Runtime-Session`（JWT 格式）有效期通常在 **30 天左右**。当凭证过期导致点单失败时，请按照以下步骤更新：

### 第一步：抓取最新 Cookie / Token（只需 1 分钟）
1. 在电脑浏览器（Chrome 或 Edge）中打开原点单页面：
   `https://reachauto.feishuapp.com/app/app_17fmvspzbtf`
2. 按 `F12` 打开开发者工具，切换到 **Network（网络）** 选项卡，筛选 `Fetch/XHR`。
3. 刷新页面或点击一次菜单，找到请求列表中的任意一个接口（例如 `/api/coffee/menu` 或 `/api/coffee/points/summary`）。
4. 在该请求上右键 -> **Copy** -> **Copy as cURL (bash)**。
5. 从 cURL 中提取以下两个关键参数：
   - `suda-csrf-token`：位于 Cookie 或 `x-suda-csrf-token` 请求头。
   - `X-Force-Runtime-Session`：位于 Cookie 中（形如 `N:b6f7bd6edc...` 的长字符串）。

### 第二步：在服务器上更新配置文件
通过 SSH 登录服务器：
```bash
ssh aliyun-cf
cd /home/duyingfang/cafe-service
nano .env
```
修改 `.env` 中的对应项并保存：
```ini
CSRF_TOKEN=新复制的_suda-csrf-token
SESSION_TOKEN=新复制的_X-Force-Runtime-Session
```

### 第三步：重启服务
```bash
sudo systemctl restart cafe-service
```
检查服务状态确保运行正常：
```bash
sudo systemctl status cafe-service
```

---

## 🛠️ 服务端部署与日常维护命令

- **查看服务运行日志**：
  ```bash
  sudo journalctl -u cafe-service -f
  ```
- **重启服务**：
  ```bash
  sudo systemctl restart cafe-service
  ```
- **停止服务**：
  ```bash
  sudo systemctl stop cafe-service
  ```
- **重新加载 Nginx 配置**：
  ```bash
  sudo nginx -t && sudo systemctl reload nginx
  ```
