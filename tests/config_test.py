# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test cases for the config module."""

import unittest
from unittest.mock import patch, mock_open
from ads_mcp.config import ToolsConfig


class TestToolsConfig(unittest.TestCase):
    """Test cases for ToolsConfig parser."""

    def test_default_config_exposes_all_with_default_namespaces(self):
        """Tests that if no config is supplied, all default namespaces are enabled."""
        config = ToolsConfig()
        self.assertTrue(config.is_namespace_enabled("customers"))
        self.assertTrue(config.is_namespace_enabled("search"))
        self.assertTrue(config.is_namespace_enabled("metadata"))
        self.assertFalse(config.is_namespace_enabled("unknown_category"))

        self.assertEqual(config.get_namespace_prefix("customers"), "customers")
        self.assertEqual(config.get_namespace_prefix("search"), "search")

        self.assertTrue(config.is_tool_enabled("search", "search"))

    def test_boolean_namespaces(self):
        """Tests namespaces enabled via simple booleans."""
        data = {
            "namespaces": {
                "customers": True,
                "search": False,
            }
        }
        config = ToolsConfig(data)
        self.assertTrue(config.is_namespace_enabled("customers"))
        self.assertFalse(config.is_namespace_enabled("search"))
        # Not listed => False by default if namespaces dict is present
        self.assertFalse(config.is_namespace_enabled("metadata"))

        self.assertEqual(config.get_namespace_prefix("customers"), "customers")
        self.assertTrue(
            config.is_tool_enabled("customers", "list_accessible_customers")
        )
        self.assertFalse(config.is_tool_enabled("search", "search"))

    def test_custom_namespace_prefixes(self):
        """Tests namespaces configured with custom prefix strings."""
        data = {
            "namespaces": {
                "customers": "accounts",
                "search": "query_engine",
            }
        }
        config = ToolsConfig(data)
        self.assertTrue(config.is_namespace_enabled("customers"))
        self.assertTrue(config.is_namespace_enabled("search"))

        self.assertEqual(config.get_namespace_prefix("customers"), "accounts")
        self.assertEqual(config.get_namespace_prefix("search"), "query_engine")

    def test_fine_grained_tool_enablement(self):
        """Tests selectively enabling/disabling specific tools under a namespace."""
        data = {
            "namespaces": {
                "customers": {
                    "enabled": True,
                    "prefix": "users",
                    "enabled_tools": [
                        {"list_accessible_customers": True},
                        {"another_tool": False},
                    ],
                },
                "search": {
                    "enabled": True,
                    # No prefix specified, defaults to category name
                    "enabled_tools": ["search"],
                },
            }
        }
        config = ToolsConfig(data)
        self.assertTrue(config.is_namespace_enabled("customers"))
        self.assertTrue(config.is_namespace_enabled("search"))

        self.assertEqual(config.get_namespace_prefix("customers"), "users")
        self.assertEqual(config.get_namespace_prefix("search"), "search")

        self.assertTrue(
            config.is_tool_enabled("customers", "list_accessible_customers")
        )
        self.assertFalse(config.is_tool_enabled("customers", "another_tool"))
        self.assertFalse(config.is_tool_enabled("customers", "unlisted_tool"))

        self.assertTrue(config.is_tool_enabled("search", "search"))
        self.assertFalse(
            config.is_tool_enabled("search", "unlisted_search_tool")
        )

    @patch("os.path.exists")
    def test_load_missing_file_raises_file_not_found(self, mock_exists):
        """Tests that load raises FileNotFoundError if the config file does not exist."""
        mock_exists.return_value = False
        with self.assertRaises(FileNotFoundError):
            ToolsConfig.load("missing.yaml")

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="invalid-yaml: {")
    def test_load_invalid_file_raises_value_error(self, mock_file, mock_exists):
        """Tests that load raises ValueError if the config file contains invalid YAML."""
        mock_exists.return_value = True
        with self.assertRaises(ValueError):
            ToolsConfig.load("invalid.yaml")
