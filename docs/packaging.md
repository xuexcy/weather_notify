# 打包为飞牛 fnOS 应用（.fpk）

把本项目打成 fnOS 应用中心可安装的 fpk 包，装完在桌面有图标、应用中心能启停/升级/卸载，
配置和日志落在应用数据目录，升级、卸载重装都不丢。

## 前置条件

| 条件 | 说明 |
|---|---|
| Linux x86_64 构建机 | 直接用 fnOS NAS 本机最省事（`/usr/local/bin/fnpack` 系统自带） |
| `python3` + `pip` | fnOS 自带 3.11；依赖 wheel 按构建机的架构/Python 版本解析，**换 CPU 架构或 Python 大版本必须重新打包** |
| `fnpack` | 飞牛官方打包工具，NAS 上已预装；下载见 <https://developer.fnnas.com/docs/cli/fnpack> |

## 包目录结构

```
fpk/
├── manifest              # 应用身份（appname=weathernotify、service_port=18770…）
├── ICON.PNG / ICON_256.PNG   # 应用中心图标（64 / 256）
├── assets/icon.svg       # 图标源文件（rsvg-convert 渲染成上面的 PNG）
├── cmd/                  # 生命周期脚本（fnOS 调用，见下表）
│   ├── main              # start / stop / status（status：0=运行中，3=未运行）
│   ├── install_init / install_callback
│   ├── upgrade_init / upgrade_callback
│   ├── uninstall_init / uninstall_callback
│   └── config_init / config_callback
├── config/
│   ├── privilege         # {"defaults":{"run-as":"package"},…} 以应用专属用户运行
│   └── resource          # {} 无额外资源声明（fnpack 要求文件存在）
└── app/                  # 对应安装后的 target/（只读目录）
    ├── src/ web/         # 构建时从项目根复制进来（build-fpk.sh 做）
    ├── libs/             # 构建时 pip install --target 进来的第三方依赖
    └── ui/
        ├── config        # 桌面入口（type=url，指向 http://<NAS_IP>:18770）
        └── images/       # icon-64.png / icon-256.png
```

关键约定：

- **数据目录**：`cmd/main` 给服务设置 `WEATHER_NOTIFY_DATA_DIR=$TRIM_PKGVAR`，
  于是 `conf/`（config.json、Ed25519 私钥、状态文件）和 `logs/` 都落在
  `/vol*/@appdata/weathernotify/`——这个目录**升级和卸载都会保留**。
- **端口**：manifest `service_port=18770` + `checkport=true`，安装时 fnOS 会检查占用。
- **启动参数**：`--host 0.0.0.0 --port $TRIM_SERVICE_PORT`，命令行参数优先于配置文件。
- **进程身份**：`run-as: package`，以 `weathernotify` 专属用户运行，无需 root。

## 构建

```bash
# 在 NAS 上（项目目录随便放，下面以沙箱目录为例）
cd /vol2/1000/agent沙箱/weather-notify
./build-fpk.sh              # 用 fpk/manifest 里的 version
./build-fpk.sh 1.0.1        # 或显式指定版本号（会写回 manifest）
```

脚本做的事：复制 `fpk/` 骨架 → 复制 `src/`、`web/` 并清理缓存 →
`pip install --target app/libs` 内置依赖 → `python3 -m py_compile` 语法自检 →
`fnpack build` 出包。产物在 `dist/weathernotify_<版本>.fpk`（约 5–6 MB）。

Mac 上想检查组装结果可以 `SKIP_PACK=1 ./build-fpk.sh`（不调用 fnpack，
注意此时 libs 里是 macOS 的二进制，仅看结构用）。

## 安装 / 升级 / 卸载

- **安装**：应用中心 → 设置 → 手动安装 → 选 fpk。安装前先停掉手动部署的旧进程
  （占用 18770 会因 `checkport` 装不进去）。
- **升级**：应用中心里直接更新（或再手动装一次高版本 fpk）。流程是
  `upgrade_init`（停进程）→ 替换 target → `upgrade_callback`（修数据目录权限），
  `conf/` 原地保留。
- **卸载**：`uninstall_init` 先停进程；`@appdata/weathernotify` 保留，重装后配置还在。
  彻底清空需手动删该数据目录。

装完桌面会出现「天气提醒」图标（所有用户可见，设置页仍有访问密码），
首次打开和手动部署一样：没有配置就自动生成模板，`/settings` 先设访问密码。

## 日常排查

| 看什么 | 位置 |
|---|---|
| 服务标准输出 / 启停记录 | `@appdata/weathernotify/logs/service.log` |
| 应用运行日志（推荐） | `@appdata/weathernotify/logs/weather-notify.log` |
| 手动启停 | `/var/apps/weathernotify/cmd/main start|stop|status` |
| 安装失败弹窗内容 | 即 `cmd` 脚本写入 `$TRIM_TEMP_LOGFILE` 的文本 |

## 已知取舍

- `libs/` 内置依赖绑定了 CPython 3.11（fnOS 当前系统版本）；fnOS 未来换 Python
  大版本时需要重新打包，或届时改用 venv 方案。
- 桌面入口用 `type=url` 直连 `IP:18770`，从 fnOS 的 HTTPS 域名访问时会另开 HTTP 页签；
  若想全走网关反代，需要改成 `iframe` + `gatewaySocket`（参考 FnMessageBot 的做法）。
- 应用中心上架需另行走飞牛开发者流程，本包按「手动安装第三方应用」设计。
