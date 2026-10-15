# TG-Butler · Telegram 群组管家

在 Debian 服务器常驻运行的个人 Telegram 助手：监控指定群组和用户、生成每日摘要、定时发送签到命令，并通过你创建的 Telegram 机器人通知你。

## 首版功能

- 按群组配置监控范围，通过数字用户 ID 指定关注用户；支持全群实时提醒。
- 将收到的消息文本保存到本机 SQLite，默认保留 30 天，重复消息不会重复提醒。
- 按配置时区，在每天指定时间汇总**前一天**的群消息，通过机器人私聊发送。
- 默认摘要包含消息数量、活跃用户、关注用户消息和最近消息摘录；可选接入兼容 Chat Completions 的模型服务生成话题摘要。
- 为不同群组设置不同签到时间和命令，分别通知“已发送”“已确认”“失败回复”“等待超时”。
- 签到任务按任务名称和当地日期持久化去重，服务重启后也不会再次发送同一天的命令。
- 提供登录、列出群组/成员 ID、校验配置、测试通知、手动日报及任务状态查询命令。
- 使用 systemd 管理开机启动、故障重启和日志。

这是基于配置文件的命令行首版。新增监控用户和签到任务时修改 `config.yaml` 并重启服务；当前没有网页管理界面或机器人管理命令。

## 工作方式

你的 **Telegram 用户账号**通过 Telethon 读取已加入群组的新消息并发送签到命令；你在 **BotFather 创建的机器人**只负责给你发送通知。机器人本身不需要加入被监控的群组。

开始前准备：

1. 在 [my.telegram.org](https://my.telegram.org) 创建应用，取得 `api_id` 和 `api_hash`。
2. 在 [@BotFather](https://t.me/BotFather) 创建通知机器人，取得 Bot Token。
3. 向这个机器人发送 `/start`，并取得你自己的数字用户 ID，作为私聊通知的 `TG_NOTIFY_CHAT_ID`。可通过 [@userinfobot](https://t.me/userinfobot) 查询自己的 ID。
4. 确认你的 Telegram 用户账号已加入目标群组，并能查看消息、发送对应签到命令。

账号登录需要验证码；启用两步验证时还需要密码。登录仅在你自己的终端进行。`.env`、`config.yaml`、`data/` 和登录会话均已加入 `.gitignore`，不要手动强制提交这些文件。`data/user.session` 是账号登录凭据，需要和密码一样保管。

## Debian 部署

支持 Python 3.11+，建议 Debian 12 / 13。以下命令在服务器上执行，需要 sudo 权限。服务器必须能够连接 Telegram；程序不开放 HTTP 端口。

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
TG_BOT_TOKEN=你创建的机器人TOKEN
TG_NOTIFY_CHAT_ID=你的数字用户ID
SUMMARY_API_KEY=
```

先完成账号登录，查找实际群组 ID 和成员 ID，再替换示例配置中的假 ID：

```bash
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml login
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml dialogs
sudo -u tgbutler /opt/tg-butler/.venv/bin/tg-butler --config /opt/tg-butler/config.yaml members --limit 100 -1001234567890
```

`dialogs` 输出 `群组ID + 名称`；超级群组 ID 通常以 `-100` 开头，务必保留负号。`members` 输出成员 ID、用户名和显示名，能列出的成员范围取决于群权限。不能列举群成员时，可以从其他已知消息或可信方式取得目标用户 ID。

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

时间使用 `HH:MM` 格式并加引号，全部以 `timezone` 为准。建议使用 `Asia/Shanghai` 等无夏令时的时区。`watch_users: []` 表示不指定关注用户；`notify_all: true` 表示群内所有收到的新消息都提醒。群内消息不论是否来自关注用户，都会入库以便日报使用。

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

服务默认只能写入 `/opt/tg-butler/data`。如果修改 `data_dir`，也要修改 service 文件中的 `ReadWritePaths` 并重新执行 `daemon-reload`。服务不需要 root 权限，也不需要开放入站端口。若 Telegram 不可直连，可先为服务器配置可用的出站网络；本项目当前没有内置代理配置项。

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

```bash
sudo systemctl stop tg-butler
cd /opt/tg-butler
sudo -u tgbutler git pull --ff-only
sudo -u tgbutler /opt/tg-butler/.venv/bin/pip install --upgrade /opt/tg-butler
sudo systemctl start tg-butler
```

备份前先停止服务，再复制 `.env`、`config.yaml` 和整个 `data/`，并妥善加密保管。`data/` 包含 SQLite 数据库及可能的 WAL 文件，复制单个数据库文件可能丢失尚未合并的记录。

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
