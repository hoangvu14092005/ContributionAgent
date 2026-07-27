"""Tests for contribai.agents.skill_loader — Skill markdown loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from contribai.agents.skill_loader import (
    DEFAULT_AGENTS_DIR,
    Skill,
    SkillLoader,
    SkillParseError,
    _parse_frontmatter,
    _parse_inputs,
)


class TestParseFrontmatter:
    def test_valid_yaml(self):
        text = "---\nname: foo\ntrigger: /foo\n---\nbody line\n"
        fm, body = _parse_frontmatter(text)
        assert fm == {"name": "foo", "trigger": "/foo"}
        assert body.strip() == "body line"

    def test_no_frontmatter(self):
        text = "just plain markdown\n"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_invalid_yaml_raises(self):
        text = "---\n: : invalid\n---\nbody\n"
        with pytest.raises(SkillParseError):
            _parse_frontmatter(text)

    def test_non_mapping_frontmatter_raises(self):
        text = "---\n- just a list\n- not a map\n---\nbody\n"
        with pytest.raises(SkillParseError):
            _parse_frontmatter(text)


class TestParseInputs:
    def test_none_returns_empty(self):
        assert _parse_inputs(None) == ()

    def test_dict_legacy(self):
        result = _parse_inputs({"PR_URL": "the URL"})
        assert len(result) == 1
        assert result[0].name == "PR_URL"
        assert result[0].description == "the URL"
        assert result[0].type == "string"
        assert result[0].required is True

    def test_dict_structured(self):
        result = _parse_inputs(
            {
                "BRANCH_NAME": {
                    "type": "string",
                    "description": "branch",
                    "required": False,
                }
            }
        )
        assert result[0].required is False

    def test_list_of_dicts(self):
        result = _parse_inputs(
            [
                {"name": "PR_URL", "type": "string", "description": "url"},
                {"name": "BRANCH_NAME"},
            ]
        )
        assert len(result) == 2
        assert result[0].name == "PR_URL"
        assert result[1].type == "string"

    def test_list_of_strings(self):
        result = _parse_inputs(["PR_URL", "BRANCH_NAME"])
        assert [i.name for i in result] == ["PR_URL", "BRANCH_NAME"]


class TestSkillLoaderDefaults:
    def test_default_agents_dir(self):
        loader = SkillLoader()
        assert loader.agents_dir == DEFAULT_AGENTS_DIR
        assert loader.agents_dir.name == ".agents"

    def test_custom_dir(self, tmp_path):
        loader = SkillLoader(tmp_path)
        assert loader.agents_dir == tmp_path


class TestSkillLoaderListAll:
    def test_list_all_finds_all_markdown_files(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        skills = loader.list_all()
        # we created 12 markdown files (6 workflows + 4 agents + 2 knowledge)
        assert len(skills) >= 12

    def test_categories_present(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        cats = loader.categories()
        assert cats["workflow"] >= 6
        assert cats["agent"] >= 4
        assert cats["knowledge"] >= 2

    def test_all_skills_have_description(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        for skill in loader.list_all():
            assert skill.description, f"{skill.name} is missing description"

    def test_categories_correct(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        for skill in loader.list_all():
            assert skill.category in {"agent", "workflow", "knowledge"}


class TestSkillLoaderFind:
    def test_find_by_trigger_with_slash(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        skill = loader.find_by_trigger("/address_pr_comments")
        assert skill is not None
        assert skill.category == "workflow"
        assert "Address PR Comments" in skill.body or "PR Comments" in skill.name

    def test_find_by_trigger_without_slash(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        skill = loader.find_by_trigger("address_pr_comments")
        assert skill is not None

    def test_find_by_trigger_case_insensitive(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        skill = loader.find_by_trigger("/ADDRESS_PR_COMMENTS")
        assert skill is not None

    def test_find_by_trigger_nonexistent(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        assert loader.find_by_trigger("/nonexistent_skill_xyz") is None

    def test_find_by_trigger_empty_returns_none(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        assert loader.find_by_trigger("") is None

    def test_find_by_name(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        skill = loader.find_by_name("update_test")
        assert skill is not None
        assert skill.trigger == "/update_test"


class TestSkillLoaderSearch:
    def test_search_finds_relevant(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        results = loader.search("PR")
        assert len(results) >= 2

    def test_search_by_description_keyword(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        results = loader.search("Linus")
        assert any("roasted" in s.name.lower() or "linus" in s.body.lower() for s in results)

    def test_search_empty_returns_all(self):
        loader = SkillLoader(DEFAULT_AGENTS_DIR)
        assert loader.search("") == loader.list_all()


class TestSkillLoaderWithTmp:
    """Isolated tests using a tmp directory."""

    def _write_md(self, base: Path, category: str, name: str, body: str) -> Path:
        d = base / category
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.md"
        path.write_text(body, encoding="utf-8")
        return path

    def test_loads_custom_skill(self, tmp_path):
        self._write_md(
            tmp_path,
            "workflows",
            "demo",
            "---\ndescription: Demo skill\ntrigger: /demo\n---\n\nBody text.\n",
        )
        loader = SkillLoader(tmp_path)
        skills = loader.list_all()
        assert len(skills) == 1
        assert skills[0].name == "demo"
        assert skills[0].trigger == "/demo"
        assert skills[0].category == "workflow"
        assert "Body text." in skills[0].body

    def test_skips_unrelated_directories(self, tmp_path):
        (tmp_path / "agents").mkdir()
        (tmp_path / "random_subdir").mkdir()
        (tmp_path / "random_subdir" / "foo.md").write_text("---\ndescription: x\n---\nbody")
        loader = SkillLoader(tmp_path)
        assert loader.list_all() == []

    def test_handles_missing_dir(self, tmp_path):
        nonexistent = tmp_path / "no_such"
        loader = SkillLoader(nonexistent)
        assert loader.list_all() == []

    def test_skips_malformed_file(self, tmp_path, caplog):
        # malformed frontmatter
        self._write_md(
            tmp_path,
            "workflows",
            "bad",
            "---\n: invalid: yaml: : :\n---\nbody\n",
        )
        loader = SkillLoader(tmp_path)
        with caplog.at_level("WARNING"):
            skills = loader.list_all()
        assert skills == []

    def test_no_frontmatter_uses_filename(self, tmp_path):
        self._write_md(tmp_path, "workflows", "nofront", "Just a body without frontmatter.\n")
        loader = SkillLoader(tmp_path)
        skills = loader.list_all()
        assert len(skills) == 1
        assert skills[0].name == "nofront"
        assert skills[0].description == ""

    def test_input_parsing_from_list(self, tmp_path):
        self._write_md(
            tmp_path,
            "workflows",
            "with_inputs",
            (
                "---\n"
                "description: x\n"
                "trigger: /x\n"
                "inputs:\n"
                "  - name: PR_URL\n"
                "    type: string\n"
                "    description: the url\n"
                "  - name: BRANCH\n"
                "    type: string\n"
                "---\n"
                "body\n"
            ),
        )
        loader = SkillLoader(tmp_path)
        skill = loader.list_all()[0]
        assert {i.name for i in skill.inputs} == {"PR_URL", "BRANCH"}


class TestSkillDataclass:
    def test_to_dict(self):
        from contribai.agents.skill_loader import SkillInput

        s = Skill(
            name="demo",
            description="Demo",
            file_path=Path("/x/demo.md"),
            category="workflow",
            trigger="/demo",
            inputs=(SkillInput(name="URL"),),
        )
        d = s.to_dict()
        assert d["name"] == "demo"
        assert d["category"] == "workflow"
        assert d["trigger"] == "/demo"
        assert d["inputs"][0]["name"] == "URL"

    def test_has_trigger(self):

        with_trigger = Skill(
            name="a",
            description="",
            file_path=Path("/x.md"),
            category="agent",
            trigger="/a",
        )
        without = Skill(
            name="b",
            description="",
            file_path=Path("/y.md"),
            category="agent",
        )
        assert with_trigger.has_trigger is True
        assert without.has_trigger is False
