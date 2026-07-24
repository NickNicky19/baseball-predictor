#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 2
fi
if [[ "$#" -ne 1 || ! "$1" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: sudo install_exact_release.sh <authorized-40-character-commit>" >&2
  exit 2
fi

commit="$1"
service_user="baseball-shadow"
service_group="baseball-shadow"
remote="git@github.com:NickNicky19/baseball-predictor.git"
release_root="/opt/baseball-predictor-projected-lineup-label"
release="$release_root/releases/$commit"
current="$release_root/current"
evidence_root="/srv/baseball-shadow/projected-lineup-label-receipts"
roster_root="/srv/baseball-shadow/projected-lineup-roster-receipts"
deploy_key="/srv/baseball-shadow/.ssh/id_github_repo"
known_hosts="/srv/baseball-shadow/.ssh/known_hosts_github"
host_key_source="deploy/forward_pitcher_receipts/github.com_known_hosts"
host_key_sha256="6233fddbb0a29afc8c4e8c699733c1a188c3a41f2fb63a2640653dc4aea624ce"

id "$service_user" >/dev/null 2>&1 || { echo "Missing $service_user account" >&2; exit 2; }
test -f "$deploy_key" || { echo "Missing read-only GitHub deploy key" >&2; exit 2; }
test "$(stat -c '%U' "$deploy_key")" = "$service_user" || { echo "Deploy key owner differs" >&2; exit 2; }
key_mode="$(stat -c '%a' "$deploy_key")"
[[ "$key_mode" = "600" || "$key_mode" = "400" ]] || { echo "Deploy key mode must be 600 or 400" >&2; exit 2; }

install -d -o "$service_user" -g "$service_group" -m 0750 "$release_root" "$release_root/releases"
install -d -o "$service_user" -g "$service_group" -m 0700 /srv/baseball-shadow/.ssh
install -d -o "$service_user" -g "$service_group" -m 0750 "$evidence_root"
install -d -o "$service_user" -g "$service_group" -m 0750 "$roster_root"

ssh_command="ssh -i $deploy_key -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts"
if [[ -e "$release" ]]; then
  test -d "$release/.git" || { echo "Existing release path is not a Git checkout" >&2; exit 2; }
  actual="$(sudo -u "$service_user" git -C "$release" rev-parse HEAD)"
  [[ "$actual" = "$commit" ]] || { echo "Existing release path has another commit" >&2; exit 2; }
  test -z "$(sudo -u "$service_user" git -C "$release" status --porcelain --untracked-files=all)" || { echo "Existing release is dirty" >&2; exit 2; }
else
  temporary="$(mktemp -d "$release_root/releases/.install-$commit-XXXXXX")"
  cleanup() {
    case "$temporary" in
      "$release_root"/releases/.install-*) rm -rf -- "$temporary" ;;
      *) echo "Refusing unsafe temporary cleanup" >&2 ;;
    esac
  }
  trap cleanup EXIT
  chown "$service_user:$service_group" "$temporary"
  sudo -u "$service_user" env GIT_SSH_COMMAND="$ssh_command" git clone --filter=blob:none --no-checkout "$remote" "$temporary/repo"
  sudo -u "$service_user" env GIT_SSH_COMMAND="$ssh_command" git -C "$temporary/repo" fetch --no-tags origin "$commit"
  sudo -u "$service_user" env GIT_SSH_COMMAND="$ssh_command" git -C "$temporary/repo" checkout --detach "$commit"
  [[ "$(sudo -u "$service_user" git -C "$temporary/repo" rev-parse HEAD)" = "$commit" ]] || { echo "Checkout differs from authorized commit" >&2; exit 2; }
  test -z "$(sudo -u "$service_user" git -C "$temporary/repo" status --porcelain --untracked-files=all)" || { echo "Fresh release is dirty" >&2; exit 2; }
  mv "$temporary/repo" "$release"
  trap - EXIT
  rmdir "$temporary"
fi

[[ "$(sha256sum "$release/$host_key_source" | awk '{print $1}')" = "$host_key_sha256" ]] || { echo "Release GitHub host-key pin differs" >&2; exit 2; }
install -o "$service_user" -g "$service_group" -m 0600 "$release/$host_key_source" "$known_hosts"
/usr/bin/python3 "$release/scripts/check_aws_projected_lineup_label_offline.py"
sudo -u "$service_user" /usr/bin/python3 "$release/scripts/check_tracked_secrets.py"

if [[ -e "$current" && ! -L "$current" ]]; then
  echo "Projected-lineup label current release path exists and is not a symlink" >&2
  exit 2
fi
next_link="$release_root/.current-$commit"
ln -s "$release" "$next_link"
mv -Tf "$next_link" "$current"

install -o root -g root -m 0644 "$release/deploy/projected_lineup_label/baseball-projected-lineup-label-tick.service" /etc/systemd/system/baseball-projected-lineup-label-tick.service
install -o root -g root -m 0644 "$release/deploy/projected_lineup_label/baseball-projected-lineup-label-tick.timer" /etc/systemd/system/baseball-projected-lineup-label-tick.timer
systemctl daemon-reload
systemctl enable --now baseball-projected-lineup-label-tick.timer
systemctl start baseball-projected-lineup-label-tick.service
systemctl --no-pager --full status baseball-projected-lineup-label-tick.timer baseball-projected-lineup-label-tick.service
echo "Installed exact research-only projected-lineup label release $commit"
