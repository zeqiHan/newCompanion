# Mac Clash 代理安装说明

适用环境：**Apple Silicon（M 芯片）Mac**。  
本机已按此流程装过：Clash Verge Rev + 三毛机场订阅。

---

## 1. 安装 Clash Verge Rev

只从 GitHub 官方包安装：

- 发布页：https://github.com/clash-verge-rev/clash-verge-rev/releases/tag/v2.5.2
- **Mac（M 芯片）**：https://github.com/clash-verge-rev/clash-verge-rev/releases/download/v2.5.2/Clash.Verge_2.5.2_aarch64.dmg
- Mac（Intel）：https://github.com/clash-verge-rev/clash-verge-rev/releases/download/v2.5.2/Clash.Verge_2.5.2_x64.dmg
- Windows：https://github.com/clash-verge-rev/clash-verge-rev/releases/download/v2.5.2/Clash.Verge_2.5.2_x64-setup.exe

安装：打开 DMG → 拖到「应用程序」→ 打开 **Clash Verge**。  
若提示损坏/无法打开：终端执行后重开：

```bash
xattr -cr "/Applications/Clash Verge.app"
```

配置目录（一般不用手改）：

```text
~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/
```

---

## 2. 导入订阅（三毛机场）

订阅链接（Clash 格式）在机场后台复制；形如：

```text
https://sub-1.smjcdh.top/smjc/api/v1/client/subscribe?token=<你的token>
```

> Token 是密钥。对话或文档里出现过就要当可能泄露；去三毛机场后台**重置订阅**，再在客户端里更新。

### 图形界面（推荐）

1. 左侧 **订阅** → **新建**
2. 名称：`三毛机场`
3. 粘贴订阅链接 → 保存
4. 等待拉节点（大约 26+ 个）

### 本机已导入时的状态

- 配置名：`三毛机场`
- 推荐节点：`2x专线-香港-1`（或选 `♻️ 自动选择`）
- 系统代理端口：`127.0.0.1:7897`
- 模式：**规则（Rule）**

---

## 3. 让浏览器走代理

1. 选中刚导入的订阅
2. 节点选 **2x专线-香港-1**（或自动选择）
3. 打开 **系统代理**
4. 模式用 **规则（Rule）**
5. 浏览器打开 https://www.google.com

还不行再开 **TUN 模式**（可能要输密码 / 授权）。

### 怎么确认代理开了

打开 https://ipinfo.io ：

- 应显示 **Hong Kong**（或你选的节点地区）
- 不应是家里宽带的真实 IP

命令行快速自检（需 Clash 已开、系统代理开着）：

```bash
# 系统代理是否指向 Clash
scutil --proxy | rg 'HTTPEnable|HTTPProxy|HTTPPort'

# 经本地代理访问（7897 为 mixed-port）
curl -sS -m 15 -x http://127.0.0.1:7897 https://ipinfo.io/json
curl -sS -m 15 -x http://127.0.0.1:7897 -o /dev/null -w 'google %{http_code}\n' https://www.google.com
```

期望：ipinfo 为香港等节点地区；google 返回 `200`。

---

## 4. 日常使用

| 操作 | 做法 |
|------|------|
| 上网翻墙 | 打开 Clash Verge → 开 **系统代理** |
| 关掉代理 | Clash 里关 **系统代理**（或退出 Clash） |
| 换节点 | 代理页选节点，如 `2x专线-香港-1` |
| 更新订阅 | 订阅页对该条点更新；或后台重置 token 后改链接再更新 |
| 手机共用同一订阅 | 安卓：Clash Meta / sing-box；iOS：Shadowrocket → 订阅里粘贴同一链接 |

---

## 5. 本机当前结论（装好后的验收）

按本流程验收过：

- Clash Verge Rev **2.5.2**（aarch64）在 `/Applications`
- 订阅已导入，节点含 `2x专线-香港-1`
- 系统代理 `127.0.0.1:7897` 开启时，ipinfo 显示香港，Google HTTP 200

若以后「突然又上不了网」：先看菜单栏 Clash 是否在跑、**系统代理**是否打开，再用上面的 `ipinfo` / `curl` 自检。
