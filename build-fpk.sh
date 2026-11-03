#!/bin/bash
# 构建飞牛 fnOS 应用包（.fpk）
#
# 必须在 Linux x86_64 上执行（fnOS NAS 本机即可，需要 python3 + pip + fnpack）：
#   ssh 到 NAS，把整个项目目录拷过去后运行：
#     cd weather-notify && ./build-fpk.sh [版本号]
#
# 做四件事：
#   1. 复制 fpk/ 骨架（manifest、cmd/、图标、ui/config）到临时构建目录
#   2. 复制 src/ 与 web/ 进 app/（对应安装后的 target/）
#   3. pip install --target app/libs 把 Python 依赖随包内置
#      （要求构建机与目标机的 CPU 架构、glibc、Python 版本一致，NAS 本机构建天然满足）
#   4. 调 fnpack build 产出 dist/*.fpk
#
# 可选：SKIP_PACK=1 只组装不调用 fnpack（本机无 fnpack 时检查产物结构用）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
FPK_SRC="${ROOT}/fpk"
DIST="${ROOT}/dist"
BUILD="${DIST}/stage"
APPNAME="weathernotify"

manifest_version() {
    awk -F'=' '/^version[[:space:]]*=/{gsub(/[[:space:]]/, "", $2); print $2; exit}' \
        "${FPK_SRC}/manifest"
}

VER="${1:-$(manifest_version)}"
[ -n "${VER}" ] || { echo "错误：manifest 里没有 version，也没在命令行指定" >&2; exit 1; }

if [ ! -x "$(command -v fnpack || true)" ] && [ -z "${SKIP_PACK:-}" ]; then
    echo "错误：找不到 fnpack。请在 fnOS NAS 上构建（/usr/local/bin/fnpack），" >&2
    echo "      或加 SKIP_PACK=1 只组装目录结构。" >&2
    exit 1
fi

# ---------- 组装构建目录 ----------
rm -rf "${BUILD}"
mkdir -p "${BUILD}"
cp -R "${FPK_SRC}" "${BUILD}/${APPNAME}"
APP="${BUILD}/${APPNAME}/app"
mkdir -p "${APP}"

# 源码与页面
cp -R "${ROOT}/src" "${APP}/src"
cp -R "${ROOT}/web" "${APP}/web"
find "${APP}" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "${APP}" -name "*.pyc" -delete 2>/dev/null || true
find "${APP}" -name "*.bak*" -delete 2>/dev/null || true

# Python 依赖随包内置（构建机 = 目标机时才安全，见文件头注释）
echo "== 安装依赖到 app/libs =="
python3 -m pip install --target "${APP}/libs" --no-compile --quiet \
    -r "${ROOT}/requirements.txt"
find "${APP}/libs" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

# 指定版本号时同步写回 manifest（参数缺省时直接用 manifest 现有值）
if [ -n "${1:-}" ]; then
    sed -i "s/^version[[:space:]]*=.*/version               = ${VER}/" \
        "${BUILD}/${APPNAME}/manifest"
fi

# 语法自检：任何 py 文件编译不过都别出包
echo "== 语法自检 =="
find "${APP}/src" -name "*.py" -print0 | xargs -0 -n 20 python3 -m py_compile -q

# ---------- 打包 ----------
if [ -n "${SKIP_PACK:-}" ]; then
    echo "== SKIP_PACK=1：跳过 fnpack，产物在 ${BUILD}/${APPNAME} =="
    exit 0
fi

echo "== fnpack build =="
( cd "${BUILD}/${APPNAME}" && fnpack build -d . )

mkdir -p "${DIST}"
fpk_file=$(ls -1t "${BUILD}/${APPNAME}"/*.fpk 2>/dev/null | head -n 1 || true)
[ -n "${fpk_file}" ] || { echo "错误：fnpack 没有产出 .fpk" >&2; exit 1; }
dest="${DIST}/${APPNAME}_${VER}.fpk"
mv "${fpk_file}" "${dest}"

echo
echo "构建完成：${dest}"
echo "安装：应用中心 → 设置 → 手动安装，选择该 fpk（安装前先停掉占用 ${PORT:-18770} 端口的旧进程）"
