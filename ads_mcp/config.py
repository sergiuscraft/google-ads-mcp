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

"""Configuration management for the Google Ads MCP server."""

import os
from typing import Any, Dict, List, Union
import yaml
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "tools_config.yaml"

# Default categories that are supported by the server
ALL_CATEGORIES = ["customers", "search", "metadata"]


class ToolsConfig:
    """Manages tool registration configuration parsed from YAML."""

    def __init__(self, config_dict: Dict[str, Any] | None = None):
        self._config = config_dict or {}

    @classmethod
    def load(cls, filepath: str = DEFAULT_CONFIG_FILE) -> "ToolsConfig":
        """Loads configuration from a YAML file. Raises an exception if missing or corrupt."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(
                f"Mandatory tools configuration file '{filepath}' not found. "
                f"Please create this file or copy 'tools_config.yaml.example' to '{filepath}'."
            )

        try:
            with open(filepath, "r") as file:
                data = yaml.safe_load(file)
                if not isinstance(data, dict):
                    raise ValueError(
                        "Configuration root must be a YAML mapping/dictionary"
                    )
                return cls(data)
        except Exception as e:
            raise ValueError(
                f"Failed to parse configuration file '{filepath}': {e}"
            ) from e

    def is_namespace_enabled(self, category: str) -> bool:
        """Determines if a tool category/namespace is enabled."""
        namespaces = self._config.get("namespaces", {})
        if not namespaces:
            # By default, if no config is specified, all known categories are enabled
            return category in ALL_CATEGORIES

        category_config = namespaces.get(category)
        if category_config is None:
            return False

        if isinstance(category_config, bool):
            return category_config

        if isinstance(category_config, str):
            return True

        if isinstance(category_config, dict):
            return category_config.get("enabled", True)

        return False

    def get_namespace_prefix(self, category: str) -> str | None:
        """Returns the prefix/namespace to use for the category.

        Returns None if no prefix should be applied.
        """
        namespaces = self._config.get("namespaces", {})
        if not namespaces:
            return category

        category_config = namespaces.get(category)
        if isinstance(category_config, str):
            return category_config

        if isinstance(category_config, dict):
            # If explicit prefix is given, use it
            if "prefix" in category_config:
                return category_config["prefix"]
            # Default to category name if enabled_tools dict is provided
            return category

        if category_config is True:
            return category

        return None

    def is_tool_enabled(self, category: str, tool_name: str) -> bool:
        """Determines if a specific tool within a category is enabled."""
        if not self.is_namespace_enabled(category):
            return False

        namespaces = self._config.get("namespaces", {})
        if not namespaces:
            return True

        category_config = namespaces.get(category)
        if not isinstance(category_config, dict):
            # If category is enabled as a simple boolean or string, all tools in it are enabled
            return True

        enabled_tools = category_config.get("enabled_tools")
        if enabled_tools is None:
            # No explicit enabled_tools filter means all are enabled
            return True

        # Handle list of dictionaries or list of strings
        # Format from proposal:
        # enabled_tools:
        #   - create_asset: true
        #   - upload_video: true
        if isinstance(enabled_tools, list):
            for item in enabled_tools:
                if isinstance(item, dict):
                    if tool_name in item:
                        return bool(item[tool_name])
                elif isinstance(item, str):
                    if item == tool_name:
                        return True
            return False

        return True
