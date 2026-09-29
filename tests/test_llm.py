import json

from app.common.llm import text_tool_calls


def test_text_tool_calls():
    names = {"set_pantry", "get_profile"}
    c = text_tool_calls("[set_pantry(user_id=b57c, key=будаа, amount=2.0), get_profile(user_id=\"b57c\")]", names)
    assert [x["function"]["name"] for x in c] == ["set_pantry", "get_profile"]
    assert json.loads(c[0]["function"]["arguments"]) == {"user_id": "b57c", "key": "будаа", "amount": 2.0}
    assert json.loads(c[1]["function"]["arguments"]) == {"user_id": "b57c"}
    assert text_tool_calls("Сайн байна уу", names) is None
    assert text_tool_calls("[wallet_pay(amount=1)]", names) is None
