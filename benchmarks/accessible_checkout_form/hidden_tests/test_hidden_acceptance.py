from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path.cwd()


class CheckoutParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.labels: set[str] = set()
        self.legend_count = 0
        self.fieldset_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = {key: value or "" for key, value in attrs}
        self.tags.append((tag, normalized))
        if tag == "label" and normalized.get("for"):
            self.labels.add(normalized["for"])
        if tag == "legend":
            self.legend_count += 1
        if tag == "fieldset":
            self.fieldset_count += 1


def html_parser() -> CheckoutParser:
    parser = CheckoutParser()
    parser.feed((ROOT / "index.html").read_text(encoding="utf-8"))
    return parser


def node_eval(expression: str) -> object:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node es obligatorio para evaluar el contrato JavaScript")
    script = (
        "const api=require('./checkout.js');"
        f"const value=({expression});"
        "process.stdout.write(JSON.stringify(value));"
    )
    proc = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_required_files_and_semantic_form() -> None:
    for name in ("index.html", "checkout.js", "styles.css"):
        assert (ROOT / name).is_file()
    parser = html_parser()
    forms = [attrs for tag, attrs in parser.tags if tag == "form"]
    assert any(attrs.get("id") == "checkout-form" for attrs in forms)
    assert parser.fieldset_count >= 1 and parser.legend_count >= 1


def test_fields_have_labels_and_input_metadata() -> None:
    parser = html_parser()
    inputs = {attrs.get("name"): attrs for tag, attrs in parser.tags if tag == "input"}
    expected = {"full_name", "email", "card_number", "expiry", "cvc"}
    assert expected.issubset(inputs)
    for name in expected:
        field_id = inputs[name].get("id")
        assert field_id and field_id in parser.labels
        assert inputs[name].get("autocomplete")
    assert inputs["card_number"].get("inputmode") in {"numeric", "decimal"}
    assert inputs["cvc"].get("inputmode") in {"numeric", "decimal"}


def test_accessible_summary_and_status_regions() -> None:
    parser = html_parser()
    by_id = {attrs.get("id"): attrs for _, attrs in parser.tags if attrs.get("id")}
    assert by_id["error-summary"].get("role") == "alert"
    assert by_id["error-summary"].get("tabindex") == "-1"
    assert by_id["order-status"].get("role") == "status"
    assert by_id["order-status"].get("aria-live") == "polite"


def test_luhn_accepts_formatting_and_rejects_invalid_cards() -> None:
    assert node_eval("api.luhnValid('4242 4242-4242 4242')") is True
    assert node_eval("api.luhnValid('4242 4242 4242 4241')") is False
    assert node_eval("api.luhnValid('abc')") is False


def test_validation_accepts_valid_payload_and_does_not_mutate() -> None:
    expression = """(()=>{const d={full_name:' Ada Lovelace ',email:'ada@example.com',card_number:'4242 4242 4242 4242',expiry:'12/99',cvc:'123'};const before=JSON.stringify(d);const errors=api.validateCheckout(d);return {errors,unchanged:before===JSON.stringify(d)}})()"""
    result = node_eval(expression)
    assert result == {"errors": {}, "unchanged": True}


def test_validation_rejects_every_field_in_stable_order() -> None:
    result = node_eval("api.validateCheckout({full_name:'',email:'bad',card_number:'123',expiry:'00/20',cvc:'x'})")
    assert list(result) == ["full_name", "email", "card_number", "expiry", "cvc"]
    assert all(isinstance(message, str) and message.strip() for message in result.values())


def test_expiry_accepts_current_month_and_rejects_bad_shape() -> None:
    expression = """(()=>{const now=new Date();const mm=String(now.getMonth()+1).padStart(2,'0');const yy=String(now.getFullYear()).slice(-2);const base={full_name:'A',email:'a@b.co',card_number:'4242424242424242',cvc:'123'};return {current:api.validateCheckout({...base,expiry:mm+'/'+yy}).expiry||null,bad:api.validateCheckout({...base,expiry:'13/30'}).expiry||null}})()"""
    result = node_eval(expression)
    assert result["current"] is None
    assert isinstance(result["bad"], str) and result["bad"]


def test_dom_error_contract_is_implemented_without_alert() -> None:
    source = (ROOT / "checkout.js").read_text(encoding="utf-8")
    required = ["preventDefault", "aria-invalid", "aria-describedby", "error-summary", "order-status", ".focus("]
    assert all(token in source for token in required)
    assert not re.search(r"\balert\s*\(", source)
    creates_anchor = re.search(r"createElement\s*\(\s*['\"]a['\"]\s*\)", source)
    renders_anchor = re.search(r"<a\s+[^>]*href\s*=\s*['\"]?#", source, re.I)
    assert creates_anchor or renders_anchor


def test_css_has_focus_error_responsive_and_reduced_motion_contracts() -> None:
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    compact = re.sub(r"\s+", " ", css.lower())
    assert ":focus-visible" in compact
    assert "error" in compact and "border" in compact
    assert re.search(r"@media[^\{]*min-width\s*:\s*768px", compact)
    assert "grid-template-columns" in compact
    assert "prefers-reduced-motion" in compact


def test_html_loads_assets_and_avoids_inline_event_handlers() -> None:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert re.search(r"<link[^>]+styles\.css", html, re.I)
    assert re.search(r"<script[^>]+checkout\.js", html, re.I)
    assert not re.search(r"\son[a-z]+\s*=", html, re.I)
