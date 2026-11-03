# 打包为飞牛 fnOS 应用（.fpk）

把本项目打成 fnOS 应用中心可安装的 fpk 包，装完在桌面有图标、应用中心能启停/升级/卸载，
配置和日志落在应用数据目录，升级、卸载重装都不丢。

## 前置条件

| 条件 | 说明 |
|---|---|
| Linux x86_64 构建机 | 直接用 fnOS NAS 本机最省事（`/usr/local/bin/fnpack` 系统自带） |
| `python3` + `pip` | fnOS 自带 3.11；依赖 wheel 按**目标架构**与 Python 版本解析，**换 Python 大版本必须重新打包** |
| `fnpack` | 飞牛官方打包工具，NAS 上已预装；下载见 <https://developer.fnnas.com/docs/cli/fnpack> |

x86_64 与 ARM64（aarch64）都可以出包，**构建机本身是 x86 也能出 ARM 包**：
ARM 包的依赖用 `pip --only-binary --platform manylinux*_aarch64` 交叉下载 wheel，
只下载解压不编译，所以不需要 ARM 机器或 qemu。但正因如此，**ARM 包无法在 x86 机器上实测运行**，
只能做架构静态校验（`file *.so` 看是否 aarch64）。

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
- **端口**：manifest `service_port=18770`，同时 `checkport=false`（关掉系统的端口检查），
  改由 `cmd/install_init` 自己查：端口空闲或占用者就是本应用的旧进程（覆盖安装/升级）时放行，
  被**其他**程序占用时写 `$TRIM_TEMP_LOGFILE` 并 `exit 1` 中止安装。
  之所以不用 `checkport=true`：系统级检查分不清“被别的程序占着”和“被本应用旧版本占着”，
  后者在升级时是正常状态，却会一律拦下，逼用户先手动停应用。
- **启动参数**：`--host 0.0.0.0 --port $TRIM_SERVICE_PORT`，命令行参数优先于配置文件。
- **进程身份**：`run-as: package`，以 `weathernotify` 专属用户运行，无需 root。

## 构建

```bash
# 在 NAS 上（项目目录随便放，下面以沙箱目录为例）
cd /vol2/1000/agent沙箱/weather-notify
./build-fpk.sh                 # 用 manifest 里的 version，出 x86 包
./build-fpk.sh 0.1.1           # 指定版本号（会写回 fpk/manifest）
./build-fpk.sh --arch arm      # 出 ARM64 包
./build-fpk.sh --arch both     # 两个架构都出
```

脚本做的事：复制 `fpk/` 骨架 → 复制 `src/`、`web/` 并清理缓存 →
按目标架构装依赖到 `app/libs`（ARM 走交叉下载）→ 校验 `.so` 架构 →
`python3 -m py_compile` 语法自检 → 改 manifest 的 `version` 与 `platform` → `fnpack build`。

产物：

| 架构 | 文件名 | manifest |
|---|---|---|
| x86_64 | `dist/weathernotify_<版本>.fpk` | `platform = x86` |
| ARM64 | `dist/weathernotify_<版本>_arm.fpk` | `platform = arm` |

两个包各自约 5.7–5.8 MB，`stage-x86/`、`stage-arm/` 是中间目录，可随时删。

为什么不能出一个包通吃两个架构：fnOS 的 `platform` 字段不接受多值（写多个会装不上），
而包里又带着架构相关的二进制（`cryptography` 的 `_rust.abi3.so`、`cffi`、
`charset_normalizer` 的 cp311 扩展），所以必须按架构分开出包。
`platform = all` 只适用于纯脚本 / Docker 型应用。

Mac 上想检查组装结果可以 `SKIP_PACK=1 ./build-fpk.sh`（不调用 fnpack，
注意此时 libs 里是 macOS 的二进制，仅看结构用）。

## 安装 / 升级 / 卸载

- **安装**：应用中心 → 设置 → 手动安装 → 选 fpk。**先看设备架构**：
  Intel/AMD 的 NAS 用 `weathernotify_<版本>.fpk`，RK3588/树莓派等 ARM 设备用
  `weathernotify_<版本>_arm.fpk`（选错会被应用中心以架构不符拒绝）。
  只有端口被**其他程序**占着时安装才会中止（提示里会打出占用进程）。
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

- `libs/` 内置依赖绑定了 CPython 3.11（fnOS 当前系统版本）与目标 CPU 架构；
  fnOS 未来换 Python 大版本时需要重新打包（改 `PY_TAG` 或改用 venv 方案）。
- ARM 包只能在 ARM 设备上验证，x86 构建机只做了架构静态校验。
- 桌面入口用 `type=url` 直连 `IP:18770`，从 fnOS 的 HTTPS 域名访问时会另开 HTTP 页签；
  若想全走网关反代，需要改成 `iframe` + `gatewaySocket`（参考 FnMessageBot 的做法）。
- 应用中心上架需另行走飞牛开发者流程，本包按「手动安装第三方应用」设计。
