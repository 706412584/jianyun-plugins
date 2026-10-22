# 简云插件仓库

简云 Pro 的插件索引与发布仓库。App 内「插件 → 市场」读取本仓库的
[`index.json`](index.json) 展示可安装插件。

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

### 2. 不会 Git：网页表单

打开 [提交插件](https://github.com/706412584/jianyun-plugins/issues/new?template=plugin-submission.yml)，
填写名称、说明、版本，把插件包（**`.zip`**，不是 `.plugin`）拖进文本框提交。

机器人会自动下载、校验、发布，并在 Issue 里回复结果。失败会说明原因，改完重新提交即可。

> GitHub 的附件上传不支持 `.plugin` 扩展名，所以表单收 `.zip`；
> 机器人会重命名为 `.plugin` 再发布，App 侧无差别。

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
| 危险命令 | `rm -rf /`、`curl \| sh`、`chmod 777 /`、写 `/data/data/`、`/system` |
| 外连域名 | 未在 manifest 声明时给出警告 |

本地也能跑：

```bash
python tools/validate.py plugins/my-plugin
```

---

## 信任分级

| 标记 | 含义 |
|---|---|
| `verified` | 已知维护者（`.github/trusted-publishers.txt`） |
| `community` | 社区投稿，自动校验通过 |
| `disputed` | 被举报，App 内显示红色警告 |

首次投稿的发布者会进入 `submissions/` 等待人工查看，之后自动发布。

---

## 安全说明

插件在**容器内**运行，不进入 App 进程。但这不意味着绝对安全：

- 插件能访问容器里的所有数据，包括你的站点文件、数据库、环境变量里的凭据
- 安装前请在 App 的确认弹窗里**看清 `installSteps` 要执行的命令**
- 发现可疑插件请提 Issue 举报

本仓库不做包签名，完整性靠 HTTPS + SHA-256 保证。索引本身可被仓库维护者替换，
这是已知的残余风险。

---

## 许可

插件由各自作者授权，见各插件 `manifest.json` 的 `homepage` 或包内说明。
本仓库的工具脚本（`tools/`）以 MIT 授权。
