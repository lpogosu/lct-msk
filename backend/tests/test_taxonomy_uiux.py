"""UI/UX-дизайн: специализация «по опросу, тест в дорожной карте» (ответы организаторов: приоритет — технические роли)."""

from __future__ import annotations

from app.core.configs import load_config
from app.matching.brief_parser import parse_brief


def test_uiux_design_is_survey_only_without_blueprint():
    specs = {s["code"]: s for s in load_config("taxonomy")["specializations"]}
    spec = specs["uiux_design"]
    assert spec["status"] == "survey_only" and spec["name"] == "UI/UX-дизайн" and spec["direction"] == "design"
    assert "blueprint" not in spec  # теста нет: категория не присваивается, кандидат попадает в лист ожидания


def test_designer_brief_points_to_uiux_and_design_skills_stay_untestable():
    brief = parse_brief("Ищем UI/UX-дизайнера в команду мобильного банка, Figma, дизайн-система, 150–200 тыс. на руки")
    assert brief.top_spec == "uiux_design"
    skills = load_config("skills")["skills"]
    assert skills["figma"]["untestable"] is True and skills["ui_ux"]["untestable"] is True
    assert "тест в дорожной карте" in load_config("skills")["untestable_reasons"]["design"]
