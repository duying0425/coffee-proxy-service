# R Café 咖啡点单开放中继服务 (Feishu Coffee Proxy)

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?style=flat&logo=FastAPI&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat&logo=Python&logoColor=white)](https://www.python.org/)
[![Nginx](https://img.shields.io/badge/Nginx-Reverse%20Proxy-009639.svg?style=flat&logo=Nginx&logoColor=white)](https://nginx.org/)
[![License](https://img.shields.io/badge/License-Internal%20Use-blue.svg)](#)

> 为公司内部飞书企业应用（ReachAuto R Café）量身打造的点单中继代理与自动化服务。  
> 破解与封装底层下单协议，允许同事在无需拥有个人 R 豆账户的情况下自助点单，**并将点单人姓名自动打印在咖啡杯贴备注上**。

---

## 📖 目录

- [🌟 核心特性](#-核心特性)
- [☕ 双通道点单体验](#-双通道点单体验)
- [📐 架构设计与工作流](#-架构设计与工作流)
- [🚀 快速开始与本地运行](#-快速开始与本地运行)
- [🛠️ 生产环境部署与运维](#️-生产环境部署与运维)
- [📑 飞书多维表格与自动化配置](#-飞书多维表格与自动化配置)
- [🔌 API 接口规范](#-api-接口规范)
- [🔑 Token 凭证维护指南 (约30天周期)](#-token-凭证维护指南-约30天周期)

---

## 🌟 核心特性

- **杯贴署名自动打印**：同事提交姓名后，系统自动构造 `点单人: <姓名>` 写入咖啡官方订单备注，吧台出餐杯贴清晰可见，彻底避免拿错。
- **飞书多维表格深度打通**：支持通过飞书表单实名点单，多维表格 Webhook 自动化自动扣豆下单，回填取餐码。
- **智能出餐提醒（无二次打扰）**：
  - **下单成功**：立即获得取餐码；
  - **制作完成**：当吧台状态变更为 `ready`（待取餐）时，后台巡检服务自动向飞书推送 1 次私信强提醒；
  - **取餐完成**：当状态变为 `completed`（已取餐）时静默归档，**绝不进行二次打扰**。
- **演练与测试模式 (DRY_RUN & Mock)**：
  - 支持免扣豆的全流程演练测试。
  - 智能白名单：只要姓名包含 `测试`、`test` 或 `Test`，自动走 Mock 模拟下单，10 秒后自动触发模拟出餐通知，方便随时验证全链路。
- **极速响应与 0ms 即时交互**：
  - 点单按钮点击瞬间提供加载反馈与防止连击；
  - 订单制作看板支持客户端实时按姓名/取餐码实时筛选；
  - 会话复用与接口优化，单次下单响应耗时从 ~3s 缩减至 <1s。
- **极简依赖**：纯原生 FastAPI + Requests，无重型数据库负担，资源占用极小（~30MB 内存）。

---

## ☕ 双通道点单体验

系统为不同习惯的同事量身设计了双通道并行体验：

| 体验维度 | 通道一：飞书多维表格（实名点单） ⭐ *推荐* | 通道二：Web 点单端（花名/匿名） |
| :--- | :--- | :--- |
| **入口地址** | [飞书自助点单问卷](https://reachauto.feishu.cn/share/base/form/shrcn2SzTgUb8yjIVE81vOPqkbb) | [https://cafe.tmhcorps.cn](https://cafe.tmhcorps.cn) |
| **适用场景** | 想要省心、不愿一直盯着网页的同事 | 喜欢用花名/外号、临时即开即用的同事 |
| **姓名录入** | 飞书员工账号自动关联 | 自由输入姓名或花名 |
| **取餐通知** | **飞书私信强弹窗**（制作完成瞬间飞书推送取餐码卡片） | **网页看板自查**（点单后显示动态进度条，支持实时搜索） |
| **实现机制** | 多维表格 Webhook -> FastAPI -> 订单巡检推送 | FastAPI 服务端内嵌响应式 Web 页面 |

---

## 📐 架构设计与工作流

```text
       [ 同事 A: 飞书表单 (实名) ]           [ 同事 B: Web 界面 (花名) ]
                    │                                     │
                    ▼                                     ▼
        飞书多维表格工作流 (POST)                 FastAPI Web 路由 (/api/order)
                    │                                     │
                    └──────────────────┬──────────────────┘
                                       ▼
                       FastAPI 咖啡代理服务 (cafe-service)
                                       │
                      ┌────────────────┴────────────────┐
                      ▼                                 ▼
              [ 演练/测试分支 ]                 [ 真实下单分支 ]
             (姓名含测试/DRY_RUN)             (构造请求 & 写入点单人备注)
                      │                                 │
                      ▼                                 ▼
             模拟 10 秒后就绪              ReachAuto R Café 官方接口 (扣豆)
                      │                                 │
                      └────────────────┬────────────────┘
                                       ▼
                           订单入库 / 内存监控巡检队列
                                       │
                           (后台每 15 秒轮询一次状态)
                                       │
                      ┌────────────────┴────────────────┐
                      ▼                                 ▼
             【待取餐 (ready)】                 【已取餐 (completed)】
                      │                                 │
                      ▼                                 ▼
             调用飞书通知 Webhook               静默标记为已通知
          (私聊推送出餐卡片，仅 1 次)              (绝对不二次打扰)
```

---

## 🚀 快速开始与本地运行

### 1. 环境依赖
* Python 3.10+
* Git

### 2. 克隆与安装依赖
```bash
git clone https://github.com/duying0425/coffee-proxy-service.git
cd coffee-proxy-service

# 创建虚拟环境
python -m venv venv
# Linux / macOS 激活：source venv/bin/activate
# Windows PowerShell 激活：.\venv\Scripts\Activate.ps1

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置环境变量
复制配置模版并填入飞书凭证：
```bash
cp .env.example .env
```
编辑 `.env` 文件：
```ini
BASE_URL=https://reachauto.feishuapp.com/app/app_17fmvspzbtf
CSRF_TOKEN=your_csrf_token_here
SESSION_TOKEN=your_session_jwt_here
WEB_DID=your_web_did_here

PORT=8085
HOST=0.0.0.0
DRY_RUN=false
FEISHU_NOTIFY_WEBHOOK=https://reachauto.feishu.cn/base/workflow/webhook/event/xxx
FEISHU_FORM_URL=https://reachauto.feishu.cn/share/base/form/xxx
```

### 4. 启动服务
```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8085 --reload
```
访问 `http://localhost:8085` 即可打开点单前端。

---

## 🛠️ 生产环境部署与运维

线上服务部署于阿里云 Linux 服务器，采用 **Systemd 守护进程 + Nginx 反向代理 + HTTPS (Cloudflare)** 架构。

### 1. Systemd 守护进程
服务配置文件位于 [cafe-service.service](file:///C:/Users/duyin/.gemini/antigravity/scratch/coffee_service/cafe-service.service)：
```ini
[Unit]
Description=R Cafe Ordering Service for Feishu
After=network.target

[Service]
Type=simple
User=duyingfang
WorkingDirectory=/home/duyingfang/cafe-service
ExecStart=/home/duyingfang/cafe-service/venv/bin/python app.py
Restart=always
RestartSec=5
EnvironmentFile=/home/duyingfang/cafe-service/.env

[Install]
WantedBy=multi-user.target
```

**常用运维命令**：
```bash
# 查看实时运行日志
sudo journalctl -u cafe-service -f

# 重启服务
sudo systemctl restart cafe-service

# 检查服务状态
sudo systemctl status cafe-service
```

### 2. Nginx 反向代理配置
Nginx 配置参考 [nginx.conf](file:///C:/Users/duyin/.gemini/antigravity/scratch/coffee_service/nginx.conf)，转发至本机 `8085` 端口：
```nginx
server {
    listen 80;
    listen 443 ssl;
    server_name cafe.tmhcorps.cn;

    ssl_certificate     /etc/nginx/ssl/selfsigned.crt;
    ssl_certificate_key /etc/nginx/ssl/selfsigned.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    charset utf-8;

    location / {
        proxy_pass         http://127.0.0.1:8085;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

---

## 📑 飞书多维表格与自动化配置

### 1. 多维表格字段推荐配置
| 字段名称 | 字段类型 | 说明 |
| :--- | :--- | :--- |
| **姓名** | 人员 / 文本 | 点单同事姓名（表单填写时自动关联创建人） |
| **饮品** | 单选 | 选项：`美式咖啡`、`拿铁` |
| **冷热** | 单选 | 选项：`热饮`、`冰饮` |
| **备注** | 文本 | 额外需求（如无糖等） |
| **取餐码** | 文本 | 接口下单成功后回填（例如：`B08`） |
| **订单编号** | 文本 | 官方订单流水号 |
| **状态** | 单选 | `待制作`、`制作中`、`待取餐`、`已完成` |

### 2. 自动化工作流 1：表单提交下单
1. **触发器**：当添加记录或表单提交时；
2. **动作 1**：发送 HTTP 请求至代理服务：
   - URL: `https://cafe.tmhcorps.cn/api/feishu/webhook`
   - Method: `POST`
   - Body:
     ```json
     {
       "user_name": "{{点单人.name}}",
       "product": "{{饮品}}",
       "temp": "{{冷热}}",
       "remark": "{{备注}}"
     }
     ```
3. **动作 2**：更新当前记录，将响应数据中的 `pickup_no` 回填至「取餐码」字段，将 `order_no` 回填至「订单编号」；
4. **动作 3**：向创建人发送飞书卡片消息：“点单成功！取餐码为：`{{取餐码}}`”。

### 3. 自动化工作流 2：制作完成推送
1. **触发器**：Webhook 触发（地址填入 `.env` 中的 `FEISHU_NOTIFY_WEBHOOK`）；
2. **动作 1**：在表格中「查找记录」，匹配条件为 `订单编号 等于 {{order_no}}` 或 `取餐码 等于 {{pickup_no}}`；
3. **动作 2**：更新记录状态为「待取餐」；
4. **动作 3**：向该记录的创建人发送私信消息：
   > ☕ 您的咖啡已制作完成！  
   > **取餐码**：`{{pickup_no}}`（{{product_name}} / {{temp}}）  
   > 请尽快前往吧台取餐~

---

## 🔌 API 接口规范

### 1. 服务与状态查询
* `GET /api/info`：获取服务运行状态、当前账户 R 豆余额、是否处于演练模式、可点商品列表及价格。
* `GET /api/menu`：获取可选饮品与规格选项。
* `GET /api/orders`：获取当前订单监控列表（包含待制作、制作中、待取餐订单状态）。

### 2. Web 端下单
* `POST /api/order`
```json
{
  "userName": "张三",
  "product": "latte",
  "temp": "热饮",
  "remark": "少冰"
}
```
**响应**：
```json
{
  "status": "success",
  "orderNo": "ORD20261118123456",
  "pickupNo": "B12",
  "productName": "拿铁",
  "temp": "热饮",
  "colleague": "张三",
  "balance": 433.0,
  "msg": "下单成功！取餐码：B12"
}
```

### 3. 飞书 Webhook 下单
* `POST /api/feishu/webhook`
兼容多维表格字段格式，接收 `user_name` / `姓名`、`product` / `饮品`、`temp` / `冷热`、`remark` / `备注`。

---

## 🔑 Token 凭证维护指南 (约30天周期)

飞书秒搭应用的登录凭证 `X-Force-Runtime-Session`（JWT 格式）有效期通常在 **30 天左右**。当凭证过期导致点单报错时，请按以下 1 分钟指引更新：

1. **获取最新凭证**：
   - 电脑浏览器（Edge / Chrome）打开 `https://reachauto.feishuapp.com/app/app_17fmvspzbtf` 并登录；
   - 按 `F12` 打开开发者工具，切换到 **网络 (Network)** 面板；
   - 刷新页面，在任意接口请求上右键 -> **复制 (Copy)** -> **复制为 cURL (bash)**。
2. **提取两个关键参数**：
   - `suda-csrf-token: <csrf_token>`
   - `X-Force-Runtime-Session: <session_token>`
3. **更新生产服务器**：
   ```bash
   ssh aliyun-cf
   nano /home/duyingfang/cafe-service/.env  # 替换 CSRF_TOKEN 和 SESSION_TOKEN
   sudo systemctl restart cafe-service      # 重启使新凭证生效
   ```
4. **验证可用性**：
   ```bash
   curl -s https://cafe.tmhcorps.cn/api/info | jq .
   ```
   若 `status` 显示 `"online"` 且正确返回 `rBalance` 余额，即说明 Token 续期成功！

---

## 📄 开源与免责声明

本项目仅供学习交流及企业内部协同办公提效使用。使用本项目请遵守相关企业规章制度与接口规范。
