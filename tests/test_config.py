from pathlib import Path

from researchos.config import CONFIG_NAME, Settings, is_within_workspace


def test_defaults_are_subscription_only_and_gpu_on(tmp_path):
    s = Settings(workspace_root=str(tmp_path))
    assert s.providers.allow_paid_fallback is False
    assert s.providers.order == ["codex", "claude_code"]
    assert s.kaggle.enable_gpu is True
    assert s.kaggle.enable_internet is True


def test_round_trip_save_and_load(tmp_path):
    s = Settings(workspace_root=str(tmp_path), active_project="thesis")
    s.providers.codex_model = "gpt-5.6-terra"
    s.save(tmp_path / CONFIG_NAME)
    loaded = Settings.load(tmp_path / CONFIG_NAME)
    assert loaded.active_project == "thesis"
    assert loaded.providers.codex_model == "gpt-5.6-terra"
    assert loaded.providers.allow_paid_fallback is False


def test_config_contains_secret_paths_not_secret_values(tmp_path):
    s = Settings(workspace_root=str(tmp_path))
    text = s.save(tmp_path / CONFIG_NAME).read_text()
    assert ".secrets/telegram_token" in text
    assert "token_file" in text
    # the file is a path reference only, so committing it is safe
    assert "8951586512" not in text


def test_discover_walks_up_to_find_config(tmp_path):
    Settings(workspace_root=str(tmp_path), active_project="thesis").save(
        tmp_path / CONFIG_NAME
    )
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert Settings.discover(nested).active_project == "thesis"


def test_discover_falls_back_to_defaults(tmp_path):
    assert Settings.discover(tmp_path).active_project is None


# ---------------- machine safety ----------------


def test_paths_inside_workspace_are_allowed(tmp_path):
    s = Settings(workspace_root=str(tmp_path))
    assert is_within_workspace(s, tmp_path / "projects" / "thesis" / "src" / "train.py")


def test_workspace_root_itself_is_allowed(tmp_path):
    assert is_within_workspace(Settings(workspace_root=str(tmp_path)), tmp_path)


def test_paths_outside_workspace_are_rejected(tmp_path):
    s = Settings(workspace_root=str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    assert not is_within_workspace(s, tmp_path / "elsewhere" / "secrets.txt")


def test_parent_traversal_is_rejected(tmp_path):
    """.. must not escape the configured root."""
    root = tmp_path / "workspace"
    root.mkdir()
    s = Settings(workspace_root=str(root))
    assert not is_within_workspace(s, root / ".." / ".." / "Windows" / "system32")


def test_system_paths_are_rejected(tmp_path):
    s = Settings(workspace_root=str(tmp_path))
    assert not is_within_workspace(s, Path("C:/Windows/System32/drivers/etc/hosts"))
