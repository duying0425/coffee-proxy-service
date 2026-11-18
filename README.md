# R Café 咖啡点单开放服务 (Feishu Coffee Proxy)

这是一个为公司内部飞书企业应用（ReachAuto R Café）量身打造的点单中继代理服务。
通过反向代理与协议封装，允许其他同事在不直接拥有余额账户的情况下自助点单，并**自动将点单人姓名打印在杯贴备注上**。

---

## 🌟 双通道点单模式

系统提供两种并行的点单体验：

| 通道 | 点单入口 | 适用人群 | 取餐通知机制 |
| :--- | :--- | :--- | :--- |
| **通道一：飞书实名表单** ⭐ *推荐* | [飞书自助点单问卷](https://reachauto.feishu.cn/share/base/form/shrcn2SzTgUb8yjIVE81vOPqkbb) | 想要省心、不愿盯网页的同事 | **飞书自动私信弹窗通知**（咖啡制作完成瞬间私聊强提醒，附带取餐码） |
| **通道二：网页花名点单** | [https://cafe.tmhcorps.cn](https://cafe.tmhcorps.cn) | 喜欢用昵称/花名、即开即用的同事 | **仅网页端自查**（提交后通过网页动态进度条或「点单记录」Tab 查看） |

---

## 📋 接口与服务规范

### 1. 网页前端
- **访问地址**：`https://cafe.tmhcorps.cn/`
- **功能**：
  - 顶部直接提供直达飞书实名问卷的卡片。
  - 网页花名点单（自动轮询制作进度：待制作 -> 制作中 -> 待取餐 -> 已完成）。
  - 点单记录看板，支持按姓名/取餐号快速查询出餐状态。

### 2. 飞书多维表格 Webhook 自动化
- **下单 Webhook 地址**：`https://cafe.tmhcorps.cn/api/feishu/webhook`
- **取餐通知回调地址**：`https://reachauto.feishu.cn/base/workflow/webhook/event/ZaoIa3r7gwGqqthZRmcclb0cn1e`

---

## 🔑 维护和更新凭证的方法

飞书秒搭的登录凭证 `X-Force-Runtime-Session`（JWT 格式）有效期通常在 **30 天左右**。当凭证过期导致点单失败时，请按照以下步骤更新：

1. **抓取新 Cookie**：在电脑 Edge/Chrome 打开 `https://reachauto.feishuapp.com/app/app_17fmvspzbtf`，按 `F12` 切换到网络（Network）标签，刷新页面并在任意请求右键选择 **Copy as cURL**。
2. **提取两个参数**：
   - `suda-csrf-token`
   - `X-Force-Runtime-Session`
3. **更新服务器并重启**：
   ```bash
   ssh aliyun-cf
   nano /home/duyingfang/cafe-service/.env  # 替换上述两项的值
   sudo systemctl restart cafe-service      # 重启生效
   ```

---

## 🛠️ 服务端部署与日常维护命令

- **查看服务运行日志**：
  ```bash
  ssh aliyun-cf "sudo journalctl -u cafe-service -f"
  ```
- **重启服务**：
  ```bash
  ssh aliyun-cf "sudo systemctl restart cafe-service"
  ```
