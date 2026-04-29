#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Tests for the Globato CLI Framework.
"""

import os
import pytest
import yaml
from click.testing import CliRunner

from globato.cli import cli


@pytest.fixture
def runner():
    """Fixture to provide a Click CliRunner for all tests."""

    return CliRunner()


def test_cli_base_help(runner):
    """Ensure the base command runs and all subcommands are registered."""

    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Continuous Digital Elevation Models" in result.output

    # expected_commands = [
    #     "recipe",
    #     "raster",
    #     "region",
    #     "fetch",
    #     "pointz",
    #     "viz",
    #     "transform",
    # ]
    expected_commands = [
        "cudem",
        "gritz",
        "regions",
        "fetchez",
        "pointz",
        "perspecto",
        "transformez",
    ]
    for cmd in expected_commands:
        assert cmd in result.output, f"Missing '{cmd}' command in CLI help!"


def test_region_echo_bbox(runner):
    """Test the spatial parsing engine (No network required)."""

    result = runner.invoke(
        cli, ["regions", "echo", "-R", "-120/-119/34/35", "-F", "gmt"]
    )

    assert result.exit_code == 0
    assert "-120.0/-119.0/34.0/35.0" in result.output.strip()


def test_recipe_build_save_only(runner):
    """Test the recipe builder's YAML generation in an isolated filesystem."""

    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            [
                "cudem",
                "build",
                "-R",
                "-120/-119/34/35",
                "-E",
                "1s",
                "-O",
                "test_dem",
                "--save-only",
                "mbdb+rq:threshold=50",
            ],
        )

        assert result.exit_code == 0
        assert "Recipe saved to test_dem_recipe.yaml" in result.output
        assert os.path.exists("test_dem_recipe.yaml")

        with open("test_dem_recipe.yaml", "r") as f:
            config = yaml.safe_load(f)

        assert config["project"]["name"] == "test_dem"
        assert config["modules"][0]["module"] == "mbdb"

        hooks = config["modules"][0]["hooks"]
        assert hooks[0]["name"] == "stream_data"
        assert hooks[1]["name"] == "rq"
        assert hooks[1]["args"]["threshold"] == 50


def test_recipe_info_source_eager(runner):
    """Test that the eager callback intercepts the command and exits cleanly."""

    result = runner.invoke(cli, ["cudem", "build", "--info-source", "file"])

    assert result.exit_code == 0
    assert "SOURCE: FILE" in result.output
    assert "Explicitly pass specific local files" in result.output
