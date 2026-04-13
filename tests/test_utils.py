from app.utils import (
    filter_agent_output,
    normalize_bool,
    sanitize_plain_text,
)


def test_sanitize_removes_markdown():
    assert sanitize_plain_text("**hello** `world`") == "hello world"
    assert sanitize_plain_text("# Title\n## Sub") == "Title\n Sub"


def test_normalize_bool_variants():
    assert normalize_bool(True) is True
    assert normalize_bool("1") is True
    assert normalize_bool("si") is True
    assert normalize_bool(False) is False
    assert normalize_bool("0") is False
    assert normalize_bool(None) is None
    assert normalize_bool("maybe") is None


def test_filter_agent_output_strips_escalation():
    result = filter_agent_output("Te conecto con un asesor. [ESCALAR]")
    assert result.should_escalate is True
    assert "[ESCALAR]" not in result.text
    assert "asesor" in result.text


def test_filter_agent_output_no_escalation():
    result = filter_agent_output("Manejamos categorias A2, B1, C1.")
    assert result.should_escalate is False
    assert "A2" in result.text


def test_filter_agent_output_removes_urls():
    result = filter_agent_output("Visita https://malicious.com para info")
    assert "https://" not in result.text


def test_filter_agent_output_removes_unknown_phones():
    result = filter_agent_output("Llama al 3001234567 para info")
    assert "3001234567" not in result.text


def test_filter_agent_output_keeps_allowed_phone():
    result = filter_agent_output("Nuestro WhatsApp es 573134246298")
    assert "573134246298" in result.text


def test_filter_agent_output_truncates_long():
    long_text = "Hola " * 300
    result = filter_agent_output(long_text)
    assert len(result.text) <= 810  # 800 + "..."


def test_filter_agent_output_removes_markdown():
    result = filter_agent_output("**Hola** `mundo` # titulo")
    assert "*" not in result.text
    assert "`" not in result.text
