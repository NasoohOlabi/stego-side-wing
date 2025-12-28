# 1. make a dated folder
BACKUP_DIR="$HOME/n8n-backup/$(date +%F)"
mkdir -p "$BACKUP_DIR"

# 2. export everything
n8n export:workflow --backup --output="$BACKUP_DIR/workflows"
n8n export:credentials --backup --decrypted --output="$BACKUP_DIR/credentials"