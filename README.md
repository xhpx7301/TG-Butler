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

脚本会安装依赖、创建专用账号、下载代码、建立 Python 虚拟环境并注册 systemd 服务，然后进入 **bu 中文管理菜单**。按编号逐项填写凭据、登录 Telegram、配置群组、测试通知和启动服务，无需在编辑器中修改配置文件。首次安装仍需你输入验证码和可能的两步验证密码，脚本无法代替 Telegram 的账号验证。

运行前请准备下文列出的 Telegram 应用凭据、通知机器人 Token 和你自己的用户 ID，并向通知机器人发送 `/start`。群组 ID 会在登录后列出；关注用户 ID、签到命令和签到机器人的数字 ID 需按实际群组填写。

重复运行会更新 `main` 代码、保留 `.env`、`config.yaml` 和 `data/`，并在重新配置前停止服务。安装中断时检查终端错误后重新执行即可。默认安装到 `/opt/tg-butler`，首次使用保持 `data_dir: data`；自定义路径需按下文调整 systemd 服务。

只安装环境、稍后手动配置，可在已经克隆的仓库执行：

```bash
sudo bash deploy/install.sh --non-interactive
```

此模式不填写凭据、不登录、不启动服务；如果已有运行中的服务，会先停止。安装完输入 `bu` 即可配置。完整手动安装流程见下文“Debian 部署”。

## bu 中文管理菜单

安装后在服务器任意目录输入：

```bash
bu
```

普通用户会自动请求 sudo 权限，root 可直接使用。菜单包含：

| 选项 | 功能 |
| --- | --- |
| 1–4 | 填写 Telegram 凭据和通知机器人、登录账号、查看群组与成员 ID |
| 5 | 添加、编辑、删除监控群组，配置多个关注用户 ID、全群提醒、群组日报开关 |
| 6 | 日报开关、发送时间、AI 接口和模型、输入条数与字数限制 |
| 7 | 添加、编辑、删除签到任务，设置时间、命令、回复机器人、成功/失败规则及超时 |
| 8–11 | 时区、消息保留天数、通知队列、配置总览、配置校验、通知测试 |
| 12–18 | 启停与重启服务、开机启动、服务状态、日志、执行记录、手动日报 |
| 19 | 从 GitHub 更新脚本、依赖和 bu 菜单，保留凭据、配置与数据 |
| 0 | 退出菜单，保持当前服务状态 |

首次配置推荐顺序：**1 → 2 → 3 → 5 → 6/7（按需）→ 11 → 12**。示例群组 ID 必须替换成真实 ID，或在菜单中删除。菜单输入回车保留当前值；可选项提示中支持 `-` 清空。密钥输入不回显，配置总览隐藏密钥。

保存参数前会校验，校验失败不覆盖文件；保存成功会重启正在运行的服务，使参数生效。服务原本停止时保持停止。登录、查看群组/成员以及手动日报会临时暂停服务，完成后恢复原运行状态，以免共用账号会话。暂停期间的新消息不会自动补抓。菜单通过系统锁限制同时打开多个 `bu`。

更新选项只允许干净的 `main` 分支及官方 origin，并使用快进合并；遇到代码冲突会中止。更新成功恢复原有服务运行状态；失败时若服务已停止，修复后需重新更新并手动启动。现有 systemd 服务文件会保留；模板有变化时另存为 `.service.new` 供人工比对。更新完成退出菜单，再次输入 `bu` 加载新版。

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

提供 `bu` 中文终端菜单和命令行；也可直接修改 `config.yaml` 并重启服务。当前没有网页管理界面或机器人管理命令。

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

手动安装如需 `bu` 菜单，再执行 `sudo install -m 755 /opt/tg-butler/deploy/bu /usr/local/bin/bu`（普通用户还需安装 `sudo`）。

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

推荐运行 `bu` 并选择 **19. 更新脚本**。不打开菜单也可以执行：

```bash
sudo bash /opt/tg-butler/deploy/update.sh
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
