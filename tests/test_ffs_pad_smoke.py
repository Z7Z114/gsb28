"""顺利路径冒烟测试：只覆盖最容易踩到的几条路径。

    python -m pytest -q

这些用例用的是 samples/ 下的 ARM64EC 镜像样本（布局见 README）。规格的逐条验收，
以及每个缺陷的边界，请自己补测试。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "ec-ffs-pad.py"
SAMPLES = ROOT / "samples"


def run(*args):
    return subprocess.run([sys.executable, str(TOOL)] + [str(a) for a in args],
                          capture_output=True, text=True)


def sample(tmp_path, name):
    dst = tmp_path / name
    shutil.copy(SAMPLES / name, dst)
    return dst


def test_pads_an_arm64ec_image_in_place(tmp_path):
    img = sample(tmp_path, "arm64ec_demo.dll")
    before = (SAMPLES / "arm64ec_demo.dll").read_bytes()
    r = run(img)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK" in r.stdout and "thunks padded" in r.stdout
    after = img.read_bytes()
    assert len(after) == len(before), "the image is rewritten in place, not resized"
    assert after != before, "the thunks and the slack should have been rewritten"
    assert "1 padded, 0 skipped, 0 FAILED" in r.stdout


def test_skips_an_image_whose_slack_is_too_small(tmp_path):
    img = sample(tmp_path, "arm64ec_slack_short.dll")
    before = img.read_bytes()
    r = run(img)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "slack" in r.stdout
    assert img.read_bytes() == before
    assert "1 skipped" in r.stdout


def test_skips_an_image_without_hexpthk(tmp_path):
    img = sample(tmp_path, "plain_x64.dll")
    before = img.read_bytes()
    r = run(img)
    assert r.returncode == 0, r.stdout + r.stderr
    assert ".hexpthk" in r.stdout
    assert img.read_bytes() == before


def test_reports_a_file_it_cannot_read(tmp_path):
    r = run(tmp_path / "not-there.dll")
    assert r.returncode == 0
    assert "not-there.dll" in r.stdout


def test_usage_without_arguments():
    r = run()
    assert r.returncode == 2
    assert "usage" in (r.stdout + r.stderr).lower()


def test_handles_several_files_in_one_run(tmp_path):
    good = sample(tmp_path, "arm64ec_demo.dll")
    plain = sample(tmp_path, "plain_x64.dll")
    before_good = (SAMPLES / "arm64ec_demo.dll").read_bytes()
    before_plain = plain.read_bytes()
    r = run(good, plain)
    assert r.returncode == 0, r.stdout + r.stderr
    assert good.read_bytes() != before_good
    assert plain.read_bytes() == before_plain
    assert "1 padded" in r.stdout and "1 skipped" in r.stdout
