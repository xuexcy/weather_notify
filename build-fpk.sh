#!/bin/bash
# 构建飞牛 fnOS 应用包（.fpk），支持 x86_64 与 ARM64 双架构
#
# 用法（在 Linux 上执行，fnOS NAS 本机最省事，需要 python3 + pip + fnpack）：
#   ./build-fpk.sh                 # 用 manifest 里的版本，构建本机架构对应的包（默认 x86）
#   ./build-fpk.sh 0.1.1           # 指定版本号（同时写回 fpk/manifest）
#   ./build-fpk.sh --arch arm      # 构建 ARM64 包
#   ./build-fpk.sh --arch both     # 两个架构都出包
#
# 两个架构差在哪：
#   - manifest 的 platform 字段（x86 / arm，fnOS 不允许写多个值，所以必须分两个包）
#   - app/libs 里的第三方依赖：x86 包直接本机 pip 安装；ARM 包用 pip 交叉下载
#     aarch64 的 manylinux wheel（纯下载解压，不编译，所以在 x86 机器上也能出 ARM 包）
#
# 可选环境变量：
#   SKIP_PACK=1   只组装目录、不调用 fnpack（检查产物结构用）
#   PY_TAG=311    指定目标 CPython 版本（默认 311，需与 fnOS 系统 python3 一致）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
FPK_SRC="${ROOT}/fpk"
DIST="${ROOT}/dist"
APPNAME="weathernotify"
PY_TAG="${PY_TAG:-311}"

manifest_version() {
    awk -F'=' '/^version[[:space:]]*=/{gsub(/[[:space:]]/, "", $2); print $2; exit}' \
        "${FPK_SRC}/manifest"
}

# ---------- 参数 ----------
ARCH="x86"
VER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --arch)   ARCH="${2:-}"; shift 2 ;;
        --arch=*) ARCH="${1#*=}"; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *)        VER="$1"; shift ;;
    esac
done
case "${ARCH}" in
    x86|arm|both) ;;
    *) echo "错误：--arch 只能是 x86 / arm / both（收到 ${ARCH}）" >&2; exit 1 ;;
esac
[ -n "${VER}" ] || VER="$(manifest_version)"
[ -n "${VER}" ] || { echo "错误：manifest 里没有 version，也没在命令行指定" >&2; exit 1; }

if [ ! -x "$(command -v fnpack || true)" ] && [ -z "${SKIP_PACK:-}" ]; then
    echo "错误：找不到 fnpack。请在 fnOS NAS 上构建（/usr/local/bin/fnpack），" >&2
    echo "      或加 SKIP_PACK=1 只组装目录结构。" >&2
    exit 1
fi

# ---------- 依赖安装 ----------
# x86：本机 pip 安装（wheel 与构建机环境天然匹配）
# arm：交叉下载 aarch64 wheel；--only-binary 保证不会尝试编译
install_deps() {
    local libs="$1" arch="$2"
    if [ "${arch}" = "x86" ]; then
        python3 -m pip install --target "${libs}" --no-compile --quiet \
            -r "${ROOT}/requirements.txt"
    else
        python3 -m pip install --target "${libs}" --no-compile --quiet \
            --only-binary :all: \
            --platform manylinux2014_aarch64 \
            --platform manylinux_2_28_aarch64 \
            --platform any \
            --python-version "${PY_TAG}" --implementation cp --abi "cp${PY_TAG}" \
            -r "${ROOT}/requirements.txt"
    fi
    find "${libs}" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
}

check_deps_arch() {
    local libs="$1" arch="$2"
    local expect="x86-64"
    [ "${arch}" = "arm" ] && expect="ARM aarch64"
    local so
    so=$(find "${libs}" -name "*.so" | head -n 1)
    [ -n "${so}" ] || return 0
    if file "${so}" | grep -qi "${expect}"; then
        echo "   依赖架构校验通过（$(basename "${so}") → ${expect}）"
    else
        echo "   错误：${so} 不是 ${expect}，依赖架构不对" >&2
        file "${so}" >&2
        exit 1
    fi
}

# ---------- 构建单个架构 ----------
build_one() {
    local arch="$1" ver="$2"
    local platform suffix stage
    if [ "${arch}" = "x86" ]; then
        platform="x86"; suffix=""
    else
        platform="arm"; suffix="_arm"
    fi
    stage="${DIST}/stage-${arch}"
    local pkg="${stage}/${APPNAME}"
    local app="${pkg}/app"

    echo "== 组装 ${arch} 包（version=${ver}）=="
    rm -rf "${stage}"
    mkdir -p "${app}"
    cp -R "${FPK_SRC}/." "${pkg}/"

    cp -R "${ROOT}/src" "${app}/src"
    cp -R "${ROOT}/web" "${app}/web"
    find "${app}" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
    find "${app}" -name "*.pyc" -delete 2>/dev/null || true
    find "${app}" -name "*.bak*" -delete 2>/dev/null || true

    echo "== 安装依赖（${arch}）=="
    install_deps "${app}/libs" "${arch}"
    check_deps_arch "${app}/libs" "${arch}"

    # manifest：版本号与架构
    sed -i "s/^version[[:space:]]*=.*/version               = ${ver}/" "${pkg}/manifest"
    sed -i "s/^platform[[:space:]]*=.*/platform              = ${platform}/" "${pkg}/manifest"

    echo "== 语法自检 =="
    find "${app}/src" -name "*.py" -print0 | xargs -0 -n 20 python3 -m py_compile -q

    if [ -n "${SKIP_PACK:-}" ]; then
        echo "== SKIP_PACK=1：跳过 fnpack，目录在 ${pkg} =="
        return 0
    fi

    echo "== fnpack build（${arch}）=="
    ( cd "${pkg}" && fnpack build -d . )

    local fpk
    fpk=$(ls -1t "${pkg}"/*.fpk 2>/dev/null | head -n 1 || true)
    [ -n "${fpk}" ] || { echo "错误：fnpack 没有产出 .fpk（${arch}）" >&2; exit 1; }
    mkdir -p "${DIST}"
    local dest="${DIST}/${APPNAME}_${ver}${suffix}.fpk"
    mv "${fpk}" "${dest}"
    echo "--> ${dest}（$(du -h "${dest}" | cut -f1)）"
}

case "${ARCH}" in
    x86)  build_one x86 "${VER}" ;;
    arm)  build_one arm "${VER}" ;;
    both) build_one x86 "${VER}"; build_one arm "${VER}" ;;
esac

echo
echo "构建完成，产物在 ${DIST}/"
echo "安装：应用中心 → 设置 → 手动安装，选择对应架构的 fpk（覆盖安装/升级不必先停应用）"
