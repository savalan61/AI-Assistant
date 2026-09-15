"""Tests for application configuration loading (app/core/config.py).

Pins two invariants that matter in production:

* an unrecognised key in the env FILE cannot take the process down at import
  time, and can never reveal its value — only its NAME is reported; and
* an invalid value for a *known* setting fails closed with a message that names
  the setting and never renders the submitted value.

No real credential is used: every value here is an obviously fake marker.
"""

import logging
import traceback
from pathlib import Path

import pytest

import app.core.config as config_module
from app.core.config import ENV_FILE, Settings, load_settings, settings

# Obvious stand-in for "a secret someone pasted into the wrong env key".
FAKE_SECRET = "not-a-real-secret-9c1f-a7b2"


def _env_file(tmp_path: Path, body: str) -> Path:
    """Write a throwaway env file for one test."""
    path = tmp_path / "test.env"
    path.write_text(body, encoding="utf-8")
    return path


# --- module-level settings (the real project configuration) -------------------


def test_module_level_settings_load_for_the_real_project_configuration() -> None:
    """Importing app.core.config must succeed: this is the app's startup path."""
    assert isinstance(settings, Settings)
    assert ENV_FILE == ".env"
    assert settings.APP_ENV  # non-empty; the app selects behaviour by environment


# --- unknown env-file keys are ignored, and reported by name only -------------


def test_unknown_key_in_env_file_is_ignored_not_fatal(tmp_path: Path) -> None:
    """A stray key must not abort startup (previously: extra_forbidden)."""
    path = _env_file(tmp_path, f"INVESTOR_PASSWORD={FAKE_SECRET}\n")

    loaded = load_settings(env_file=path)

    assert isinstance(loaded, Settings)
    assert FAKE_SECRET not in repr(loaded)


def test_unknown_key_is_reported_by_name_and_never_by_value(tmp_path: Path, caplog) -> None:
    path = _env_file(tmp_path, f"INVESTOR_PASSWORD={FAKE_SECRET}\n")

    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        load_settings(env_file=path)

    assert "INVESTOR_PASSWORD" in caplog.text
    assert FAKE_SECRET not in caplog.text


def test_commented_and_blank_lines_are_not_reported_as_unknown(tmp_path: Path, caplog) -> None:
    path = _env_file(tmp_path, "\n# NOT_A_SETTING=ignored\n   \nAPP_NAME=Named\n")

    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        load_settings(env_file=path)

    assert "NOT_A_SETTING" not in caplog.text
    assert "unrecognised" not in caplog.text


# --- invalid values fail closed without echoing the value ---------------------


@pytest.mark.parametrize(
    ("body", "expected_loc", "expected_type"),
    [
        (f"AGENT_DAILY_REQUEST_LIMIT={FAKE_SECRET}\n", "AGENT_DAILY_REQUEST_LIMIT", "int_parsing"),
        (f"DEBUG={FAKE_SECRET}\n", "DEBUG", "bool_parsing"),
    ],
)
def test_invalid_setting_error_names_the_setting_and_omits_the_value(
    tmp_path: Path, body: str, expected_loc: str, expected_type: str
) -> None:
    """The rendered error output must never contain the submitted value."""
    path = _env_file(tmp_path, body)

    with pytest.raises(RuntimeError) as excinfo:
        load_settings(env_file=path)

    message = str(excinfo.value)
    assert expected_loc in message
    assert expected_type in message
    assert FAKE_SECRET not in message

    # The full rendered traceback is what an operator actually sees.
    rendered = "".join(traceback.format_exception(excinfo.value))
    assert FAKE_SECRET not in rendered
    # The original pydantic error embeds the value, so it must not be chained
    # into the traceback (raise ... from None).
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_known_settings_are_still_validated_strictly(tmp_path: Path) -> None:
    """Ignoring unknown keys must not weaken validation of known ones."""
    path = _env_file(tmp_path, "AGENT_MAX_MESSAGE_LENGTH=not-an-integer\n")

    with pytest.raises(RuntimeError, match="AGENT_MAX_MESSAGE_LENGTH"):
        load_settings(env_file=path)


# --- the ordinary paths keep working ------------------------------------------


def test_valid_values_are_still_read_from_the_env_file(tmp_path: Path) -> None:
    path = _env_file(tmp_path, "APP_NAME=Custom From File\nDEBUG=False\n")

    loaded = load_settings(env_file=path)

    assert loaded.APP_NAME == "Custom From File"
    assert loaded.DEBUG is False


def test_missing_env_file_is_not_an_error() -> None:
    assert isinstance(load_settings(env_file=None), Settings)


def test_unrelated_process_environment_variable_is_not_a_settings_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal deployment environment (CI/platform variables) must not break us."""
    monkeypatch.setenv("SOME_UNRELATED_DEPLOYMENT_VAR", FAKE_SECRET)

    loaded = load_settings(env_file=None)

    assert isinstance(loaded, Settings)
    assert not hasattr(loaded, "SOME_UNRELATED_DEPLOYMENT_VAR")


def test_reading_no_env_file_still_reads_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``env_file=None`` drops only the dotenv source, not the environment."""
    monkeypatch.setenv("APP_NAME", "From Process Env")

    assert load_settings(env_file=None).APP_NAME == "From Process Env"


# --- the env-file override stays statically visible ---------------------------


def test_env_file_is_selected_through_model_config_not_an_init_argument() -> None:
    """Pins the typing fix for the Pylance diagnostic.

    pydantic's ``@dataclass_transform`` makes type checkers synthesise
    ``__init__`` from the model fields alone, so pydantic-settings' injected
    ``_env_file`` argument is invisible to Pylance ("No parameter named
    '_env_file'"). The dotenv path must therefore be selected through
    ``model_config``, which is statically visible and runtime-identical.
    """
    source_file = config_module.__file__
    assert source_file is not None
    source = Path(source_file).read_text(encoding="utf-8")

    # The keyword form is what Pylance rejects; the dotted names above only
    # appear as ordinary helper/parameter names and in explanatory comments.
    assert "_env_file=" not in source
    assert "model_config" in source


def test_default_env_file_keeps_the_settings_class_unchanged() -> None:
    """The production path must instantiate Settings exactly as before."""
    assert type(load_settings()) is Settings
    assert type(load_settings(env_file=Path(ENV_FILE))) is Settings


def test_env_file_override_reads_the_requested_file(tmp_path: Path) -> None:
    """The override really switches the file pydantic reads."""
    path = _env_file(tmp_path, "APP_NAME=From The Override\n")

    loaded = load_settings(env_file=path)

    assert loaded.APP_NAME == "From The Override"
    assert type(loaded) is not Settings  # a derived variant, not the base class
    assert isinstance(loaded, Settings)
