# TG-Butler · Telegram 群组管家

在 Debian 服务器常驻运行的个人 Telegram 助手：监控指定群组和用户、生成每日摘要、定时发送签到命令，并通过你创建的 Telegram 机器人通知你。

## 一键安装（Debian 12 / 13）

在服务器的 SSH 终端中执行（需要已安装 `curl`，普通用户需要 sudo 权限）：

```bash
curl -fsSL https://raw.githubusercontent.com/xhpx7301/TG-Butler/main/deploy/install.sh | sudo bash
```

如果当前就是 root 用户，使用：

```bash
curl -fsSL https://raw.githubusercontent.com/xhpx7301/TG-Butler/main/deploy/install.sh | bash
```

如果提示 `curl: command not found`，先执行 `sudo apt-get update && sudo apt-get install -y curl`（root 用户去掉 `sudo`）。

脚本会安装依赖、创建专用账号、下载代码、建立 Python 虚拟环境并注册任务服务，自动启动网页后台并设置开机自启，然后进入 **bu 中文管理菜单**。首次默认管理员用户名为 **pingzi**，密码为 **pingzi**；已有管理员账户会保留。按下文建立 SSH 隧道即可登录网页完成配置，也可在网页或 **bu → 1 网页后台 → 2 管理员账户** 修改管理员账户。Telegram 首次登录仍需你输入验证码和可能的两步验证密码。

运行前请准备下文列出的 Telegram 应用凭据、监控/日报机器人 Token 和你自己的用户 ID，并分别向两个机器人发送 `/start`。群组 ID 会在登录后列出；关注用户 ID、签到命令和签到机器人的数字 ID 需按实际群组填写。

重复运行会更新 `main` 代码、保留 `.env`、`config.yaml` 和 `data/`，并在重新配置前停止服务。安装中断时检查终端错误后重新执行即可。默认安装到 `/opt/tg-butler`，首次使用保持 `data_dir: data`；自定义路径需按下文调整 systemd 服务。

只安装环境、稍后手动配置，可在已经克隆的仓库执行：

```bash
sudo bash deploy/install.sh --non-interactive
```

此模式不填写凭据、不登录、不启动监控任务；网页后台会安装并启动。如果已有运行中的监控任务，会先停止。安装完输入 `bu` 即可配置。完整手动安装流程见下文“Debian 部署”。

## 中文网页管理后台

后台提供应用凭据、Telegram 手机号/验证码/两步验证登录、群组与成员 ID 查询、监控用户、通知机器人、日报/AI、签到任务、运行设置、执行记录、日志和任务服务控制。签到任务可单独执行“测试签到”，真实发送已保存的签到命令、等待回复并报告判定结果，不会占用当天正式签到次数。概览页还提供“更新 TG-Butler”按钮，可从 GitHub 更新最新脚本和依赖。每个参数旁均说明用途和获取位置。配置和密钥与 `bu` 共用，不需要重复填写；网页保存参数后，点击概览页的“重启任务服务”生效。

### 首次打开 / 旧版升级

1. 已安装用户先在 `bu` 中选择 **6 更新脚本**（旧版为 **19**），更新后重新输入 `bu`。安装和更新均会自动启动网页后台并设置开机自启。若旧版更新脚本尚未启动网页服务，进入 **1 网页后台 → 4 安装 / 启动网页后台**。
2. 尚未设置管理员时自动创建默认账号 **pingzi / pingzi**（用户名 / 密码），无需先填写 Telegram 信息。已有管理员账户不会被覆盖。可在网页的“管理员账户”或 **bu → 1 网页后台 → 2 管理员账户** 修改；自行设置的新密码需为 12–256 个字符。
3. 在**你自己的电脑终端**建立 SSH 隧道，替换下面的服务器地址和 SSH 用户，保持此终端连接：

```bash
ssh -N -L 15678:127.0.0.1:15678 root@你的服务器IP
```

4. 在电脑浏览器打开 [http://127.0.0.1:15678](http://127.0.0.1:15678)，使用默认管理员或已有账号登录。如电脑的 15678 被占用，将 `-L` 左侧改为 `15679`，然后访问 `http://127.0.0.1:15679`。
5. 按网页指引完成 Telegram 应用凭据 → 账号登录 → 通知机器人 → 群组/日报/签到 → 启动任务服务。可以不填 Telegram 凭据先启动后台，不影响首次配置。

默认仅监听服务器的 **127.0.0.1:15678**，不需要开放公网 15678。网页后台是 `tg-butler-web.service`，监控任务是 `tg-butler.service`，二者独立。服务日志：`sudo journalctl -u tg-butler-web -n 80`。网页查询群组、成员和登录时会短暂停止监控任务并恢复原运行状态，暂停期间不补抓消息。

### 管理员和网页监听设置

- **网页 → 管理员账户**：输入当前密码，修改用户名和密码。当前为单管理员模式；全部网页登录会话会失效，需要重新登录。
- **bu → 1 网页后台 → 2 管理员账户**：创建、修改或重置同一管理员账号。忘记密码时通过服务器终端重置，无须知道旧密码。管理员账户与 Telegram 账号相互独立。
- **bu → 1 网页后台 → 3 监听设置**：设置网页监听地址、端口和 HTTPS Cookie。默认本机监听 `127.0.0.1:15678`；网页不能更改监听或数据目录。更新会保留已保存的监听设置；已有安装如仍使用旧端口，请在此改为 `15678`，保存后自动重启网页后台。
- **bu → 1 网页后台 → 4 / 5**：安装/启动后台、查看后台服务状态。脚本更新使用主菜单 **6**，网页不支持执行任意系统命令。

管理员密码以 scrypt 哈希保存在 `data/web.sqlite3`，配置与运行 `bu` / 网页后台的服务账户共用。登录会话有效期为 8 小时；退出、改密或菜单重置后需重新登录。所有写操作检查 CSRF，Cookie 设置 HttpOnly / SameSite，凭据读取接口只返回是否已配置，不返回密钥正文。登录限制为每个来源 15 分钟内 5 次、全局 30 次，成功与失败均计数；反向代理默认不信任客户端伪造的来源头。

### 可选 HTTPS 反向代理

有域名和有效 TLS 证书时，将 HTTPS 域名代理至 `127.0.0.1:15678`。例如 Nginx 已配置证书的 `server` 中加入：

```nginx
location / {
    proxy_pass http://127.0.0.1:15678;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 120s;
}
```

之后在 **bu → 1 网页后台 → 3 监听设置** 将“仅允许 HTTPS Cookie”设为 `y`。SSH 隧道通过本地 HTTP 访问时保持 `n`，否则浏览器无法携带登录 Cookie。默认 SSH 隧道即可使用，不自动申请证书或修改防火墙。不要通过未加密的公网 HTTP 输入密码、验证码或账号密钥。

更新和备份会保留 `data/web.sqlite3`（管理员、会话签名密钥、登录限速）及 `data/web.json`（监听设置）。网页概览中的更新按钮会异步启动与网页服务独立的更新任务，更新期间网页可能短暂断开；完成后刷新页面即可。首次无需修改这些文件。管理员设置、Telegram 会话和服务器备份均不要提交到 GitHub。

## bu 中文管理菜单

安装后在服务器任意目录输入：

```bash
bu
```

普通用户会自动请求 sudo 权限，root 可直接使用。首页显示以下 7 个入口，进入分类后选择具体操作，输入 **0** 返回主菜单；在主菜单输入 **0** 退出。

| 主菜单 | 分类内操作 |
| --- | --- |
| 1. 网页后台 | 1 访问说明、2 管理员账户、3 监听设置、4 安装/启动、5 服务状态、6 日志 |
| 2. Telegram 配置 | 1 应用凭据、2 账号登录、3 通知机器人、4 通知测试、5 群组 ID、6 成员 ID |
| 3. 监控任务 | 1 群组与关注用户、2 日报与 AI 设置、3 AI 密钥、4 定时签到、5 手动日报、6 执行记录 |
| 4. 监控服务与日志 | 1 启动/开机自启、2 停止、3 重启、4 状态、5 日志 |
| 5. 运行参数与配置检查 | 1 时区/保留天数/通知队列、2 配置总览、3 校验监控配置 |
| 6. 更新脚本 | 从 GitHub 更新代码、依赖和 bu 菜单，保留凭据、配置与数据 |
| 7. 卸载并清除数据 | 输入 DELETE 确认后，移除两个服务、bu、专用账号及安装目录内的全部配置和数据 |

首页分别显示网页后台和监控任务的运行状态。网页安装后默认启动，不依赖 Telegram 凭据；监控服务需要完成 Telegram 配置后手动启动。原主菜单的 19（更新）、22–25（网页管理）作为兼容快捷入口保留。

首次配置推荐在网页完成。使用菜单时，先在 **2 Telegram 配置** 中填写应用凭据、登录、查询群组，设置通知机器人并测试；再进入 **3 监控任务** 设置群组、日报和签到，最后在 **4 监控服务与日志 → 1** 启动监控。示例群组 ID 必须替换成真实 ID，或在菜单中删除。菜单输入回车保留当前值；可选项提示中支持 `-` 清空。密钥输入不回显，配置总览隐藏密钥。

API ID / API HASH 是 Telegram **应用凭据**，首次需要手动填写。用你的 Telegram 账号登录 [my.telegram.org](https://my.telegram.org)，进入 **API development tools**，创建应用后复制 `api_id` 和 `api_hash`。它们用于登录用户账号、读取群消息与发送签到，不是用户 ID、短信验证码或 Bot Token。账号登录还需要手机号、验证码及可能的两步验证密码。

通知机器人在网页“通知机器人”页分别配置，Token 来自 [@BotFather](https://t.me/BotFather)。“关注-日报机器人”负责关注用户的全部消息、每日日报和签到结果；“自定义机器人”仅在关注用户消息命中关键词、链接或图片规则时提醒，并通过 Telegram 复制原消息（包括媒体）。复制原消息要求自定义机器人已加入来源群组且能读取消息。菜单仍支持旧版单机器人变量；需要分开配置时直接在 `.env` 或网页填写四个新变量。AI 服务密钥在 **bu → 3 监控任务 → 3** 单独填写。各部分可独立保存；登录与列出群组只需要应用凭据，测试通知只需要机器人凭据，启动完整服务前才要求两部分都配置好。密钥显示“已设置”仅代表本地已填写，不代表已经通过 Telegram 验证；旧版示例占位值会被视为未设置并拒绝使用。

保存参数前会校验，校验失败不覆盖文件；保存成功会重启正在运行的服务，使参数生效。服务原本停止时保持停止。登录、查看群组/成员以及手动日报会临时暂停服务，完成后恢复原运行状态，以免共用账号会话。暂停期间的新消息不会自动补抓。菜单通过系统锁限制同时打开多个 `bu`。

更新选项只允许干净的 `main` 分支及官方 origin，并使用快进合并；遇到代码冲突会中止。更新成功恢复监控任务原有运行状态，网页后台自动启动并设置开机自启；失败时若服务已停止，修复后需重新更新并手动启动。现有 systemd 服务文件会保留；模板有变化时另存为 `.service.new` 供人工比对。更新完成退出菜单，再次输入 `bu` 加载新版。

**已经安装旧版、还没有 bu 的服务器**：先退出旧安装流程的 nano（`Ctrl+X`；不保存示例凭据也可以），在 SSH 终端重新执行上方一键安装命令，即可保留数据并安装菜单。安装过程会停止已有服务，之后在菜单中启动。也可以执行：

```bash
sudo -u tgbutler git -C /opt/tg-butler pull --ff-only origin main
sudo bash /opt/tg-butler/deploy/install.sh --non-interactive
bu
```

不要在旧 nano 提示界面内粘贴 Shell 命令。

## 首版功能

- 按群组配置监控范围，通过数字用户 ID 指定关注用户；支持全群实时提醒。
- 将收到的消息文本保存到本机 SQLite，默认保留 30 天，重复消息不会重复提醒。
- 按配置时区，在每天指定时间汇总**前一天**的群消息，通过机器人私聊发送。
- 默认摘要包含消息数量、活跃用户、关注用户消息和最近消息摘录；可选接入兼容 Chat Completions 的模型服务生成话题摘要。
- 为不同群组设置不同签到时间和命令，分别通知“已发送”“已确认”“失败回复”“等待超时”。
- 签到任务按任务名称和当地日期持久化去重，服务重启后也不会再次发送同一天的命令。
- 提供登录、列出群组/成员 ID、校验配置、测试通知、手动日报及任务状态查询命令。
- 使用 systemd 管理开机启动、故障重启和日志。

提供中文网页后台、`bu` 终端菜单和命令行；也可直接修改 `config.yaml` 并重启服务。当前没有机器人管理命令。

## 工作方式

你的 **Telegram 用户账号**通过 Telethon 读取已加入群组的新消息并发送签到命令；你在 **BotFather 创建的机器人**只负责给你发送通知。机器人本身不需要加入被监控的群组。

开始前准备：

1. 在 [my.telegram.org](https://my.telegram.org) 创建应用，取得 `api_id` 和 `api_hash`。
2. 在 [@BotFather](https://t.me/BotFather) 创建通知机器人，取得 Bot Token。
3. 向这个机器人发送 `/start`，并取得你自己的数字用户 ID，作为私聊通知的 `TG_NOTIFY_CHAT_ID`。可通过 [@userinfobot](https://t.me/userinfobot) 查询自己的 ID。
4. 确认你的 Telegram 用户账号已加入目标群组，并能查看消息、发送对应签到命令。

账号登录需要验证码；启用两步验证时还需要密码。可在通过 SSH 隧道 / HTTPS 访问的网页或自己的终端完成登录。`.env`、`config.yaml`、`data/` 和登录会话均已加入 `.gitignore`，不要手动强制提交这些文件。`data/user.session` 是账号登录凭据，需要和密码一样保管。

## Debian 部署

支持 Python 3.11+，建议 Debian 12 / 13。以下命令在服务器上执行，需要 sudo 权限。服务器必须能够连接 Telegram；网页默认只在本机 127.0.0.1:15678 提供 HTTP 服务。

### 1. 安装

```bash
sudo apt update
sudo apt install -y git python3 python3-venv tzdata
sudo useradd --system --home-dir /opt/tg-butler --shell /usr/sbin/nologin tgbutler
sudo git clone https://github.com/xhpx7301/TG-Butler.git /opt/tg-butler
sudo chown -R tgbutler:tgbutler /opt/tg-butler
sudo -u tgbutler python3 -m venv /opt/tg-butler/.venv
sudo -u tgbutler /opt/tg-butler/.venv/bin/pip install /opt/tg-butler
cd /opt/tg-butler
sudo -u tgbutler cp .env.example .env
sudo -u tgbutler cp config.example.yaml config.yaml
sudo -u tgbutler mkdir -p data
sudo chmod 600 .env config.yaml
sudo chmod 700 data
```

若 `tgbutler` 用户已存在，跳过 `useradd`。`/opt/tg-butler` 必须为空才能首次 clone。

### 2. 填写凭据和配置

```bash
sudo -u tgbutler nano /opt/tg-butler/.env
sudo -u tgbutler nano /opt/tg-butler/config.yaml
```

`.env`：

```dotenv
TG_API_ID=你的应用数字ID
TG_API_HASH=你的应用HASH
# 旧版单机器人配置仍可用，会同时作为下面两个机器人的回退值
TG_BOT_TOKEN=旧版机器人TOKEN
TG_NOTIFY_CHAT_ID=你的数字用户ID
# 推荐：拆分监控机器人和日报机器人
TG_MONITOR_BOT_TOKEN=监控机器人TOKEN
TG_MONITOR_NOTIFY_CHAT_ID=接收监控消息的数字用户ID
TG_REPORT_BOT_TOKEN=日报机器人TOKEN
TG_REPORT_NOTIFY_CHAT_ID=接收日报的数字用户ID
SUMMARY_API_KEY=
```

关注-日报机器人会发送关注用户的每条新消息、每日日报和签到结果；自定义机器人发送命中关键词、链接或图片条件的关注用户提醒，并复制原消息。自定义机器人需加入监控群组才能复制消息。两个机器人都要先发送 `/start`，接收 ID 通常填写自己的数字用户 ID。若只保留旧版变量，两个角色会使用同一个机器人；由于它已收到关注用户的全部消息，不会重复发送筛选提醒。

先完成账号登录，查找实际群组 ID 和成员 ID，再替换示例配置中的假 ID：

```bash
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml login
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml dialogs
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml members --limit 100 -1001234567890
```

`dialogs` 输出 `群组ID + 名称`；超级群组 ID 通常以 `-100` 开头，务必保留负号。`members` 输出成员 ID、用户名和显示名，能列出的成员范围取决于群权限。不能列举群成员时，可以从其他已知消息或可信方式取得目标用户 ID。

网页“Telegram 账号”页支持手动导入历史消息。填写群组 ID 和完整自然日天数后，程序会导入最近几天（不包含今天）的消息，默认 1 天，最多 30 天且单次最多扫描 5000 条。导入使用消息 ID 去重，不会在任务服务启动时自动拉取历史；导入完成后可在“日报与 AI”页发送测试日报。

配置示例：

```yaml
timezone: Asia/Shanghai
data_dir: data
retention_days: 30

summary:
  enabled: true
  at: "00:05"

groups:
  - id: -1001234567890
    name: 我的群组
    daily_summary: true
    notify_all: false
    watch_users: [123456789, 234567890]
    report_keywords: [重要, 项目] # 自定义机器人筛选关注用户消息
    report_match_mode: any # any=任一关键词；all=全部关键词
    report_images: false # 关注用户发图片时通知自定义机器人
    report_links: false # 关注用户发链接时通知自定义机器人

checkins:
  - name: 某群每日签到
    chat_id: -1001234567890
    at: "09:00"
    command: "/checkin"
    responder_id: 987654321
    success_pattern: "签到成功|已经签到|已签到"
    failure_pattern: "签到失败|无权限"
    timeout_seconds: 60
    require_reply: true
```

时间使用 `HH:MM` 格式并加引号，全部以 `timezone` 为准。建议使用 `Asia/Shanghai` 等无夏令时的时区。`watch_users: []` 表示不指定关注用户；`notify_all: true` 表示关注-日报机器人提醒群内所有收到的新消息。`report_keywords`、`report_images`、`report_links` 是自定义机器人的筛选条件，多个条件之间是“或”；全部关闭时自定义机器人不会额外发送关注用户消息。群内消息不论是否来自关注用户，都会入库以便日报使用。

签到群可以只配置在 `checkins` 中，无须同时放入 `groups`。每个签到任务名称必须唯一，每个群 ID 在 `groups` 中也只能出现一次。

### 3. 校验并测试

```bash
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml check-config
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml notify-test
```

`check-config` 只校验格式；`notify-test` 会通过机器人实际发送一条测试消息。随后可以前台试运行并按 Ctrl+C 停止：

```bash
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml run
```

启动时会确认账号已登录、配置中的群组在账号会话列表中，以及通知通道可用。签到规则需用目标群的真实机器人回复验证，当前仓库不包含你的 Telegram 凭据。

### 4. 启用 systemd

```bash
sudo cp /opt/tg-butler/deploy/tg-butler.service /etc/systemd/system/tg-butler.service
sudo systemctl daemon-reload
sudo systemctl enable --now tg-butler
sudo systemctl status tg-butler
sudo journalctl -u tg-butler -f
```

手动安装网页后台请执行 `sudo bash /opt/tg-butler/deploy/setup-web.sh`。如需 `bu` 菜单，再执行 `sudo install -m 755 /opt/tg-butler/deploy/bu /usr/local/bin/bu`（普通用户还需安装 `sudo`）。

服务默认只能写入 `/opt/tg-butler/data`。如果修改 `data_dir`，也要修改 service 文件中的 `ReadWritePaths` 并重新执行 `daemon-reload`。服务不需要 root 权限，网页通过 SSH 隧道访问时也不需要开放额外入站端口。若 Telegram 不可直连，可先为服务器配置可用的出站网络；本项目当前没有内置代理配置项。

## 日报与 AI 摘要

默认每天 `00:05` 汇总前一天的本地消息。无模型服务时发送统计和摘录，不会假装生成语义摘要。

如需 AI 总结，在 `config.yaml` 添加完整 HTTPS 接口地址和模型名，并在 `.env` 中配置 `SUMMARY_API_KEY`：

```yaml
summary:
  enabled: true
  at: "00:05"
  api_url: "https://your-provider.example/v1/chat/completions"
  model: "your-model-name"
  max_messages: 500
  max_chars: 30000
```

此功能会将采样的群消息文本和发言者显示名发送给你指定的模型服务。默认最多输入最近 500 条、约 30000 个字符，并在日报中注明实际输入数量。接口异常时自动退回本地统计。不要将示例域名直接用于运行。

网页后台的“日报与 AI”页面支持 OpenAI Responses 和 Chat Completions 兼容服务。填写接口地址后，可以点击“读取模型”从服务商的 `/models` 接口获取模型并选择；也可以直接手动填写模型名。点击“测试连接”会使用当前地址、模型和 API Key 发起一次最小请求，验证地址、鉴权和模型是否可用。测试时填写在页面中的 API Key 会优先使用，留空则使用已保存的 `SUMMARY_API_KEY`。接口地址必须使用 `https://`，请求使用 `Authorization: Bearer <API Key>`。

例如 CC Switch 中转站提供 OpenAI 兼容端点时，可以填写站点根地址（程序会依次尝试 `/v1/responses` 和 `/v1/chat/completions`），也可以直接填写完整端点。模型名从“读取模型”结果中选择；如果服务商没有提供兼容的 `/models` 接口，直接手动填写其文档中的模型名即可。

网页的“发送测试日报”会按运行时区生成前一个自然日的日报，并真实发送到通知机器人，用于检查消息读取、AI 摘要和通知链路。它不会写入日报执行记录，因此可以重复测试，也不会占用正式日报的当天执行名额。

补发某个尚未尝试过的日期日报：

```bash
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml summary --date 2026-10-15
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml status
```

每群每天的日报只自动尝试一次。`status` 显示 `sent` 才表示已收到通知 API 的成功响应；失败或发送中断不会自动重试整份日报，以免部分已发送内容重复。当前没有强制重发命令。手动日报命令不要与正在运行的服务并发使用。

## 签到结果的含义

| 状态 | 含义 |
| --- | --- |
| `claimed` | 已占用当天任务，尚未记录发送结果；进程中断后需人工检查 |
| `sent` | Telegram 已接受命令；没有配置回复规则时到此结束 |
| `confirmed` | 指定机器人回复当前命令，文本匹配成功规则 |
| `rejected` | 指定机器人回复匹配失败规则 |
| `timeout` | 命令已发送，但在等待时间内未收到匹配回复 |
| `uncertain` | 发送过程异常，无法确认 Telegram 是否已接收 |
| `error` | 命令发出后的处理异常 |

默认 `require_reply: true` 同时核对机器人 ID、消息顺序和回复的命令 ID，防止把别人签到的成功回复算到你头上。如果目标机器人从不使用回复消息，可显式设为 `false`，此时只能根据机器人 ID、时间窗口和关键词判断，准确性取决于群内消息。未配置 `responder_id` 和 `success_pattern` 时只报告已发送；两者必须一起填写。

签到命令在调用发送 API 前持久化占用记录，遇到网络错误也不会自动重发。保留同一任务名称可防止当天重复签到；修改任务名称会被视为新任务。实际发送可能受网络或 Telegram 限流影响，不保证秒级精度。服务器停机期间的任务不补签；运行中的调度延迟超过 60 秒也会跳过签到。

## 数据、通知与运行边界

- 仅收集运行期间收到的新消息，不补抓停机期间的历史，不处理历史编辑/删除，也不下载媒体文件。媒体仅记录说明或配文。频道/匿名发言显示实际可见的发送者身份，无法识别隐藏的个人账号。
- 私有超级群的消息链接只有具备访问权限的账号能打开；普通群组可能没有可用链接。
- 实时提醒使用有界内存队列，失败时有有限次数重试并记录日志。队列满、进程崩溃或最终发送失败可能丢失通知；已保存的群消息仍可进入日报。首版没有持久化通知发件箱。
- 默认 30 天后在每天 `03:30` 删除旧记录；启动时也会清理。删除不会立即缩小 SQLite 文件，消息留存期内请定期检查磁盘容量。去重记录也受留存期约束。
- 仅运行一个实例；不要在 systemd 服务运行时执行 `login`，也不要让其他程序共用 `data/user.session`。先停止服务再重新登录。
- 不要向其他人提供 `.env`、会话文件、数据库和包含群消息的备份。日志默认不输出消息正文和 HTTP 请求地址。

## 更新与备份

推荐运行 `bu` 并选择 **6. 更新脚本**（旧版为 **19**）。不打开菜单也可以执行：

```bash
sudo bash /opt/tg-butler/deploy/update.sh
```

备份前先停止服务，再复制 `.env`、`config.yaml` 和整个 `data/`，并妥善加密保管。`data/` 包含 SQLite 数据库及可能的 WAL 文件，复制单个数据库文件可能丢失尚未合并的记录。

## 卸载与清除

在服务器运行 **bu → 7. 卸载并清除数据**，也可直接执行：

```bash
sudo bash /opt/tg-butler/deploy/uninstall.sh
```

脚本会先列出清理范围，只有输入大写 **DELETE** 才会执行，其他输入或直接回车取消。取消后返回菜单；卸载成功后退出菜单。卸载不要求 Telegram 配置有效或服务正在运行。

确认后停止监控任务和网页后台，取消开机自启，移除 systemd 服务文件（含 `.service.new` 和覆盖配置）、`bu`、网页 sudo 规则、控制脚本及 `tgbutler` 专用账号与组，并删除 `/opt/tg-butler`。**目录内的 `.env`、`config.yaml`、Telegram 登录会话、消息数据库、网页管理员及配置会永久清除，请先备份。**

安装目录外的自定义 `data_dir`、符号链接指向的外部文件、系统 journal 日志及 apt 安装的公共依赖保留；如有外部数据，请自行备份并按需清理。若安装目录为符号链接或目录内存在挂载点，脚本拒绝执行，避免删除其他位置的数据。停止服务或其他卸载步骤失败时会中断并显示错误；部分步骤可能已完成，排查后可重新执行脚本。

## 本地开发

```bash
python -m venv .venv
# Debian / Linux
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/pytest -q
# Windows PowerShell: use .venv\Scripts\python, ruff.exe and pytest.exe instead.
```

自动化测试使用模拟的 Telegram 和 HTTP 响应，不会登录账号、发送真实通知或执行真实签到。GitHub Actions 在 Linux 上检查 Python 3.11、3.12、3.13。
