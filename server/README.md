# 服务器端 Clash 自动合并与 AI 节点筛选

## 1. 适用场景

这套方案面向 `Linux 服务器` 上直接运行的 `clash / mihomo`，不是 `Clash Verge` 的 `profiles.yaml` 工作流。

核心目标有 4 个：

- 把多个公司的 YAML 或订阅 URL 统一纳入同一套节点池
- 自动过滤香港和俄罗斯相关节点
- 只把通过 `OpenAI` 目标集探测的节点放进 `AI_AUTO`
- 在你执行 `vpnon` 打开当前 terminal 的 VPN 后，自动触发一次刷新

## 2. 仓库结构

`server/` 目录下的文件分工如下：

- `server_clash_merge.py`
  - 主脚本
  - 负责读取多个 source、合并节点、过滤地区、做资格探测、生成最终配置、热重载、写状态文件
- `sources.yaml`
  - source 索引
  - 定义有哪些 `file` 源和 `url` 源要纳入合并
- `service_targets.yaml`
  - 资格探测目标
  - 当前默认要求节点能通过 `api.openai.com`、`auth.openai.com`、`chatgpt.com`、`platform.openai.com`
- `shell_helpers.sh`
  - 提供 shell 命令
  - 包含 `vpnon`、`vpnon_wait`、`vpnoff`、`novpn`、`vpnrefresh`、`clashup`
- `run_refresh.sh`
  - 刷新包装脚本
  - 用 `flock` 做互斥，避免多次并发刷新
- `.gitignore`
  - 忽略 `cache/`、`generated/`、`logs/`

运行时会自动生成以下目录内容，但不会提交到 git：

- `cache/`
  - 远程订阅缓存
- `generated/`
  - 生成的配置和状态文件
- `logs/`
  - 刷新日志

## 3. 关于订阅 URL

像下面这种链接，从结构上看就是典型的订阅 URL：

```text
https://b.flyintpro01.com/api/v1/client/subscribe?token=xxxxxxxx
```

原因不是只看字符串里有 `subscribe`，而是它通常同时具备这几个特征：

- 路径是 `/api/v1/client/subscribe`
- 查询参数里带 `token=...`
- 访问后返回的是 Clash 可解析的 YAML，而不是普通网页

这种 URL 通常可以直接作为 `sources.yaml` 里的 `type: url` 源使用，不必再从 Windows 手工拷贝 YAML。

但它 `不保证永远不变`。稳定性取决于订阅服务商的策略，常见变化来源有：

- 你手动点了“重置订阅链接”或“重置 token”
- 套餐到期后，服务商给你重新签发了 token
- 服务商做了风控、迁移域名或更换后端
- 账户体系变化，导致旧 token 失效

所以更准确的说法是：

- `token URL 往往能稳定使用较长时间`
- `但它不是不可变常量`
- 配置时应把它当成“长期但可替换的 source”

## 4. sources.yaml 怎么组织

建议的组织原则是：

- 一个 provider 一个 `name`
- `name` 保持稳定，不要把日期写进主名称
- 尽量优先使用 `type: url`
- 只有拿不到原始订阅 URL 时，才退回 `type: file`

### 4.1 本地 YAML 源

```yaml
sources:
  - name: flyintpro_current
    type: file
    path: /cephfs/zyhuang/clash/config.yaml
    enabled: true
```

这适合你当前已经有一个现成的大 YAML 文件在服务器上的情况。

### 4.2 远程订阅源

```yaml
sources:
  - name: flyintpro_remote
    type: url
    url: https://example.com/api/v1/client/subscribe?token=REPLACE_ME
    cache_path: ./cache/flyintpro_remote.yaml
    use_proxy: true
    enabled: true
```

这里有两个实现细节要注意：

- `cache_path` 可以写相对路径，脚本会相对 `sources.yaml` 所在目录解析
- `use_proxy: true` 表示刷新这个远程订阅时，允许复用当前 terminal 的代理环境

这和你的工作流是匹配的，因为你现在希望：

- 不是开机就自动刷新
- 而是在你执行 `vpnon` 之后自动刷新

## 5. shell 命令怎么用

先在 shell 初始化文件里 source 这个文件：

```bash
source /root/clash_auto_refresh/server/shell_helpers.sh
```

如果你希望新 terminal 直接可用这些命令，可以把上面这行放进 `~/.bashrc` 和 `~/.bash_profile`。

### 5.1 打开 VPN 并后台刷新

```bash
vpnon
```

它做的事情是：

1. 确认 `clash` 进程已启动，不在则自动拉起
2. 只给当前 terminal 设置代理环境变量
3. 后台执行一次 `run_refresh.sh`
4. 输出当前 terminal 的代理环境变量

### 5.2 打开 VPN 并前台等刷新完成

```bash
vpnon_wait
```

适合你想立刻观察刷新是否成功的场景。

### 5.3 关闭当前 terminal 的 VPN

```bash
vpnoff
```

它只清当前 shell 的代理变量，不会影响别的 terminal，也不会停止后台运行的 `clash` 进程。

### 5.4 某一条命令临时直连

```bash
novpn <command> [args...]
```

例如：

```bash
novpn curl https://example.com
```

这会只对这一条命令取消代理，不改当前 shell 的长期状态。

### 5.5 已开 VPN 后手动刷新

```bash
vpnrefresh
```

### 5.6 只拉起 clash 进程

```bash
clashup
```

## 6. 手动执行主脚本

### 6.1 只生成配置，不热重载

```bash
python /root/clash_auto_refresh/server/server_clash_merge.py --generate-only
```

这个模式适合先验证 `sources.yaml` 是否写对，以及过滤统计是否符合预期。

### 6.2 完整刷新并热重载

```bash
python /root/clash_auto_refresh/server/server_clash_merge.py
```

### 6.3 实际由 vpnon 触发的包装脚本

```bash
/root/clash_auto_refresh/server/run_refresh.sh
```

## 7. 生成结果

脚本会生成如下逻辑结构：

- `AI_AUTO`
  - 通过资格筛选后的节点集合
  - 用 `url-test` 持续自动选当前最快节点
- `AI_STABLE`
  - 同一批合格节点的 `fallback` 兜底
- `AI_ALLOWED`
  - 聚合 `AI_AUTO`、`AI_STABLE` 和全部合格节点
- `BLOCKED_REGIONS`
  - 被识别为香港或俄罗斯的节点
- `ALL_NODES`
  - 所有节点，便于调试
- `GLOBAL`
  - 默认出口组

识别被拦截地区时，不只看 `name`，还会检查：

- `server`
- `sni`
- `plugin-opts.host`
- `ws-opts.headers.Host`

所以像名字没写 `HK`、但后端实际仍是 `hkxxx` 的节点，也会被过滤。

## 8. 当前推荐工作流

推荐按下面顺序使用：

1. 先把各家 provider 的订阅 URL 写进 `sources.yaml`
2. `source /root/clash_auto_refresh/server/shell_helpers.sh`
3. 在需要代理的 terminal 里执行 `vpnon`
4. 刷新完成后开始用当前 terminal 做需要代理的工作
5. 某个 terminal 不想走代理时，执行 `vpnoff`

这个工作流的优点是：

- 不需要每次从 Windows 手动拷 YAML
- 不需要每个新 terminal 手工打一长串 `export`
- 某个 terminal 关代理时不会影响其他 terminal
- 节点选择不是只靠延迟 URL，而是先满足 OpenAI 目标集可达
