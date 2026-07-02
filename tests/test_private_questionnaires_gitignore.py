"""PR 1A — `private_questionnaires/` and `private_review_packs/` must actually be
gitignored — verified via a REAL `git check-ignore` call (not just grepping
.gitignore text, which could pass while the pattern doesn't actually match). A
temporary probe file is created and removed in `finally`, so nothing leaks into
the repo tree even if the assertion fails.
"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _is_git_ignored(rel_path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", rel_path],
        cwd=ROOT, capture_output=True)
    return result.returncode == 0


def test_private_questionnaires_dir_is_gitignored():
    probe_dir = ROOT / "private_questionnaires"
    probe = probe_dir / "._privacy_test_probe.json"
    try:
        probe_dir.mkdir(exist_ok=True)
        probe.write_text("{}", encoding="utf-8")
        assert _is_git_ignored("private_questionnaires/._privacy_test_probe.json"), (
            "private_questionnaires/ is NOT actually gitignored by git — a "
            "copyrighted questionnaire instrument could be committed by accident")
    finally:
        if probe.exists():
            probe.unlink()
        if probe_dir.exists() and not any(probe_dir.iterdir()):
            probe_dir.rmdir()


def test_private_review_packs_dir_is_gitignored():
    probe_dir = ROOT / "private_review_packs"
    probe = probe_dir / "._privacy_test_probe.json"
    try:
        probe_dir.mkdir(exist_ok=True)
        probe.write_text("{}", encoding="utf-8")
        assert _is_git_ignored("private_review_packs/._privacy_test_probe.json"), (
            "private_review_packs/ is NOT actually gitignored by git — a "
            "psychologist_review_pack export could be committed by accident")
    finally:
        if probe.exists():
            probe.unlink()
        if probe_dir.exists() and not any(probe_dir.iterdir()):
            probe_dir.rmdir()
