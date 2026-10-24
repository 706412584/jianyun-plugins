# 简云插件仓库

简云 Pro 的插件索引与发布仓库。App 内「插件 → 市场」读取本仓库的
[`index.json`](index.json) 展示可安装插件。

**要写插件**（不只是投稿）→ 看 **[插件开发指南](docs/plugin-dev.md)**。

## 三种投稿方式

### 1. 会 Git：提 PR（推荐）

```bash
git clone https://github.com/706412584/jianyun-plugins
cd jianyun-plugins
mkdir -p plugins/my-plugin/payload
# 写入 plugins/my-plugin/manifest.json 与 payload/ 下的文件
git checkout -b add-my-plugin
git add plugins/my-plugin
git commit -m "feat: 新增 my-plugin"
git push origin add-my-plugin
```

然后在 GitHub 上开 PR。CI 会自动校验（格式、大小、危险命令、zip 结构），
全绿后由维护者合并。合并后 CI 自动打包发布，`index.json` 随之更新。

插件包结构：

```
my-plugin/
├── manifest.json
└── payload/
    ├── start.sh
    └── ...
```

### 2. 不会 Git：网页表单 / App 内直接投稿

两种入口，效果一样：

- 网页：[提交插件](https://github.com/706412584/jianyun-plugins/issues/new?template=plugin-submission.yml)
- App 内：**插件 → 市场 → 右上角「发布」**，选一个 `.plugin` 包，App 会自动
  导出一份 `.zip` 到手机的「下载」目录，并打开填好信息的表单页；
  你把刚导出的 zip 拖进「插件包」字段即可。

机器人会自动下载附件并校验，通过后**开一个 PR 等你合并**，并在 Issue 里回复 PR 链接。

> 为什么要人工合并：自动校验只能挡住已知的危险命令模式
> （`rm -rf /`、`curl | sh`、写 `/system` 等），挡不住「把容器里的凭据发到某个
> 看起来正常的域名」这类行为。插件在用户容器里以 root 运行，发布权必须经过人。

> GitHub 的附件上传不支持 `.plugin` 扩展名，所以表单收 `.zip`；
> 机器人会解包成源目录，最终仍打包成 `.plugin` 发布，App 侧无差别。
> 两者本质都是 zip（宿主不靠后缀判格式），详见
> [插件开发指南的「包的后缀」](docs/plugin-dev.md#包的后缀plugin-与-zip)。

### 3. 什么都不会：发给维护者

把插件包发到 QQ `706412584`，说明用途，由维护者代传。

---

## 插件清单（manifest.json）

```json
{
  "schemaVersion": 1,
  "id": "my-plugin",
  "name": "我的插件",
  "description": "一句话说明它做什么",
  "version": "1.0.0",
  "publisher": "your-github-name",
  "category": "network",
  "architectures": ["arm64"],
  "permissions": ["NETWORK"],
  "servicePort": 8080,
  "servicePath": "/health",
  "installSteps": ["apt-get install -y --no-install-recommends curl"],
  "launchCommand": "bin/start.sh",
  "verifyCommand": "test -x bin/start.sh",
  "settings": [],
  "endpoints": [],
  "actions": [],
  "infoCommand": ""
}
```

字段说明见 App 内插件详情页，或 `app/src/main/java/com/zm920/jianyunserverpro/ai/plugin/PluginManifest.kt`。

### id 规则

`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` —— 字母或数字开头，之后可含 `.` `_` `-`，
最长 64 字符。**不允许** `..`、`.`、斜杠、空格、非 ASCII 字符。

### 版本规则

语义化版本（`1.2.3`）。同一 id 的新版本必须**大于**已发布版本，否则 CI 拒绝。

---

## 校验规则

| 检查 | 规则 |
|---|---|
| 包大小 | ≤ 20 MB |
| 解压后大小 | ≤ 100 MB |
| zip 条目 | 不得含 `..`、绝对路径、符号链接 |
| payload 二进制 | 不允许 ELF / PE / Mach-O（脚本可以） |
| 危险命令 | `rm -rf /`、`curl \| sh`、`chmod 777 /`、写 `/data/data/`、`/system`。**`payload/` 里的脚本与 manifest 里会被执行的命令字段（installSteps / uninstallSteps / launchCommand / verifyCommand / infoCommand / actions[].command）都扫** |
| 外连域名 | 未在 manifest 声明时给出警告 |

本地也能跑：

```bash
python tools/validate.py plugins/my-plugin
```

---

## 信任分级

| 标记 | 含义 |
|---|---|
| `verified` | 维护者**人工看过**这个插件的命令并签字（见下） |
| `community` | 社区投稿，自动校验通过，未经人工审核 |
| `disputed` | 被举报，App 内显示红色警告 |

**`verified` 不是自动的。** manifest 里的 `publisher` 是投稿者自己填的字符串，
不是身份证明——任何人写 `"publisher": "706412584"` 都能命中
`.github/trusted-publishers.txt`。所以「已验证」要求两个条件同时成立：

1. `publisher` 在 `.github/trusted-publishers.txt` 里；
2. 插件 id 在 `.github/verified-plugins.txt` 里（维护者人工看过后加进去）。

条件 2 只能通过 PR 由维护者合并来满足，投稿者改不到它。

**投稿路径**：

- Issue 表单投稿 → 自动校验 → 开 PR，等维护者合并（**不会直接进 master**）；
- 若 Issue 作者本人就是 `publisher` 且在受信列表里 → 自动发布，但仍标 `community`
  （受信发布者的新插件也要先签字才能变 `verified`）；
- PR 投稿 → CI 校验 → 维护者合并。

---

## 安全说明

插件在**容器内**运行，不进入 App 进程。但这不意味着绝对安全：

- 插件能访问容器里的所有数据，包括你的站点文件、数据库、环境变量里的凭据
- 安装前请在 App 的确认弹窗里**看清 `installSteps` 要执行的命令**
- 发现可疑插件请提 Issue 举报

本仓库不做包签名，完整性靠 HTTPS + SHA-256 保证。索引本身可被仓库维护者替换，
这是已知的残余风险。

---

## 维护者备忘

### 必需的标签

Issue 表单模板声明了 `labels: [plugin-submission]`，但 **GitHub 不会自动创建标签**，
只会静默忽略。而 `ingest-issue.yml` 的触发条件是
`contains(github.event.issue.labels.*.name, 'plugin-submission')`——
标签不存在时 workflow 永远不跑，投稿者提交后什么也不会发生（且没有任何报错）。

新建仓库或 fork 后必须执行：

```bash
gh label create plugin-submission --description "通过 Issue 表单提交的插件投稿" --color 0E8A16
```

`needs-fix` 标签由失败回复步骤自动添加（`--add-label needs-fix || true`）：
**缺了它不影响投稿处理**，只是用户看不到「需要修改」的标记，所以可选。
要建的话：

```bash
gh label create needs-fix --description "投稿未通过，需要作者修改" --color D93F0B
```

### 允许 Actions 创建 PR

仓库设置里 **Settings → Actions → General → Workflow permissions** 必须勾选
**Allow GitHub Actions to create and approve pull requests**。

默认是关的。关着的时候 `gh pr create` 会直接失败，社区投稿（也就是不会用
Git 的那条通道）整条走不通——而失败回复又会把它报成「仓库配置问题」。

命令行等价操作（需要仓库 admin 权限）：

```bash
gh api -X PUT repos/<owner>/<repo>/actions/permissions/workflow \
  -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=true
```

### 给插件签字（标为 verified）

把插件 id 加进 `.github/verified-plugins.txt`（一行一个）。加之前请自己打开
`plugins/<id>/manifest.json` 看一遍 `installSteps` 与 `payload/` 里的脚本。

---

## Gitee 镜像

**为什么有这个东西**：国内移动网络访问不了 GitHub 的 Release 附件
（`cdn.jsdelivr.net` 直接连不上、`github.com` 的附件下载常被阻断），而 App 的市场
读取清单、下载插件包都要走网络。镜像让国内用户不挂代理也能用市场。

**App 侧已内置**：`PluginMarket.indexUrls()` 把 Gitee 排在第一通道，
`PluginMarket.sourcesFor()` 会把 GitHub Release 附件地址自动换成 Gitee 等价地址。
镜像没同步时自动回落到 raw / gh-proxy / ghfast，不影响可用性。

**镜像要同步两样**（缺一不可）：

| 内容 | 不同步的后果 |
|---|---|
| 代码与 `index.json` | 市场列表拉不到 |
| Release 附件（插件包本体） | 列表能显示，但点安装必失败 |

`.github/workflows/mirror-gitee.yml` 在上游「打包并发布」成功后自动双推。

### 一次性配置（维护者）

1. 在 Gitee 建一个与 GitHub 同名的仓库（如 `jianyun-plugins`）。
2. 本仓库 **Settings → Secrets and variables → Actions** 加
   `GITEE_TOKEN`：Gitee 的**私人令牌**，勾 `projects` 权限。
3. `mirror-gitee.yml` 里的 `GITEE_OWNER` 改成你的 Gitee 用户名/组织名。
4. 手动跑一次该 workflow（Actions → 同步到 Gitee 镜像 → Run workflow）验证。

> Gitee 的 Release 附件单文件上限 **100 MB**，单仓库容量也有限。
> 插件包上限 20 MB（见校验规则），不会碰到这个限制。

## 许可

插件由各自作者授权，见各插件 `manifest.json` 的 `homepage` 或包内说明。
本仓库的工具脚本（`tools/`）以 MIT 授权。
