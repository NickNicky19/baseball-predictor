#!/usr/bin/env bash
set -euo pipefail

# Install only a reviewed exact comparator commit. Existing pitcher and shared
# PA collectors are read-only dependencies and are never restarted or edited.

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
release_root="/opt/baseball-predictor-shared-pa-comparators"
release="$release_root/releases/$commit"
venv="$release_root/venvs/$commit"
current="$release_root/current"
current_venv="$release_root/current-venv"
evidence_root="/srv/baseball-shadow/shared-pa-comparators"
pitcher_root="/srv/baseball-shadow/pitcher-receipts"
shared_pa_root="/srv/baseball-shadow/shared-pa-forward/ledgers"
deploy_key="/srv/baseball-shadow/.ssh/id_github_repo"
known_hosts="/srv/baseball-shadow/.ssh/known_hosts_github_comparators"
github_host_key="github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"

id "$service_user" >/dev/null 2>&1 || { echo "Missing $service_user account" >&2; exit 2; }
test -r "$pitcher_root/plans" || { echo "Pitcher plan root unavailable" >&2; exit 2; }
test -r "$pitcher_root/plan-receipts" || { echo "Pitcher plan receipt root unavailable" >&2; exit 2; }
test -r "$pitcher_root/ledgers" || { echo "Pitcher ledger root unavailable" >&2; exit 2; }
test -r "$shared_pa_root" || { echo "Shared PA ledger root unavailable" >&2; exit 2; }
test -f "$deploy_key" || { echo "Missing read-only GitHub deploy key" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 unavailable" >&2; exit 2; }

install -d -o "$service_user" -g "$service_group" -m 0750 \
  "$release_root" "$release_root/releases" "$release_root/venvs"
install -d -o "$service_user" -g "$service_group" -m 0750 \
  "$evidence_root" "$evidence_root/pregame" "$evidence_root/records" \
  "$evidence_root/runtime" "$evidence_root/locks" "$evidence_root/health"
install -d -o "$service_user" -g "$service_group" -m 0700 "$evidence_root/secrets"
if [[ ! -e "$evidence_root/secrets/provider.env" ]]; then
  install -o "$service_user" -g "$service_group" -m 0600 /dev/null "$evidence_root/secrets/provider.env"
fi

printf '%s\n' "$github_host_key" > "$known_hosts"
chown "$service_user:$service_group" "$known_hosts"
chmod 0600 "$known_hosts"
ssh_command="ssh -i $deploy_key -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts"

if [[ ! -e "$release" ]]; then
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
  mv "$temporary/repo" "$release"
  trap - EXIT
  rmdir "$temporary"
fi
[[ "$(sudo -u "$service_user" git -C "$release" rev-parse HEAD)" = "$commit" ]] || { echo "Release commit differs" >&2; exit 2; }
test -z "$(sudo -u "$service_user" git -C "$release" status --porcelain --untracked-files=all)" || { echo "Release is dirty" >&2; exit 2; }

if [[ ! -x "$venv/bin/python" ]]; then
  temporary_venv="$(mktemp -d "$release_root/venvs/.install-$commit-XXXXXX")"
  rmdir "$temporary_venv"
  sudo -u "$service_user" python3 -m venv "$temporary_venv"
  sudo -u "$service_user" "$temporary_venv/bin/python" -m pip install --require-hashes -r "$release/requirements-aws-comparator.lock"
  sudo -u "$service_user" "$temporary_venv/bin/python" -m pip check
  mv "$temporary_venv" "$venv"
fi

sudo -u "$service_user" "$venv/bin/python" "$release/scripts/check_shared_pa_comparator_release_manifest.py"
sudo -u "$service_user" "$venv/bin/python" "$release/scripts/check_aws_shared_pa_comparator_offline.py"
sudo -u "$service_user" "$venv/bin/python" "$release/scripts/check_tracked_secrets.py"

for link in "$current" "$current_venv"; do
  [[ ! -e "$link" || -L "$link" ]] || { echo "Current path is not a symlink: $link" >&2; exit 2; }
done
next_release="$release_root/.current-$commit"
next_venv="$release_root/.current-venv-$commit"
ln -s "$release" "$next_release"
ln -s "$venv" "$next_venv"
mv -Tf "$next_release" "$current"
mv -Tf "$next_venv" "$current_venv"

for unit in \
  baseball-shared-pa-comparator-predictions.service baseball-shared-pa-comparator-predictions.timer \
  baseball-shared-pa-comparator-markets.service baseball-shared-pa-comparator-markets.timer \
  baseball-shared-pa-comparator-finalize.service baseball-shared-pa-comparator-finalize.timer \
  baseball-shared-pa-comparator-health.service baseball-shared-pa-comparator-health.timer
do
  install -o root -g root -m 0644 "$release/deploy/shared_pa_comparators/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now \
  baseball-shared-pa-comparator-predictions.timer \
  baseball-shared-pa-comparator-markets.timer \
  baseball-shared-pa-comparator-finalize.timer \
  baseball-shared-pa-comparator-health.timer
systemctl --no-pager --full status \
  baseball-shared-pa-comparator-predictions.timer \
  baseball-shared-pa-comparator-markets.timer \
  baseball-shared-pa-comparator-finalize.timer \
  baseball-shared-pa-comparator-health.timer

echo "Installed exact isolated research-only comparator release $commit"
echo "ODDS_API_KEY configured: $(grep -q '^ODDS_API_KEY=.' "$evidence_root/secrets/provider.env" && echo yes || echo no)"
