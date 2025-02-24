"""
Tests for the configuration manager module.
"""

import pytest
from pathlib import Path
from requirements_checker.config_manager import ConfigManager, EnvironmentConfig

@pytest.fixture
def temp_config_file(tmp_path):
    """Create a temporary configuration file for testing."""
    config_file = tmp_path / "test_config.json"
    return config_file

@pytest.fixture
def config_manager(temp_config_file):
    """Create a ConfigManager instance with a temporary config file."""
    return ConfigManager(temp_config_file)

def test_config_creation(temp_config_file):
    """Test that configuration file is created if it doesn't exist."""
    assert not temp_config_file.exists()
    ConfigManager(temp_config_file)
    assert temp_config_file.exists()

def test_set_and_get_value(config_manager):
    """Test setting and getting configuration values."""
    config_manager.set_value("test_key", "test_value")
    assert config_manager.get_value("test_key") == "test_value"

def test_environment_config(config_manager):
    """Test creating and retrieving environment configuration."""
    test_config = {
        "env_type": "venv",
        "venv_path": "/test/path",
        "project_path": "/test/project"
    }
    for key, value in test_config.items():
        config_manager.set_value(key, value)
    
    env_config = config_manager.get_environment_config()
    assert isinstance(env_config, EnvironmentConfig)
    assert env_config.env_type == "venv"
    assert env_config.venv_path == "/test/path"
