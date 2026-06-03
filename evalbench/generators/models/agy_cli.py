from .generator import QueryGenerator
from .tool_naming import canonicalize_agy_tool_name, parse_agy_mcp_tool_call
import collections
import subprocess
import os
import json
import logging
import re
import shutil
import sys
import dateutil.parser
from util.context import rpc_id_var

# Bare command name on PATH. agy's installer exposes no version pinning and the
# binary self-updates in the background, so there is nothing to configure.
AGY_CLI = "agy"

# Accepts a Secret Manager resource path with a numeric version or ``latest``.
# Unlike the shared ``get_db_secret`` helper (numeric only), agy OAuth tokens
# rotate, so ``latest`` is the common case.
_AGY_SECRET_PATH_RE = re.compile(
    r"^projects/[^/]+/secrets/[^/]+/versions/(\d+|latest)$"
)


def _fetch_agy_secret(path: str) -> str:
    """Returns the decoded payload of a Secret Manager version.

    ``path`` is ``projects/<p>/secrets/<s>/versions/<N|latest>``. The import is
    local to avoid pulling the Secret Manager client into non-secret runs.
    """
    if not _AGY_SECRET_PATH_RE.match(path):
        raise ValueError(
            f"Not a Secret Manager resource path "
            f"(projects/.../secrets/.../versions/<N|latest>): {path}"
        )
    from databases.util import access_secret

    return access_secret(path)


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, cwd=None):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.cwd = cwd


class AgyCliGenerator(QueryGenerator):
    """Generator that queries via the Antigravity CLI (``agy``).

    Surface targeted here is what the v1.0.3 binary actually exposes:
    ``agy -p <prompt> --dangerously-skip-permissions [--continue]``. The
    on-disk layout lives under ``~/.gemini/antigravity-cli/`` (the binary
    calls this ``appDataDir``). Skills are delivered via plugins: ``agy
    plugin install <target>`` reads a plugin manifest (Claude/Gemini/Codex
    formats), materializes any bundled skills under
    ``<HOME>/.gemini/config/plugins/<name>/`` and records the install in
    ``<HOME>/.gemini/config/import_manifest.json``. There is no
    ``--output-format`` flag and no stdout stream protocol; structured
    tool-call data is read out of the per-conversation JSONL transcript at
    ``<appDataDir>/brain/<uuid>/.system_generated/logs/transcript.jsonl``.
    """

    APP_DATA_SUBPATH = os.path.join(".gemini", "antigravity-cli")

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "agy_cli"

        self.env = querygenerator_config.get("env") or {}

        # Top-level `model` key. agy exposes no --model flag and reads no
        # model env var, so the model is selected by writing it into
        # settings.json -- see _initialize_settings_file. The value must be
        # the exact agy UI label (e.g. "Gemini 3.1 Pro (High)"), not an API
        # id; an unrecognized value is silently ignored and agy falls back
        # to its default model.
        self.model = querygenerator_config.get("model")

        # Optional Secret Manager sources for agy's auth files. When set, they
        # take precedence over mirroring from the host's real appDataDir -- the
        # canonical path for CI, where there is no interactive login on disk.
        # Values are Secret Manager resource paths
        # (projects/.../secrets/.../versions/<N|latest>), parallel to codex's
        # openai_api_key_secret.
        self.oauth_token_secret = querygenerator_config.get(
            "agy_oauth_token_secret"
        )
        self.installation_id_secret = querygenerator_config.get(
            "agy_installation_id_secret"
        )

        # Order is load-bearing: paths/dirs must exist before settings and
        # auth write into them, and self.env must carry HOME before auth
        # resolves ADC. Keep these calls in sequence.
        self._init_paths(querygenerator_config)
        self.env["HOME"] = self.fake_home
        self._initialize_settings_file()
        self._setup_auth()

        self.setup_config = querygenerator_config.get("setup", {})
        if self.setup_config:
            self._setup_tools()

    def _init_paths(self, querygenerator_config):
        """Resolves the sandbox ``HOME`` and all derived agy paths, and
        creates the directories agy will read/write."""
        self.real_home = os.environ.get("HOME", os.path.expanduser("~"))

        if sys.argv[0].endswith("eval_server.py"):
            session_id = querygenerator_config.get("session_id")
            if not session_id:
                ctx_id = rpc_id_var.get()
                session_id = ctx_id if ctx_id != "default" else "default"
            self.fake_home = os.path.join(
                "/tmp_sessions", session_id, "fake_home"
            )
        else:
            self.fake_home = os.path.abspath(
                os.path.join(".venv", "fake_home_agy")
            )

        self.app_data_dir = os.path.join(self.fake_home, self.APP_DATA_SUBPATH)
        self.settings_path = os.path.join(self.app_data_dir, "settings.json")
        self.config_dir = os.path.join(self.fake_home, ".gemini", "config")
        self.mcp_config_path = os.path.join(self.config_dir, "mcp_config.json")
        # agy records installed plugins (which carry the skills) here.
        self.plugin_manifest_path = os.path.join(
            self.config_dir, "import_manifest.json"
        )

        os.makedirs(self.fake_home, exist_ok=True)
        os.makedirs(self.app_data_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)

    def _setup_auth(self):
        """Seeds agy's OAuth state into the sandbox and wires up gcloud ADC
        so the sandboxed CLI authenticates without an interactive login."""
        self._mirror_agy_auth_state()

        adc_path = self.env.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not adc_path:
            adc_path = os.path.join(
                self.real_home,
                ".config",
                "gcloud",
                "application_default_credentials.json",
            )
            if os.path.exists(adc_path):
                self.env["GOOGLE_APPLICATION_CREDENTIALS"] = adc_path

        if adc_path and os.path.exists(adc_path):
            fake_gcloud_dir = os.path.join(self.fake_home, ".config", "gcloud")
            os.makedirs(fake_gcloud_dir, exist_ok=True)
            fake_adc_path = os.path.join(
                fake_gcloud_dir, "application_default_credentials.json"
            )
            if os.path.abspath(adc_path) != os.path.abspath(fake_adc_path):
                shutil.copy2(adc_path, fake_adc_path)

        if "CLOUDSDK_CONFIG" not in self.env:
            self.env["CLOUDSDK_CONFIG"] = os.path.join(
                self.real_home, ".config", "gcloud"
            )

    def _mirror_agy_auth_state(self):
        """Seeds agy's OAuth token + installation id into the sandboxed
        appDataDir so the sandboxed CLI does not re-prompt for an interactive
        login.

        agy is OAuth-only (no env-var API key, no ADC), and the harness
        overrides ``HOME``, so without this the sandbox looks like a
        brand-new install and ``agy -p`` blocks on the device-code URL.

        Each auth file is sourced independently, preferring Secret Manager
        when ``agy_oauth_token_secret`` / ``agy_installation_id_secret`` are
        configured (the CI path -- no interactive login exists on disk), and
        otherwise mirroring from the host's real appDataDir at
        ``~/.gemini/antigravity-cli/`` (the local-dev path -- run ``agy``
        once interactively to seed it; this then refreshes the copy on every
        run).
        """
        real_app_data = os.path.join(self.real_home, self.APP_DATA_SUBPATH)

        # The token is load-bearing (required=True); a missing installation_id
        # is non-fatal for agy (required=False).
        auth_files = (
            ("antigravity-oauth-token", self.oauth_token_secret, True),
            ("installation_id", self.installation_id_secret, False),
        )

        for fname, secret_path, required in auth_files:
            dst = os.path.join(self.app_data_dir, fname)

            if secret_path:
                try:
                    payload = _fetch_agy_secret(secret_path)
                except Exception as e:
                    msg = (
                        f"Failed to fetch agy {fname} from Secret Manager "
                        f"({secret_path}): {e}"
                    )
                    if required:
                        raise RuntimeError(msg) from e
                    logging.warning(msg)
                    continue
                with open(dst, "w") as f:
                    f.write(payload)
                os.chmod(dst, 0o600)
                logging.info(
                    "Seeded agy %s from Secret Manager (%s).",
                    fname, secret_path,
                )
                continue

            src = os.path.join(real_app_data, fname)
            if not os.path.exists(src):
                if required:
                    logging.warning(
                        "agy OAuth token not found at %s and no "
                        "agy_oauth_token_secret configured -- run `agy` "
                        "interactively once, or set the secret for CI.",
                        src,
                    )
                continue
            try:
                shutil.copy2(src, dst)
                os.chmod(dst, 0o600)
            except OSError as e:
                logging.warning(
                    "Failed to mirror agy auth file %s -> %s: %s",
                    src, dst, e,
                )

    def _initialize_settings_file(self):
        """Writes the ``gcp.project``/``gcp.location`` block into agy's
        ``settings.json``.

        This block is load-bearing: agy resolves the project for its Vertex
        model backend from ``settings.json`` -> ``gcp.project``, **not** from
        the ``GOOGLE_CLOUD_PROJECT`` env var (verified empirically -- with the
        block removed, every ``agy -p`` turn returns an empty response and
        makes no tool calls, even though ``GOOGLE_CLOUD_PROJECT`` is exported
        and the MCP server still attaches). This is why agy is the only
        harness that writes a gcp block; the others pass the project purely
        through the environment.
        """
        current_settings = {}
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r") as f:
                    current_settings = json.load(f)
            except json.JSONDecodeError:
                logging.warning(
                    "Invalid JSON in agy settings at %s; using defaults.",
                    self.settings_path,
                )

        gcp_config = current_settings.setdefault("gcp", {})

        env_project = self.env.get("GOOGLE_CLOUD_PROJECT")
        env_location = self.env.get("GOOGLE_CLOUD_LOCATION")

        # Resolve project/location/model, preferring (in order): the env /
        # config, then any values the sandbox settings.json already carries
        # from a previous run. The model value is an agy UI label like
        # "Gemini 3.1 Pro (High)" -- confirmed by selecting a model via the
        # interactive `/model` command and diffing settings.json; an
        # unrecognized value (e.g. an API id like "gemini-2.5-pro") is
        # silently ignored and agy falls back to its default model.
        project = env_project or gcp_config.get("project")
        location = env_location or gcp_config.get("location")
        model = self.model or current_settings.get("model")

        # Only consult the host's real settings.json for whatever the env and
        # sandbox file did not already supply. When the sandbox already covers
        # everything we skip the read entirely -- both to avoid the extra I/O
        # and to avoid noise from an empty/absent real file, which is a normal
        # state for sandboxed and CI runs.
        if project and location and model:
            logging.info(
                "agy settings: project/location/model satisfied by env and "
                "sandbox %s; skipping real settings.json read.",
                self.settings_path,
            )
        else:
            real_settings = self._read_real_settings()
            real_gcp = real_settings.get("gcp", {})
            project = project or real_gcp.get("project")
            location = location or real_gcp.get("location")
            model = model or real_settings.get("model")

        location = location or "global"

        if project:
            gcp_config["project"] = project
        if location:
            gcp_config["location"] = location
        if model:
            current_settings["model"] = model

        logging.info(
            "agy settings resolved: project=%s location=%s model=%s",
            project, location, model,
        )

        with open(self.settings_path, "w") as f:
            json.dump(current_settings, f, indent=2)

    def _read_real_settings(self) -> dict:
        """Reads the host's real ``settings.json`` as a fallback source for
        project/location/model. Returns ``{}`` when the file is absent or
        empty -- both are normal states (e.g. sandboxed/CI runs, or a fresh
        agy install) and not worth a warning. Only genuinely malformed
        (non-empty, non-JSON) content is warned about.
        """
        path = os.path.join(
            self.real_home, self.APP_DATA_SUBPATH, "settings.json"
        )
        if not os.path.exists(path):
            logging.info(
                "agy real settings.json not present at %s; using defaults.",
                path,
            )
            return {}
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
        except OSError as e:
            logging.warning(
                "Failed to read real settings.json %s: %s", path, e
            )
            return {}
        if not raw:
            logging.info(
                "agy real settings.json at %s is empty; using defaults.", path,
            )
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logging.warning(
                "Ignoring malformed real settings.json at %s: %s", path, e,
            )
            return {}

    def _setup_tools(self):
        """Performs initial setup for agy CLI."""
        mcp_servers_config = self.setup_config.get("mcp_servers", {})
        self._setup_mcp_servers(mcp_servers_config)
        if "fake_mcp_servers" in self.setup_config:
            self._setup_mcp_servers(self.setup_config["fake_mcp_servers"])

        skills_config = self.setup_config.get("skills", [])
        self._setup_skills(skills_config)

        # Probe agy once now -- before any scenarios run -- to detect
        # account-eligibility / MCP-load failures and fail fast with a
        # clear error instead of silently degrading to gcloud shell-outs.
        configured_servers = list(mcp_servers_config or {}) + list(
            self.setup_config.get("fake_mcp_servers", {}) or {}
        )
        if configured_servers:
            self._verify_mcp_runtime(configured_servers)

    # Transcript step type agy's runtime writes for an executed MCP call.
    # A genuine ``call_mcp_tool`` wrapper invocation is always recorded as a
    # dedicated ``MCP_TOOL`` result step (native tools get VIEW_FILE,
    # RUN_COMMAND, etc.), so this is the signal that the wrapper actually ran.
    _AGY_MCP_RESULT_TYPE = "MCP_TOOL"

    # Transcript step types / fields used while parsing a turn.
    _STEP_USER_INPUT = "USER_INPUT"
    _STEP_PLANNER_RESPONSE = "PLANNER_RESPONSE"
    _STEP_STATUS_DONE = "DONE"
    # ``source`` value agy stamps on every model-emitted step (tool results
    # and planner responses alike).
    _SOURCE_MODEL = "MODEL"
    # MODEL steps that are not themselves results (they carry no tool output).
    _NON_RESULT_MODEL_TYPES = (
        None, "PLANNER_RESPONSE", "CONVERSATION_HISTORY", "GENERIC",
    )

    # Transcripts carry no token counts, so every token bucket is zero.
    _ZERO_TOKENS = {
        "input": 0, "prompt": 0, "candidates": 0,
        "total": 0, "cached": 0, "thoughts": 0, "tool": 0,
    }

    # Fatal log-line markers that mean MCP will not work in this run.
    _MCP_FATAL_MARKERS = (
        "Account ineligible",
        "failed to read mcp_config",
        "invalid mcp_config",
        "failed to start mcp instance",
        "failed to parse mcp_config_json",
    )

    def _verify_mcp_runtime(self, configured_servers: list):
        """Spawns a short-lived ``agy -p`` probe and confirms each
        configured MCP server actually attached and discovered tools.

        The authoritative signal is on disk: when agy attaches an MCP
        server it discovers the server's tools and writes one JSON schema
        file per tool to ``<appDataDir>/mcp/<server>/<tool>.json`` (the
        lazy-load schema cache that ``call_mcp_tool`` later reads). This
        happens at attach time, independent of what the model does -- a
        noop probe prompt still populates it -- so a server that fails to
        attach (e.g. a config with the wrong URL field, which agy accepts
        silently and exposes zero tools) leaves its directory empty. The
        old approach of only grepping ``cli.log`` for fatal markers could
        not catch that silent failure, which is exactly what made the
        agent fall back to ``gcloud`` shell-outs.

        We clear each server's schema dir first so the check reflects this
        run's config and never passes on a stale cache. Fatal log markers
        are still scanned to enrich the error message. Timeout is bounded
        so a backend hang doesn't stall the whole eval.
        """
        mcp_schema_root = os.path.join(self.app_data_dir, "mcp")
        for server in configured_servers:
            stale = os.path.join(mcp_schema_root, server)
            if os.path.isdir(stale):
                shutil.rmtree(stale, ignore_errors=True)

        log_dir = os.path.join(self.app_data_dir, "log")
        before = set(os.listdir(log_dir)) if os.path.isdir(log_dir) else set()

        env = self._merged_env()
        cmd = self._base_agy_command(AGY_CLI, "ping")
        try:
            subprocess.run(
                cmd, env=env, cwd=self.fake_home,
                capture_output=True, text=True, timeout=120, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(
                f"agy MCP verification probe failed to run: {e}. "
                f"Configured MCP servers: {configured_servers}.",
            )

        # Collect fatal log markers (diagnostic context for any failure).
        after = set(os.listdir(log_dir)) if os.path.isdir(log_dir) else set()
        new_logs = sorted(after - before)
        marker_hits = []
        if new_logs:
            probe_log = os.path.join(log_dir, new_logs[-1])
            try:
                with open(probe_log, "r") as f:
                    for line in f:
                        if any(m in line for m in self._MCP_FATAL_MARKERS):
                            marker_hits.append(line.rstrip())
            except OSError as e:
                logging.warning(
                    "agy MCP probe log %s unreadable: %s", probe_log, e,
                )

        # Authoritative check: each server must have discovered >=1 tool.
        failed = []
        loaded = {}
        for server in configured_servers:
            server_dir = os.path.join(mcp_schema_root, server)
            tools = []
            if os.path.isdir(server_dir):
                tools = [
                    f[:-len(".json")]
                    for f in os.listdir(server_dir)
                    if f.endswith(".json")
                ]
            if tools:
                loaded[server] = sorted(tools)
            else:
                failed.append(server)

        if failed:
            msg = (
                f"agy MCP server(s) {failed} attached no tools "
                f"(no schemas under {mcp_schema_root}/<server>/). The "
                "server likely failed to load -- check the URL field "
                "(agy uses 'serverUrl', not 'httpUrl'), auth, and "
                "reachability. agy degrades silently to shell-outs when "
                "MCP tools are missing."
            )
            if marker_hits:
                msg += "\nProbe log fatal markers:\n" + "\n".join(
                    f"  {h}" for h in marker_hits
                )
            raise RuntimeError(msg)

        for server, tools in loaded.items():
            logging.info(
                "agy MCP server '%s' attached %d tools: %s",
                server, len(tools), tools,
            )

    # A target is a git URL (to be cloned) rather than a local path or a
    # ``plugin@marketplace`` spec when it carries a remote scheme or ends
    # in ``.git``.
    _GIT_URL_PATTERN = re.compile(r"^(https?|git|ssh)://|^git@|\.git(#.*)?$")

    def _setup_skills(self, skills: list):
        """Installs skill-bearing plugins via ``agy plugin install``.

        Verified against agy v1.0.3: ``agy plugin install <target>`` reads
        a plugin manifest (Claude/Gemini/Codex formats), processes any
        bundled skills, materializes them under
        ``<HOME>/.gemini/config/plugins/<name>/`` and records the install
        in ``<HOME>/.gemini/config/import_manifest.json``. There is no
        ``skill`` subcommand and dropping SKILL.md folders on disk
        registers nothing.

        Two input shapes are supported, matching the cross-CLI convention
        used by codex_cli and claude_code:

        * ``"<target>"`` -- a local plugin directory, a ``plugin@marketplace``
          spec, or a git URL. Git URLs are cloned first, then installed.
        * ``{"action": "install_from_repo", "url"|"path": "..."}`` -- same,
          via an explicit dict.
        """
        if not skills:
            return

        clone_workdir = os.path.join(self.app_data_dir, ".skill_clones")
        os.makedirs(clone_workdir, exist_ok=True)

        setup_env = self._merged_env()

        installed_any = False
        for skill_config in skills:
            target = self._resolve_skill_target(skill_config)
            if not target:
                continue
            if self._GIT_URL_PATTERN.search(target):
                target = self._clone_skill_repo(
                    target, clone_workdir, setup_env
                )
                if not target:
                    continue
            if self._install_agy_plugin(target, setup_env):
                installed_any = True

        if installed_any:
            self._log_installed_plugins()

    def _resolve_skill_target(self, skill_config) -> str:
        """Maps a skills-config entry to an ``agy plugin install`` target.

        Returns an install target (local dir, ``plugin@marketplace`` spec,
        or git URL) or an empty string when the entry is unusable.
        """
        if isinstance(skill_config, str):
            return skill_config
        if isinstance(skill_config, dict):
            action = skill_config.get("action")
            if action == "install_from_repo":
                target = skill_config.get("url") or skill_config.get("path")
                if not target:
                    logging.warning(
                        "install_from_repo requires 'url' or 'path': %s",
                        skill_config,
                    )
                return target or ""
            logging.warning(
                "Unsupported skill action %r; use a string target or "
                "install_from_repo.",
                action,
            )
            return ""
        logging.warning("Unsupported skill config entry: %r", skill_config)
        return ""

    def _install_agy_plugin(self, target: str, env: dict) -> bool:
        """Runs ``agy plugin install <target>``; returns True on success."""
        cmd = [AGY_CLI, "plugin", "install", target]
        result = self._execute_cli_command(cmd, env=env, cwd=self.fake_home)
        if result.returncode != 0:
            logging.error(
                "agy plugin install '%s' failed (rc=%s): %s",
                target, result.returncode,
                (result.stderr or result.stdout or "").strip(),
            )
            return False
        logging.info("Installed agy plugin from '%s'", target)
        return True

    def _log_installed_plugins(self):
        """Logs plugin names registered in the agy import manifest."""
        try:
            with open(self.plugin_manifest_path, "r") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logging.warning(
                "Could not read agy plugin manifest at %s: %s",
                self.plugin_manifest_path, e,
            )
            return
        plugins = manifest.get("plugins", manifest)
        if isinstance(plugins, dict):
            names = sorted(plugins.keys())
        elif isinstance(plugins, list):
            names = sorted(
                p.get("name", str(p)) if isinstance(p, dict) else str(p)
                for p in plugins
            )
        else:
            names = []
        logging.info("agy registered plugins: %s", names)

    def _clone_skill_repo(self, url: str, workdir: str, env: dict):
        """Clones a skill repo. Supports ``<url>#<tag>`` pinning.

        Returns the clone directory on success, or None on failure.
        """
        clone_url, _, version_tag = url.partition("#")
        repo_name = re.sub(r"\.git$", "", clone_url.rstrip("/").split("/")[-1])
        clone_target = os.path.join(workdir, repo_name)
        if os.path.exists(clone_target):
            shutil.rmtree(clone_target)

        cmd = ["git", "clone", "--depth", "1"]
        if version_tag:
            cmd.extend(["--branch", version_tag])
        cmd.extend([clone_url, clone_target])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                env=env, timeout=120,
            )
            if result.returncode != 0:
                logging.error(
                    "Failed to clone repo '%s': %s", url, result.stderr.strip()
                )
                return None
            logging.info("Cloned agy skill repo '%s' to %s", url, clone_target)
            return clone_target
        except subprocess.TimeoutExpired:
            logging.error("Cloning repo '%s' timed out", url)
            return None

    def _setup_mcp_servers(self, mcp_servers_config: dict):
        """Writes MCP servers into ``<HOME>/.gemini/config/mcp_config.json``
        under the ``mcpServers`` key.

        The path and key are verified from the agy binary itself: the
        load-error string ``Failed to load JSON config file
        <HOME>/.gemini/config/mcp_config.json`` reveals the path, and
        the binary's struct tag ``json:"mcpServers"`` (from
        ``struct { McpServers map[string]interface {} }``) reveals the
        key. agy has no offline verification subcommand, so this step
        only writes config -- it does not confirm the server actually
        loads. Failures will surface at eval time via the transcript.
        """
        if not mcp_servers_config:
            return

        current_config = {}
        if os.path.exists(self.mcp_config_path):
            try:
                with open(self.mcp_config_path, "r") as f:
                    raw = f.read().strip()
                    if raw:
                        current_config = json.loads(raw)
            except json.JSONDecodeError:
                logging.warning(
                    "Invalid JSON in agy mcp_config at %s; overwriting.",
                    self.mcp_config_path,
                )

        existing = current_config.setdefault("mcpServers", {})
        for stale in [k for k in existing if k not in mcp_servers_config]:
            logging.info("Removing stale MCP server configuration: %s", stale)
            del existing[stale]
        for server_name, config in mcp_servers_config.items():
            existing[server_name] = self._translate_mcp_config(dict(config))
            logging.info("Configured MCP server: %s", server_name)

        with open(self.mcp_config_path, "w") as f:
            json.dump(current_config, f, indent=2)

    @staticmethod
    def _translate_mcp_config(config: dict) -> dict:
        """Normalizes a cross-harness MCP server config into agy's schema.

        agy's HTTP transport field is ``serverUrl`` (Windsurf/cortex
        lineage), confirmed from the v1.0.3 binary's config struct
        (``ServerUrl *string json:"serverUrl"``). There is no ``httpUrl``
        field, so a gemini-style ``httpUrl`` value would be parsed to a
        nil URL -- the server attaches with no transport and exposes no
        tools, which is exactly the silent failure that made the agent
        fall back to ``gcloud`` shell-outs. We therefore map the common
        gemini alias ``httpUrl`` onto ``serverUrl``.

        Everything else passes through untouched: ``authProviderType``
        (``google_credentials`` is a valid enum), ``oauth.scopes``,
        ``headers``, and the stdio fields (``command``/``args``/``env``)
        are all native agy schema fields, so no Bearer-token injection is
        needed (unlike claude_code).
        """
        if "httpUrl" in config and "serverUrl" not in config:
            config["serverUrl"] = config.pop("httpUrl")
        return config

    def _merged_env(self, extra: dict | None = None) -> dict:
        """Returns the process environment overlaid with the generator's
        configured env (and an optional per-call ``extra``)."""
        env = os.environ.copy()
        env.update(self.env)
        if extra:
            env.update(extra)
        return env

    @staticmethod
    def _base_agy_command(cli: str, prompt: str, resume: bool = False) -> list:
        """Builds the non-interactive ``agy -p`` argv shared by the eval
        turn path and the setup-time MCP probe."""
        command = [cli, "-p", prompt, "--dangerously-skip-permissions"]
        if resume:
            command.append("--continue")
        return command

    def generate_internal(self, cli_cmd):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(AGY_CLI, str(cli_cmd))
        return self._run_agy_cli(cli_cmd)

    def _execute_cli_command(
        self, command, env=None, cwd=None
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=cwd if cwd else self.fake_home,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                command, 127, "", f"Error: Command not found: {command[0]}"
            )
        except OSError as e:
            logging.warning("agy CLI invocation failed: %s", e)
            return subprocess.CompletedProcess(
                command, 1, "", f"An unexpected error occurred: {e}"
            )

    def _run_agy_cli(self, cli_cmd: CLICommand):
        env = self._merged_env(cli_cmd.env)
        command = self._base_agy_command(
            cli_cmd.cli, cli_cmd.prompt, cli_cmd.resume
        )
        cwd = cli_cmd.cwd if cli_cmd.cwd else self.fake_home
        result = self._execute_cli_command(command, env=env, cwd=cwd)

        if result.returncode == 0:
            try:
                result.stdout = self._parse_transcript_jsonl(
                    cwd, fallback_response=result.stdout or "",
                )
            except Exception:
                logging.exception(
                    "Failed to parse agy transcript for cwd=%s", cwd,
                )

        return result

    @staticmethod
    def _unwrap_agy_mcp_args(raw_args: dict, is_mcp: bool) -> dict:
        """Returns the real MCP-tool arguments for a ``call_mcp_tool`` call.

        The wrapper's ``Arguments`` field is a JSON ``RawMessage`` -- it may
        arrive already parsed (dict) or as a JSON-encoded string. For native
        (non-MCP) tools (``is_mcp`` False) the args are returned unchanged.
        """
        if not is_mcp:
            return raw_args
        for key in ("Arguments", "arguments", "args"):
            if key in raw_args:
                inner = raw_args[key]
                if isinstance(inner, str):
                    try:
                        return json.loads(inner)
                    except (json.JSONDecodeError, ValueError):
                        return {"_raw": inner}
                if isinstance(inner, dict):
                    return inner
                return {"_raw": inner}
        return {}

    def _conversation_id_for_cwd(self, cwd: str):
        cache_path = os.path.join(
            self.app_data_dir, "cache", "last_conversations.json"
        )
        try:
            with open(cache_path, "r") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(cache, dict):
            return None
        return cache.get(os.path.abspath(cwd))

    def _read_transcript(self, conversation_id: str):
        transcript_path = os.path.join(
            self.app_data_dir, "brain", conversation_id,
            ".system_generated", "logs", "transcript.jsonl",
        )
        steps = []
        try:
            with open(transcript_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        steps.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return steps

    def _parse_transcript_jsonl(
        self, cwd: str, fallback_response: str = "",
    ) -> str:
        """Builds the same envelope as the old ``_parse_stream_json``
        output, sourcing tool calls and the assistant response from the
        per-conversation JSONL transcript that agy writes under
        ``<appDataDir>/brain/<uuid>/.system_generated/logs/``.

        Only the most-recent turn is reported -- the transcript
        accumulates across turns when ``--continue`` is used, so the
        slice from the last ``USER_INPUT`` step onward is the new
        material from this invocation.
        """
        # Transcripts don't carry token counts; downstream
        # token_consumption scorers will see zeros.
        final_obj = {"session_id": "", "response": "", "stats": {}}

        conversation_id = self._conversation_id_for_cwd(cwd)
        if not conversation_id:
            final_obj["response"] = fallback_response
            return json.dumps(final_obj, indent=2)
        final_obj["session_id"] = conversation_id

        steps = self._read_transcript(conversation_id)
        if not steps:
            final_obj["response"] = fallback_response
            return json.dumps(final_obj, indent=2)

        # Take only the steps from the last USER_INPUT onward (this turn).
        last_user_idx = max(
            (i for i, s in enumerate(steps)
             if s.get("type") == self._STEP_USER_INPUT),
            default=-1,
        )
        turn_steps = steps[last_user_idx:] if last_user_idx >= 0 else steps

        calls = []          # ordered (call_dict, ts)
        result_for = {}     # call index -> (result_step, ts)
        # call indices awaiting an adjacent result
        pending = collections.deque()
        response_text_parts = []
        turn_start_ts = None
        turn_end_ts = None

        # agy records a tool call on a ``PLANNER_RESPONSE`` step and the
        # tool's result on the *immediately following* MODEL step (MCP_TOOL
        # for genuine MCP calls; VIEW_FILE / RUN_COMMAND / ... for native
        # tools). We pair by strict adjacency: a call is bound only to the
        # result step that directly follows the planner step that emitted it.
        # The pending window is reset at every new planner step and at any
        # other intervening step, so a call that never produced a runtime
        # result -- e.g. a ``call_mcp_tool`` line the agent forged via
        # ``run_command`` -- stays unpaired instead of stealing a later
        # call's result. (An earlier FIFO scheme paired across steps and
        # mis-attributed a forged MCP call to a subsequent shell-out's result.)
        for step in turn_steps:
            ts = step.get("created_at")
            if ts and turn_start_ts is None:
                turn_start_ts = ts
            if ts:
                turn_end_ts = ts

            stype = step.get("type")
            if stype == self._STEP_PLANNER_RESPONSE:
                # A new planner step ends the previous adjacency window.
                pending.clear()
                tool_calls = step.get("tool_calls") or []
                if tool_calls:
                    for call in tool_calls:
                        pending.append(len(calls))
                        calls.append((call, ts))
                else:
                    content = step.get("content")
                    if content:
                        response_text_parts.append(content)
            elif (step.get("source") == self._SOURCE_MODEL
                  and stype not in self._NON_RESULT_MODEL_TYPES):
                # A result step consumes the next call awaiting in the current
                # adjacency window. Consecutive results pair with consecutive
                # calls from the same planner step (rare multi-call case).
                if pending:
                    result_for[pending.popleft()] = (step, ts)
            else:
                # Any other intervening step breaks adjacency.
                pending.clear()

        final_obj["response"] = "\n".join(response_text_parts).strip() \
            or fallback_response

        # Approximate end-to-end latency from transcript timestamps.
        total_duration_ms = 0
        if turn_start_ts and turn_end_ts:
            try:
                t0 = dateutil.parser.isoparse(turn_start_ts)
                t1 = dateutil.parser.isoparse(turn_end_ts)
                total_duration_ms = int((t1 - t0).total_seconds() * 1000)
            except (ValueError, TypeError):
                total_duration_ms = 0

        tools_by_name = {}
        for idx, (call, call_ts) in enumerate(calls):
            raw_name = call.get("name", "unknown")
            raw_args = call.get("args", {}) or {}
            # agy wraps every MCP invocation in the native ``call_mcp_tool``
            # tool; the real server/tool identity and arguments live in the
            # wrapper's args. Canonicalize to ``<server>__<tool>`` and surface
            # the unwrapped arguments so trajectory/parameter scorers compare
            # against the actual MCP call, not the wrapper envelope.
            is_mcp = parse_agy_mcp_tool_call(raw_name, raw_args) is not None
            tname = canonicalize_agy_tool_name(raw_name, raw_args)
            call_args = self._unwrap_agy_mcp_args(raw_args, is_mcp)
            slot = tools_by_name.setdefault(tname, {
                "count": 0, "success": 0, "fail": 0, "durationMs": 0,
                "parameters": [],
                "decisions": {
                    "accept": 0, "reject": 0, "modify": 0, "auto_accept": 0,
                },
            })
            slot["count"] += 1
            slot["parameters"].append(call_args)
            slot["decisions"]["accept"] += 1
            slot["decisions"]["auto_accept"] += 1

            duration = 0
            paired = result_for.get(idx)
            if paired:
                result_step, result_ts = paired
                done = result_step.get("status") == self._STEP_STATUS_DONE
                # A genuine MCP invocation is executed by agy's runtime and
                # recorded as a dedicated ``MCP_TOOL`` result step. If a
                # ``call_mcp_tool`` wrapper is paired with any other result
                # type, it did not actually run as an MCP call, so we do not
                # credit it as a success. This bounds (it cannot fully
                # prevent) crediting transcript lines an agent may have forged
                # via shell-outs; the authoritative attach guarantee is the
                # setup-time schema-cache check in ``_verify_mcp_runtime``.
                if (is_mcp and result_step.get("type")
                        != self._AGY_MCP_RESULT_TYPE):
                    done = False
                if done:
                    slot["success"] += 1
                else:
                    slot["fail"] += 1
                if call_ts and result_ts:
                    try:
                        t1 = dateutil.parser.isoparse(call_ts)
                        t2 = dateutil.parser.isoparse(result_ts)
                        duration = int((t2 - t1).total_seconds() * 1000)
                    except (ValueError, TypeError):
                        duration = 0
            elif is_mcp:
                # An MCP wrapper call with no runtime result step never
                # executed -- count it as a failure rather than silently
                # leaving it neutral.
                slot["fail"] += 1
            slot["durationMs"] += duration

        tools_stats = {
            "totalCalls": len(calls),
            "totalSuccess": sum(s["success"] for s in tools_by_name.values()),
            "totalFail": sum(s["fail"] for s in tools_by_name.values()),
            "totalDurationMs": sum(
                s["durationMs"] for s in tools_by_name.values()
            ),
            "decisions": {
                "accept": len(calls),
                "reject": 0,
                "modify": 0,
                "auto_accept": len(calls),
            },
            "byName": tools_by_name,
        }

        # The transcript does not echo the model name, so key the bucket
        # under the configured model label (matching claude_code/codex_cli),
        # falling back to "agy" when no model is configured.
        model_name = self.model or "agy"
        models = {
            model_name: {
                "api": {
                    "totalRequests": 1,
                    "totalErrors": 0,
                    "totalLatencyMs": total_duration_ms,
                },
                "tokens": dict(self._ZERO_TOKENS),
                "roles": {
                    "main": {
                        "totalRequests": 1,
                        "totalErrors": 0,
                        "totalLatencyMs": total_duration_ms,
                        "tokens": dict(self._ZERO_TOKENS),
                    },
                },
            }
        }

        final_obj["stats"]["models"] = models
        final_obj["stats"]["tools"] = tools_stats
        return json.dumps(final_obj, indent=2)

    def parse_response(self, stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            logging.error("Failed to parse JSON response: %s...", stdout[:100])
            return {}

    def extract_tools(self, stdout: str) -> list:
        """Extracts the list of tools used from the CLI output."""
        output_json = self.parse_response(stdout)
        try:
            return list(output_json["stats"]["tools"]["byName"].keys())
        except (KeyError, TypeError):
            return []

    def extract_skills(self, stdout: str) -> list:
        """Extracts activated skill names from the activate_skill tool."""
        output_json = self.parse_response(stdout)
        try:
            by_name = output_json["stats"]["tools"]["byName"]
            activate_calls = by_name.get("activate_skill", {})
            parameters_list = activate_calls.get("parameters", [])
            skills = []
            for params in parameters_list:
                skill_name = (
                    params.get("skill_name")
                    or params.get("skillName")
                    or params.get("skill")
                    or params.get("name")
                )
                if skill_name and skill_name not in skills:
                    skills.append(skill_name)
            return skills
        except (KeyError, TypeError):
            return []

    def safe_generate(
        self, cli_cmd: CLICommand
    ) -> subprocess.CompletedProcess:
        result = self.generate_internal(cli_cmd)
        if isinstance(result, str):
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=result
            )

        if not result.stdout and result.returncode != 0:
            result.stderr += "\nError: Generator returned empty response."
        return result

    def create_command(
        self, cli: str, prompt: str, env: dict = None, resume: bool = False,
        cwd: str = None,
    ) -> CLICommand:
        # Only the per-call overrides are stored here; the generator's
        # configured ``self.env`` and the process environment are layered in
        # once at invocation time by ``_run_agy_cli`` via ``_merged_env``.
        return CLICommand(cli=cli, prompt=prompt, env=env or {},
                          resume=resume, cwd=cwd)
