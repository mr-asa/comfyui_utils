"""
Pytest configuration and shared fixtures.
"""

import pytest
import os
import json
from pathlib import Path
from unittest.mock import MagicMock

@pytest.fixture
def temp_config_file(tmp_path):
    """Create a temporary config file for testing."""
    config_path = tmp_path / "config.json"
    config_data = {
        "env_type": "venv",
        "venv_path": str(tmp_path / "venv"),
        "custom_nodes_path": str(tmp_path / "nodes")
    }
    config_path.write_text(json.dumps(config_data))
    return config_path

@pytest.fixture
def mock_logger():
    """Provide a mock logger for testing."""
    return MagicMock()

@pytest.fixture
def requirements_file(tmp_path):
    """Create a temporary requirements.txt file for testing."""
    content = """
    package1>=1.0.0
    package2==2.0.0
    git+https://github.com/test/repo.git
    --extra-index-url https://test.pypi.org
    """
    req_file = tmp_path / "requirements.txt"
    req_file.write_text(content)
    return req_file

@pytest.fixture
def mock_environment_state():
    """Provide mock environment state for testing."""
    return {
        "packages": {
            "package1": "1.0.0",
            "package2": "2.0.0"
        },
        "python_version": "3.8.0",
        "platform": "test_platform"
    }
