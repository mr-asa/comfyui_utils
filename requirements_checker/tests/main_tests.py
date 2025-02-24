"""
Test suite for main module.

Tests the main execution flow and integration between components.
"""

import pytest
from unittest.mock import patch, MagicMock
from collections import OrderedDict

from requirements_checker.main import (
    process_package,
    main,
    process_requirements_file
)

@pytest.fixture
def mock_environment():
    """Set up mock environment for tests."""
    with patch('requirements_checker.main.config_manager') as mock_config, \
         patch('requirements_checker.main.activate_virtual_environment') as mock_activate, \
         patch('requirements_checker.main.get_installed_version') as mock_installed, \
         patch('requirements_checker.main.get_latest_version') as mock_latest, \
         patch('requirements_checker.main.get_all_versions') as mock_versions:
        
        mock_config.get_value.return_value = '/test/path'
        mock_installed.return_value = '1.0.0'
        mock_latest.return_value = '1.1.0'
        mock_versions.return_value = ['1.1.0', '1.0.0', '0.9.0']
        
        yield {
            'config': mock_config,
            'activate': mock_activate,
            'installed': mock_installed,
            'latest': mock_latest,
            'versions': mock_versions
        }

def test_process_package(mock_environment):
    """Test package processing function."""
    test_values = [
        [None, '>=', '1.0.0', 'test_dir'],
        [None, None, None, 'another_dir']
    ]
    test_versions = ['1.1.0', '1.0.0', '0.9.0']

    with patch('builtins.print') as mock_print:
        process_package('test-package', test_values, test_versions)
        
        # Verify that version checks were performed
        mock_environment['installed'].assert_called_once_with('test-package')
        mock_environment['latest'].assert_called_once_with('test-package')
        
        # Verify output was printed
        assert mock_print.call_count > 0
        printed_messages = [str(call[0][0]) for call in mock_print.call_args_list]
        assert any('test-package' in msg for msg in printed_messages)

@patch('requirements_checker.main.process_requirements_file')
@patch('os.walk')
def test_main_requirements_processing(mock_walk, mock_process_file, mock_environment):
    """Test main function's requirements processing."""
    mock_walk.return_value = [
        ('/test/path', ['dir1'], ['requirements.txt']),
        ('/test/path/dir1', [], ['other.txt'])
    ]

    with patch('builtins.input') as mock_input:
        mock_input.return_value = ''
        main()
        
        # Verify environment was activated
        mock_environment['activate'].assert_called_once()
        
        # Verify requirements file was processed
        mock_process_file.assert_called_once()

@pytest.mark.parametrize("test_input,expected", [
    (
        {"test-package": [None, ">=", "1.0.0", "test_dir"]},
        {"test-package": ["1.0.0"]}
    ),
    (
        {"git": [None, "+", "https://github.com/test/repo.git", "test_dir"]},
        {"git": ["https://github.com/test/repo.git"]}
    )
])
def test_process_requirements_file(test_input, expected):
    """Test requirements file processing."""
    requirements_dict = OrderedDict()
    
    with patch('requirements_checker.main.get_active_requirements') as mock_get_reqs, \
         patch('requirements_checker.main.parse_conditional_dependencies') as mock_parse:
        
        mock_get_reqs.return_value = list(test_input.keys())
        mock_parse.return_value = test_input
        
        process_requirements_file('/test/path', 'requirements.txt', requirements_dict)
        
        assert requirements_dict == expected

def test_main_error_handling():
    """Test error handling in main function."""
    with patch('requirements_checker.main.config_manager.get_value') as mock_config:
        mock_config.side_effect = Exception("Test error")
        
        with patch('builtins.print') as mock_print, \
             patch('builtins.input') as mock_input:
            mock_input.return_value = ''
            main()
            
            # Verify error was printed
            error_printed = any(
                'Test error' in str(call[0][0])
                for call in mock_print.call_args_list
            )
            assert error_printed

@pytest.mark.parametrize("package_name,values,expected_state", [
    ("test-package", [[None, ">=", "1.0.0", "test_dir"]], ">=1.0.0"),
    ("git", [[None, "+", "https://github.com/test/repo.git", "test_dir"]], "custom"),
    ("--extra-index-url", [[None, " ", "https://test.pypi.org", "test_dir"]], "custom")
])
def test_package_processing_types(package_name, values, expected_state):
    """Test processing of different package types."""
    with patch('builtins.print') as mock_print:
        if package_name in ["git", "--extra-index-url"]:
            process_custom_entry(package_name, values)
        else:
            process_package(package_name, values, ["1.1.0", "1.0.0"])
        
        assert mock_print.call_count > 0
