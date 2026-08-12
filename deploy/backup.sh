#!/usr/bin/env bash
# SQLite 备份：sqlite3 .backup 到带日期的文件，保留最近 14 份。
# 图片上传目录（uploads/）一并 tar 打包，同保留策略。
#
# Docker 部署（推荐）：直接对宿主机上的 volume 目录做备份
#   bash deploy/backup.sh /var/lib/docker/volumes/us-app_us-data/_data
# 也可以先找到 volume 实际路径：
#   docker volume inspect us-app_us-data --format '{{.Mountpoint}}'
#
# 非 Docker 部署：直接指向 server/data
#   bash deploy/backup.sh /opt/us-app/server/data
#
# 加入 crontab（每天凌晨 3:17 执行）：
#   crontab -e
#   17 3 * * * bash /opt/us-app/deploy/backup.sh /opt/us-app/server/data >> /var/log/us-backup.log 2>&1
set -euo pipefail

DATA_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../server/data" && pwd)}"
BACKUP_DIR="$DATA_DIR/backups"
KEEP=14

if [ ! -f "$DATA_DIR/app.db" ]; then
  echo "[backup] 未找到 $DATA_DIR/app.db，退出" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/app-$STAMP.db"

# .backup 是 SQLite 官方在线备份方式，运行中的库也能安全拷贝
sqlite3 "$DATA_DIR/app.db" ".backup '$DEST'"
echo "[backup] 已备份到 $DEST"

# 图片上传目录一并打包（与 sqlite 备份同保留策略）
if [ -d "$DATA_DIR/uploads" ]; then
  tar -czf "$BACKUP_DIR/uploads-$STAMP.tar.gz" -C "$DATA_DIR" uploads
  echo "[backup] 已打包 uploads 到 $BACKUP_DIR/uploads-$STAMP.tar.gz"
fi

# 只保留最近 KEEP 份
ls -1t "$BACKUP_DIR"/app-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
  echo "[backup] 已清理旧备份 $old"
done
ls -1t "$BACKUP_DIR"/uploads-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
  echo "[backup] 已清理旧备份 $old"
done
