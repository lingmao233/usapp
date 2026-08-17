#!/usr/bin/env bash
# 「我们」一键部署脚本：装 Docker → 配 .env → docker compose up -d --build → 健康检查。
# 幂等：任何一步中断后重跑都能安全继续。
#
# 用法：
#   bash deploy/setup.sh           # 交互式（会询问 API key）
#   bash deploy/setup.sh --yes     # 非交互（跳过询问，直接用现有 .env）
#   curl -fsSL <地址> | bash       # 管道执行自动进入非交互模式
set -euo pipefail

# ---------- 输出辅助 ----------
c_green() { printf '\033[32m%s\033[0m\n' "$1"; }
c_yellow() { printf '\033[33m%s\033[0m\n' "$1"; }
c_red() { printf '\033[31m%s\033[0m\n' "$1"; }
info() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
ok() { c_green "  ✔ $1"; }
warn() { c_yellow "  ⚠ $1"; }
err() { c_red "  ✘ $1"; }

ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=1 ;;
    *) err "未知参数：$arg"; echo "用法：bash deploy/setup.sh [--yes]"; exit 2 ;;
  esac
done

# 非交互环境（stdin 不是终端，如 curl | bash）自动跳过询问
if [ ! -t 0 ]; then
  ASSUME_YES=1
fi

# sudo 包装：root 直接执行，否则走 sudo
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

# docker 命令包装：用户可能还没重新登录获得 docker 组权限，必要时用 sudo docker
DOCKER="docker"

# ---------- 步骤 1：检测 OS ----------
check_os() {
  info "第 1 步：检测操作系统"
  if [ ! -f /etc/os-release ]; then
    err "无法识别操作系统（缺少 /etc/os-release）。"
    echo "  本脚本支持 Ubuntu/Debian 系；其他系统请手动安装 Docker 后运行：docker compose up -d --build"
    exit 1
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  if ! command -v apt-get >/dev/null 2>&1; then
    err "未找到 apt-get，当前系统（${NAME:-未知}）不在支持范围内。"
    echo "  支持：Ubuntu 20.04+ / Debian 11+。其他发行版请参照 docs/部署指南.md 手动安装 Docker。"
    exit 1
  fi
  case "${ID:-}" in
    ubuntu|debian) ok "${PRETTY_NAME:-$ID}，支持" ;;
    *)
      warn "${PRETTY_NAME:-$ID} 是 Debian 系衍生版，尝试继续；失败请参照 docs/部署指南.md 手动安装"
      ;;
  esac
  if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null 2>&1; then
    err "当前不是 root 且没有 sudo，请用 root 执行本脚本。"
    exit 1
  fi
}

# ---------- 步骤 2：安装/检测 Docker ----------
install_docker() {
  info "第 2 步：检测 Docker"
  if command -v docker >/dev/null 2>&1; then
    ok "Docker 已安装（$(docker --version 2>/dev/null | head -1)），跳过安装"
  else
    echo "  正在用官方脚本安装 Docker（约 1-2 分钟）…"
    curl -fsSL https://get.docker.com | $SUDO bash
    $SUDO systemctl enable --now docker
    ok "Docker 安装完成"
  fi

  # 当前用户 docker 组权限
  if [ "$(id -u)" -ne 0 ]; then
    if docker ps >/dev/null 2>&1; then
      ok "当前用户已可直接使用 docker"
    else
      echo "  正在把当前用户加入 docker 组…"
      $SUDO usermod -aG docker "$(whoami)" || true
      if docker ps >/dev/null 2>&1; then
        ok "已可直接使用 docker"
      else
        DOCKER="sudo docker"
        warn "docker 组权限要重新登录才生效，本次部署改用 sudo docker 继续。"
        echo "      下次登录后可免 sudo 使用 docker（或执行 newgrp docker）。"
      fi
    fi
  fi
}

# ---------- 步骤 3：检测 compose 插件 ----------
check_compose() {
  info "第 3 步：检测 docker compose 插件"
  if $DOCKER compose version >/dev/null 2>&1; then
    ok "docker compose 可用（$($DOCKER compose version --short 2>/dev/null || echo unknown)）"
  else
    echo "  正在安装 docker-compose-plugin…"
    $SUDO apt-get update -qq
    $SUDO apt-get install -y docker-compose-plugin
    $DOCKER compose version >/dev/null 2>&1 || { err "compose 插件安装失败，请手动执行：apt-get install -y docker-compose-plugin"; exit 1; }
    ok "docker compose 安装完成"
  fi
}

# ---------- 步骤 4：配置 .env ----------
# 把 KEY=value 写进 .env（存在则替换，不存在则追加）
env_set() {
  local key="$1" value="$2" file="$3"
  if grep -q "^${key}=" "$file"; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$file" && rm -f "$file.bak"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

env_get() {
  local key="$1" file="$2"
  grep "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2-
}

mask() {
  local v="$1"
  if [ -z "$v" ]; then echo "（空）"; else echo "（已填，尾号 ${v: -4}）"; fi
}

configure_env() {
  local root="$1"
  local env_file="$root/.env"
  info "第 4 步：配置 .env"

  if [ -f "$env_file" ]; then
    ok ".env 已存在，不覆盖"
  else
    cp "$root/.env.example" "$env_file"
    ok "已从 .env.example 复制生成 .env"
  fi

  local llm_key
  llm_key="$(env_get LLM_API_KEY "$env_file")"

  if [ "$ASSUME_YES" -eq 1 ]; then
    ok "非交互模式，跳过询问，直接使用现有 .env"
  else
    echo ""
    echo "  接下来询问 API key，直接回车保留当前值（留空则 AI 走 mock 本地桩）。"
    echo "  三组参数（LLM/EMBEDDING/VISION）同厂商时只需填 LLM_API_KEY，其余自动回退。"
    local input
    printf '  LLM_API_KEY 当前 %s，新值：' "$(mask "$llm_key")"
    read -r input
    [ -n "$input" ] && { env_set LLM_API_KEY "$input" "$env_file"; llm_key="$input"; }
    ok ".env 配置完成"
  fi

  if [ -z "$llm_key" ]; then
    warn "LLM_API_KEY 未填写，AI 能力将运行于 mock 模式（本地桩，功能完整但智能程度有限）。"
    echo "      - 分类/摘要/周报/方案走 mock，embedding 走 mock（语义检索精度下降）"
    echo "      之后随时编辑 $env_file 补填，再重跑本脚本即可生效。"
  else
    ok "LLM_API_KEY 已配置"
  fi
}

# ---------- 步骤 5：构建并启动 ----------
compose_up() {
  local root="$1"
  info "第 5 步：构建并启动（首次约 3-5 分钟）"
  (cd "$root" && $DOCKER compose up -d --build)
  ok "容器已启动"
}

# ---------- 步骤 6：健康检查 ----------
health_check() {
  local root="$1"
  info "第 6 步：健康检查（最多等 60 秒）"
  local i
  for i in $(seq 1 30); do
    if curl -fsS -m 3 http://localhost:8000/api/health >/dev/null 2>&1; then
      ok "服务已就绪"
      echo ""
      local ip
      ip="$(curl -fsS -m 5 https://ifconfig.me 2>/dev/null || true)"
      if [ -n "$ip" ]; then
        c_green "  🎉 部署成功！访问地址：http://${ip}:8000"
      else
        c_green "  🎉 部署成功！"
        echo "  获取公网 IP 失败，请在云控制台查看服务器公网 IP，访问 http://<IP>:8000"
      fi
      echo ""
      warn "别忘了在云服务器控制台的「安全组/防火墙」放行 TCP 8000 端口，否则外网打不开。"
      echo "  配域名 + HTTPS、每日备份等见 docs/部署指南.md 第 5/7 节。"
      return 0
    fi
    sleep 2
  done
  err "健康检查超时，服务未正常起来。最近 50 行日志："
  echo "--------------------------------------------------"
  (cd "$root" && $DOCKER compose logs --tail 50 2>&1) || true
  echo "--------------------------------------------------"
  echo "排查建议："
  echo "  1. 上方日志里找 ERROR/Traceback"
  echo "  2. 磁盘是否满了：df -h"
  echo "  3. 8000 端口是否被占用：ss -tlnp | grep 8000"
  echo "  4. 修复后重跑本脚本即可（幂等）：bash deploy/setup.sh"
  exit 1
}

main() {
  local script_dir root
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  root="$(dirname "$script_dir")"

  echo "=================================================="
  echo "  「我们」一键部署"
  echo "  项目目录：$root"
  echo "=================================================="

  check_os
  install_docker
  check_compose
  configure_env "$root"
  compose_up "$root"
  health_check "$root"
}

# 允许测试时 source 本脚本单独调用函数
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
