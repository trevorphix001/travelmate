from app.core.config import Settings


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-SECRET123")
    monkeypatch.setenv("LLM_FALLBACKS", "groq, gemini")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test")
    s = Settings()
    assert s.llm_fallbacks == ["groq", "gemini"]
    assert s.cors_origins == ["http://a.test"]
    assert s.nvidia_api_key.get_secret_value() == "nvapi-SECRET123"
    assert "SECRET123" not in repr(s)
    assert "SECRET123" not in str(s.model_dump())
