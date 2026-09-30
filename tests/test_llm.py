import json

from app.agents.core.llm import text_tool_calls


def test_text_tool_calls():
    names = {"set_pantry", "get_profile"}
    c = text_tool_calls("[set_pantry(user_id=b57c, key=будаа, amount=2.0), get_profile(user_id=\"b57c\")]", names)
    assert [x["function"]["name"] for x in c] == ["set_pantry", "get_profile"]
    assert json.loads(c[0]["function"]["arguments"]) == {"user_id": "b57c", "key": "будаа", "amount": 2.0}
    assert json.loads(c[1]["function"]["arguments"]) == {"user_id": "b57c"}
    assert text_tool_calls("Сайн байна уу", names) is None
    assert text_tool_calls("[wallet_pay(amount=1)]", names) is None


def test_manager_is_locked_to_oyullm_others_follow_their_role(monkeypatch):
    from app.agents.core import llm
    monkeypatch.setattr(llm, "PROVIDERS", {"oyullm": ("u", "k", "oyuLLM"), "openai": ("u", "k", "gpt"),
                                           "ollama": ("u", "ollama", None)})
    monkeypatch.setenv("MODEL_MANAGER", "openai")
    assert llm.chain("manager") == ["oyullm"]
    assert llm.chain("procurement") == ["openai", "oyullm"]
    monkeypatch.setenv("MODEL_PROCUREMENT", "oyullm")
    assert llm.chain("procurement") == ["oyullm"]


def test_unconfigured_provider_falls_back_to_oyullm(monkeypatch):
    from app.agents.core import llm
    monkeypatch.setattr(llm, "PROVIDERS", {"oyullm": ("u", "k", "oyuLLM"), "openai": (None, None, "gpt"),
                                           "ollama": ("u", "ollama", None)})
    assert llm.chain("store") == ["oyullm"]
