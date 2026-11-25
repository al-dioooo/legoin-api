import pytest
from pydantic import ValidationError

from legoin_api.config import Settings
from legoin_api.main import warn_about_deployment


def test_the_models_pipeline_refuses_more_than_one_scan_worker(monkeypatch):
    monkeypatch.setenv("LEGOIN_PIPELINE", "models")
    monkeypatch.setenv("LEGOIN_SCAN_WORKERS", "2")

    with pytest.raises(ValidationError, match="LEGOIN_SCAN_WORKERS must be 1"):
        Settings()


def test_the_stub_may_run_several_scan_workers():
    assert Settings(pipeline="stub", scan_workers=4).scan_workers == 4
    assert Settings(pipeline="models", scan_workers=1).scan_workers == 1


@pytest.mark.parametrize("field", ["scan_workers", "scan_queue_size"])
def test_scan_workers_and_queue_size_are_at_least_one(field):
    with pytest.raises(ValidationError):
        Settings(**{field: 0})


@pytest.mark.parametrize(
    ("container", "public_url", "warns"),
    [(True, None, True), (True, "https://api.example.com", False), (False, None, False)],
)
def test_the_container_warns_when_the_public_url_is_unset(caplog, container, public_url, warns):
    warn_about_deployment(Settings(container=container, public_url=public_url))

    assert ("LEGOIN_PUBLIC_URL is unset" in caplog.text) is warns


def test_the_image_says_it_is_a_container():
    from pathlib import Path

    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    assert "LEGOIN_CONTAINER=1" in dockerfile


# What writes ideas

FAKE_KEY = "test-key-not-real"


def test_ideas_come_from_the_templates_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert Settings().ideas_kind == "templates"


def test_ideas_come_from_claude_when_a_key_is_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    assert Settings().ideas_kind == "anthropic"
    monkeypatch.setenv("LEGOIN_IDEAS_GENERATOR", "templates")
    assert Settings().ideas_kind == "templates"


def test_claude_without_a_key_refuses_to_start(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LEGOIN_IDEAS_GENERATOR", "anthropic")

    with pytest.raises(ValidationError, match="needs ANTHROPIC_API_KEY"):
        Settings()


def test_the_model_and_effort_default_to_sonnet_5_5_at_low(monkeypatch):
    settings = Settings()
    assert (settings.ideas_model, settings.ideas_effort) == ("claude-sonnet-5-5", "low")

    monkeypatch.setenv("LEGOIN_IDEAS_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("LEGOIN_IDEAS_EFFORT", "medium")
    settings = Settings()
    assert (settings.ideas_model, settings.ideas_effort) == ("claude-opus-5-5", "medium")
    monkeypatch.setenv("LEGOIN_IDEAS_EFFORT", "extreme")
    with pytest.raises(ValidationError):
        Settings()


def test_the_claude_generator_fits_inside_the_deadline(monkeypatch):
    from legoin_api.ideas.claude import AnthropicIdeaGenerator
    from legoin_api.main import load_idea_generator

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    generator = load_idea_generator(Settings(ideas_effort="medium"))

    assert isinstance(generator, AnthropicIdeaGenerator)
    assert (generator.model, generator.effort, generator.budget_s) == (
        "claude-sonnet-5-5",
        "medium",
        24.0,
    )
    # About 20 s per request and one retry, cut short by the 24 s budget.
    assert (generator.client.timeout, generator.client.max_retries) == (20.0, 1)


def test_health_says_claude_writes_the_ideas(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from legoin_api.ideas.generator import TemplateIdeaGenerator
    from legoin_api.main import create_app

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    settings = Settings(pipeline="stub", data_dir=tmp_path)

    with TestClient(create_app(settings, idea_generator=TemplateIdeaGenerator())) as client:
        health = client.get("/health").json()

    assert (health["ideas"], health["ideasModel"]) == ("anthropic", "claude-sonnet-5-5")
