from legoin_api import __version__
from legoin_api.config import Settings


def test_health_reports_the_pipeline(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "pipeline": "stub",
        "ideas": "templates",
    }


def test_cors_allows_the_web_app(client):
    response = client.options(
        "/health",
        headers={
            "Origin": "https://legoin-web.vercel.app",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Content-Type, Legoin-Device-Id",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://legoin-web.vercel.app"
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "legoin-device-id" in response.headers["access-control-allow-headers"].lower()


def test_cors_refuses_other_origins(client):
    response = client.get("/health", headers={"Origin": "https://example.com"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_origins_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("LEGOIN_CORS_ORIGINS", "http://localhost:3100, https://preview.example ")

    assert Settings().cors_origin_list == ["http://localhost:3100", "https://preview.example"]


def test_empty_variables_mean_the_default(monkeypatch):
    for name in ("LEGOIN_TORCH_THREADS", "LEGOIN_MODEL_DIR", "LEGOIN_PUBLIC_URL"):
        monkeypatch.setenv(name, "")

    settings = Settings()

    assert (settings.torch_threads, settings.model_dir, settings.public_url) == (None, None, None)
