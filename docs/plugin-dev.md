# 插件开发指南

写给要写一个简云插件的人。读完能自己做出一个可安装、可启动、能在 App 里配置和操作插件。

- 只想**投稿**（已经写好包了）→ 看 [README 的三种投稿方式](../README.md)
- 想知道**校验规则**和**信任分级** → 看 [README 的校验规则](../README.md)

---

## 1. 插件是什么

一个插件 = 一个 zip 包，里面是**清单**加**载荷**：

```
my-plugin/
├── manifest.json      # 清单：声明式描述，宿主只读、不执行
└── payload/           # 载荷：真正跑起来的东西
    ├── start.sh
    └── ...
```

宿主（App）读 `manifest.json` 决定怎么展示、怎么装、怎么启动；`payload/` 下的文件会被
解到容器里执行。**清单里没有代码**，宿主也从不执行清单内容——所以清单能写的只是
「要执行什么命令、要显示什么」，不是「怎么执行」。

### 包的后缀：`.plugin` 与 `.zip`

**两者是同一个东西，都是 zip 压缩包**，区别只在文件名后缀，装起来完全一样：

| 后缀 | 用在哪 |
|---|---|
| **`.plugin`** | 插件的正规后缀。市场下载下来的包就是它，用来标识「这是一个插件包」 |
| `.zip` | 等价的通用后缀，导入时同样接受 |

宿主**不靠后缀判断格式**——它直接用 zip 解析器打开，格式由包内的 `manifest.json` 决定。
所以：

- 自己打包时，叫 `my-plugin-1.0.0.plugin` 或 `.zip` 都能装；
- 投稿表单要求 `.zip`，只是因为 **GitHub 的附件上传不接受 `.plugin` 扩展名**，
  不是格式不同（机器人解包后仍以 `.plugin` 发布，App 侧无差别）。

> 实践建议：**对外分发用 `.plugin`**。它是插件的标识，用户一眼能看出这是插件包而不是
> 普通压缩文件；`.zip` 在系统文件管理器里会和普通压缩包混在一起。

### 运行环境

插件在**容器内**运行（proot 起的 Debian/Kali rootfs），不是在 App 进程里：

| 事实 | 说明 |
|---|---|
| 身份 | `root` |
| 工作目录 | 插件目录（启动命令先 `cd` 进去再跑） |
| 插件目录 | `/root/.jianyun/plugins/<id>`（宿主侧对应 `filesDir/agent/plugins/<id>`） |
| 初始系统 | Debian/Kali 基础镜像，只有 `python3`，**没有** `python`、`node`、`curl` 等 |
| 缺失依赖 | 在 `installSteps` 里 `apt-get install -y` 自己装 |
| 无 systemd | 不要写 service 文件；常驻进程由宿主托管（见 [第 5 节](#5-常驻服务)） |
| 网络 | 默认可用（出网域名建议在 `permissions` 里声明） |

> **`python` 不存在**：镜像里只有 `python3`。脚本首行写 `#!/usr/bin/env python3`。
> 这是最常见的第一次踩坑。

---

## 2. 最小可用插件

`manifest.json`：

```json
{
  "schemaVersion": 1,
  "id": "hello-service",
  "name": "问候服务",
  "description": "一个最小的常驻 HTTP 服务，用于验证安装与启动流程",
  "version": "1.0.0",
  "publisher": "your-name",
  "category": "示例",
  "servicePort": 18080,
  "launchCommand": "payload/start.sh",
  "permissions": ["NETWORK"]
}
```

`payload/start.sh`（记得 `chmod +x`）：

```sh
#!/bin/sh
exec python3 -m http.server "$SERVICE_PORT"
```

要点：
- `id` 是插件的永久身份，**发布后不要再改**（改了等于新插件，旧版本记录对不上）
- `version` 必须**大于**已发布版本，否则 CI 和 App 预检都会拒
- `servicePort` 非 0 表示这是**常驻服务**，宿主会托管它并轮询就绪

---

## 3. 清单字段

### 必填

| 字段 | 说明 |
|---|---|
| `schemaVersion` | 固定 `1` |
| `id` | `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`。字母/数字开头，之后可含 `.` `_` `-`，≤64 字符。**不允许** `..`、`.`、`/`、空格、中文 |
| `name` | 显示名 |
| `description` | 一句话说明它做什么（投稿表单和详情页都显示它） |
| `version` | 语义化版本 `1.2.3`，必须单调递增 |
| `publisher` | 你的 GitHub 用户名（不是身份证明，见 README 的信任分级） |

### 安装与运行

| 字段 | 说明 |
|---|---|
| `installSteps` | 安装时逐条在容器里执行的命令。**用户会在确认弹窗里看到原文**，所以别写看不懂的东西 |
| `uninstallSteps` | 卸载时逐条执行 |
| `verifyCommand` | 安装后自检，退出码非 0 视为安装失败并回滚 |
| `launchCommand` | 启动命令。绝对路径原样用；否则先找插件自带 `bin/` 下的同名入口，再回退 `PATH` |
| `environment` | 启动时注入的环境变量（`{"KEY": "value"}`） |

### 服务与展示

| 字段 | 说明 |
|---|---|
| `servicePort` | 服务端口，`0` 表示非常驻。宿主轮询它判定「已就绪」 |
| `servicePath` | 就绪探测路径，如 `/health`。配合 `servicePort` |
| `launchType` | `web` / `service` / `command`，当前只用于展示 |
| `architectures` | `["arm64"]` 等；留空表示不限 |
| `permissions` | 安装前提示用，如 `NETWORK`、`WORKSPACE_READ`。**仅提示，不强制拦截** |
| `homepage` | 项目地址 |
| `category` | 分类，见 README 的投稿表单选项 |

### 扩展点

见 [第 4 节](#4-设置连接方式与操作)。

---

## 4. 设置、连接方式与操作

这三样让插件在 App 里「像一个原生功能」。

### settings —— 让用户在 App 里配置

```json
"settings": [
  { "key": "SERVICE_PORT", "label": "监听端口", "type": "number",
    "default": "18080", "min": 1024, "max": 65535 },
  { "key": "AUTH_TOKEN", "label": "访问令牌", "type": "text",
    "hint": "留空则不校验" },
  { "key": "MODE", "label": "运行模式", "type": "select",
    "options": [ {"value": "fast", "label": "快速"}, {"value": "safe", "label": "安全"} ] },
  { "key": "ENABLE_CACHE", "label": "启用缓存", "type": "toggle", "default": "true" }
]
```

| 字段 | 说明 |
|---|---|
| `key` | **同时是环境变量名**。必须合法（字母开头，字母数字下划线），否则会被忽略 |
| `label` | 表单里显示的名字 |
| `type` | `text` / `number` / `toggle` / `select` / `textarea` |
| `default` | 默认值（都以字符串写） |
| `hint` | 输入框下方的说明 |
| `options` | 仅 `select` 用：`[{"value": "...", "label": "..."}]` |
| `min` / `max` | 仅 `number` 用 |
| `group` | 表单分组标题 |

用户改了的值存在插件目录的 `settings.json`。**优先级：用户设置 > 清单 `environment`**，
所以同名时用户值覆盖清单默认。启动脚本直接读环境变量即可。

### endpoints —— 展示连接地址

```json
"endpoints": [
  { "label": "WebSocket", "url": "ws://127.0.0.1:{SERVICE_PORT}{WS_PATH}",
    "hint": "把这段填进客户端" }
]
```

`url` 里可以写 `{设置项 key}` 占位符，界面会用**当前设置值**替换后再显示——
用户改了端口，显示的地址自动跟着变。不必自己拼字符串。

### actions —— 详情页按钮

```json
"actions": [
  { "key": "refresh", "label": "刷新数据",
    "command": "python3 payload/action_refresh.py",
    "description": "拉一次最新数据" },
  { "key": "import", "label": "导入配置",
    "command": "python3 payload/import.py",
    "pick": "json", "description": "选一个 JSON 文件导入" },
  { "key": "export", "label": "导出结果",
    "command": "python3 payload/export.py",
    "export": "data/exports/*.json", "exportName": "result.json",
    "description": "生成并保存到手机" }
]
```

命令**在容器里、以插件目录为工作目录**执行，环境变量与安装步骤一致。

| 字段 | 说明 |
|---|---|
| `pick` | 非空时点击先让用户选文件；文件存进插件数据目录，容器内路径通过 `JY_IMPORT_FILE` 传给你的命令 |
| `export` | 非空时命令跑完去插件目录按这个通配符找产物（取最新的），让用户「另存为」到手机 |
| `exportName` | 保存时提议的文件名 |

### infoCommand —— 自定义详情页信息

```json
"infoCommand": "python3 payload/info.py"
```

宿主执行它，把 **stdout 当 JSON** 渲染成详情页的「插件信息」区。
有了它，要展示什么完全由插件自己决定（能从自己的设置、运行状态里现算），
不用等 App 加字段、也不用发新版本。

---

## 5. 常驻服务

`servicePort` 非 0 即视为常驻服务，宿主会：

1. 执行 `launchCommand` 起进程
2. 轮询 `servicePort`（配合 `servicePath`）判定就绪
3. 展示「运行中/已停止」，提供启停按钮
4. 用户停止时**按端口**结束占用者（依次尝试插件目录、环境变量、工作目录、端口四种匹配方式）

因此你**不要**在脚本里 `&` 后台化——正常前台运行，让宿主管生命周期。
`launchCommand` 用 `exec` 起真正的进程，免得中间多一层 shell 影响停止时的进程匹配。

---

## 6. 环境变量

宿主会在安装、卸载、启动、动作执行前注入：

| 变量 | 值 |
|---|---|
| `JY_PLUGIN_DIR` | 插件目录（容器内），如 `/root/.jianyun/plugins/hello-service` |
| `JY_PLUGIN_PAYLOAD` | `<插件目录>/payload` |
| `JY_PLUGIN_DATA` | `<插件目录>/data`，**放运行期数据用这里** |
| `JY_IMPORT_FILE` | 仅带 `pick` 的动作：用户所选文件在容器内的路径 |
| `<你的 settings key>` | 用户设置值（覆盖清单 `environment` 的同名项） |

`TAIXU_PLUGIN_PAYLOAD` / `TAIXU_TOOL_DIR` / `TAIXU_TOOL_DATA` 是旧名的兼容别名，
新插件用 `JY_` 前缀。

> **`data/` 是留给运行期的**：升级时 `payload/` 会被新版本覆盖，
> 把用户数据放在 `payload/` 里会在升级时丢掉。

---

## 7. 调试

| 手段 | 位置 |
|---|---|
| 查看插件日志 | App → 插件 → 点插件 → 「查看日志」 |
| 看安装/启动输出 | 安装失败弹窗里会直接贴容器输出原文 |
| 手动进容器 | App → 终端（容器 shell），`cd /root/.jianyun/plugins/<id>` |

迭代时不必每次投稿：把包推到手机，App → 插件 → 顶部「导入」，选 `.plugin` 或 `.zip` 即可
（后缀不限，见「[包的后缀](#包的后缀plugin-与-zip)」）。

---

## 8. 校验与硬限制

投稿会自动校验，本地也能提前跑：

```bash
python tools/validate.py plugins/my-plugin
```

| 检查 | 规则 |
|---|---|
| 包体积 | ≤ 20 MB |
| 解压后体积 | ≤ 100 MB |
| 条目数 | ≤ 2000 |
| zip 结构 | 不得含 `..`、绝对路径、符号链接 |
| payload 二进制 | 不允许 ELF / PE / Mach-O（脚本可以） |
| 危险命令 | `rm -rf /`、`curl \| sh`、`chmod 777 /`、写 `/data/data/`、`/system` 等 |
| 扫描范围 | `payload/` 下的脚本，**以及清单里所有会被执行的命令字段**（`installSteps` / `uninstallSteps` / `launchCommand` / `verifyCommand` / `infoCommand` / `actions[].command`） |
| 外连域名 | 未声明时给警告 |

> 「不允许二进制」是硬性的：插件是脚本生态，塞 ELF 会被拒。
> 需要外部二进制就往 `installSteps` 里写 `apt-get install`，或让插件在运行时下载。

---

## 9. 常见坑

| 现象 | 原因 |
|---|---|
| 安装后启动即失败，日志 `python: not found` | 镜像里只有 `python3`，改用 `python3` |
| 装完就报「端口被占用」 | `servicePort` 与容器里已有服务冲突，换一个 |
| 用户改的设置没生效 | `key` 不是合法环境变量名（须字母开头、仅字母数字下划线），被静默忽略 |
| 升级后用户数据没了 | 把数据写进了 `payload/`；应写 `JY_PLUGIN_DATA` |
| 停止按钮点了进程还在 | 脚本里 `&` 后台化了，中间 shell 退出导致进程匹配不到；改成前台运行 |
| 版本被拒 | 新 `version` 必须**大于**已发布版本（不是不等于） |
| 投稿后没反应 | Issue 缺少 `plugin-submission` 标签（仓库未初始化时的已知问题，见 README 维护者备忘） |

---

## 10. 完整示例

`plugins/hello-service/` 是一个可运行的最小常驻服务：起一个 HTTP 服务返回问候语，
带 `installSteps`、`verifyCommand`、一个 `settings`（问候语可改）和一个 `endpoints`。
**直接复制这个目录改名**就是新插件的起点。

读它的 `manifest.json` 时对照本指南第 3–4 节，能看清每个字段实际怎么填。
