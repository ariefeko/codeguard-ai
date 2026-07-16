#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot identify the operating system." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_CODENAME:-}" != "noble" ]]; then
  echo "This installer supports Ubuntu 24.04 (noble) only." >&2
  exit 1
fi

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT
expected_fingerprint="2596A99EAAB33821893C0A79458CA832957F5868"

curl -fsSLo "${work_dir}/tailscale-archive-keyring.gpg" \
  https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg
curl -fsSLo "${work_dir}/tailscale.list" \
  https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list

actual_fingerprint="$(
  gpg --show-keys --with-colons "${work_dir}/tailscale-archive-keyring.gpg" \
    | awk -F: '$1 == "fpr" { print $10; exit }'
)"

if [[ "${actual_fingerprint}" != "${expected_fingerprint}" ]]; then
  echo "Unexpected Tailscale signing key fingerprint: ${actual_fingerprint}" >&2
  exit 1
fi
echo "Verified Tailscale signing key: ${actual_fingerprint}"

sudo install -D -m 0644 \
  "${work_dir}/tailscale-archive-keyring.gpg" \
  /usr/share/keyrings/tailscale-archive-keyring.gpg
sudo install -D -m 0644 \
  "${work_dir}/tailscale.list" \
  /etc/apt/sources.list.d/tailscale.list
sudo apt-get update
sudo apt-get install -y tailscale
sudo systemctl enable --now tailscaled

tailscale version
echo
echo "Authenticate this host with your personal Tailscale account:"
sudo tailscale up
