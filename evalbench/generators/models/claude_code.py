from .generator import QueryGenerator
import subprocess
import os
import json
import logging
import shlex
import sys
import re
import shutil


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, session_id=None, allowedTools=None):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.session_id = session_id
        self.allowedTools = allowedTools


class ClaudeCodeGenerator(QueryGenerator):
    """Generator queries using Claude Code CLI."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "claude_code"

        self.real_home = os.environ.get("HOME", os.path.expanduser("~"))

        # If running via eval_server.py (gRPC), use session-specific path in shared volume
        if sys.argv[0].endswith("eval_server.py"):
            session_id = querygenerator_config.get("session_id", "default")
            self.fake_home = os.path.join(
                "/tmp_sessions", session_id, "fake_home")
        else:
            self.fake_home = os.path.abspath(
                os.path.join(".venv", "fake_home_claude"))

        self.claude_config_dir = os.path.join(self.fake_home, ".claude")

        os.makedirs(self.fake_home, exist_ok=True)
        os.makedirs(self.claude_config_dir, exist_ok=True)

        # When running as root, chown fake_home so the non-root claudeuser
        # (used to run Claude Code) can write to it.
        self._chown_for_claudeuser = os.getuid() == 0

        self.env = querygenerator_config.get("env", {})
        self.env["HOME"] = self.fake_home
        self.env["IS_SANDBOX"] = "1"

        api_key = self.env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

        if api_key:
            self.env["ANTHROPIC_API_KEY"] = api_key
            self.use_vertex = False
            self.env.pop("CLAUDE_CODE_USE_VERTEX", None)
        else:
            self.use_vertex = querygenerator_config.get("use_vertex", False)
            if self.use_vertex:
                self.env["CLAUDE_CODE_USE_VERTEX"] = "1"
                vertex_project = querygenerator_config.get(
                    "vertex_project_id"
                ) or self.env.get("ANTHROPIC_VERTEX_PROJECT_ID") or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
                if vertex_project:
                    self.env["ANTHROPIC_VERTEX_PROJECT_ID"] = vertex_project

                vertex_region = querygenerator_config.get(
                    "vertex_region"
                ) or self.env.get("CLOUD_ML_REGION") or os.environ.get("CLOUD_ML_REGION")
                if vertex_region:
                    self.env["CLOUD_ML_REGION"] = vertex_region

                # Skip ADC setup if Service Account key is available
                if not os.path.exists("/etc/evalbench-sa-key/key.json"):
                    real_gcloud_dir = os.path.join(self.real_home, ".config", "gcloud")
                    fake_gcloud_dir = os.path.join(self.fake_home, ".config", "gcloud")

                    # Copy entire gcloud config directory to fake home
                    # This includes credentials.db, access_tokens.db, and other auth data
                    if os.path.exists(real_gcloud_dir):
                        os.makedirs(os.path.dirname(fake_gcloud_dir), exist_ok=True)
                        if os.path.exists(fake_gcloud_dir):
                            shutil.rmtree(fake_gcloud_dir)
                        try:
                            shutil.copytree(real_gcloud_dir, fake_gcloud_dir,
                                           ignore=shutil.ignore_patterns('logs', '*.db-wal'))
                            logging.info(f"Copied gcloud config from {real_gcloud_dir} to {fake_gcloud_dir}")
                        except (OSError, PermissionError) as e:
                            logging.warning(f"Could not copy gcloud directory: {e}")

                    if "CLOUDSDK_CONFIG" not in self.env:
                        self.env["CLOUDSDK_CONFIG"] = fake_gcloud_dir

                    # Set GOOGLE_APPLICATION_CREDENTIALS to help toolbox SDK find credentials
                    fake_adc_path = os.path.join(fake_gcloud_dir, "application_default_credentials.json")
                    if os.path.exists(fake_adc_path) and "GOOGLE_APPLICATION_CREDENTIALS" not in self.env:
                        self.env["GOOGLE_APPLICATION_CREDENTIALS"] = fake_adc_path
                        logging.info(f"Set GOOGLE_APPLICATION_CREDENTIALS: {fake_adc_path}")
                else:
                    # Explicitly set GOOGLE_APPLICATION_CREDENTIALS for Claude if secret is mounted
                    self.env["GOOGLE_APPLICATION_CREDENTIALS"] = "/etc/evalbench-sa-key/key.json"

        # Copy Claude Code auth credentials from real home to fake home
        # so the CLI can authenticate in the sandboxed environment
        real_claude_dir = os.path.join(self.real_home, ".claude")
        if os.path.exists(real_claude_dir):
            for fname in os.listdir(real_claude_dir):
                src = os.path.join(real_claude_dir, fname)
                dst = os.path.join(self.claude_config_dir, fname)
                if os.path.isfile(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)

        self.claude_code_version = querygenerator_config.get(
            "claude_code_version", "claude"
        )
        self.model = querygenerator_config.get("model")
        self.allowed_tools = querygenerator_config.get("allowed_tools")

        self.setup_config = querygenerator_config.get("setup", {})
        if self.setup_config:
            self._setup()

    def _setup(self):
        """Performs initial setup for Claude Code CLI.

        Skills can be set up in two ways:
        1. install_from_repo: Clone skills from a git repository (with optional version tag)
           Example: {action: "install_from_repo", url: "https://github.com/repo.git#v1.0.0"}
        2. skills_dir: Copy skills from a local directory
           Example: skills_dir: "/path/to/skills"
        """
        # Setup MCP Servers
        mcp_servers_config = self.setup_config.get("mcp_servers", {})
        if mcp_servers_config:
            self._setup_mcp_servers(mcp_servers_config)

        settings_config = self.setup_config.get("settings", {})
        if settings_config:
            self._setup_settings(settings_config)

        # Setup Plugin Marketplace
        plugins_config = self.setup_config.get("plugins", [])
        if plugins_config:
            self._setup_plugins(plugins_config)

        # Setup Skills - Method 1: From repository
        skills_config = self.setup_config.get("skills", [])
        if skills_config:
            self._setup_skills(skills_config)

        # Setup Skills - Method 2: From directory
        skills_dir_path = self.setup_config.get("skills_dir")
        if skills_dir_path:
            self._setup_skills_from_dir(skills_dir_path)

    def _setup_mcp_servers(self, mcp_servers_config: dict):
        """Configures MCP servers in a JSON config file for Claude Code.

        Supports the same config shape as Gemini CLI for HTTP MCP servers:
          httpUrl:           URL (translated to Claude Code's `url` + `type: "http"`)
          authProviderType:  "google_credentials" -> injects `gcloud auth print-access-token` as Bearer
          headers:           passed through as-is

        Stdio servers (command/args) are passed through unchanged.
        """
        mcp_config = {"mcpServers": {}}

        for server_name, config in mcp_servers_config.items():
            mcp_config["mcpServers"][server_name] = self._translate_mcp_config(
                server_name, dict(config)
            )

        self.mcp_config_path = os.path.join(
            self.claude_config_dir, "mcp_servers.json")
        with open(self.mcp_config_path, "w") as f:
            json.dump(mcp_config, f, indent=2)

        logging.info(f"MCP server config written to {self.mcp_config_path}")

    def _translate_mcp_config(self, server_name: str, config: dict) -> dict:
        """Translates a Gemini-style MCP server config into Claude Code format."""
        # Stdio server (command + args): pass through as-is
        if "command" in config:
            return config

        # HTTP/SSE server: translate Gemini-style `httpUrl` -> `url` + `type`
        if "httpUrl" in config and "url" not in config:
            config["url"] = config.pop("httpUrl")
        if "url" in config and "type" not in config:
            config["type"] = "http"

        # Translate `authProviderType: google_credentials` into Bearer header
        auth_provider = config.pop("authProviderType", None)
        # Gemini-style `oauth.scopes` is ignored by Claude Code; drop it
        config.pop("oauth", None)

        if auth_provider == "google_credentials":
            headers = config.get("headers", {}) or {}
            if "Authorization" not in headers:
                token = self._fetch_gcloud_access_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                else:
                    logging.warning(
                        f"MCP server '{server_name}' requires google_credentials but "
                        "failed to fetch access token via `gcloud auth print-access-token`."
                    )
            config["headers"] = headers

        return config

    def _fetch_gcloud_access_token(self) -> str:
        """Fetches a Google Cloud access token via gcloud."""
        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logging.error(f"Failed to retrieve gcloud access token: {e}")
            return ""

    def _setup_plugins(self, plugins_config: list):
        """Sets up Claude Code plugins from marketplace URLs.

        Note: Plugin marketplace add/install commands are interactive and
        should be run manually in Claude Code:
          /plugin marketplace add <marketplace_url>
          /plugin install <name>@<marketplace_name>

        This method just documents the plugin configuration.
        For automated skill setup, use install_from_repo instead.
        """
        for plugin_config in plugins_config:
            if isinstance(plugin_config, dict):
                marketplace_url = plugin_config.get("marketplace_url")
                name = plugin_config.get("name")
                if marketplace_url and name:
                    logging.info(
                        f"Configured plugin: {name}\n"
                        f"  Marketplace: {marketplace_url}\n"
                        f"  Manual setup (run in Claude Code):\n"
                        f"    /plugin marketplace add {marketplace_url}\n"
                        f"    /plugin install {name}@{name}-marketplace"
                    )

    def _setup_settings(self, settings_config: dict):
        """Writes Claude Code settings.json."""
        settings_path = os.path.join(self.claude_config_dir, "settings.json")

        current_settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f:
                    current_settings = json.load(f)
            except json.JSONDecodeError as e:
                logging.warning(
                    f"Invalid JSON in Claude Code settings at {settings_path}; "
                    f"proceeding with empty settings. Error: {e}"
                )

        current_settings.update(settings_config)

        with open(settings_path, "w") as f:
            json.dump(current_settings, f, indent=2)

        logging.info(f"Claude Code settings written to {settings_path}")

    def _setup_skills(self, skills: list):
        """Sets up skills from repository URL or copies from a provided skills directory.

        Supported configurations:
        - action: install_from_repo, url: "https://...repo.git" or "https://...repo.git#version"
        """
        claude_skills_dir = os.path.join(self.claude_config_dir, "skills")
        os.makedirs(claude_skills_dir, exist_ok=True)
        setup_env = os.environ.copy()
        setup_env.update(self.env)

        self.plugin_registrations = []  # Track plugins for registration

        for skill_config in skills:
            if isinstance(skill_config, dict):
                action = skill_config.get("action")
                url = skill_config.get("url")

                if action == "install_from_repo" and url:
                    if os.path.exists(claude_skills_dir):
                        for entry in os.listdir(claude_skills_dir):
                            if entry != "_repos":
                                entry_path = os.path.join(claude_skills_dir, entry)
                                if os.path.isdir(entry_path):
                                    shutil.rmtree(entry_path)
                                    logging.info(f"Cleared old skill: {entry}")
                    plugin_info = self._install_skills_from_repo(url, claude_skills_dir, setup_env)
                    if plugin_info:
                        self.plugin_registrations.append(plugin_info)
                else:
                    logging.warning(f"Unsupported skill config: {skill_config}. "
                                  "Only 'action: install_from_repo' with 'url' is supported.")

    def _install_skills_from_repo(self, url: str, target_dir: str, env: dict) -> dict | None:
        """Clones a skills repository and links each skill found in it.

        Supports versioned URLs in the format: https://...repo.git#version
        where #version is a tag/branch/release to checkout.

        Returns plugin registration info (marketplace_url, plugin_name) if found, else None.
        """
        # Extract version tag if present (format: url#version)
        version_tag = None
        clone_url = url
        if "#" in url:
            clone_url, version_tag = url.rsplit("#", 1)

        repo_name = re.sub(r"\.git$", "", clone_url.rstrip("/").split("/")[-1])
        clone_target = os.path.join(target_dir, "_repos", repo_name)
        os.makedirs(os.path.dirname(clone_target), exist_ok=True)
        if os.path.exists(clone_target):
            shutil.rmtree(clone_target)
        try:
            # Build git clone command with optional --branch parameter
            cmd = ["git", "clone", "--depth", "1"]
            if version_tag:
                cmd.extend(["--branch", version_tag])
                logging.info(f"Cloning repo with version tag: {version_tag}")
            cmd.extend([clone_url, clone_target])

            result = subprocess.run(
                cmd,
                capture_output=True, text=True, check=False, env=env, timeout=120,
            )
            if result.returncode != 0:
                logging.error(f"Failed to clone repo '{url}'. Error: {result.stderr}")
                return None

            # Extract plugin metadata from .claude-plugin directory
            plugin_info = self._extract_plugin_metadata(clone_target, clone_url, version_tag)

            skills_root = clone_target
            skills_subdir = os.path.join(clone_target, "skills")
            if os.path.isdir(skills_subdir):
                skills_root = skills_subdir
                logging.info(f"Found skills in subdirectory: {skills_subdir}")
            for entry in os.listdir(skills_root):
                entry_path = os.path.join(skills_root, entry)
                if os.path.isdir(entry_path) and os.path.exists(os.path.join(entry_path, "SKILL.md")):
                    dst = os.path.join(target_dir, entry)
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(entry_path, dst)
                    logging.info(f"Installed skill from cloned repo: {entry}")

            return plugin_info
        except subprocess.TimeoutExpired:
            logging.error(f"Cloning repo '{url}' timed out.")
            return None
        except Exception as e:
            logging.error(f"Failed to install skills from repo '{url}': {e}")
            return None

    def _extract_plugin_metadata(self, clone_target: str, clone_url: str, version_tag: str | None) -> dict | None:
        """Extracts plugin metadata from .claude-plugin directory.

        Returns {"marketplace_url": ..., "plugin_name": ..., "marketplace_name": ...} or None.
        """
        claude_plugin_dir = os.path.join(clone_target, ".claude-plugin")
        if not os.path.isdir(claude_plugin_dir):
            logging.debug(f"No .claude-plugin directory found in {clone_target}")
            return None

        marketplace_json_path = os.path.join(claude_plugin_dir, "marketplace.json")
        if not os.path.exists(marketplace_json_path):
            logging.debug(f"No marketplace.json found in {claude_plugin_dir}")
            return None

        try:
            with open(marketplace_json_path, "r", encoding="utf-8") as f:
                marketplace_data = json.load(f)

            # Extract marketplace name and plugin name from marketplace.json
            marketplace_name = marketplace_data.get("name", "")
            plugins = marketplace_data.get("plugins", [])
            if not plugins or not isinstance(plugins, list):
                logging.warning("No plugins found in marketplace.json")
                return None

            # First plugin is the main one
            plugin_ref = plugins[0]
            if isinstance(plugin_ref, dict):
                plugin_name = plugin_ref.get("name", "")
            else:
                plugin_name = str(plugin_ref)

            if not plugin_name or not marketplace_name:
                logging.warning("Missing plugin_name or marketplace_name in marketplace.json")
                return None

            # Build marketplace URL with version tag if provided
            marketplace_url = clone_url
            if version_tag:
                marketplace_url = f"{clone_url}#{version_tag}"

            return {
                "marketplace_url": marketplace_url,
                "plugin_name": plugin_name,
                "marketplace_name": marketplace_name,
            }
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Failed to read marketplace.json: {e}")
            return None

    def _setup_skills_from_dir(self, skills_dir_path: str):
        """Installs all skills from a local directory."""
        if not os.path.isdir(skills_dir_path):
            logging.warning(f"Skills directory not found: {skills_dir_path}")
            return
        claude_skills_dir = os.path.join(self.claude_config_dir, "skills")
        os.makedirs(claude_skills_dir, exist_ok=True)
        for entry in os.listdir(skills_dir_path):
            entry_path = os.path.join(skills_dir_path, entry)
            if os.path.isdir(entry_path) and os.path.exists(os.path.join(entry_path, "SKILL.md")):
                dst = os.path.join(claude_skills_dir, entry)
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(entry_path, dst)
                logging.info(f"Linked skill from skills_dir: {entry}")

    def _register_plugins(self):
        """Registers plugins with Claude Code using /plugin commands via stdin.

        Must be called before first generation to ensure plugins are available.
        Runs Claude Code interactively with /plugin marketplace add and /plugin install commands.
        """
        if not hasattr(self, "plugin_registrations") or not self.plugin_registrations:
            logging.debug("No plugins to register")
            return

        logging.info(f"Registering {len(self.plugin_registrations)} plugin(s)")

        # Build registration commands for all plugins
        registration_commands = []
        for plugin_info in self.plugin_registrations:
            marketplace_url = plugin_info.get("marketplace_url")
            plugin_name = plugin_info.get("plugin_name")
            marketplace_name = plugin_info.get("marketplace_name")

            if marketplace_url and plugin_name and marketplace_name:
                registration_commands.append(f"/plugin marketplace add {marketplace_url}")
                registration_commands.append(f"/plugin install {plugin_name}@{marketplace_name}")
                logging.info(f"Registering plugin: {plugin_name} from {marketplace_url}")

        if not registration_commands:
            logging.debug("No valid plugin registrations found")
            return

        # Run Claude Code interactively with stdin input for /plugin commands
        try:
            logging.info("Running plugin registration via interactive session")
            env = os.environ.copy()
            env.update(self.env)

            cli = self.claude_code_version
            if cli.startswith("@") or "/" in cli:
                command = ["npm", "exec", "--yes", cli, "--"]
            else:
                command = [cli]

            command.extend(["--dangerously-skip-permissions"])

            # Send /plugin commands via stdin and exit with Ctrl+D
            stdin_input = "\n".join(registration_commands) + "\nexit\n"

            logging.info(f"Running Claude Code CLI for plugin registration: {' '.join(command)}")
            result = subprocess.run(
                command,
                input=stdin_input,
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            logging.info("Plugin registration session output:")
            for line in result.stdout.split("\n")[:50]:
                if line:
                    logging.info(f"  {line}")
            if result.returncode != 0 and result.stderr:
                logging.warning(f"Plugin registration stderr: {result.stderr[:500]}")
            logging.info("Plugin registration completed")
        except subprocess.TimeoutExpired:
            logging.warning("Plugin registration timed out (non-critical)")
        except Exception as e:
            logging.warning(f"Plugin registration failed (non-critical): {e}")

    def generate_internal(self, cli_cmd):
        # Register plugins once on first generation
        if not getattr(self, "_plugins_registered", False):
            self._register_plugins()
            self._plugins_registered = True

        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.claude_code_version, str(cli_cmd))
        return self._run_claude_code(cli_cmd)

    def _execute_cli_command(
        self, command: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, env=env,
                stdin=subprocess.DEVNULL
            )
            return result
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                command, 127, "", f"Error: Command not found: {command[0]}"
            )
        except Exception as e:
            return subprocess.CompletedProcess(
                command, 1, "", f"An unexpected error occurred: {e}"
            )

    def _run_claude_code(self, cli_cmd: CLICommand):
        env = os.environ.copy()
        env.update(self.env)
        env.update(cli_cmd.env)

        # If the version looks like an npm package spec (contains "/" or starts
        # with "@"), use `npm exec` to pin that version (like Gemini CLI does).
        # Otherwise, invoke the binary directly (e.g. "claude").
        cli = cli_cmd.cli
        if cli.startswith("@") or "/" in cli:
            command = ["npm", "exec", "--yes", cli, "--"]
        else:
            command = [cli]

        # -p "prompt" for non-interactive single-prompt mode
        command.extend(["-p", cli_cmd.prompt])

        # Auto-accept all tool uses (like --yolo in Gemini CLI)
        command.append("--dangerously-skip-permissions")

        # Output format (stream-json requires --verbose with --print)
        command.extend(["--output-format", "stream-json", "--verbose"])

        # Model override
        model = self.model
        if model:
            command.extend(["--model", model])

        # MCP server config
        if hasattr(self, "mcp_config_path") and os.path.exists(self.mcp_config_path):
            command.extend(["--mcp-config", self.mcp_config_path])

        # Resume session: `--resume <session_id>` takes the UUID as its value.
        # `--fork-session` creates a new session ID from the resumed history,
        # which is required when scenarios run concurrently (they share the
        # same sandboxed ~/.claude session store and otherwise conflict).
        if cli_cmd.resume and cli_cmd.session_id:
            command.extend(["--resume", cli_cmd.session_id, "--fork-session"])

        # Allowed tools
        allowed_tools = cli_cmd.allowedTools or self.allowed_tools
        if allowed_tools:
            for tool in allowed_tools:
                command.extend(["--allowedTools", tool])

        # Claude Code refuses --dangerously-skip-permissions when running as
        # root.  Wrap with `su` to drop privileges to a non-root user.
        # Recursively chown the fake_home so claudeuser can write to it
        # (covers .claude dir, gcloud creds, MCP config copied during init).

        logging.info(f"Running Claude Code CLI: {' '.join(command)}")

        result = self._execute_cli_command(command, env=env)
        if result.stdout:
            result.stdout = self._parse_stream_json(result.stdout)

        return result

    def _parse_stream_json(self, stream_output: str) -> str:
        """Parses Claude Code stream-json output into a normalized format
        compatible with the eval pipeline."""

        final_obj = {"session_id": "", "response": "", "stats": {}}
        tool_uses = {}
        tool_results = {}
        # Fall back to the configured model if the stream's `system` init
        # event doesn't include one (e.g., truncated output).
        model_name = self.model or "unknown"

        for line in stream_output.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("type")

                if event_type == "system":
                    final_obj["session_id"] = event.get("session_id", "")
                    if "model" in event:
                        model_name = event["model"]

                elif event_type == "assistant":
                    # Assistant message with content blocks
                    message = event.get("message", {})
                    content_blocks = message.get("content", [])
                    for block in content_blocks:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type == "text":
                            final_obj["response"] += block.get("text", "")
                        elif block_type == "tool_use":
                            tool_id = block.get("id", "")
                            tool_uses[tool_id] = {
                                "tool_name": block.get("name", "unknown"),
                                "parameters": block.get("input", {}),
                            }

                elif event_type == "tool_result":
                    tool_id = event.get("tool_use_id") or event.get("id", "")
                    is_error = event.get("is_error", False)
                    tool_results[tool_id] = {
                        "status": "error" if is_error else "success",
                        "content": event.get("content", ""),
                    }

                elif event_type == "result":
                    if "session_id" in event:
                        final_obj["session_id"] = event["session_id"]

                    # Use result.usage for authoritative token counts
                    usage = event.get("usage", {})
                    total_input_tokens = usage.get("input_tokens", 0)
                    total_output_tokens = usage.get("output_tokens", 0)
                    total_cache_read = usage.get("cache_read_input_tokens", 0)
                    total_cache_creation = usage.get(
                        "cache_creation_input_tokens", 0)
                    total_tokens = total_input_tokens + total_output_tokens

                    duration_ms = event.get("duration_ms", 0)
                    cost_usd = event.get("total_cost_usd", 0)

                    # Use modelUsage if available for per-model breakdown
                    model_usage = event.get("modelUsage", {})

                    models = {}
                    if model_usage:
                        for m_name, m_stats in model_usage.items():
                            m_input = m_stats.get("inputTokens", 0)
                            m_output = m_stats.get("outputTokens", 0)
                            m_cached = m_stats.get("cacheReadInputTokens", 0)
                            m_cache_creation = m_stats.get(
                                "cacheCreationInputTokens", 0)
                            models[m_name] = {
                                "api": {
                                    "totalRequests": 1,
                                    "totalErrors": 0,
                                    "totalLatencyMs": duration_ms,
                                },
                                "tokens": {
                                    "input": m_input,
                                    "prompt": m_input,
                                    "candidates": m_output,
                                    "total": m_input + m_output,
                                    "cached": m_cached,
                                    "cache_creation": m_cache_creation,
                                    "thoughts": 0,
                                    "tool": 0,
                                },
                                "cost_usd": m_stats.get("costUSD", 0),
                                "roles": {
                                    "main": {
                                        "totalRequests": 1,
                                        "totalErrors": 0,
                                        "totalLatencyMs": duration_ms,
                                        "tokens": {
                                            "input": m_input,
                                            "prompt": m_input,
                                            "candidates": m_output,
                                            "total": m_input + m_output,
                                            "cached": m_cached,
                                            "thoughts": 0,
                                            "tool": 0,
                                        },
                                    }
                                },
                            }
                    else:
                        models[model_name] = {
                            "api": {
                                "totalRequests": 1,
                                "totalErrors": 0,
                                "totalLatencyMs": duration_ms,
                            },
                            "tokens": {
                                "input": total_input_tokens,
                                "prompt": total_input_tokens,
                                "candidates": total_output_tokens,
                                "total": total_tokens,
                                "cached": total_cache_read,
                                "cache_creation": total_cache_creation,
                                "thoughts": 0,
                                "tool": 0,
                            },
                            "cost_usd": cost_usd,
                            "roles": {
                                "main": {
                                    "totalRequests": 1,
                                    "totalErrors": 0,
                                    "totalLatencyMs": duration_ms,
                                    "tokens": {
                                        "input": total_input_tokens,
                                        "prompt": total_input_tokens,
                                        "candidates": total_output_tokens,
                                        "total": total_tokens,
                                        "cached": total_cache_read,
                                        "thoughts": 0,
                                        "tool": 0,
                                    },
                                }
                            },
                        }
                    final_obj["stats"]["models"] = models

                    # Build tool stats
                    tools_stats = {
                        "totalCalls": len(tool_uses),
                        "totalSuccess": sum(
                            1
                            for tr in tool_results.values()
                            if tr.get("status") == "success"
                        ),
                        "totalFail": sum(
                            1
                            for tr in tool_results.values()
                            if tr.get("status") != "success"
                        ),
                        "totalDurationMs": 0,
                        "decisions": {
                            "accept": len(tool_uses),
                            "reject": 0,
                            "modify": 0,
                            "auto_accept": len(tool_uses),
                        },
                        "byName": {},
                    }

                    for tid, tu in tool_uses.items():
                        tname = tu.get("tool_name", "unknown")
                        if tname not in tools_stats["byName"]:
                            tools_stats["byName"][tname] = {
                                "count": 0,
                                "success": 0,
                                "fail": 0,
                                "durationMs": 0,
                                "parameters": [],
                                "decisions": {
                                    "accept": 0,
                                    "reject": 0,
                                    "modify": 0,
                                    "auto_accept": 0,
                                },
                            }

                        tstat = tools_stats["byName"][tname]
                        tstat["count"] += 1
                        tstat["parameters"].append(tu.get("parameters", {}))
                        tstat["decisions"]["accept"] += 1
                        tstat["decisions"]["auto_accept"] += 1

                        tr = tool_results.get(tid)
                        if tr:
                            if tr.get("status") == "success":
                                tstat["success"] += 1
                            else:
                                tstat["fail"] += 1

                    final_obj["stats"]["tools"] = tools_stats

                    # Store the final text response from result if not yet captured
                    if not final_obj["response"] and event.get("result"):
                        final_obj["response"] = event["result"]

            except Exception as e:
                logging.debug(f"Failed to parse stream JSON line: {e}")

        return json.dumps(final_obj, indent=2)

    def parse_response(self, stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            logging.error(f"Failed to parse JSON response: {stdout[:100]}...")
            return {}

    def extract_tools(self, stdout: str) -> list[str]:
        """Extracts the list of tools used from the CLI output."""
        output_json = self.parse_response(stdout)
        if (
            "stats" in output_json
            and "tools" in output_json["stats"]
            and "byName" in output_json["stats"]["tools"]
        ):
            return list(output_json["stats"]["tools"]["byName"].keys())
        return []

    def _get_installed_skills(self) -> set[str]:
        """Returns set of installed skill directory names."""
        claude_skills_dir = os.path.join(self.claude_config_dir, "skills")
        installed_skills = set()
        if os.path.isdir(claude_skills_dir):
            for entry in os.listdir(claude_skills_dir):
                if os.path.isdir(os.path.join(claude_skills_dir, entry)):
                    installed_skills.add(entry)
        return installed_skills

    def _extract_script_names(self, by_name: dict) -> list[str]:
        """Extracts script names from bash tool executions (e.g., list_instances.js)."""
        scripts = []
        bash_tool = by_name.get("bash", {})
        if bash_tool and "commands" in bash_tool:
            for cmd in bash_tool.get("commands", []):
                match = re.search(r'/scripts/([a-z_-]+\.js)', cmd)
                if match:
                    script_name = match.group(1)
                    if script_name not in scripts:
                        scripts.append(script_name)
        return scripts

    def extract_skills(self, stdout: str) -> list[str]:
        """Extracts skill names and script invocations from stream output.

        Returns list of skill execution items:
        - Skill names when activated
        - Script names (e.g., list_instances.js) when executed within skills
        """
        output_json = self.parse_response(stdout)
        try:
            by_name = output_json["stats"]["tools"]["byName"]
        except (KeyError, TypeError):
            return []

        installed_skills = self._get_installed_skills()
        items = []

        # Pattern 1: Direct skill tool names
        for tool_name in by_name:
            if tool_name in installed_skills and tool_name not in items:
                items.append(tool_name)

        # Pattern 2: "Skill" tool calls with skill parameters
        skill_tool = by_name.get("Skill", {})
        for params in skill_tool.get("parameters", []):
            name = params.get("skill") or params.get("name") or params.get("skill_name")
            if name and name not in items:
                items.append(name)

        # Pattern 3: Script names from bash executions
        items.extend(self._extract_script_names(by_name))

        return items

    def safe_generate(self, cli_cmd: CLICommand) -> subprocess.CompletedProcess:
        result = self.generate_internal(cli_cmd)
        if isinstance(result, str):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=result)

        if not result.stdout and result.returncode != 0:
            result.stderr += "\nError: Generator returned empty response."
        return result

    def create_command(
        self, cli: str, prompt: str, env: dict = None, resume: bool = False,
        session_id: str = None
    ) -> CLICommand:
        merged_env = self.env.copy()
        if env:
            merged_env.update(env)
        return CLICommand(
            cli=cli, prompt=prompt, env=merged_env,
            resume=resume, session_id=session_id
        )
