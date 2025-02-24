"""
Test suite for utils module.

Tests color formatting, logging, error handling, and other utility functions.
"""

import pytest
import logging
from datetime import datetime
from unittest.mock import patch, MagicMock
from colorama import Fore, Style

from requirements_checker.utils import (
    OutputColors,
    colors,
    format_command,
    format_version,
    format_package_info,
    print_section_header,
    print_command_help,
    log_operation_time,
    handle_error
)

def test_output_colors():
    """Test that color constants are properly defined."""
    assert colors.SUCCESS == Fore.GREEN
    assert colors.WARNING == Fore.YELLOW
    assert colors.ERROR == Fore.RED
    assert colors.INFO == Fore.BLUE
    assert colors.COMMAND == Fore.CYAN
    assert colors.RESET == Style.RESET_ALL

def test_format_command():
    """Test command formatting."""
    command = "pip install package"
    formatted = format_command(command)
    assert formatted.startswith(colors.COMMAND)
    assert formatted.endswith(colors.RESET)
    assert command in formatted

@pytest.mark.parametrize("version,is_latest", [
    ("1.0.0", True),
    ("0.9.0", False)
])
def test_format_version(version, is_latest):
    """Test version number formatting."""
    formatted = format_version(version, is_latest)
    expected_color = colors.SUCCESS if is_latest else colors.INFO
    assert formatted.startswith(expected_color)
    assert formatted.endswith(colors.RESET)
    assert version in formatted

@pytest.mark.parametrize("package,version", [
    ("test-package", "1.0.0"),
    ("another-package", None)
])
def test_format_package_info(package, version):
    """Test package information formatting."""
    formatted = format_package_info(package, version)
    assert formatted.startswith(colors.SUCCESS)
    assert formatted.endswith(colors.RESET)
    assert package in formatted
    if version:
        assert version in formatted

@patch('builtins.print')
def test_print_section_header(mock_print):
    """Test section header printing."""
    title = "Test Section"
    print_section_header(title)
    mock_print.assert_called_once()
    call_args = mock_print.call_args[0][0]
    assert title in call_args
    assert colors.INFO in call_args
    assert colors.RESET in call_args

@patch('builtins.print')
def test_print_command_help(mock_print):
    """Test command help printing."""
    print_command_help()
    assert mock_print.call_count > 0
    # Check that at least one command was printed
    assert any('pip show' in str(args) for args, _ in mock_print.call_args_list)

def test_log_operation_time():
    """Test operation time logging decorator."""
    @log_operation_time
    def test_function():
        return "test"

    with patch('logging.getLogger') as mock_logger:
        result = test_function()
        assert result == "test"
        mock_logger.return_value.info.assert_called_once()
        log_message = mock_logger.return_value.info.call_args[0][0]
        assert "test_function" in log_message
        assert "completed in" in log_message

def test_handle_error():
    """Test error handling decorator."""
    @handle_error
    def failing_function():
        raise ValueError("Test error")

    with patch('logging.getLogger') as mock_logger:
        result = failing_function()
        assert result is None
        mock_logger.return_value.error.assert_called_once()
        error_message = mock_logger.return_value.error.call_args[0][0]
        assert "failing_function" in error_message
        assert "Test error" in error_message
