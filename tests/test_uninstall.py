"""Run the real Bash flow with all system mutations replaced by logging functions."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="Linux Bash")
SCRIPT = Path(__file__).resolve().parents[1] / "deploy/uninstall.sh"

# Only the root/systemd prerequisite is bypassed. Real path checks still run, while
# shell functions ensure no systemctl, rm, userdel or groupdel can touch the host.
HARNESS = r'''
source "$1"
INSTALL_DIR=$2
scenario=$3
require_environment() { :; }
findmnt() {
    printf '/\n'
    if [[ "$scenario" == mount ]]; then printf '%s/data\n' "$INSTALL_DIR"; fi
}
id() {
    if [[ "$scenario" == absent ]]; then return 1; fi
    case "${1:-}" in
        -u) printf '123\n' ;;
        -gn) printf 'tgbutler\n' ;;
    esac
}
getent() {
    if [[ "$scenario" == absent ]]; then return 2; fi
    if [[ "$1" == passwd ]]; then
        if [[ "$scenario" == wrong-home ]]; then
            printf 'tgbutler:x:123:123::/someone-else:/usr/sbin/nologin\n'
        else
            printf 'tgbutler:x:123:123::%s:/usr/sbin/nologin\n' "$INSTALL_DIR"
        fi
    fi
}
systemctl() {
    if [[ "$1" == show ]]; then
        if [[ "$scenario" == absent ]]; then printf 'not-found\n'; else printf 'loaded\n'; fi
    else
        printf 'CALL systemctl %s\n' "$*"
        if [[ "$1" == stop && "$scenario" == stop-fails ]]; then return 1; fi
    fi
}
rm() { printf 'CALL rm %s\n' "$*"; }
userdel() { printf 'CALL userdel %s\n' "$*"; }
groupdel() { printf 'CALL groupdel %s\n' "$*"; }
main
'''


def run_uninstall(tmp_path, confirmation="DELETE\n", scenario="normal", *, symlink=False):
    directory = tmp_path / "install"
    directory.mkdir()
    target = directory
    if symlink:
        target = tmp_path / "linked"
        target.symlink_to(directory, target_is_directory=True)
    result = subprocess.run(
        ["bash", "-c", HARNESS, "uninstall-test", str(SCRIPT), str(target), scenario],
        input=confirmation,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result, target


@pytest.mark.parametrize("confirmation", ["\n", "delete\n", "yes\n", ""])
def test_cancellation_never_changes_system(tmp_path, confirmation):
    result, _ = run_uninstall(tmp_path, confirmation)
    assert result.returncode == 2
    assert "CALL " not in result.stdout
    assert "已取消卸载" in result.stdout
    assert "卸载中断" not in result.stderr


def test_uninstall_stops_both_services_before_cleanup(tmp_path):
    result, directory = run_uninstall(tmp_path)
    assert result.returncode == 0, result.stderr
    calls = [line for line in result.stdout.splitlines() if line.startswith("CALL ")]
    assert calls[:6] == [
        f"CALL systemctl {action} {unit}"
        for unit in ("tg-butler-web.service", "tg-butler.service")
        for action in ("stop", "disable", "reset-failed")
    ]
    assert "/etc/sudoers.d/tg-butler-web" in calls[6]
    assert "/usr/local/bin/bu" in calls[6]
    assert "tg-butler-web.service.d" in calls[7]
    assert calls[8:] == [
        "CALL systemctl daemon-reload",
        "CALL userdel tgbutler",
        "CALL groupdel tgbutler",
        f"CALL rm -rf --one-file-system -- {directory}",
    ]
    assert "已卸载" in result.stdout


def test_service_stop_failure_aborts_before_removing_anything(tmp_path):
    result, _ = run_uninstall(tmp_path, scenario="stop-fails")
    assert result.returncode == 1
    assert "CALL systemctl stop tg-butler-web.service" in result.stdout
    assert "CALL rm " not in result.stdout
    assert "CALL userdel " not in result.stdout
    assert "已卸载" not in result.stdout
    assert "卸载中断" in result.stderr


@pytest.mark.parametrize("scenario", ["mount", "wrong-home"])
def test_unsafe_installation_refused_before_changes(tmp_path, scenario):
    result, _ = run_uninstall(tmp_path, scenario=scenario)
    assert result.returncode == 1
    assert "CALL " not in result.stdout
    assert "卸载失败" in result.stderr


def test_symlink_installation_refused_before_changes(tmp_path):
    result, _ = run_uninstall(tmp_path, symlink=True)
    assert result.returncode == 1
    assert "CALL " not in result.stdout
    assert "真实目录" in result.stderr


def test_partial_installation_without_units_or_account_can_be_cleaned(tmp_path):
    result, _ = run_uninstall(tmp_path, scenario="absent")
    assert result.returncode == 0, result.stderr
    assert "CALL systemctl stop" not in result.stdout
    assert "CALL userdel" not in result.stdout
    assert "CALL groupdel" not in result.stdout
    assert "已卸载" in result.stdout
