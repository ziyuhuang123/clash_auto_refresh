# Clash 多订阅自动合并与 AI 节点自动选择工具

## 1. 项目定位

这是一个给 `Clash Verge` / `Mihomo` 使用的本地自动化工具，目标是：

- 自动发现并合并所有 `remote` 类型订阅
- 自动过滤不适合访问 `OpenAI / Codex / GPT` 等服务的地区节点
- 自动创建探测策略组，并且只在同时通过 `OpenAI + Medium` 探测的节点中自动切换
- 定时自动重跑，减少手工刷新和手工切换图形界面的次数

它不是某一家订阅厂商专用脚本，而是基于 `Clash Verge` 本地配置结构工作的通用脚本。

## 2. 适用条件

适用前提如下：

- 使用的是 `Clash Verge` 或兼容其本地目录结构的客户端
- 本地存在 `profiles.yaml`
- 你的订阅已经被导入到客户端里，并且在 `profiles.yaml` 中属于 `type: remote`
- 本机有 Python 3
- Python 环境可用以下依赖：
  - `requests`
  - `PyYAML`

## 3. 这套工具解决的问题

它主要解决三类问题：

### 3.1 多个订阅统一纳入自动选择范围

只要订阅已经导入到 `Clash Verge` 中，并且类型是 `remote`，脚本每次运行时都会自动重新扫描并纳入合并范围。

这意味着：

- 你新增了一家公司的订阅，脚本下次运行就会自动包含它
- 你删除了一家公司的订阅，脚本下次运行就不会再包含它
- 你刷新了已有订阅，脚本下次运行会优先使用最新订阅内容

### 3.2 针对 AI 服务过滤不合适节点

默认会从自动选择组中排除以下地区：

- 香港
- 俄罗斯

识别时不只看节点名，还会看：

- `server`
- `sni`
- `plugin-opts.host`
- 部分 `ws-opts.headers.Host`

所以即使节点名没写地区，但服务器字段里带有 `hk`、`ru` 等明显特征，也会被识别出来。

### 3.3 自动探测与失效检测

脚本生成的自动策略组会使用 Mihomo 原生探测机制。

也就是说：

- 当前已加载的节点集合里，Mihomo 会持续自动重测并切换
- 如果某个节点失效，只要组里还有其他活节点，Mihomo 会自动改用其他节点
- 如果自动组里所有节点都不可用，脚本会把失败写入状态文件，并尽量区分更像是“本机没网”还是“节点整体失效”

## 4. 是否会一直动态刷新节点选择

结论是：会，但要分成两个层面理解。

### 4.1 已加载节点中的自动选择会动态刷新

会。

脚本生成的 `AI_AUTO` 是 `url-test` 组，Mihomo 会按固定间隔持续探测。

默认参数：

- 探测地址：`https://auth.openai.com`
- 探测间隔：`300` 秒

因此，只要：

- Mihomo 还在运行
- 当前加载的是这份合并配置

那么 `AI_AUTO` 就会在允许节点中持续重新评估，并自动切换。

### 4.2 订阅内容本身不会凭空刷新

这一层依赖脚本再次执行。

为了解决这个问题，本项目已经提供计划任务安装脚本，可以让它定时自动执行。

## 5. 文件说明

本项目目录下的主要文件如下：

- `clash_auto_merge.py`
  - 主脚本
  - 负责读取订阅、合并节点、过滤地区、生成配置、热重载、检查状态

- `run_clash_auto_merge.cmd`
  - 手工执行入口
  - 适合命令行或双击运行

- `scheduled_run.ps1`
  - 给 Windows 计划任务调用的后台执行脚本
  - 负责日志落盘和并发互斥，避免多个计划任务实例重叠运行

- `scheduled_task_runner.cmd`
  - 给任务计划程序用的最小包装层
  - 避免把复杂的 PowerShell 参数直接塞给 `schtasks /TR`

- `install_scheduled_task.ps1`
  - 安装计划任务
  - 默认创建“固定间隔重复执行”的任务，并在安装后立即补跑一次

- `uninstall_scheduled_task.ps1`
  - 删除计划任务

- `.gitignore`
  - 避免把状态文件、日志、缓存类文件误提交到公开仓库

## 6. 主脚本工作流程

`clash_auto_merge.py` 的流程如下：

1. 自动定位 `Clash Verge` 配置目录
2. 读取 `profiles.yaml`
3. 找出全部 `type: remote` 的订阅
4. 优先尝试访问订阅 URL 获取最新配置
5. 如果远程拉取失败，则回退到本地缓存 YAML
6. 抽取所有真实代理节点
7. 过滤掉“剩余流量”“套餐到期”“提示”“联系我们”等说明性假节点
8. 给节点名前加订阅来源前缀，避免不同订阅的同名节点冲突
9. 按地区规则把节点分成：
   - 允许自动选路的节点
   - 被拦截地区节点
10. 生成新的 Mihomo 配置
11. 通过 Mihomo 控制端口热重载这份配置
12. 把 `GLOBAL` 切换到 `AI_AUTO`
13. 读取 Mihomo 健康检查结果
14. 写入状态文件

## 7. 代码结构说明

为了便于阅读和二次开发，下面列出几个关键函数的职责：

- `detect_verge_dir()`
  - 自动识别 `Clash Verge` 的工作目录

- `fetch_profile_snapshot()`
  - 拉远程订阅
  - 拉取失败时回退到本地缓存

- `collect_remote_profiles()`
  - 汇总全部订阅节点
  - 去重
  - 过滤说明性假节点

- `proxy_search_blob()`
  - 把节点名、服务器、SNI、Host 等信息拼成一个可搜索文本

- `is_blocked_region_proxy()`
  - 根据节点综合信息判断是不是被拦截地区

- `build_config()`
  - 生成最终配置和策略组

- `ControllerClient`
  - 负责访问 Mihomo 本地控制端口

- `apply_generated_config()`
  - 热重载生成后的配置

- `group_health()`
  - 读取 Mihomo 的策略组健康信息
  - 判断自动组里是否仍存在活节点

- `redact_url()`
  - 对状态文件中的订阅地址做脱敏处理，避免直接写出 token

## 8. 自动策略组说明

脚本会生成以下策略组：

- `AI_AUTO`
  - 类型：`url-test`
  - 作用：只在同时通过 `OpenAI + Medium` 探测的节点中自动选择当前最快可用节点

- `AI_STABLE`
  - 类型：`fallback`
  - 作用：作为同一批双可用节点的稳定兜底组

- `AI_ALLOWED`
  - 类型：`select`
  - 作用：把 `AI_AUTO`、`AI_STABLE` 和所有允许节点集中到一个组里，方便手工切换

- `BLOCKED_REGIONS`
  - 类型：`select`
  - 作用：单独保留被排除的地区节点，默认不参与 AI 自动选路

- `ALL_NODES`
  - 类型：`select`
  - 作用：显示全部节点，便于手工调试

- `GLOBAL`
  - 类型：`select`
  - 作用：规则未命中时的默认出口组，脚本会在执行后将其切换到 `AI_AUTO`

## 9. 如何手工执行

最常用方式：

```bat
run_clash_auto_merge.cmd
```

或者：

```bat
python clash_auto_merge.py
```

常用参数如下：

只使用本地缓存：

```bat
run_clash_auto_merge.cmd --offline
```

只生成配置，不热重载：

```bat
run_clash_auto_merge.cmd --generate-only
```

失败时不弹窗：

```bat
run_clash_auto_merge.cmd --no-popup
```

自定义超时：

```bat
run_clash_auto_merge.cmd --timeout 20
```

## 10. 如何安装自动定时执行

### 10.1 默认安装方式

默认每 10 分钟执行一次，并且安装完成后立即执行一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1
```

### 10.2 自定义执行间隔

例如改成每 20 分钟执行一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -Minutes 20
```

### 10.3 只使用本地缓存的计划任务

```powershell
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -Offline
```

### 10.4 删除计划任务

如果你之前按默认 10 分钟安装过：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_scheduled_task.ps1
```

如果你之前安装的是 20 分钟版本：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_scheduled_task.ps1 -Minutes 20
```

## 11. 计划任务的行为说明

安装后会创建一个任务：

- `ClashAutoMerge-Every10Min`
  - 固定间隔重复执行
  - 安装脚本在创建完成后会主动触发一次立即执行

后台执行时调用的是 `scheduled_run.ps1`。

这个脚本额外做了两件事：

- 记录日志到 `logs/`
- 使用命名互斥锁避免重叠执行

也就是说，即使计划任务触发得比较密，前一次还没跑完时，新的实例也会自动跳过，不会并发打架。

## 12. 输出结果与状态文件

每次执行后会生成或更新状态文件：

- `codex_auto_merge_status.json`

状态文件包含：

- 执行时间
- 合并后总节点数
- 允许节点数
- 被地区规则排除的节点数
- 每个订阅来源的处理结果
- 当前自动组选中的节点
- 自动组的活节点数量

注意：

- 状态文件中的订阅 URL 已做脱敏处理
- 但状态文件仍然属于本地运行产物，不建议提交到公开仓库

## 13. 日志文件

计划任务日志会写入：

- `logs/scheduled-task-YYYYMMDD.log`

用途：

- 看计划任务是否真的跑了
- 看执行时用了哪个 Python
- 看脚本退出码
- 出错时看具体异常

## 14. 失败时如何判断原因

脚本优先读取 Mihomo 的探测结果。

大致判断逻辑如下：

- 自动组里还有活节点
  - 说明允许范围内还有可用节点

- 自动组里没有活节点，且本机直连网络也失败
  - 更像是本机没网、DNS 问题、路由器问题、上游网络问题

- 自动组里没有活节点，但本机直连正常
  - 更像是当前允许范围内的节点整体失效，或订阅质量有问题

## 15. 仍然存在的局限

当前版本有几个边界：

- 默认地区过滤规则目前是写在代码里的
- 没有做图形界面
- 没有做跨平台代理层适配
- 依赖 `Clash Verge` / `Mihomo` 的本地目录结构和控制端口

如果后续要继续公开化，比较合适的增强方向是：

1. 把地区过滤规则做成独立配置文件
2. 增加命令行参数支持自定义探测地址和探测间隔
3. 增加导出示例配置
4. 增加更标准的 CLI 包装

## 16. 最简使用建议

如果你只想先用起来：

手工执行：

```bat
run_clash_auto_merge.cmd
```

安装计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1
```

这样之后会形成两层自动化：

- Mihomo 在当前已加载节点里持续自动测延迟并切换
- 计划任务定时刷新订阅、重新合并、重新热重载

这两层叠加后，手工干预会显著减少。
